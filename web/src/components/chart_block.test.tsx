import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChartBlock } from '@/components/ChartBlock'
import type { ChartItem } from '@/lib/types'

/**
 * 懒加载探针（globalThis 级）：
 * - 模块级 vi.hoisted 对象在 mock 工厂与测试上下文间不共享（实测），故状态放 globalThis；
 * - mock 工厂只执行一次（模块缓存），「工厂是否被调用」无法反映组件是否发起加载，
 *   改用 default getter：组件每次 `import(...).default` 取用都会经过它，逐次计数；
 * - failLoad 置位时 getter 抛错，等价于 plotly 模块加载失败。
 */
function probe() {
  const g = globalThis as unknown as {
    __plotlyLoads?: number
    __plotlyFailLoad?: boolean
    __plotlyStub?: { newPlot: ReturnType<typeof vi.fn> }
  }
  return {
    get loads() {
      return g.__plotlyLoads ?? 0
    },
    resetLoads() {
      g.__plotlyLoads = 0
    },
    setFailLoad(value: boolean) {
      g.__plotlyFailLoad = value
    },
    get stub() {
      g.__plotlyStub ??= { newPlot: vi.fn().mockResolvedValue(undefined) }
      return g.__plotlyStub
    },
  }
}

vi.mock('plotly.js-dist-min', () => ({
  get default() {
    const g = globalThis as unknown as {
      __plotlyLoads?: number
      __plotlyFailLoad?: boolean
      __plotlyStub?: { newPlot: ReturnType<typeof vi.fn> }
    }
    g.__plotlyLoads = (g.__plotlyLoads ?? 0) + 1
    if (g.__plotlyFailLoad) throw new Error('plotly 加载失败')
    g.__plotlyStub ??= { newPlot: vi.fn().mockResolvedValue(undefined) }
    return g.__plotlyStub
  },
}))

afterEach(() => {
  probe().setFailLoad(false)
})

describe('ChartBlock', () => {
  it('image 形态渲染 <img>，裸 base64 自动补 data: 前缀', () => {
    const item: ChartItem = { type: 'image', image_b64: 'eHl6' }
    render(<ChartBlock item={item} />)
    const img = screen.getByTestId('chart-image') as HTMLImageElement
    expect(img.src).toBe('data:image/png;base64,eHl6')
  })

  it('image 形态已带 data: 前缀时不重复拼接', () => {
    const item: ChartItem = { type: 'image', image_b64: 'data:image/svg+xml;utf8,<svg/>' }
    render(<ChartBlock item={item} />)
    const img = screen.getByTestId('chart-image') as HTMLImageElement
    expect(img.src).toBe('data:image/svg+xml;utf8,<svg/>')
  })

  it('image 形态直出，不触发 plotly 懒加载', async () => {
    probe().resetLoads()
    render(<ChartBlock item={{ type: 'image', image_b64: 'eHl6' }} />)
    expect(screen.getByTestId('chart-image')).toBeInTheDocument()
    // flush 异步 import 微任务后仍为 0 = plotly 模块从未被组件 import
    await act(async () => {})
    expect(probe().loads).toBe(0)
  })

  it('plotly 形态挂载后调用 Plotly.newPlot', async () => {
    const p = probe()
    p.resetLoads()
    const Plotly = p.stub
    Plotly.newPlot.mockClear()
    const figure = {
      data: [{ x: [1, 2], y: [3, 4], type: 'scatter' }],
      layout: { title: 'PCA' },
    }
    render(<ChartBlock item={{ type: 'plotly', figure_json: JSON.stringify(figure) }} />)

    await waitFor(() => expect(Plotly.newPlot).toHaveBeenCalledTimes(1))
    const [root, data, layout] = Plotly.newPlot.mock.calls[0]
    expect((root as HTMLElement).getAttribute('data-testid')).toBe('chart-plotly')
    expect(data).toEqual(figure.data)
    expect(layout).toMatchObject({ title: 'PCA' })
    // 正向对照：进视口确实发生了加载（证明上面的 0 计数探针有效）
    expect(p.loads).toBeGreaterThan(0)
  })

  it('figure_json 是数组形态（无 data/layout 包裹）也能渲染', async () => {
    const Plotly = probe().stub
    Plotly.newPlot.mockClear()
    const trace = [{ type: 'bar', x: ['a'], y: [1] }]
    render(<ChartBlock item={{ type: 'plotly', figure_json: JSON.stringify(trace) }} />)

    await waitFor(() => expect(Plotly.newPlot).toHaveBeenCalledTimes(1))
    expect(Plotly.newPlot.mock.calls[0][1]).toEqual(trace)
  })

  it('figure_json 非法时显示失败占位且不抛错', () => {
    const item: ChartItem = { type: 'plotly', figure_json: '{ 坏掉的 json' }
    render(<ChartBlock item={item} />)
    expect(screen.getByText('图表解析失败')).toBeInTheDocument()
  })

  it('figure_json 为空字符串时显示解析失败占位且不抛错', () => {
    render(<ChartBlock item={{ type: 'plotly', figure_json: '' }} />)
    expect(screen.getByText('图表解析失败')).toBeInTheDocument()
  })

  it('newPlot 渲染失败时显示渲染失败占位且不抛错', async () => {
    const Plotly = probe().stub
    Plotly.newPlot.mockClear()
    Plotly.newPlot.mockRejectedValueOnce(new Error('boom'))
    render(<ChartBlock item={{ type: 'plotly', figure_json: '{"data":[]}' }} />)
    expect(await screen.findByText('图表渲染失败')).toBeInTheDocument()
  })

  it('未进入视口时不加载 plotly（懒加载不触发）', async () => {
    const OriginalIO = globalThis.IntersectionObserver
    // 永不回调的 IO：模拟「还没滚到」
    globalThis.IntersectionObserver = class {
      root = null
      rootMargin = ''
      thresholds: ReadonlyArray<number> = []
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return []
      }
    } as unknown as typeof IntersectionObserver
    try {
      probe().resetLoads()
      render(<ChartBlock item={{ type: 'plotly', figure_json: '{"data":[]}' }} />)
      expect(screen.getByTestId('chart-plotly')).toBeInTheDocument()
      // flush 异步 import 微任务后仍为 0 = 组件没有发起 plotly 加载
      await act(async () => {})
      expect(probe().loads).toBe(0)
    } finally {
      globalThis.IntersectionObserver = OriginalIO
    }
  })

  it('plotly 加载失败时显示渲染失败占位且不抛错', async () => {
    // getter 抛错 = 模块加载失败（import().default 取用即抛，组件必须降级不崩溃）
    probe().setFailLoad(true)
    render(<ChartBlock item={{ type: 'plotly', figure_json: '{"data":[]}' }} />)
    expect(await screen.findByText('图表渲染失败')).toBeInTheDocument()
    // 模块取用确实发生了（失败注入点生效）
    expect(probe().loads).toBeGreaterThan(0)
  })
})
