/** 对话主页（T8）：两栏布局 + 本地输入区 + 会话/历史/卡片接线。
 *
 * 职责边界（组件层全部为受控展示组件，副作用只在这里）：
 * - 输入框文本是本地 state（store.draft 是流式输出，二者不可混用）；
 * - 打开页面自动选中首个会话 → useMessages 首拉走 SessionWorkspace.loadMessages
 *   （module 负责「每会话首次成功才带 ?restore=1」，本层不手工拼查询串）；
 * - 历史查询成功后 hydrate 进 store；busy（流式进行中）时跳过，
 *   绝不借 hydrate 关闭 busy（约束 2）；
 * - busy 只由 endTurn / failTurn / onEnd 关闭；停止按钮与 Esc 只置 abort，
 *   文案不承诺瞬时取消（约束 3）。
 */
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowUp, Home, LogOut, Square } from 'lucide-react'
import { ConfirmCard } from '@/components/ConfirmCard'
import { LocalInputDialog } from '@/components/LocalInputDialog'
import { MessageList } from '@/components/MessageList'
import { RerankSetting } from '@/components/RerankSetting'
import { Sidebar } from '@/components/Sidebar'
import { ThemeToggle } from '@/components/ThemeToggle'
import { UploadDialog } from '@/components/UploadDialog'
import {
  ApiError,
  confirmAction,
  createSession,
  logout,
  registerLocalInput,
  startNewWorkspace,
  uploadInput,
} from '@/lib/api'
import { navigate } from '@/lib/navigation'
import { confirmReducer, initialConfirmState, scriptStatusForAction } from '@/lib/confirmState'
import { queryKeys, useMessages, useProjects, useSessions, useSidebar } from '@/lib/queries'
import { sessionWorkspace } from '@/lib/sessionWorkspace'
import {
  collectArchivedProjectIds,
  isActiveSession,
  nextSessionAfterDelete,
} from '@/lib/sessions'
import type {
  ConfirmRequestBody,
  EnvStatus,
  KbStats,
  SessionSummary,
  WorkspaceInfo,
} from '@/lib/types'
import { useChatStore } from '@/store/chat'

const DEFAULT_KB: KbStats = { initialized: false }
const DEFAULT_ENV: EnvStatus = {
  rscript: null,
  kb_path: '',
  kb_ok: false,
  ollama: { state: 'unavailable', managed: false, detail: '' },
  ollama_start_mode: 'lazy',
  rerank: { configured: false, model: '' },
}

function workspaceSummary(workspace?: WorkspaceInfo): string {
  if (!workspace) return ''
  if (workspace.kind !== 'ephemeral') {
    return `永久工作区${workspace.label ? ` · ${workspace.label}` : ''}`
  }
  if (workspace.expired || workspace.remaining_seconds === 0) return '临时体验空间 · 已到期'
  const hours = Math.max(1, Math.ceil((workspace.remaining_seconds ?? 0) / 3600))
  return `临时体验空间 · 24 小时（剩余 ${hours} 小时）`
}

