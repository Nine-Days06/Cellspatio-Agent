/** 对话主页（T8）：两栏布局 + 本地输入区 + 会话/历史/卡片接线。
 *
 * 职责边界（组件层全部为受控展示组件，副作用只在这里）：
 * - 输入框文本是本地 state（store.draft 是流式输出，二者不可混用）；
 * - 打开页面自动选中首个会话 → useMessages 首拉走 fetchMessages（其内部
 *   已实现「首次成功才带 ?restore=1」，本层不手工拼查询串）；
 * - 历史查询成功后 hydrate 进 store；busy（流式进行中）时跳过，
 *   绝不借 hydrate 关闭 busy（约束 2）；
 * - busy 只由 endTurn / failTurn / onEnd 关闭；停止按钮与 Esc 只置 abort，
 *   文案不承诺瞬时取消（约束 3）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ConfirmCard } from '@/components/ConfirmCard'
import { MessageList } from '@/components/MessageList'
import { Sidebar } from '@/components/Sidebar'
import { ApiError, confirmAction } from '@/lib/api'
import { queryKeys, useMessages, useSessions, useSidebar } from '@/lib/queries'
import type { ConfirmRequestBody, EnvStatus, KbStats, SessionSummary } from '@/lib/types'
import { useChatStore } from '@/store/chat'

const DEFAULT_KB: KbStats = { initialized: false }
const DEFAULT_ENV: EnvStatus = {
  rscript: null,
  kb_path: '',
  kb_ok: false,
  ollama: { state: 'unavailable', managed: false, detail: '' },
}

export function ChatPage() {
  // 输入区本地状态：draft 属于流式输出，输入框不共用
  const [input, setInput] = useState('')
  /** 已请求停止（诚实文案）；busy 结束时由效果复位 */
  const [stopping, setStopping] = useState(false)
  /** 超 30s 无 store 事件（约束 3c） */
  const [slowHint, setSlowHint] = useState(false)
  /** POST /api/confirm 失败的后端中文 detail；发起新一轮确认前置 null */
  const [confirmError, setConfirmError] = useState<string | null>(null)

  const sessionId = useChatStore((s) => s.sessionId)
  const history = useChatStore((s) => s.history)
  const draft = useChatStore((s) => s.draft)
  const busy = useChatStore((s) => s.busy)
  const tools = useChatStore((s) => s.tools)
  const notice = useChatStore((s) => s.notice)
  const softWarn = useChatStore((s) => s.softWarn)
  const scriptCard = useChatStore((s) => s.scriptCard)
  const dataCard = useChatStore((s) => s.dataCard)
  const send = useChatStore((s) => s.send)
  const setSession = useChatStore((s) => s.setSession)
  const setScriptCard = useChatStore((s) => s.setScriptCard)
  const setDataCard = useChatStore((s) => s.setDataCard)

  const sessionsQuery = useSessions()
  const messagesQuery = useMessages(sessionId)
  const sidebarQuery = useSidebar(sessionId ?? undefined)
  const queryClient = useQueryClient()

  // ① 打开页面自动选中首个会话（一次性；已有活动会话时不覆盖）
  const autoSelectedRef = useRef(false)
  useEffect(() => {
    if (autoSelectedRef.current) return
    const first = sessionsQuery.data?.sessions[0]
    if (!first) return
    autoSelectedRef.current = true
    if (!useChatStore.getState().sessionId) setSession(first.id, [])
  }, [sessionsQuery.data, setSession])

  // ② 历史拉取成功 → hydrate：history + soft_warn + 恢复的 pending 脚本卡。
  //    顶层 pending_script 是 restore 的权威结果（后端先取 messages 后置 expired，
  //    messages[].pending_script 状态是旧值），message_id 从携带卡片的消息取。
  useEffect(() => {
    const data = messagesQuery.data
    if (!data) return
    const state = useChatStore.getState()
    if (state.busy || state.sessionId !== data.session.id) return
    setSession(data.session.id, data.messages)
    if (data.soft_warn) useChatStore.setState({ softWarn: true })
    if (data.pending_script) {
      const holder = [...data.messages].reverse().find((m) => m.pending_script !== null)
      setScriptCard({ ...data.pending_script, message_id: holder?.id ?? -1 })
    }
  }, [messagesQuery.data, setSession, setScriptCard])

  // ③ busy 收尾 → 复位输入区提示（stopping / slowHint 只在本轮内有意义）
  useEffect(() => {
    if (busy) return
    setStopping(false)
    setSlowHint(false)
  }, [busy])

  // ④ 请求停止（按钮 + Esc 共用）：只置 abort 与诚实文案，busy 等 onEnd 收尾
  const requestStop = useCallback(() => {
    const state = useChatStore.getState()
    if (!state.busy) return
    setStopping(true)
    state.stop()
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') requestStop()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [requestStop])

  // ⑤ 超 30s 无 store 事件（SSE 未推进）→ 「仍在执行，可能耗时较久」
  const lastEventRef = useRef(0)
  useEffect(
    () =>
      useChatStore.subscribe(() => {
        lastEventRef.current = Date.now()
      }),
    [],
  )
  useEffect(() => {
    if (!busy) return
    lastEventRef.current = Date.now()
    const timer = setInterval(() => {
      if (Date.now() - lastEventRef.current >= 30_000) setSlowHint(true)
    }, 1_000)
    return () => clearInterval(timer)
  }, [busy])

  function handleSend(): void {
    const prompt = input.trim()
    if (!prompt || busy) return
    setInput('')
    setConfirmError(null)
    void send(prompt)
  }

  /** 约束 1 重试：重发本会话最后一条用户消息 */
  function handleRetry(): void {
    const state = useChatStore.getState()
    if (state.busy) return
    const lastUser = [...state.history].reverse().find((m) => m.role === 'user')
    if (lastUser) void send(lastUser.content)
  }

  /** 确认执行：发起前先清旧错误；成功按 action 回写卡片状态 / 清卡（T7 契约） */
  async function handleConfirm(body: ConfirmRequestBody): Promise<void> {
    setConfirmError(null)
    try {
      await confirmAction(body)
      if (body.action === 'data_confirm') {
        setDataCard(null)
        return
      }
      const card = useChatStore.getState().scriptCard
      if (card) {
        setScriptCard({
          ...card,
          status: body.action === 'script_confirm' ? 'confirmed' : 'cancelled',
        })
      }
    } catch (err) {
      setConfirmError(err instanceof ApiError ? err.message : '确认执行失败')
    }
  }

  /** 重新生成：用 pending_script.user_request 再发一轮（不是 confirm 动作） */
  function handleRegen(): void {
    const card = useChatStore.getState().scriptCard
    if (!card) return
    setConfirmError(null)
    void send(card.user_request)
  }

  /** 切换会话：先停旧流（防事件串台）再复位；重复选中当前会话不重置历史 */
  const switchTo = useCallback(
    (id: string) => {
      if (id === useChatStore.getState().sessionId) return
      useChatStore.getState().stop()
      setStopping(false)
      setSlowHint(false)
      setConfirmError(null)
      setSession(id, [])
    },
    [setSession],
  )

  const handleCreated = useCallback(
    (session: SessionSummary) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
      switchTo(session.id)
    },
    [queryClient, switchTo],
  )

  const handleDeleted = useCallback(
    (id: string) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
      const state = useChatStore.getState()
      if (state.sessionId !== id) return
      const remaining = (sessionsQuery.data?.sessions ?? []).filter((s) => s.id !== id)
      if (remaining[0]) {
        switchTo(remaining[0].id)
        return
      }
      // store 无 clearSession：复用 setSession 的复位语义，再把 sessionId 置空
      setSession('', [])
      useChatStore.setState({ sessionId: null })
    },
    [queryClient, sessionsQuery.data, setSession, switchTo],
  )

  const sessions = sessionsQuery.data?.sessions ?? []
  const kbStats = sidebarQuery.data?.kb_stats ?? DEFAULT_KB
  const env = sidebarQuery.data?.env ?? DEFAULT_ENV

  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar
        sessions={sessions}
        activeId={sessionId}
        kbStats={kbStats}
        env={env}
        loading={sessionsQuery.isLoading}
        onSelect={switchTo}
        onCreated={handleCreated}
        onDeleted={handleDeleted}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <MessageList
          messages={history}
          draft={draft}
          busy={busy}
          tools={tools}
          notice={notice}
          softWarn={softWarn}
          onRetry={handleRetry}
        />
        {(scriptCard || dataCard) && (
          <div className="border-t border-border px-4 py-3">
            <ConfirmCard
              scriptCard={scriptCard}
              dataCard={dataCard}
              busy={busy}
              confirmError={confirmError}
              onConfirm={handleConfirm}
              onRegen={handleRegen}
            />
          </div>
        )}
        {/* 输入区：状态行只看 store.busy（约束 2），停止文案不承诺瞬时取消（约束 3） */}
        <div className="border-t border-border p-3">
          {busy && !stopping && (
            <p className="mb-1.5 text-xs text-muted-foreground">生成中…</p>
          )}
          {busy && stopping && (
            <p className="mb-1.5 text-xs text-muted-foreground">
              已请求停止，正在结束当前步骤…（工具执行中无法立即中断）
            </p>
          )}
          {busy && slowHint && (
            <p className="mb-1.5 text-xs text-muted-foreground">仍在执行，可能耗时较久</p>
          )}
          <div className="flex items-end gap-2">
            <textarea
              aria-label="输入你的问题"
              placeholder="输入你的问题…"
              rows={2}
              className="flex-1 resize-none rounded-lg border border-border bg-card p-2 text-sm outline-none focus-visible:ring-1 focus-visible:ring-ring"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                // Enter 发送 / Shift+Enter 换行；IME 组合期回车不发送
                if (e.key !== 'Enter' || e.shiftKey) return
                if (e.nativeEvent.isComposing) return
                e.preventDefault()
                handleSend()
              }}
            />
            {busy ? (
              <button
                type="button"
                onClick={requestStop}
                disabled={stopping}
                className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
              >
                停止
              </button>
            ) : (
              <button
                type="button"
                onClick={handleSend}
                disabled={!input.trim()}
                className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
              >
                发送
              </button>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
