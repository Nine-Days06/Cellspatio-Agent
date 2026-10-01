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
  at?: number
}

interface ChatState {
  sessionId: string | null
  history: WireMessage[]
  draft: string
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
  beginTurn: (prompt: string) => void
  appendDelta: (text: string) => void
  pushTool: (entry: ToolStatusEntry) => void
  pushChart: (item: ChartItem) => void
  setScriptCard: (payload: ScriptCardPayload | null) => void
  setDataCard: (payload: DataCardPayload | null) => void
  setNotice: (notice: ChatNotice | null) => void
  /** done 事件：把助手消息落进 history（约束 1：空内容照常记录） */
  finalizeTurn: (message: WireMessage) => void
  /** done / aborted / closed 的幂等收尾：关 busy、清过程条；error 不走这里 */
  endTurn: (reason: TurnEndReason) => void
  /** error 收尾：关 busy + 写错误提示；busy 已关时幂等（不覆盖已有提示） */
  failTurn: (message: string) => void
  /** 发起一轮对话：beginTurn → streamChat 接线；busy 或无会话时直接返回 */
  send: (prompt: string) => Promise<void>
  /** 中止当前流：只 abort 信号，busy 等 onEnd(aborted) 收尾才关 */
  stop: () => void
}

/** 本地 user 消息用负 id/seq，与服务端正 id 区分。 */
let localSeq = 0
/** 当前进行中的流；stop() 通过它 abort。 */
let activeStream: AbortController | null = null

export const useChatStore = create<ChatState>((set, get) => {
  /** SSE 事件分发：只推进状态；唯一允许关 busy 的分支是 error → failTurn（约束 2）。 */
  const dispatch = (event: SseEvent): void => {
    switch (event.type) {
      case 'delta':
        if (typeof event.text === 'string') get().appendDelta(event.text)
        break
      case 'tool_status': {
        const phase = event.phase
        if (typeof event.name !== 'string' || (phase !== 'start' && phase !== 'end')) break
        get().pushTool({
          name: event.name,
          phase,
          label: typeof event.label === 'string' ? event.label : undefined,
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
          get().pushChart(chartFromEvent({ type: 'chart', plotly_json: plotly, image_b64: image }))
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
    busy: false,
    tools: [],
    notice: null,
    charts: [],
    scriptCard: null,
    dataCard: null,
    softWarn: false,

    setSession(sessionId, history) {
      set({
        sessionId,
        history,
        draft: '',
        busy: false,
        tools: [],
        notice: null,
        charts: [],
        scriptCard: null,
        dataCard: null,
        softWarn: false,
      })
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
      set((state) => ({ draft: state.draft + text }))
    },

    pushTool(entry) {
      // 同名工具只留最新一条（start → end 覆盖）
      set((state) => ({ tools: [...state.tools.filter((t) => t.name !== entry.name), entry] }))
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
        return {
          history: [...state.history, { ...message, results }],
          draft: '',
        }
      })
    },

    endTurn() {
      if (!get().busy) return // 幂等：重复收尾不改状态
      set({ busy: false, tools: [], draft: '' })
    },

    failTurn(message) {
      if (!get().busy) return // 幂等：busy 已关时不覆盖已有提示
      set({ busy: false, tools: [], draft: '', notice: { kind: 'error', text: message } })
    },

    async send(prompt) {
      const { sessionId, busy } = get()
      if (busy || !sessionId) return

      get().beginTurn(prompt)
      const controller = new AbortController()
      activeStream = controller
      try {
        await streamChat({
          sessionId,
          prompt,
          signal: controller.signal,
          onEvent: dispatch,
          onEnd: (reason) => {
            // error 的收尾由 error 事件或 catch 完成，这里不覆盖其 notice（约束 2）
            if (reason !== 'error') get().endTurn(reason)
          },
        })
      } catch (err) {
        const text = err instanceof Error ? err.message : String(err)
        if (get().busy) {
          get().failTurn(text)
        } else {
          // busy 已被 error 事件收尾时，直接写提示
          get().setNotice({ kind: 'error', text })
        }
      } finally {
        if (activeStream === controller) activeStream = null
      }
    },

    stop() {
      activeStream?.abort()
    },
  }
})
