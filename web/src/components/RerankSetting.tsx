import { useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { readRerankPreference, writeRerankPreference } from '@/lib/preferences'

/** 检索设置：重排开关与开启/关闭差异说明；偏好只存浏览器本地。 */
export function RerankSetting({ configured, model }: { configured: boolean; model: string }) {
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState(readRerankPreference)

  function toggle(): void {
    const next = !enabled
    writeRerankPreference(next)
    setEnabled(next)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="ghost" size="sm" className="min-h-11" />}>
        <SlidersHorizontal aria-hidden="true" />
        检索设置
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>检索设置</DialogTitle>
          <DialogDescription>
            控制文献检索在返回证据前是否做相关性重排。偏好只保存在当前浏览器。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-4 rounded-lg border border-border p-3">
            <div>
              <p id="rerank-switch-label" className="text-sm font-medium">
                文献重排
              </p>
              <p className="text-xs text-muted-foreground">
                {!configured ? '当前部署不可用' : enabled ? '已开启' : '已关闭（默认）'}
              </p>
            </div>
            <Button
              type="button"
              role="switch"
              variant={enabled ? 'secondary' : 'outline'}
              aria-checked={enabled}
              aria-labelledby="rerank-switch-label"
              disabled={!configured}
              onClick={toggle}
            >
              {enabled ? '关闭' : '开启'}
            </Button>
          </div>
          {configured ? (
            <p className="text-xs text-muted-foreground">当前模型：{model || '未指定'}</p>
          ) : (
            <p role="status" className="text-xs text-amber-600 dark:text-amber-400">
              当前部署未配置重排服务，开关不可用。
            </p>
          )}
          <div className="space-y-2 text-xs text-muted-foreground">
            <p>
              <span className="font-medium text-foreground">开启：</span>
              按相关性重排证据，相关文献更可能靠前；增加一次重排打分耗时，排序会变化，
              分页游标与重排配置绑定。查询与候选片段会发往部署配置的重排服务，请确认该服务在允许的数据范围内。
            </p>
            <p>
              <span className="font-medium text-foreground">关闭：</span>
              完全按检索服务返回顺序，不调用重排模型，无额外延迟。
            </p>
          </div>
        </div>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  )
}
