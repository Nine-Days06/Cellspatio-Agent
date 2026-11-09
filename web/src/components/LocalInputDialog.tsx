import { useState, type FormEvent } from 'react'
import { FolderOpen } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
import { ApiError } from '@/lib/api'

/** 本地 10x 目录登记：不经过 LLM，QC 成功后由消息区接管后续人工 gate。 */
export function LocalInputDialog({ onSubmit }: { onSubmit: (path: string) => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!path.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      await onSubmit(path.trim())
      setOpen(false)
      setPath('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '本地输入登记失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        setOpen(next)
        setError(null)
      }}
    >
      <DialogTrigger render={<Button variant="outline" className="min-h-9" />}>
        <FolderOpen className="size-4" aria-hidden="true" />
        本地数据
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>本地 10x 数据</DialogTitle>
            <DialogDescription>
              直接读取本机目录，不经过模型。QC 在本机运行；过滤、PCA、聚类与 marker 仍各自需要人工授权。
            </DialogDescription>
          </DialogHeader>
          <label className="block space-y-1 text-xs">
            <span>数据目录或 counts CSV 的绝对路径</span>
            <Input
              aria-label="本地数据目录"
              value={path}
              disabled={busy}
              placeholder="D:/data/pbmc3k/filtered_gene_bc_matrices/hg19"
              onChange={(event) => setPath(event.target.value)}
            />
          </label>
          <p className="text-xs text-muted-foreground">
            10x 目录需包含 matrix.mtx、barcodes.tsv 与 features.tsv（支持 .gz）。首次运行需加载
            R/Seurat，可能耗时数十秒。
          </p>
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <DialogClose disabled={busy}>取消</DialogClose>
            <Button type="submit" disabled={busy || !path.trim()}>
              {busy ? '正在运行 QC…' : '开始 QC'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
