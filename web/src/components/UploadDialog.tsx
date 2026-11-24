import { useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

const MAX_FILE_BYTES = 100 * 1024 * 1024

export function UploadDialog({
  onSubmit,
}: {
  onSubmit: (file: File) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  async function submit() {
    if (!file) {
      setError('请选择 CSV 或 zip 文件')
      return
    }
    if (file.size > MAX_FILE_BYTES) {
      setError('单个文件不能超过 100 MB')
      return
    }
    setError('')
    setPending(true)
    try {
      await onSubmit(file)
      setOpen(false)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline" size="sm" className="inline-flex">
            <Upload />
            上传数据
          </Button>
        }
      />
      <DialogContent aria-busy={pending}>
        <DialogHeader>
          <DialogTitle>上传单细胞数据</DialogTitle>
          <DialogDescription>
            支持 10x 三件套 zip 或 counts CSV。文件只保存在当前工作区，单文件上限 100 MB。
          </DialogDescription>
        </DialogHeader>
        <input
          ref={inputRef}
          aria-label="选择上传文件"
          type="file"
          accept=".csv,.zip"
          className={cn(
            'block w-full rounded-lg border border-input bg-background px-3 py-2 text-sm transition-colors duration-[var(--motion-fast)] file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-2 file:py-1 file:text-sm',
            file && 'border-primary/60 bg-primary/5',
          )}
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
        {pending && (
          <div
            role="progressbar"
            aria-label="上传进行中"
            className="h-1 overflow-hidden rounded-full bg-muted"
          >
            <div className="motion-shimmer h-full w-2/5 rounded-full bg-primary" />
            <span className="sr-only">正在上传，无法预估剩余进度</span>
          </div>
        )}
        {error && (
          <p role="alert" className="motion-fade-fast text-sm text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={pending}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={pending || !file}>
            {pending ? '正在上传…' : '上传并运行 QC'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
