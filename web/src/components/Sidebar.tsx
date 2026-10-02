import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
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
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)

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

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-card">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-3">
        <span className="text-sm font-semibold tracking-tight">CellSpatio</span>
        <Button
          size="sm"
          variant="outline"
          aria-label="新建会话"
          onClick={handleCreate}
        >
          <Plus className="size-4" aria-hidden="true" />
          新会话
        </Button>
      </div>

      {createError && (
        <p className="px-3 py-2 text-xs text-destructive">{createError}</p>
      )}

      <nav className="flex-1 overflow-y-auto p-2" aria-label="会话列表">
        {loading && <p className="px-2 py-1 text-xs text-muted-foreground">加载中…</p>}
        {!loading && sessions.length === 0 && (
          <p className="px-2 py-1 text-xs text-muted-foreground">暂无会话</p>
        )}
        <ul className="flex flex-col gap-1">
          {sessions.map((session) => (
            <li key={session.id} className="group flex items-center gap-1">
              <button
                type="button"
                aria-current={session.id === activeId ? 'true' : 'false'}
                onClick={() => onSelect(session.id)}
                className={`flex-1 truncate rounded-lg px-2 py-1.5 text-left text-sm ${
                  session.id === activeId
                    ? 'bg-accent text-accent-foreground'
                    : 'text-foreground hover:bg-muted'
                }`}
              >
                {session.title}
                <span className="ml-1 text-xs text-muted-foreground">
                  {session.message_count}
                </span>
              </button>
              <button
                type="button"
                aria-label={`删除 ${session.title}`}
                onClick={() => {
                  setDeleteError(null)
                  setPendingDelete(session)
                }}
                className="rounded-lg p-1.5 text-muted-foreground opacity-0 hover:bg-muted hover:text-destructive group-hover:opacity-100"
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <section className="border-t border-border px-3 py-3 text-xs" aria-label="运行环境状态">
        <h2 className="mb-2 font-medium text-muted-foreground">状态</h2>
        <ul className="flex flex-col gap-1.5">
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
            <span className="truncate" title={env.kb_path}>
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
            className="mt-2 space-y-1 break-all text-amber-500"
          >
            <p className="font-medium">应用不会自动下载模型</p>
            <p>{env.ollama.detail}</p>
          </div>
        )}
        {kbStats.error && (
          <p className="mt-2 break-all text-muted-foreground">知识库错误：{kbStats.error}</p>
        )}
      </section>

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
    </aside>
  )
}
