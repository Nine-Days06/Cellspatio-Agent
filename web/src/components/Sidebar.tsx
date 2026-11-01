import { useState } from 'react'
import { Menu, Plus, Search, Trash2 } from 'lucide-react'
import { ApiError, createSession, deleteSession } from '@/lib/api'
import type { EnvStatus, KbStats, SessionSummary } from '@/lib/types'
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
  activeId: string | null
  kbStats: KbStats
  env: EnvStatus
  loading?: boolean
  onSelect: (sessionId: string) => void
  onCreated: (session: SessionSummary) => void
  onDeleted: (sessionId: string) => void
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

export function Sidebar({
  sessions,
  activeId,
  kbStats,
  env,
  loading,
  onSelect,
  onCreated,
  onDeleted,
}: SidebarProps) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const visibleSessions = sessions.filter((session) => session.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))

  async function handleCreate() {
    setCreateError(null)
    try {
      const { session } = await createSession()
      onCreated({
        id: session.id,
        title: session.title,
        updated_at: session.updated_at,
        message_count: 0,
      })
      setMobileOpen(false)
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : '新建会话失败')
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
      // 409：会话有任务在进行 → 保留列表，只提示（约束 3b）
      setDeleteError(err instanceof ApiError ? err.message : '删除会话失败')
    }
  }

  const navigation = <>
      <div className="px-5 pb-4 pt-6">
        <span className="text-2xl font-semibold tracking-tight">CellSpatio<span className="text-primary" aria-hidden="true">.</span></span>
        <p className="mt-1 mb-5 text-xs text-muted-foreground">单细胞与时空组学工作台</p>
        <Button
          className="min-h-10 w-full justify-start"
          variant="default"
          aria-label="新建会话"
          onClick={handleCreate}
        >
          <Plus className="size-4" aria-hidden="true" />
          新建会话
        </Button>
        <label className="mt-4 flex items-center gap-2 rounded-lg border border-border bg-card/70 px-3 focus-within:ring-2 focus-within:ring-ring">
          <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <input aria-label="搜索会话" placeholder="搜索会话" type="search" value={search} onChange={(event) => setSearch(event.target.value)} className="min-h-11 min-w-0 w-full bg-transparent text-sm outline-none focus-visible:outline-none" />
        </label>
      </div>

      {createError && (
        <p className="px-3 py-2 text-xs text-destructive">{createError}</p>
      )}

      <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-3" aria-label="会话列表">
        <p className="px-3 pb-2 text-xs text-muted-foreground">{search.trim() ? `搜索结果 ${visibleSessions.length}` : `会话记录 ${sessions.length}`}</p>
        {loading && <p className="px-2 py-1 text-xs text-muted-foreground">加载中…</p>}
        {!loading && sessions.length === 0 && (
          <p className="px-2 py-1 text-xs text-muted-foreground">暂无会话</p>
        )}
        {!loading && sessions.length > 0 && visibleSessions.length === 0 && <p role="status" className="px-3 py-4 text-sm text-muted-foreground">没有匹配的会话，试试其他关键词。</p>}
        <ul className="flex flex-col gap-1">
          {visibleSessions.map((session) => (
            <li key={session.id} className="group flex items-center gap-1">
              <button
                type="button"
                aria-current={session.id === activeId ? 'true' : 'false'}
                title={session.title}
                onClick={() => { onSelect(session.id); setMobileOpen(false) }}
                className={`min-w-0 flex-1 rounded-lg px-3 py-3 text-left text-sm ${
                  session.id === activeId
                    ? 'session-selected bg-accent text-accent-foreground'
                    : 'text-foreground hover:bg-card/60'
                }`}
              >
                <span className="line-clamp-2 break-words font-medium">{session.title}</span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {session.message_count} 条记录
                </span>
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
          ))}
        </ul>
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
            <span>
              Ollama {env.ollama.state === 'ready' ? '就绪' : env.ollama.state}
            </span>
            {/* 启动模式是只读的配置态展示（改它要改 .env 并重启，故不给切换控件） */}
            <span
              data-testid="ollama-start-mode"
              className="ml-auto shrink-0 text-[10px] text-muted-foreground"
            >
              {env.ollama_start_mode === 'eager' ? '随程序启动' : '按需唤起'}
            </span>
          </li>
        </ul>
        {env.ollama.state === 'needs_model' && (
          <div
            data-testid="ollama-needs-model"
            className="mt-2 space-y-1 break-all text-amber-800"
          >
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

  const deleteDialog = <Dialog
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

  return (
    <>
      <aside className="workspace-sidebar hidden w-[17rem] shrink-0 flex-col border-r border-border md:flex">{navigation}</aside>
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
        {mobileOpen && deleteDialog}
      </Dialog>
      {!mobileOpen && deleteDialog}
    </>
  )
}
