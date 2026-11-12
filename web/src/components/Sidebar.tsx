import { useRef, useState } from 'react'
import {
  Archive,
  ChevronDown,
  ChevronRight,
  FolderPlus,
  Menu,
  MoreHorizontal,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
} from 'lucide-react'
import {
  ApiError,
  createProject,
  createSession,
  deleteSession,
  fetchProjectUsage,
  fetchSessionUsage,
  updateProject,
  updateSession,
} from '@/lib/api'
import type { EnvStatus, KbStats, Project, SessionSummary, UsageSummary } from '@/lib/types'
import { collectArchivedProjectIds, isActiveSession } from '@/lib/sessions'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

export interface SidebarProps {
  sessions: SessionSummary[]
  projects?: Project[]
  activeId: string | null
  kbStats: KbStats
  env: EnvStatus
  loading?: boolean
  onSelect: (sessionId: string) => void
  onCreated: (session: SessionSummary) => void
  onDeleted: (sessionId: string) => void
  onArchived?: (sessionId: string) => void
  onChanged?: () => void
}

function Dot({ ok, testId }: { ok: boolean; testId: string }) {
  return (
    <span
      data-testid={testId}
      data-state={ok ? 'ok' : 'error'}
      className={`inline-block size-2 rounded-full ${ok ? 'bg-emerald-500' : 'bg-red-500'}`}
    />
  )
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(1)} ${units[index]}`
}

export function Sidebar({
  sessions,
  projects = [],
  activeId,
  kbStats,
  env,
  loading,
  onSelect,
  onCreated,
  onDeleted,
  onArchived,
  onChanged,
}: SidebarProps) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null)
  const [pendingSession, setPendingSession] = useState<SessionSummary | null>(null)
  const [pendingProject, setPendingProject] = useState<Project | null>(null)
  const [newProjectOpen, setNewProjectOpen] = useState(false)
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set())
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [projectLabel, setProjectLabel] = useState('')
  const [projectRoot, setProjectRoot] = useState('')
  const [editLabel, setEditLabel] = useState('')
  const [editRoot, setEditRoot] = useState('')
  const [moveTarget, setMoveTarget] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  const [usageLoading, setUsageLoading] = useState(false)
  const [usageError, setUsageError] = useState<string | null>(null)
  const usageRequest = useRef(0)

  const archivedProjectIds = collectArchivedProjectIds(projects)
  const query = search.trim().toLocaleLowerCase()
  const visibleSessions = sessions.filter((session) =>
    session.title.toLocaleLowerCase().includes(query),
  )
  const activeSessions = visibleSessions.filter((session) =>
    isActiveSession(session, archivedProjectIds),
  )
  const archivedSessions = visibleSessions.filter(
    (session) =>
      Boolean(session.archived_at) &&
      !(session.project_id && archivedProjectIds.has(session.project_id)),
  )
  const activeProjects = projects.filter((project) => !project.archived_at)
  const archivedProjects = projects.filter(
    (project) =>
      project.archived_at &&
      (!query || project.label.toLocaleLowerCase().includes(query)),
  )
  const pinnedSessions = activeSessions.filter((session) => session.pinned)
  const ungroupedSessions = activeSessions.filter(
    (session) => !session.pinned && !session.project_id,
  )
  const projectSessions = (projectId: string) =>
    activeSessions.filter(
      (session) => !session.pinned && session.project_id === projectId,
    )

  function toggleProject(projectId: string) {
    setCollapsedProjects((previous) => {
      const next = new Set(previous)
      if (next.has(projectId)) next.delete(projectId)
      else next.add(projectId)
      return next
    })
  }

  async function handleCreate(projectId?: string | null) {
    setCreateError(null)
    try {
      const { session } = await createSession(undefined, projectId)
      onCreated({
        id: session.id,
        title: session.title,
        updated_at: session.updated_at,
        message_count: 0,
        project_id: session.project_id,
        pinned: session.pinned,
        archived_at: session.archived_at,
      })
      setPendingProject(null)
      setMobileOpen(false)
    } catch (err) {
      setCreateError(errorMessage(err, '新建会话失败'))
    }
  }

  async function handleConfirmDelete() {
    if (!pendingDelete) return
    setDeleteError(null)
    try {
      await deleteSession(pendingDelete.id)
      const id = pendingDelete.id
      setPendingDelete(null)
      onDeleted(id)
    } catch (err) {
      setDeleteError(errorMessage(err, '删除会话失败'))
    }
  }

  async function handleCreateProject() {
    setActionError(null)
    if (!projectLabel.trim()) {
      setActionError('项目名不能为空')
      return
    }
    try {
      await createProject(projectLabel.trim(), projectRoot.trim() || null)
      setNewProjectOpen(false)
      setProjectLabel('')
      setProjectRoot('')
      onChanged?.()
    } catch (err) {
      setActionError(errorMessage(err, '新建项目失败'))
    }
  }

  async function loadUsage(scope: 'session' | 'project', id: string) {
    const request = ++usageRequest.current
    setUsage(null)
    setUsageError(null)
    setUsageLoading(true)
    try {
      const summary =
        scope === 'session'
          ? await fetchSessionUsage(id)
          : await fetchProjectUsage(id)
      if (request !== usageRequest.current) return
      setUsage(summary)
    } catch (err) {
      if (request !== usageRequest.current) return
      setUsageError(errorMessage(err, '统计占用失败'))
    } finally {
      if (request === usageRequest.current) setUsageLoading(false)
    }
  }

  function usageLine() {
    if (usageLoading) return '正在统计占用…'
    if (usageError) return usageError
    if (!usage) return null
    return `占用：${formatBytes(usage.bytes)} · ${usage.files.toLocaleString()} 个文件`
  }

  function openProject(project: Project) {
    setPendingProject(project)
    setEditLabel(project.label)
    setEditRoot(project.root_path ?? '')
    setActionError(null)
    void loadUsage('project', project.id)
  }

  async function handleSaveProject() {
    if (!pendingProject) return
    setActionError(null)
    if (!editLabel.trim()) {
      setActionError('项目名不能为空')
      return
    }
    const body: { label?: string; root_path?: string | null } = {}
    if (editLabel.trim() !== pendingProject.label) body.label = editLabel.trim()
    if ((editRoot.trim() || null) !== (pendingProject.root_path || null)) {
      body.root_path = editRoot.trim() || null
    }
    try {
      if (Object.keys(body).length) {
        await updateProject(pendingProject.id, body)
      }
      setPendingProject(null)
      onChanged?.()
    } catch (err) {
      setActionError(errorMessage(err, '保存项目失败'))
    }
  }

  async function handleArchiveProject() {
    if (!pendingProject) return
    setActionError(null)
    try {
      await updateProject(pendingProject.id, { archived: true })
      setPendingProject(null)
      onChanged?.()
    } catch (err) {
      setActionError(errorMessage(err, '归档项目失败'))
    }
  }

  function openSession(session: SessionSummary) {
    setPendingSession(session)
    setMoveTarget(session.project_id ?? '')
    setActionError(null)
    void loadUsage('session', session.id)
  }

  async function togglePinned() {
    if (!pendingSession) return
    setActionError(null)
    try {
      await updateSession(pendingSession.id, { pinned: !pendingSession.pinned })
      setPendingSession(null)
      onChanged?.()
    } catch (err) {
      setActionError(errorMessage(err, '更新置顶失败'))
    }
  }

  async function moveSession() {
    if (!pendingSession) return
    setActionError(null)
    try {
      await updateSession(pendingSession.id, { project_id: moveTarget || null })
      setPendingSession(null)
      onChanged?.()
    } catch (err) {
      setActionError(errorMessage(err, '移动会话失败'))
    }
  }

  async function archiveSession() {
    if (!pendingSession) return
    setActionError(null)
    try {
      await updateSession(pendingSession.id, { archived: true })
      const id = pendingSession.id
      setPendingSession(null)
      onChanged?.()
      onArchived?.(id)
    } catch (err) {
      setActionError(errorMessage(err, '归档会话失败'))
    }
  }

  async function restoreSession(session: SessionSummary) {
    setActionError(null)
    try {
      await updateSession(session.id, { archived: false })
      onChanged?.()
    } catch (err) {
      setActionError(errorMessage(err, '恢复会话失败'))
    }
  }

  async function restoreProject(project: Project) {
    setActionError(null)
    try {
      await updateProject(project.id, { archived: false })
      onChanged?.()
    } catch (err) {
      setActionError(errorMessage(err, '恢复项目失败'))
    }
  }

  function renderSession(session: SessionSummary) {
    return (
      <li key={session.id} className="group flex items-center gap-1">
        <button
          type="button"
          aria-current={session.id === activeId ? 'true' : 'false'}
          title={session.title}
          onClick={() => {
            onSelect(session.id)
            setMobileOpen(false)
          }}
          className={`min-w-0 flex-1 rounded-lg px-3 py-3 text-left text-sm ${
            session.id === activeId
              ? 'session-selected bg-accent text-accent-foreground'
              : 'text-foreground hover:bg-card/60'
          }`}
        >
          <span className="line-clamp-2 break-words font-medium">
            {session.pinned && <Pin className="mr-1 inline size-3" aria-hidden="true" />}
            {session.title}
          </span>
          <span className="mt-1 block text-xs text-muted-foreground">
            {session.message_count} 条记录
          </span>
        </button>
        <button
          type="button"
          aria-label={`更多 ${session.title}`}
          onClick={() => openSession(session)}
          className="min-h-11 min-w-11 rounded-lg p-2 text-muted-foreground hover:bg-muted md:min-h-0 md:min-w-0 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 focus-visible:opacity-100"
        >
          <MoreHorizontal className="size-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label={`删除 ${session.title}`}
          onClick={() => {
            setDeleteError(null)
            setPendingDelete(session)
          }}
          className="min-h-11 min-w-11 rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-destructive md:min-h-0 md:min-w-0 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 focus-visible:opacity-100"
        >
          <Trash2 className="size-4" aria-hidden="true" />
        </button>
      </li>
    )
  }

  const searchMode = Boolean(query)

  const navigation = (
    <>
      <div className="px-5 pb-4 pt-6">
        <span className="text-2xl font-semibold tracking-tight">
          CellSpatio<span className="text-primary" aria-hidden="true">.</span>
        </span>
        <p className="mt-1 mb-5 text-xs text-muted-foreground">单细胞与时空组学工作台</p>
        <Button
          className="min-h-10 w-full justify-start"
          variant="default"
          aria-label="新建会话"
          onClick={() => void handleCreate(null)}
        >
          <Plus className="size-4" aria-hidden="true" />
          新建会话
        </Button>
        <Button
          className="mt-2 min-h-10 w-full justify-start"
          variant="outline"
          aria-label="新建项目"
          onClick={() => {
            setActionError(null)
            setNewProjectOpen(true)
          }}
        >
          <FolderPlus className="size-4" aria-hidden="true" />
          新建项目
        </Button>
        <label className="mt-4 flex items-center gap-2 rounded-lg border border-border bg-card/70 px-3 focus-within:ring-2 focus-within:ring-ring">
          <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <input
            aria-label="搜索会话"
            placeholder="搜索会话"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="min-h-11 min-w-0 w-full bg-transparent text-sm outline-none focus-visible:outline-none"
          />
        </label>
        <button
          type="button"
          onClick={() => setShowArchived((value) => !value)}
          className="mt-2 flex min-h-9 w-full items-center gap-2 rounded-lg px-3 text-xs text-muted-foreground hover:bg-card/60"
        >
          <Archive className="size-4" aria-hidden="true" />
          {showArchived ? '隐藏已归档' : '显示已归档'}
        </button>
      </div>

      {createError && <p className="px-3 py-2 text-xs text-destructive">{createError}</p>}

      <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-3" aria-label="会话列表">
        <p className="px-3 pb-2 text-xs text-muted-foreground">
          {searchMode ? `搜索结果 ${activeSessions.length}` : `会话记录 ${activeSessions.length}`}
        </p>
        {loading && <p className="px-2 py-1 text-xs text-muted-foreground">加载中…</p>}
        {!loading && activeSessions.length === 0 && activeProjects.length === 0 && (
          <p className="px-2 py-1 text-xs text-muted-foreground">暂无会话</p>
        )}
        {!loading && searchMode && activeSessions.length === 0 && (
          <p role="status" className="px-3 py-4 text-sm text-muted-foreground">
            没有匹配的会话，试试其他关键词。
          </p>
        )}

        {searchMode && (
          <ul className="flex flex-col gap-1">{activeSessions.map(renderSession)}</ul>
        )}

        {!searchMode && pinnedSessions.length > 0 && (
          <section aria-label="置顶会话" className="mb-2">
            <p className="px-3 py-1 text-xs font-medium text-muted-foreground">置顶</p>
            <ul className="flex flex-col gap-1">{pinnedSessions.map(renderSession)}</ul>
          </section>
        )}

        {!searchMode &&
          activeProjects.map((project) => {
            const items = projectSessions(project.id)
            const collapsed = collapsedProjects.has(project.id)
            return (
              <section key={project.id} aria-label={`项目 ${project.label}`} className="mb-2">
                <div className="group flex items-center gap-1">
                  <button
                    type="button"
                    aria-expanded={!collapsed}
                    onClick={() => toggleProject(project.id)}
                    className="flex min-h-11 min-w-0 flex-1 items-center gap-1 rounded-lg px-2 text-left text-sm font-medium hover:bg-card/60"
                  >
                    {collapsed ? (
                      <ChevronRight className="size-4 shrink-0" aria-hidden="true" />
                    ) : (
                      <ChevronDown className="size-4 shrink-0" aria-hidden="true" />
                    )}
                    <span className="truncate">{project.label}</span>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {project.session_count}
                    </span>
                  </button>
                  <button
                    type="button"
                    aria-label={`在 ${project.label} 新建会话`}
                    onClick={() => void handleCreate(project.id)}
                    className="min-h-11 min-w-11 rounded-lg p-2 text-muted-foreground hover:bg-muted"
                  >
                    <Plus className="size-4" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    aria-label={`项目操作 ${project.label}`}
                    onClick={() => openProject(project)}
                    className="min-h-11 min-w-11 rounded-lg p-2 text-muted-foreground hover:bg-muted"
                  >
                    <MoreHorizontal className="size-4" aria-hidden="true" />
                  </button>
                </div>
                {!collapsed && items.length > 0 && (
                  <ul className="ml-3 flex flex-col gap-1 border-l border-border pl-2">
                    {items.map(renderSession)}
                  </ul>
                )}
              </section>
            )
          })}

        {!searchMode && ungroupedSessions.length > 0 && (
          <section aria-label="未归类会话" className="mb-2">
            <p className="px-3 py-1 text-xs font-medium text-muted-foreground">未归类</p>
            <ul className="flex flex-col gap-1">{ungroupedSessions.map(renderSession)}</ul>
          </section>
        )}

        {showArchived && (
          <section aria-label="已归档" className="mb-2">
            <p className="px-3 py-1 text-xs font-medium text-muted-foreground">已归档</p>
            <ul className="flex flex-col gap-1">
              {archivedProjects.map((project) => (
                <li key={project.id} className="flex items-center gap-1">
                  <span className="flex min-h-11 min-w-0 flex-1 items-center gap-1 rounded-lg px-2 text-sm text-muted-foreground">
                    <Archive className="size-4 shrink-0" aria-hidden="true" />
                    <span className="truncate">{project.label}</span>
                    <span className="ml-auto text-xs">{project.session_count}</span>
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`恢复项目 ${project.label}`}
                    onClick={() => void restoreProject(project)}
                  >
                    恢复
                  </Button>
                </li>
              ))}
              {archivedSessions.map((session) => (
                <li key={session.id} className="flex items-center gap-1">
                  <span className="flex min-h-11 min-w-0 flex-1 items-center rounded-lg px-3 text-sm text-muted-foreground">
                    <span className="line-clamp-2 break-words">{session.title}</span>
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`恢复会话 ${session.title}`}
                    onClick={() => void restoreSession(session)}
                  >
                    恢复
                  </Button>
                </li>
              ))}
              {archivedProjects.length === 0 && archivedSessions.length === 0 && (
                <li className="px-3 py-1 text-xs text-muted-foreground">暂无归档记录</li>
              )}
              {actionError && <li className="px-3 py-1 text-xs text-destructive">{actionError}</li>}
            </ul>
          </section>
        )}
      </nav>

      <section className="shrink-0 border-t border-border px-5 py-4 text-xs" aria-label="运行环境状态">
        <details>
          <summary className="cursor-pointer py-2 font-medium text-muted-foreground">运行环境</summary>
          <ul className="mt-3 flex flex-col gap-2">
            <li className="flex items-center gap-2">
              <Dot ok={kbStats.initialized} testId="dot-kb" />
              <span>知识库 {kbStats.initialized ? '已初始化' : '未初始化'}</span>
            </li>
            <li className="flex items-center gap-2">
              <Dot ok={Boolean(env.rscript)} testId="dot-rscript" />
              <span>Rscript {env.rscript ? '可用' : '缺失'}</span>
            </li>
            <li className="flex items-center gap-2">
              <Dot ok={env.kb_ok} testId="dot-kbpath" />
              <span className="min-w-0 break-all" title={env.kb_path}>
                知识库目录 {env.kb_ok ? '存在' : '缺失'}（{env.kb_path}）
              </span>
            </li>
            <li className="flex items-center gap-2">
              <Dot
                ok={['ready', 'starting', 'downloading', 'idle'].includes(env.ollama.state)}
                testId="dot-ollama"
              />
              <span>Ollama {env.ollama.state === 'ready' ? '就绪' : env.ollama.state}</span>
              <span className="ml-auto shrink-0 text-[10px] text-muted-foreground" data-testid="ollama-start-mode">
                {env.ollama_start_mode === 'eager' ? '随程序启动' : '按需唤起'}
              </span>
            </li>
          </ul>
          {env.ollama.state === 'needs_model' && (
            <div data-testid="ollama-needs-model" className="mt-2 space-y-1 break-all text-[color:var(--review)]">
              <p className="font-medium">应用不会自动下载模型</p>
              <p>{env.ollama.detail}</p>
            </div>
          )}
          {kbStats.error && (
            <p className="mt-2 break-all text-muted-foreground">知识库错误：{kbStats.error}</p>
          )}
        </details>
      </section>
    </>
  )

  const deleteDialog = (
    <Dialog
      open={pendingDelete !== null}
      onOpenChange={(open) => {
        if (!open) setPendingDelete(null)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>确认删除会话</DialogTitle>
          <DialogDescription>
            将删除「{pendingDelete?.title}」及其全部对话记录，删除后不可恢复。
          </DialogDescription>
        </DialogHeader>
        {deleteError && <p className="text-xs text-destructive">{deleteError}</p>}
        <DialogFooter>
          <DialogClose>取消</DialogClose>
          <Button variant="destructive" onClick={handleConfirmDelete}>
            确认删除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

  const sessionDialog = (
    <Dialog open={pendingSession !== null} onOpenChange={(open) => !open && setPendingSession(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>会话操作</DialogTitle>
          <DialogDescription>{pendingSession?.title}</DialogDescription>
        </DialogHeader>
        <p className="text-xs text-muted-foreground" data-testid="session-usage">
          {usageLine()}
        </p>
        <div className="flex flex-col gap-3">
          <Button variant="outline" className="justify-start" onClick={() => void togglePinned()}>
            {pendingSession?.pinned ? (
              <PinOff className="size-4" aria-hidden="true" />
            ) : (
              <Pin className="size-4" aria-hidden="true" />
            )}
            {pendingSession?.pinned ? '取消置顶' : '置顶'}
          </Button>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            移动到项目
            <select
              aria-label="移动到项目"
              value={moveTarget}
              onChange={(event) => setMoveTarget(event.target.value)}
              className="min-h-11 rounded-lg border border-border bg-card px-3 text-sm text-foreground"
            >
              <option value="">未归类</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.label}
                </option>
              ))}
            </select>
          </label>
          <Button variant="outline" className="justify-start" onClick={() => void moveSession()}>
            移动
          </Button>
          <Button variant="outline" className="justify-start" onClick={() => void archiveSession()}>
            <Archive className="size-4" aria-hidden="true" />
            归档会话
          </Button>
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
        </div>
        <DialogFooter>
          <DialogClose>关闭</DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

  const projectDialog = (
    <Dialog open={pendingProject !== null} onOpenChange={(open) => !open && setPendingProject(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>项目设置</DialogTitle>
          <DialogDescription>{pendingProject?.label}</DialogDescription>
        </DialogHeader>
        <p className="text-xs text-muted-foreground" data-testid="project-usage">
          {usageLine()}
        </p>
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            项目名
            <input
              aria-label="项目名"
              value={editLabel}
              onChange={(event) => setEditLabel(event.target.value)}
              className="min-h-11 rounded-lg border border-border bg-card px-3 text-sm text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            工作目录（可空）
            <input
              aria-label="工作目录"
              value={editRoot}
              onChange={(event) => setEditRoot(event.target.value)}
              className="min-h-11 rounded-lg border border-border bg-card px-3 text-sm text-foreground"
            />
          </label>
          <Button variant="outline" className="justify-start" onClick={() => void handleSaveProject()}>
            保存
          </Button>
          <Button
            variant="outline"
            className="justify-start"
            onClick={() => pendingProject && void handleCreate(pendingProject.id)}
          >
            <Plus className="size-4" aria-hidden="true" />
            新建会话
          </Button>
          <Button variant="outline" className="justify-start" onClick={() => void handleArchiveProject()}>
            <Archive className="size-4" aria-hidden="true" />
            归档项目
          </Button>
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
        </div>
        <DialogFooter>
          <DialogClose>关闭</DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

  const newProjectDialog = (
    <Dialog open={newProjectOpen} onOpenChange={setNewProjectOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建项目</DialogTitle>
          <DialogDescription>项目名与可选工作目录；会话随后归入项目。</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            项目名
            <input
              aria-label="新项目名"
              value={projectLabel}
              onChange={(event) => setProjectLabel(event.target.value)}
              className="min-h-11 rounded-lg border border-border bg-card px-3 text-sm text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            工作目录（可空）
            <input
              aria-label="新项目工作目录"
              value={projectRoot}
              onChange={(event) => setProjectRoot(event.target.value)}
              className="min-h-11 rounded-lg border border-border bg-card px-3 text-sm text-foreground"
            />
          </label>
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
        </div>
        <DialogFooter>
          <DialogClose>取消</DialogClose>
          <Button onClick={() => void handleCreateProject()}>创建项目</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

  return (
    <>
      <aside className="workspace-sidebar hidden w-[17rem] shrink-0 flex-col border-r border-border md:flex">
        {navigation}
      </aside>
      <Dialog open={mobileOpen} onOpenChange={setMobileOpen}>
        <div className="workspace-sidebar flex shrink-0 items-center justify-between border-b border-border px-5 py-1 md:hidden">
          <span className="font-semibold">CellSpatio</span>
          <DialogTrigger render={<Button variant="ghost" className="min-h-11" />}>
            <Menu aria-hidden="true" />会话
          </DialogTrigger>
        </div>
        <DialogContent className="inset-y-0 left-0 top-0 flex h-dvh w-[min(320px,calc(100%-2rem))] max-w-none translate-x-0 translate-y-0 flex-col gap-0 rounded-none bg-muted p-0 sm:max-w-none [&>[data-slot=dialog-close]]:size-11">
          <DialogTitle className="sr-only">会话导航</DialogTitle>
          <DialogDescription className="sr-only">选择或新建会话，查看运行环境。</DialogDescription>
          {navigation}
        </DialogContent>
      </Dialog>
      {deleteDialog}
      {sessionDialog}
      {projectDialog}
      {newProjectDialog}
    </>
  )
}
