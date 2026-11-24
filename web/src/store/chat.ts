/** 对话状态 store：草稿流式、消息历史、过程条、确认卡片与 SSE 接线。

两条硬约束（见计划 T5）：
- 约束 1：done 消息 content 可能为空字符串（知识库不可用时），照常落 history，不丢弃、不报错。
- 约束 2：busy 只能由 endTurn / failTurn 关闭；tool_status / delta / chart
  等中间事件绝不关闭 busy（后端不保证发 tool_status end，见 agent_runtime）。
*/
import { create } from 'zustand'
import { streamChat, type SseEvent } from '@/lib/sse'
import {
  chartFromEvent,
  type ChatMode,
  type ChartItem,
  type DataCardPayload,
  type ScriptCardPayload,
  type WireMessage,
} from '@/lib/types'

export type NoticeKind = 'info' | 'error'

export interface ChatNotice {
  kind: NoticeKind
  text: string
}

/** turn 收尾原因；error 不走 endTurn（由 error 事件 / catch 的 failTurn 处理）。 */
export type TurnEndReason = 'done' | 'aborted' | 'closed'

/** 后端 tool_status 事件的一条记录；仅用于过程展示，绝不参与 busy 收尾（约束 2）。 */
export interface ToolStatusEntry {
  name: string
  phase: 'start' | 'end'
  label?: string
  /** 检索类工具结束时的结果条数；仅用于展示事实，不代表成功/完成。 */
  count?: number
  at?: number
}

/** 思考条最多保留的字符数；超出后停止追加并就地提示已截断。 */
export const REASONING_CHAR_LIMIT = 20000

/** 本轮思考的界面状态；只用于展示，不进入正文、历史或持久化。 */
export interface ReasoningState {
  text: string
  /** 当前连续思考段的开始时间；null 表示此刻不在输出思考。 */
  startedAt: number | null
  /** 已完成思考段的累计毫秒，不含工具等待时间。 */
  elapsedMs: number
  /** 最终挂靠的助手消息 id；done 或本地 partial 落库时写入。 */
  messageId: number | null
  truncated: boolean
  expanded: boolean
  /** 用户手动展开/收起过；此后本轮不再自动改变展开状态。 */
  toggled: boolean
}

/** 清空本轮思考；新会话、新轮次和失败收尾共用。 */
export const emptyReasoning = (): ReasoningState => ({
  text: '',
  startedAt: null,
  elapsedMs: 0,
  messageId: null,
  truncated: false,
  expanded: false,
  toggled: false,
})

/** 结束当前思考段并累计耗时；不在思考中则原样返回。 */
function pauseReasoning(state: ReasoningState, at: number): ReasoningState {
  if (state.startedAt === null) return state
  return {
    ...state,
    startedAt: null,
    elapsedMs: state.elapsedMs + Math.max(0, at - state.startedAt),
  }
}

/** 知识问答阶段顺序：只用于丢弃迟到的旧阶段事件，避免状态文案回退。 */
const KNOWLEDGE_QA_STAGE_ORDER: Record<string, number> = {
  knowledge_qa_retrieval: 1,
  knowledge_qa_answer: 2,
}

interface ChatState {
  sessionId: string | null
  history: WireMessage[]
  draft: string
  reasoning: ReasoningState
  /** 流式进行中；只能被 endTurn / failTurn 关闭（约束 2） */
  busy: boolean
  tools: ToolStatusEntry[]
  notice: ChatNotice | null
  /** 本轮 chart 事件累积，done.message 无图表时兜底 */
  charts: ChartItem[]
  scriptCard: ScriptCardPayload | null
  dataCard: DataCardPayload | null
  /** 会话级：上下文接近压缩阈值；beginTurn 保留，setSession 复位 */
  softWarn: boolean

