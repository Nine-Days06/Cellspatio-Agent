interface SoftWarnBannerProps {
  visible: boolean
}

/** 压缩软阈值横幅：来源为 /api/messages 与 /api/sidebar 的 soft_warn，或 SSE compressed 事件。 */
export function SoftWarnBanner({ visible }: SoftWarnBannerProps) {
  if (!visible) return null
  return (
    <div
      role="status"
      className="mb-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-500"
    >
      上下文接近压缩阈值，较早的对话内容可能会被自动摘要。
    </div>
  )
}
