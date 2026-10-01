import { Loader2 } from 'lucide-react'
import type { ToolStatusEntry } from '@/store/chat'

/** 类型再导出：规范定义在 store（T5a 冻结契约），组件侧从本文件导入即可。 */
export type { ToolStatusEntry }

/**
 * 工具过程条：只渲染「正在进行」的过程文案。
 * 后端不保证发 phase:'end'（agent_runtime 终态提前 return），
 * 因此绝不据此显示「已完成」之类的终态语义（约束 2）。
 */
export function ToolStatus({ entry }: { entry: ToolStatusEntry | null }) {
  if (!entry || entry.phase === 'end') return null
  return (
    <div
      data-testid="tool-status"
      className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground"
    >
      <Loader2 className="size-3 animate-spin" aria-hidden="true" />
      <span>{entry.label ?? entry.name}</span>
      <span aria-hidden="true">…</span>
    </div>
  )
}