  setSession: (sessionId: string, history: WireMessage[]) => void
  /** 删除最后一个会话：中止活动流并完整清空，保留 null 表示无活动会话。 */
  clearSession: () => void
  beginTurn: (prompt: string) => void
  appendDelta: (text: string) => void
  appendReasoning: (text: string) => void
  toggleReasoning: () => void
  pushTool: (entry: ToolStatusEntry) => void
  pushChart: (item: ChartItem) => void
  setScriptCard: (payload: ScriptCardPayload | null) => void
  setDataCard: (payload: DataCardPayload | null) => void
  setNotice: (notice: ChatNotice | null) => void
  /** done 事件：把助手消息落进 history（约束 1：空内容照常记录） */
  finalizeTurn: (message: WireMessage) => void
  /** 非 SSE 的成功结果（如确认执行返回的助手消息）直接追加到 history */
  appendMessage: (message: WireMessage) => void
  /** done / aborted / closed 的幂等收尾：关 busy、清过程条；error 不走这里 */
  endTurn: (reason: TurnEndReason) => void
  /** error 收尾：关 busy + 写错误提示；busy 已关时幂等（不覆盖已有提示） */
  failTurn: (message: string) => void
  /** 发起一轮对话：beginTurn → streamChat 接线；busy 或无会话时直接返回 */
  send: (prompt: string, mode?: ChatMode) => Promise<void>
  /** 中止当前流：只 abort 信号，busy 等 onEnd(aborted) 收尾才关 */
  stop: () => void
}

