import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

/** 预览适应正文，大图保留原始像素尺寸并在弹窗内滚动。 */
export function AnalysisImage({ src, alt, testId }: { src: string; alt: string; testId?: string }) {
  const [error, setError] = useState(false)
  const [originalError, setOriginalError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => { setError(false); setOriginalError(false) }, [src])
  function retry() {
    setError(false)
    setOriginalError(false)
    // 重建 img 重试同一来源，不改精确结果路径或触发分析。
    setAttempt((value) => value + 1)
  }
  return <figure className="min-w-0 space-y-2">
    <img key={attempt} src={src} alt={alt} data-testid={testId} className={`analysis-preview h-auto max-w-full rounded-lg border border-border bg-white ${error ? 'hidden' : ''}`} onError={() => setError(true)} />
    {error ? <div className="image-error space-y-2 rounded-lg border border-border bg-muted/40 p-4"><p role="alert">图像读取失败；可只读重试，不会重新执行分析。</p><Button variant="outline" onClick={retry}>重试图像</Button></div> : <Dialog>
      <DialogTrigger render={<Button variant="outline" className="min-h-11" />} aria-label={`查看原始尺寸：${alt}`}>查看原始尺寸<span className="ml-1 text-xs text-muted-foreground" aria-hidden="true">仅查看</span></DialogTrigger>
      <DialogContent showCloseButton={false} className="original-image-dialog h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] grid-rows-[auto_minmax(0,1fr)] sm:max-w-[calc(100%-2rem)]">
        <div className="flex items-center justify-between gap-3">
          <DialogTitle className="min-w-0 leading-snug">原始尺寸图表</DialogTitle>
          <DialogClose render={<Button variant="outline" className="min-h-11 shrink-0" />}>关闭图表</DialogClose>
        </div>
        <div tabIndex={0} role="region" aria-label="原始尺寸图表，可横向和纵向滚动" className="min-h-0 min-w-0 overflow-auto rounded-lg border border-border bg-white">
          <DialogDescription className="sticky left-0 max-w-full border-b border-border bg-card p-3 leading-relaxed">{alt}。保持原始像素尺寸，可横向与纵向滚动；不改变分析结果。</DialogDescription>
          {originalError ? <div className="p-4"><p role="alert">原图读取失败，可只读重试。</p><Button variant="outline" onClick={retry}>重试原图</Button></div> : <img key={attempt} src={src} alt={alt} className="h-auto max-w-none" onError={() => setOriginalError(true)} />}
        </div>
      </DialogContent>
    </Dialog>}
  </figure>
}