export function ChatPage() {
  // 输入区本地状态：draft 属于流式输出，输入框不共用
  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const field = inputRef.current
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, 144)}px`
  }, [input])
  /** 已请求停止（诚实文案）；busy 结束时由效果复位 */
  const [stopping, setStopping] = useState(false)
  /** 超 30s 无 store 事件（约束 3c） */
  const [slowHint, setSlowHint] = useState(false)
  /** 确认卡唯一状态源；新卡/切换会话/重新生成/新一轮输入时 reset */
  const [confirmState, dispatchConfirm] = useReducer(confirmReducer, initialConfirmState)
  const sessionId = useChatStore((s) => s.sessionId)
  const history = useChatStore((s) => s.history)
  const draft = useChatStore((s) => s.draft)
  const reasoning = useChatStore((s) => s.reasoning)
  const toggleReasoning = useChatStore((s) => s.toggleReasoning)
  const busy = useChatStore((s) => s.busy)
  const tools = useChatStore((s) => s.tools)
  const notice = useChatStore((s) => s.notice)
  const softWarn = useChatStore((s) => s.softWarn)
  const scriptCard = useChatStore((s) => s.scriptCard)
  const dataCard = useChatStore((s) => s.dataCard)
  const send = useChatStore((s) => s.send)
  const setSession = useChatStore((s) => s.setSession)
  const clearSession = useChatStore((s) => s.clearSession)
  const setScriptCard = useChatStore((s) => s.setScriptCard)
  const setDataCard = useChatStore((s) => s.setDataCard)
  const appendMessage = useChatStore((s) => s.appendMessage)

  const sessionsQuery = useSessions()
  const projectsQuery = useProjects()
  const messagesQuery = useMessages(sessionId)
  const sidebarQuery = useSidebar(sessionId ?? undefined)
  const rerankEnv = sidebarQuery.data?.env.rerank ?? DEFAULT_ENV.rerank
  const queryClient = useQueryClient()
  const sessions = sessionsQuery.data?.sessions ?? []
  const projects = projectsQuery.data?.projects ?? []
  const currentSession = sessions.find((s) => s.id === sessionId)
  const currentProject = projects.find((p) => p.id === currentSession?.project_id)

  // ① 打开页面自动选中首个会话（一次性；已有活动会话时不覆盖）
  const autoSelectedRef = useRef(false)
  /** 无会话时正在建会话：挡住建会话期间的重复发送（busy 此时仍为 false）。 */
  const creatingSessionRef = useRef(false)
  useEffect(() => {
    if (autoSelectedRef.current) return
    // 项目未返回前无法判断哪些会话属于归档项目，等下一次 effect
    if (!projectsQuery.data) return
    const archived = collectArchivedProjectIds(projectsQuery.data.projects)
    const first = sessionsQuery.data?.sessions.find((session) =>
      isActiveSession(session, archived),
    )
    if (!first) return
    autoSelectedRef.current = true
    if (!useChatStore.getState().sessionId) setSession(first.id, [])
  }, [sessionsQuery.data, projectsQuery.data, setSession])

  // 会话激活边界：同一次激活只接纳一份历史；切回缓存会话时允许重新 hydrate。
  useEffect(() => {
    sessionWorkspace.activateSession(sessionId)
  }, [sessionId])

  // ② 历史拉取成功 → hydrate：history + soft_warn + 恢复的 pending 脚本卡。
  //    顶层 pending_script 是 restore 的权威结果（后端先取 messages 后置 expired，
  //    messages[].pending_script 状态是旧值），message_id 从携带卡片的消息取。
  useEffect(() => {
    const data = messagesQuery.data
    if (!data) return
    const state = useChatStore.getState()
    if (
      !sessionWorkspace.acceptsHistory({
        data,
        busy: state.busy,
        currentSessionId: state.sessionId,
      })
    ) {
      return
    }
    // 只刷历史：同会话刷新不能走“切会话”语义，确认卡与输入草稿必须保留。
    useChatStore.setState({ history: data.messages })
    if (data.soft_warn) useChatStore.setState({ softWarn: true })
    if (data.pending_script) {
      const holder = [...data.messages].reverse().find((m) => m.pending_script !== null)
      setScriptCard({ ...data.pending_script, message_id: holder?.id ?? -1 })
    }
    // busy 期间跳过后，本轮结束时必须重新尝试，否则历史永远不会 hydrate。
  }, [messagesQuery.data, setScriptCard, busy])

  // ②-b 当前展示卡的 message_id 变化（恢复出新卡/换卡）→ 丢弃上一张卡的失败/成功态；
  //      同一 message_id 的 status 回写不算换卡。scriptCard 优先，与 ConfirmCard 一致。
  const activeCardId = scriptCard?.message_id ?? dataCard?.message_id ?? null
  const activeCardIdRef = useRef(activeCardId)
  useEffect(() => {
    if (activeCardIdRef.current === activeCardId) return
    activeCardIdRef.current = activeCardId
    dispatchConfirm({ type: 'reset' })
  }, [activeCardId])

  // ③ busy 收尾 → 复位输入区提示（stopping / slowHint 只在本轮内有意义）
  useEffect(() => {
    if (busy) return
    setStopping(false)
    setSlowHint(false)
  }, [busy])

  // ③-b 一轮结束（完成/中断/报错收尾）→ 重取会话列表，让本轮自动标题立刻出现。
  //      只在 true→false 边界触发；不动流、不动确认卡与草稿。
  const wasBusyRef = useRef(false)
  useEffect(() => {
    const wasBusy = wasBusyRef.current
    wasBusyRef.current = busy
    if (!wasBusy || busy) return
    void queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
  }, [busy, queryClient])

  // ④ 请求停止（按钮 + Esc 共用）：只置 abort 与诚实文案，busy 等 onEnd 收尾
  const requestStop = useCallback(() => {
    const state = useChatStore.getState()
    if (!state.busy) return
    setStopping(true)
    state.stop()
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.querySelector('[role="dialog"]')) requestStop()
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

  async function handleSend(): Promise<void> {
    const prompt = input.trim()
    if (!prompt || busy) return
    if (!sessionId) {
      // 建会话是异步的：期间 busy 仍为 false，必须自己挡住重复回车/双击，
      // 否则会建出两个会话，其中一条提问被静默丢弃。
      if (creatingSessionRef.current) return
      creatingSessionRef.current = true
      try {
        const { session } = await createSession(undefined, null)
        refreshNavigation()
        switchTo(session.id)
      } catch (err) {
        const text = err instanceof ApiError ? err.message : '新建会话失败'
        useChatStore.getState().setNotice({ kind: 'error', text })
        return
      } finally {
        creatingSessionRef.current = false
      }
    }
    setInput('')
    dispatchConfirm({ type: 'reset' })
    void send(prompt)
  }

  /** 约束 1 重试：重发本会话最后一条用户消息 */
  function handleRetry(): void {
    const state = useChatStore.getState()
    if (state.busy) return
    const history = state.history
    const lastUserAt = history.reduce((at, m, i) => (m.role === 'user' ? i : at), -1)
    if (lastUserAt < 0) return
    dispatchConfirm({ type: 'reset' })
    void send(history[lastUserAt].content)
  }

  /** 确认执行：先派发 submit（覆盖旧失败态）；成功按 action 回写卡片状态 / 清卡（T7 契约） */
  async function handleConfirm(body: ConfirmRequestBody): Promise<void> {
    dispatchConfirm({ type: 'submit', action: body.action })
    const scriptStatus = scriptStatusForAction(body.action)
    const currentCards = () => {
      const state = useChatStore.getState()
      return { scriptCard: state.scriptCard, dataCard: state.dataCard }
    }
    const submitted = sessionWorkspace.captureConfirmation(
      body.action,
      useChatStore.getState().sessionId ?? '',
      currentCards(),
    )
    try {
      const { message } = await confirmAction(body)
      const state = useChatStore.getState()
      if (
        !sessionWorkspace.acceptsConfirmation(submitted, state.sessionId, {
          scriptCard: state.scriptCard,
          dataCard: state.dataCard,
        })
      ) {
        // 导致身份失效的切会话/换卡已经复位过确认态；这里若再 reset，会误清
        // 新会话随后发起的在途确认，使按钮提前解锁并允许重复提交。
        return
      }
      appendMessage(message)
      if (scriptStatus === null) {
        setDataCard(null)
      } else {
        const card = useChatStore.getState().scriptCard
        if (card) {
          setScriptCard({
            ...card,
            status: scriptStatus,
          })
        }
      }
      dispatchConfirm({ type: 'success' })
    } catch (err) {
      const state = useChatStore.getState()
      if (
        !sessionWorkspace.acceptsConfirmation(submitted, state.sessionId, {
          scriptCard: state.scriptCard,
          dataCard: state.dataCard,
        })
      ) {
        return
      }
      dispatchConfirm({
        type: 'failure',
        error: err instanceof ApiError ? err.message : '确认执行失败',
      })
    }
  }

  /** 重新生成：用 pending_script.user_request 再发一轮（不是 confirm 动作） */
  function handleRegen(): void {
    const card = useChatStore.getState().scriptCard
    if (!card) return
    dispatchConfirm({ type: 'reset' })
    void send(card.user_request)
  }

  /** 切换会话：先停旧流（防事件串台）再复位；重复选中当前会话不重置历史 */
  const switchTo = useCallback(
    (id: string) => {
      if (id === useChatStore.getState().sessionId) return
      setStopping(false)
      setSlowHint(false)
      dispatchConfirm({ type: 'reset' })
      setSession(id, [])
    },
    [setSession],
  )

  const refreshNavigation = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
    queryClient.invalidateQueries({ queryKey: queryKeys.projects() })
  }, [queryClient])

  const handleCreated = useCallback(
    (session: SessionSummary) => {
      refreshNavigation()
      switchTo(session.id)
    },
    [refreshNavigation, switchTo],
  )

  const handleDeleted = useCallback(
    (id: string) => {
      refreshNavigation()
      if (useChatStore.getState().sessionId !== id) return
      const next = nextSessionAfterDelete(
        sessionsQuery.data?.sessions ?? [],
        projectsQuery.data?.projects ?? [],
        id,
      )
      if (next) {
        switchTo(next)
        return
      }
      clearSession()
      dispatchConfirm({ type: 'reset' })
      setStopping(false)
      setSlowHint(false)
    },
    [clearSession, refreshNavigation, sessionsQuery.data, projectsQuery.data, switchTo],
  )

  /** 本地输入：无会话时先建会话；QC 成功后靠消息失效刷新出证据与复核链。 */
  const handleLocalInput = useCallback(
    async (path: string) => {
      let id = useChatStore.getState().sessionId
      if (!id) {
        const { session } = await createSession('本地 10x 数据', null)
        id = session.id
        handleCreated({
          id: session.id,
          title: session.title,
          title_source: session.title_source,
          updated_at: session.updated_at,
          message_count: 0,
          project_id: session.project_id,
          pinned: session.pinned,
          archived_at: session.archived_at,
        })
      }
      await registerLocalInput(id, path)
      void queryClient.invalidateQueries({ queryKey: queryKeys.messages(id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
    },
    [handleCreated, queryClient],
  )

  const handleUpload = useCallback(
    async (file: File) => {
      let id = useChatStore.getState().sessionId
      if (!id) {
        const { session } = await createSession('上传 10x 数据', null)
        id = session.id
        handleCreated({
          id: session.id,
          title: session.title,
          title_source: session.title_source,
          updated_at: session.updated_at,
          message_count: 0,
          project_id: session.project_id,
          pinned: session.pinned,
          archived_at: session.archived_at,
        })
      }
      await uploadInput(id, file)
      void queryClient.invalidateQueries({ queryKey: queryKeys.messages(id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
    },
    [handleCreated, queryClient],
  )

  const kbStats = sidebarQuery.data?.kb_stats ?? DEFAULT_KB
  const env = sidebarQuery.data?.env ?? DEFAULT_ENV
  const workspace = sidebarQuery.data?.workspace
  const workspaceExpired = Boolean(
    workspace?.expired ||
      workspace?.remaining_seconds === 0 ||
      (sidebarQuery.error instanceof ApiError && sidebarQuery.error.status === 410),
  )

  async function handleNewTrial(): Promise<void> {
    await startNewWorkspace()
    queryClient.clear()
    window.location.assign('/workspace')
  }

  return (
    <div className="workspace flex h-dvh flex-col overflow-hidden bg-background text-foreground md:flex-row">
      <Sidebar
        sessions={sessions}
        projects={projects}
        activeId={sessionId}
        kbStats={kbStats}
        env={env}
        loading={sessionsQuery.isLoading}
        onSelect={switchTo}
        onCreated={handleCreated}
        onDeleted={handleDeleted}
        onArchived={handleDeleted}
        onChanged={refreshNavigation}
      />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {workspaceExpired && (
          <div
            role="alert"
            className="motion-fade flex shrink-0 items-center justify-between gap-3 border-b border-border bg-muted px-5 py-3 text-sm md:px-9"
          >
            <span>临时体验空间已到期，数据将在清理后删除。</span>
            <button
              type="button"
              className="rounded-md border px-3 py-1.5"
              onClick={() => void handleNewTrial()}
            >
              新建临时空间
            </button>
          </div>
        )}
        <header className="workspace-header flex shrink-0 items-center justify-between gap-4 border-b border-border px-5 py-4 md:px-9">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold tracking-tight md:text-xl" title={currentSession?.title}>{currentSession?.title ?? '新会话'}</h1>
            <p className="mt-1 text-xs text-muted-foreground">
              {workspace ? `${workspaceSummary(workspace)} · ` : ''}
              {currentProject ? `${currentProject.label} · ` : ''}
              单细胞与时空组学分析记录{history.length > 0 && ` · ${history.length} 条记录`}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="flex items-center gap-2 text-xs text-muted-foreground"><span aria-hidden="true" className={`size-1.5 rounded-full ${busy ? 'bg-primary' : 'bg-muted-foreground/50'}`} />{busy ? '正在执行' : '等待输入'}</span>
            {workspace?.principal_kind === 'account' && (
              <button
                type="button"
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => {
                  void logout().then(() => navigate('/'))
                }}
              >
                <LogOut className="size-3.5" />
                退出
              </button>
            )}
            {workspace?.kind !== 'ephemeral' && (
              <LocalInputDialog
                onSubmit={handleLocalInput}
                defaultPath={currentProject?.root_path ?? ''}
              />
            )}
            {workspace?.kind === 'ephemeral' && <UploadDialog onSubmit={handleUpload} />}
            <RerankSetting configured={rerankEnv.configured} model={rerankEnv.model} />
            <ThemeToggle />
            <button
              type="button"
              aria-label="返回主页"
              className="inline-flex size-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={() => navigate('/')}
            >
              <Home className="size-4" />
            </button>
          </div>
        </header>
        <MessageList
          key={sessionId}
          historyReady={!messagesQuery.isLoading}
          onQcRecorded={() => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.messages(sessionId ?? '') })
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
          }}
          messages={history}
          draft={draft}
          reasoning={reasoning}
          onToggleReasoning={toggleReasoning}
          busy={busy}
          tools={tools}
          hideStatus={stopping || slowHint}
          notice={notice}
          softWarn={softWarn}
          onRetry={handleRetry}
          onStarter={(prompt) => { setInput(prompt); inputRef.current?.focus() }}
        />
        {(scriptCard || dataCard) && (
          <div
            data-testid="confirm-dock"
            className="confirm-dock max-h-[35dvh] min-h-0 shrink overflow-y-auto border-t border-border bg-card px-5 py-3 md:px-9"
          >
            <ConfirmCard
              scriptCard={scriptCard}
              dataCard={dataCard}
              busy={busy}
              confirmState={confirmState}
              onConfirm={handleConfirm}
              onRegen={handleRegen}
            />
          </div>
        )}
        {/* 输入区：状态行只看 store.busy（约束 2），停止文案不承诺瞬时取消（约束 3） */}
        <div className="composer shrink-0 px-5 pb-3 pt-2 md:px-9">
          <div className="mx-auto max-w-6xl">
          {busy && stopping && (
            <p className="mb-1.5 text-xs text-muted-foreground">
              已请求停止，正在结束当前步骤…（工具执行中无法立即中断）
            </p>
          )}
          {busy && slowHint && (
            <p className="mb-1.5 text-xs text-muted-foreground">仍在执行，可能耗时较久</p>
          )}
          <div className="composer-shell rounded-xl border border-border bg-card px-3 pb-2 pt-2">
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              aria-label="输入你的问题"
              placeholder="描述你的数据、样本或分析目标…"
              rows={1}
              className="composer-input min-h-12 min-w-0 flex-1 resize-none rounded-md bg-transparent px-2 py-3 text-base leading-6 focus-visible:outline-none"
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
                className="min-h-11 rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted disabled:opacity-50"
              >
                <Square className="mr-1 inline size-3 fill-current" aria-hidden="true" />
                停止
              </button>
            ) : (
              <button
                type="button"
                onClick={handleSend}
                disabled={!input.trim()}
                className="min-h-11 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                <ArrowUp className="mr-1 inline size-4" aria-hidden="true" />
                发送
              </button>
            )}
          </div>
          </div>
          <p className="composer-help mt-2 text-xs text-muted-foreground">Enter 发送，Shift + Enter 换行；分析须独立授权。</p>
          </div>
        </div>
      </main>
    </div>
  )
}