export function createChatStore() {
  /** 本地 user 消息用负 id/seq，与服务端正 id 区分。 */
  let localSeq = 0
  /** 当前进行中的流；stop() 通过它 abort。 */
  let activeStream: AbortController | null = null

  return create<ChatState>((set, get) => {
    const resetSession = (sessionId: string | null, history: WireMessage[]): void => {
      activeStream?.abort()
      activeStream = null
      set({
        sessionId,
        history,
        draft: '',
        reasoning: emptyReasoning(),
        busy: false,
        tools: [],
        notice: null,
        charts: [],
        scriptCard: null,
        dataCard: null,
        softWarn: false,
      })
    }

    /** SSE 事件分发：只推进状态；唯一允许关 busy 的分支是 error → failTurn（约束 2）。 */
    const dispatch = (event: SseEvent): void => {
      switch (event.type) {
        case 'delta':
          if (typeof event.text === 'string') {
            if (event.source === 'reasoning') get().appendReasoning(event.text)
            else get().appendDelta(event.text)
          }
          break
        case 'tool_status': {
          const phase = event.phase
          if (typeof event.name !== 'string' || (phase !== 'start' && phase !== 'end')) break
          get().pushTool({
            name: event.name,
            phase,
            label: typeof event.label === 'string' ? event.label : undefined,
            count: typeof event.count === 'number' ? event.count : undefined,
          })
          break
        }
        case 'confirm_card':
          if (event.kind === 'script' && event.payload) {
            get().setScriptCard(event.payload as ScriptCardPayload)
          } else if (event.kind === 'data' && event.payload) {
            get().setDataCard(event.payload as DataCardPayload)
          }
          break
        case 'chart': {
          const plotly = typeof event.plotly_json === 'string' ? event.plotly_json : undefined
          const image = typeof event.image_b64 === 'string' ? event.image_b64 : undefined
          if (plotly !== undefined || image !== undefined) {
            get().pushChart(
              chartFromEvent({ type: 'chart', plotly_json: plotly, image_b64: image }),
            )
          }
          break
        }
        case 'compressed':
          if (typeof event.soft_warn === 'boolean') set({ softWarn: event.soft_warn })
          break
        case 'done':
          if (event.message) get().finalizeTurn(event.message as WireMessage)
          break
        case 'error':
          get().failTurn(
            typeof event.message === 'string' && event.message ? event.message : '对话发生错误',
          )
          break
        default:
          break
      }
    }

    return {
      sessionId: null,
      history: [],
      draft: '',
      reasoning: emptyReasoning(),
      busy: false,
      tools: [],
      notice: null,
      charts: [],
      scriptCard: null,
      dataCard: null,
      softWarn: false,

      setSession(sessionId, history) {
        resetSession(sessionId, history)
      },

      clearSession() {
        resetSession(null, [])
      },

      beginTurn(prompt) {
        localSeq += 1
        const user: WireMessage = {
          id: -localSeq,
          session_id: get().sessionId ?? '',
          seq: -localSeq,
          role: 'user',
          content: prompt,
          timestamp: new Date().toISOString(),
          results: null,
          pending_script: null,
        }
        set((state) => ({
          history: [...state.history, user],
          draft: '',
          reasoning: emptyReasoning(),
          busy: true,
          tools: [],
          notice: null,
          charts: [],
          scriptCard: null,
          dataCard: null,
          // softWarn 是会话级状态，本轮保留
        }))
      },

      appendDelta(text) {
        set((state) => {
          const paused = pauseReasoning(state.reasoning, Date.now())
          const reasoning =
            paused.toggled || !paused.expanded ? paused : { ...paused, expanded: false }
          return {
            draft: state.draft + text,
            // 正式回答开始输出：结束当前思考段；未手动操作过时同时收起。
            ...(reasoning !== state.reasoning ? { reasoning } : {}),
          }
        })
      },

      appendReasoning(text) {
        if (!text) return
        set((state) => {
          const current = state.reasoning
          const room = REASONING_CHAR_LIMIT - current.text.length
          if (room <= 0) {
            return current.truncated
              ? state
              : { reasoning: { ...current, truncated: true } }
          }
          return {
            reasoning: {
              ...current,
              text: current.text + text.slice(0, room),
              // 工具调用后的新一轮思考：新开一个计时段，仍在同一轮内拼接。
              startedAt: current.startedAt ?? Date.now(),
              truncated: current.truncated || text.length > room,
            },
          }
        })
      },

      toggleReasoning() {
        set((state) => ({
          reasoning: {
            ...state.reasoning,
            expanded: !state.reasoning.expanded,
            toggled: true,
          },
        }))
      },

      pushTool(entry) {
        set((state) => {
          // 迟到的旧阶段不能覆盖已经进入的新阶段
          const incoming = KNOWLEDGE_QA_STAGE_ORDER[entry.name]
          if (incoming !== undefined) {
            const seen = Math.max(
              0,
              ...state.tools.map((tool) => KNOWLEDGE_QA_STAGE_ORDER[tool.name] ?? 0),
            )
            if (incoming < seen) return state
          }
          // 同一阶段结束后迟到的 start 不能让它重新出现
          const existing = state.tools.find((tool) => tool.name === entry.name)
          if (entry.phase === 'start' && existing?.phase === 'end') return state
          const next = { tools: [...state.tools.filter((t) => t.name !== entry.name), entry] }
          if (entry.phase === 'start' && state.reasoning.startedAt !== null) {
            return { ...next, reasoning: pauseReasoning(state.reasoning, Date.now()) }
          }
          // 同名工具只留最新一条（start → end 覆盖）
          return next
        })
      },

      pushChart(item) {
        set((state) => ({ charts: [...state.charts, item] }))
      },

      setScriptCard(payload) {
        set({ scriptCard: payload })
      },

      setDataCard(payload) {
        set({ dataCard: payload })
      },

      setNotice(notice) {
        set({ notice })
      },

      finalizeTurn(message) {
        set((state) => {
          const hasCharts = (message.results?.charts?.length ?? 0) > 0
          const results = hasCharts ? message.results : { ...message.results, charts: state.charts }
          const reasoning = pauseReasoning(state.reasoning, Date.now())
          const hasReasoning = reasoning.text.length > 0
          return {
            history: [...state.history, { ...message, results }],
            draft: '',
            reasoning: hasReasoning ? { ...reasoning, messageId: message.id } : reasoning,
          }
        })
      },

      appendMessage(message) {
        set((state) => ({ history: [...state.history, message] }))
      },

      endTurn(reason) {
        if (!get().busy) return // 幂等：重复收尾不改状态
        const state = get()
        const hasReasoning = state.reasoning.text.length > 0
        if (reason === 'done') {
          set({
            busy: false,
            tools: [],
            draft: '',
            reasoning: pauseReasoning(state.reasoning, Date.now()),
          })
          return
        }
        if (!state.draft.trim() && !hasReasoning) {
          set({
            busy: false,
            tools: [],
            draft: '',
            reasoning: emptyReasoning(),
            ...(reason === 'closed'
              ? { notice: { kind: 'error' as const, text: '连接提前结束，未收到回答，请重试。' } }
              : {}),
          })
          return
        }
        // 停止/断线时保留已生成内容，避免部分回答静默消失。
        localSeq += 1
        const partial: WireMessage = {
          id: -localSeq,
          session_id: state.sessionId ?? '',
          seq: -localSeq,
          role: 'assistant',
          content: state.draft,
          timestamp: new Date().toISOString(),
          results: state.charts.length > 0 ? { charts: state.charts } : null,
          pending_script: null,
        }
        set({
          busy: false,
          tools: [],
          draft: '',
          history: [...state.history, partial],
          reasoning: hasReasoning
            ? { ...pauseReasoning(state.reasoning, Date.now()), messageId: partial.id }
            : emptyReasoning(),
          notice:
            reason === 'aborted'
              ? {
                  kind: 'info',
                  text: state.draft.trim()
                    ? '已停止，保留已生成的部分回答。'
                    : '已停止，已保留本次思考。',
                }
              : {
                  kind: 'error',
                  text: state.draft.trim()
                    ? '连接中断，回答可能不完整。'
                    : '连接中断，已保留本次思考。',
                },
        })
      },

      failTurn(message) {
        if (!get().busy) return // 幂等：busy 已关时不覆盖已有提示
        const state = get()
        const notice: ChatNotice = { kind: 'error', text: message }
        if (!state.draft.trim()) {
          // 失败不把上一轮思考留到下一轮；下一轮 beginTurn 也会再清一次。
          set({
            busy: false,
            tools: [],
            draft: '',
            reasoning: emptyReasoning(),
            notice,
          })
          return
        }
        // 出错前已生成的文本不能丢：落成本地 partial 消息，刷新前至少可见。
        localSeq += 1
        const partial: WireMessage = {
          id: -localSeq,
          session_id: state.sessionId ?? '',
          seq: -localSeq,
          role: 'assistant',
          content: state.draft,
          timestamp: new Date().toISOString(),
          results: state.charts.length > 0 ? { charts: state.charts } : null,
          pending_script: null,
        }
        set({
          busy: false,
          tools: [],
          draft: '',
          history: [...state.history, partial],
          reasoning:
            state.reasoning.text.length > 0
              ? { ...pauseReasoning(state.reasoning, Date.now()), messageId: partial.id }
              : emptyReasoning(),
          notice,
        })
      },

      async send(prompt, mode = 'chat') {
        const { sessionId, busy } = get()
        if (busy || !sessionId) return

        get().beginTurn(prompt)
        const controller = new AbortController()
        activeStream = controller
        // 切会话或已换流后，迟到事件/收尾不得改写新会话状态。
        const isCurrent = (): boolean => activeStream === controller
        try {
          await streamChat({
            sessionId,
            prompt,
            mode,
            signal: controller.signal,
            onEvent: (event) => {
              if (isCurrent()) dispatch(event)
            },
            onEnd: (reason) => {
              if (!isCurrent()) return
              // error 的收尾由 error 事件或 catch 完成，这里不覆盖其 notice（约束 2）
              if (reason !== 'error') get().endTurn(reason)
            },
          })
        } catch (err) {
          if (!isCurrent()) return
          const text = err instanceof Error ? err.message : String(err)
          if (get().busy) {
            get().failTurn(text)
          } else {
            // busy 已被 error 事件收尾时，直接写提示
            get().setNotice({ kind: 'error', text })
          }
        } finally {
          if (isCurrent()) activeStream = null
        }
      },

      stop() {
        activeStream?.abort()
      },
    }
  })
}

export const useChatStore = createChatStore()
