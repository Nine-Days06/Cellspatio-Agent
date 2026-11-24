import { Loader2 } from 'lucide-react'
import type { ToolStatusEntry } from '@/store/chat'

/** 类型再导出：规范定义在 store（T5a 冻结契约），组件侧从本文件导入即可。 */
export type { ToolStatusEntry }

/**
 * 工具过程条：只渲染「正在进行」的过程文案。
 * 后端不保证发 phase:'end'（agent_runtime 终态提前 return），
 * 因此绝不据此显示「已完成」之类的终态语义（约束 2）。
 *
 * fallback 用于没有活动阶段时的模式兜底文案；与阶段文案共用同一位置，
 * 保证同一时刻只有一条状态。
 */
export function ToolStatus({
  entry,
  fallback,
}: {
  entry: ToolStatusEntry | null
  fallback?: string | null
}) {
  const text = entry && entry.phase !== 'end' ? (entry.label ?? entry.name) : fallback
  if (!text) return null
  return (
    <div
      data-testid="tool-status"
      className="mb-1 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground"
    >
      <Loader2 className="size-3 animate-spin" aria-hidden="true" />
      <span className="truncate">{text}</span>
      {entry && <span aria-hidden="true">…</span>}
    </div>
  )
}
