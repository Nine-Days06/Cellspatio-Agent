import { useState } from 'react'
import { ArrowRight, FolderOpen, LogIn, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ApiError, fetchWorkspace, listProjects, listSessions, logout, startNewWorkspace } from '@/lib/api'
import { navigate } from '@/lib/navigation'
import type { WorkspaceInfo } from '@/lib/types'

export function HomePage() {
  const [busy, setBusy] = useState<'trial' | 'workspace' | null>(null)
  const [confirmTrial, setConfirmTrial] = useState<{ persistent: boolean } | null>(null)
  const [error, setError] = useState('')

  async function currentWorkspace(): Promise<WorkspaceInfo | null> {
    try {
      return (await fetchWorkspace()).workspace
    } catch (err) {
      if (err instanceof ApiError && err.status === 410) return null
      throw err
    }
  }

  async function openNewTrial(persistent: boolean) {
    if (persistent) await logout()
    await startNewWorkspace()
    navigate('/workspace')
  }

  async function handleTrial() {
    setError('')
    setBusy('trial')
    try {
      const workspace = await currentWorkspace()
      if (workspace?.kind === 'ephemeral') {
        const [sessions, projects] = await Promise.all([
          listSessions(true),
          listProjects(true),
        ])
        if (sessions.sessions.length > 0 || projects.projects.length > 0) {
          setConfirmTrial({ persistent: false })
          return
        }
      }
      await openNewTrial(workspace?.kind === 'persistent')
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法创建试用空间')
    } finally {
      setBusy(null)
    }
  }

  async function handleWorkspace() {
    setError('')
    setBusy('workspace')
    try {
      const workspace = await currentWorkspace()
      if (workspace?.id) navigate('/workspace')
      else await openNewTrial(workspace?.kind === 'persistent')
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法进入工作区')
    } finally {
      setBusy(null)
    }
  }

  async function confirmNewTrial() {
    const pending = confirmTrial
    setConfirmTrial(null)
    setBusy('trial')
    try {
      await openNewTrial(pending?.persistent ?? false)
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法创建试用空间')
    } finally {
      setBusy(null)
    }
  }

  return (
    <main className="home-page relative flex min-h-dvh items-center justify-center overflow-hidden bg-background px-6 py-12 text-foreground">
      <div className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="absolute -top-40 right-[-10rem] size-[34rem] rounded-full bg-accent/70 blur-3xl" />
        <div className="absolute -bottom-52 left-[-8rem] size-[30rem] rounded-full bg-muted/80 blur-3xl" />
      </div>

      <section className="relative w-full max-w-3xl">
        <p className="motion-home-step text-xs font-medium tracking-[0.24em] text-primary" data-motion-step="1">CELLSPATIO</p>
        <h1 className="motion-home-step mt-5 max-w-2xl text-4xl leading-tight font-semibold tracking-tight md:text-5xl" data-motion-step="1">
          从单细胞数据到可复核的分析记录
        </h1>
        <p className="motion-home-step mt-5 max-w-xl text-base leading-7 text-muted-foreground" data-motion-step="2">
          CellSpatio 面向单细胞与空间组学，把数据检查、受控分析阶段和证据记录放在同一个工作台里。
          试用空间无需注册，24 小时后自动到期。
        </p>

        <div className="motion-home-step mt-9 flex flex-wrap gap-3" data-motion-step="3">
          <Button size="lg" onClick={() => navigate('/login')}>
            <LogIn />
            登录
          </Button>
          <Button size="lg" variant="outline" disabled={busy !== null} onClick={() => void handleTrial()}>
            <Play />
            {busy === 'trial' ? '正在创建…' : '试用'}
          </Button>
          <Button size="lg" variant="ghost" disabled={busy !== null} onClick={() => void handleWorkspace()}>
            <FolderOpen />
            {busy === 'workspace' ? '正在打开…' : '工作区'}
            <ArrowRight />
          </Button>
        </div>

        {error && (
          <p role="alert" className="mt-4 text-sm text-destructive">
            {error}
          </p>
        )}

        <dl className="motion-home-step mt-12 grid gap-4 text-sm text-muted-foreground sm:grid-cols-3" data-motion-step="4">
          <div>
            <dt className="font-medium text-foreground">受控阶段</dt>
            <dd className="mt-1 leading-6">QC、PCA、聚类与 marker 逐阶段审阅。</dd>
          </div>
          <div>
            <dt className="font-medium text-foreground">独立空间</dt>
            <dd className="mt-1 leading-6">会话与项目按工作区隔离，互不可见。</dd>
          </div>
          <div>
            <dt className="font-medium text-foreground">证据记录</dt>
            <dd className="mt-1 leading-6">分析输入与结果绑定，保留可核验线索。</dd>
          </div>
        </dl>
      </section>

      <Dialog open={confirmTrial !== null} onOpenChange={(open) => !open && setConfirmTrial(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>放弃当前临时数据？</DialogTitle>
            <DialogDescription>
              试用会新建一个临时工作区。当前临时空间里的会话和项目不会迁移，且无法恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmTrial(null)}>
              保留当前空间
            </Button>
            <Button onClick={() => void confirmNewTrial()}>放弃并新建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  )
}
