import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useStageReview } from '@/lib/useStageReview'

interface View {
  value: number
  running: boolean
}

const view = (value: number, running = false): View => ({ value, running })
const isRunning = (state: View | null) => state?.running ?? false

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('useStageReview', () => {
  it('首次渲染读取并同步 onLoaded', async () => {
    const load = vi.fn().mockResolvedValue(view(1))
    const loaded = vi.fn()
    const { result } = renderHook(() =>
      useStageReview<View>({ load, deps: ['a'], isRunning, onLoaded: loaded }))

    expect(result.current.busy).toBe(true)
    await waitFor(() => expect(result.current.state).toEqual(view(1)))
    expect(load).toHaveBeenCalledTimes(1)
    expect(loaded).toHaveBeenCalledWith(view(1))
    expect(result.current.busy).toBe(false)
    expect(result.current.error).toBe('')
  })

  it('依赖切换后旧响应不覆盖新身份', async () => {
    const first = deferred<View>()
    const second = deferred<View>()
    const load = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)
    const { result, rerender } = renderHook(
      ({ key }: { key: string }) =>
        useStageReview<View>({ load, deps: [key], isRunning }),
      { initialProps: { key: 'a' } },
    )

    rerender({ key: 'b' })
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    await act(async () => { second.resolve(view(2)) })
    await waitFor(() => expect(result.current.state).toEqual(view(2)))

    await act(async () => { first.resolve(view(1)) })
    expect(result.current.state).toEqual(view(2))
    expect(result.current.busy).toBe(false)
  })

  it('运行中每 1.5 秒轮询，结束后 onRecorded 只触发一次', async () => {
    vi.useFakeTimers()
    const load = vi.fn()
      .mockResolvedValueOnce(view(1, true))
      .mockResolvedValue(view(2, false))
    const recorded = vi.fn()
    const { result } = renderHook(() =>
      useStageReview<View>({ load, deps: [], isRunning, onRecorded: recorded }))

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.state).toEqual(view(1, true))

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(result.current.state).toEqual(view(2, false))
    expect(recorded).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(recorded).toHaveBeenCalledTimes(1)
  })

  it('perform 竞态只落地最新结果，旧动作不写 error', async () => {
    const load = vi.fn().mockResolvedValue(view(0))
    const first = deferred<View>()
    const second = deferred<View>()
    const { result } = renderHook(() =>
      useStageReview<View>({ load, deps: [], isRunning }))
    await waitFor(() => expect(result.current.state).toEqual(view(0)))

    let firstAction!: Promise<boolean>
    let secondAction!: Promise<boolean>
    act(() => {
      firstAction = result.current.perform(() => first.promise, { executing: true })
      secondAction = result.current.perform(() => second.promise)
    })
    await act(async () => { second.resolve(view(2)) })
    await expect(secondAction).resolves.toBe(true)
    await act(async () => { first.resolve(view(1)) })
    await expect(firstAction).resolves.toBe(false)

    expect(result.current.state).toEqual(view(2))
    expect(result.current.executing).toBe(false)
    expect(result.current.error).toBe('')
  })

  it('refresh 抢在 perform 前时不残留 executing', async () => {
    const load = vi.fn().mockResolvedValue(view(0))
    const action = deferred<View>()
    const { result } = renderHook(() =>
      useStageReview<View>({ load, deps: [], isRunning }))
    await waitFor(() => expect(result.current.state).toEqual(view(0)))

    let pending!: Promise<boolean>
    act(() => { pending = result.current.perform(() => action.promise, { executing: true }) })
    expect(result.current.executing).toBe(true)

    load.mockResolvedValue(view(3))
    await act(async () => { await result.current.refresh() })
    await act(async () => { action.resolve(view(1)) })
    await expect(pending).resolves.toBe(false)

    expect(result.current.state).toEqual(view(3))
    expect(result.current.executing).toBe(false)
    expect(result.current.busy).toBe(false)
  })

  it('perform(recorded) 返回运行态时只在轮询结束通知一次', async () => {
    vi.useFakeTimers()
    const load = vi.fn()
      .mockResolvedValueOnce(view(0))
      .mockResolvedValue(view(2))
    const recorded = vi.fn()
    const { result } = renderHook(() =>
      useStageReview<View>({ load, deps: [], isRunning, onRecorded: recorded }))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    await act(async () => {
      await result.current.perform(async () => view(1, true), { recorded: true })
    })
    expect(recorded).not.toHaveBeenCalled()

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(recorded).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(recorded).toHaveBeenCalledTimes(1)
  })

  it('perform 失败写 error，由调用方只读恢复', async () => {
    const load = vi.fn().mockResolvedValue(view(0))
    const { result } = renderHook(() =>
      useStageReview<View>({ load, deps: [], isRunning }))
    await waitFor(() => expect(result.current.state).toEqual(view(0)))

    let action!: Promise<boolean>
    await act(async () => {
      action = result.current.perform(() => Promise.reject(new Error('执行失联')))
    })
    await expect(action).resolves.toBe(false)
    expect(result.current.error).toBe('执行失联')

    load.mockResolvedValue(view(3))
    await act(async () => { await result.current.refresh() })
    expect(result.current.state).toEqual(view(3))
    expect(result.current.error).toBe('')
  })

  it('卸载清理定时器并发布 onRunning(false)', async () => {
    vi.useFakeTimers()
    const load = vi.fn().mockResolvedValue(view(1, true))
    const running = vi.fn()
    const { result, unmount } = renderHook(() =>
      useStageReview<View>({ load, deps: [], isRunning, onRunning: running }))

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(running).toHaveBeenLastCalledWith(true)
    expect(result.current.state).toEqual(view(1, true))

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    const calls = load.mock.calls.length
    unmount()
    expect(running).toHaveBeenLastCalledWith(false)

    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(load).toHaveBeenCalledTimes(calls)
  })

  it('onRunning 随 running 变化发布并分享最新 running', async () => {
    const load = vi.fn().mockResolvedValue(view(0, true))
    const running = vi.fn()
    const { result } = renderHook(() =>
      useStageReview<View>({ load, deps: [], isRunning, onRunning: running }))

    await waitFor(() => expect(result.current.state).toEqual(view(0, true)))
    expect(running).toHaveBeenLastCalledWith(true)

    act(() => { result.current.setState(view(1, false)) })
    expect(running).toHaveBeenLastCalledWith(false)
  })
})
