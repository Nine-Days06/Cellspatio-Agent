import { useEffect, useRef, useState } from 'react'
import type { ChartItem } from '@/lib/types'

/** Plotly figure：{data, layout} 对象形态或裸 trace 数组（JSON.parse 结果）。 */
type Figure = { data?: unknown[]; layout?: Record<string, unknown> }

const PARSE_FAILED = '图表解析失败'
const RENDER_FAILED = '图表渲染失败'

/** 后端 image_b64 是裸 base64（session_store.py:65）；兼容已带 data: 前缀的输入。 */
function toImageSrc(imageB64: string): string {
  return imageB64.startsWith('data:') ? imageB64 : `data:image/png;base64,${imageB64}`
}

export function ChartBlock({ item }: { item: ChartItem }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [inView, setInView] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Plotly 懒加载第一步：进入视口（rootMargin 200px）才允许挂载
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    if (!('IntersectionObserver' in window)) {
      setInView(true)
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setInView(true)
          observer.disconnect()
        }
      },
      { rootMargin: '200px 0px' },
    )
    observer.observe(host)
    return () => observer.disconnect()
  }, [])

  // Plotly 懒加载第二步：视口触发后才动态 import（plotly 独立 chunk，不进首屏主包）
  useEffect(() => {
    if (!inView || item.type !== 'plotly') return
    const host = hostRef.current
    if (!host) return

    let figure: Figure | unknown[]
    try {
      figure = JSON.parse(item.figure_json) as Figure | unknown[]
    } catch {
      setError(PARSE_FAILED)
      return
    }

    const data = Array.isArray(figure) ? figure : ((figure as Figure)?.data ?? [])
    const layout = Array.isArray(figure) ? {} : ((figure as Figure)?.layout ?? {})

    let cancelled = false
    void (async () => {
      try {
        const Plotly = (await import('plotly.js-dist-min')).default
        await Plotly.newPlot(host, data, layout, {
          responsive: true,
          displaylogo: false,
          margin: { t: 32 },
        })
      } catch {
        // plotly 加载失败或渲染失败：给实验生物学家可读占位，不崩溃
        if (!cancelled) setError(RENDER_FAILED)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [inView, item])

  if (item.type === 'image') {
    return (
      <img
        data-testid="chart-image"
        src={toImageSrc(item.image_b64)}
        alt="分析图表"
        className="max-w-full rounded-lg border border-border"
      />
    )
  }

  return (
    <div className="w-full">
      <div
        data-testid="chart-plotly"
        ref={hostRef}
        className="h-72 w-full rounded-lg border border-border"
      />
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
    </div>
  )
}
