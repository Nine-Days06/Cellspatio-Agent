import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DependencyList,
  type Dispatch,
  type SetStateAction,
} from 'react'

export interface StageReviewOptions<T> {
  load: () => Promise<T>
  /** 触发重新读取的依赖；不同渲染间长度必须恒定。 */
  deps: DependencyList
  /** 当前阶段或下游是否仍在运行；executing 仅为本次动作的乐观态。 */
  isRunning: (state: T | null, executing: boolean) => boolean
  /** 每次新状态落地后的组件副作用；hook 自己负责 setState。 */
  onLoaded?: (state: T) => void
  /** 运行结束后的父级刷新信号；每轮运行最多一次。 */
  onRecorded?: () => void
  /** 轮询结束是否触发 onRecorded；QC 由子级显式通知时为 false。 */
  recordOnPoll?: boolean
  /** 轮询专用读取；用于避免每轮重复读取较重数据。 */
  pollLoad?: (state: T | null) => Promise<T>
  onRunning?: (running: boolean) => void
  pollMs?: number
}

export interface StageReview<T> {
  state: T | null
  setState: Dispatch<SetStateAction<T | null>>
  busy: boolean
  executing: boolean
  error: string
  /** 身份变化：清空并重新读取。 */
  reload: () => Promise<void>
  /** 只读刷新：保留当前状态，成功后再替换。 */
  refresh: (options?: { clearError?: boolean }) => Promise<void>
  /** 失败后只读恢复；不递增 generation，旧身份结果不会落地。 */
  recover: (loader?: () => Promise<T>) => Promise<void>
  /**
   * 显式动作。'stale' 表示已被更新的 generation 取代；
   * 'failed' 表示本次动作失败，调用方可自行读取 load 恢复。
   */
  perform: (
    action: () => Promise<T>,
    options?: { executing?: boolean; recorded?: boolean },
  ) => Promise<'ok' | 'failed' | 'stale'>
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败'
}

/**
 * 五个评审组件共用的生命周期：generation、busy/executing、运行轮询与结束回调。
 * 轮询只调用 load；任何显式动作都递增 generation，旧响应一律丢弃。
 */
export function useStageReview<T>(options: StageReviewOptions<T>): StageReview<T> {
  const [state, setState] = useState<T | null>(null)
  const [busy, setBusy] = useState(true)
  const [executing, setExecuting] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const mounted = useRef(true)
  const recordedNotified = useRef(false)
  const pollMs = options.pollMs ?? 1500
  const recordOnPoll = options.recordOnPoll ?? true

  const callbacks = useRef({
    onLoaded: options.onLoaded,
    onRecorded: options.onRecorded,
    onRunning: options.onRunning,
  })
  callbacks.current = {
    onLoaded: options.onLoaded,
    onRecorded: options.onRecorded,
    onRunning: options.onRunning,
  }
  const loadRef = useRef(options.load)
  loadRef.current = options.load
  const pollLoadRef = useRef(options.pollLoad)
  pollLoadRef.current = options.pollLoad
  const isRunningRef = useRef(options.isRunning)
  isRunningRef.current = options.isRunning

  const running = isRunningRef.current(state, executing)

  const apply = useCallback((next: T) => {
    setState(next)
    callbacks.current.onLoaded?.(next)
  }, [])

  const load = useCallback(async (reset: boolean, clearError = true): Promise<void> => {
    const ticket = ++generation.current
    if (reset) setState(null)
    setExecuting(false)
    if (clearError) setError('')
    setBusy(true)
    try {
      const next = await loadRef.current()
      if (!mounted.current || ticket !== generation.current) return
      apply(next)
    } catch (err) {
      if (!mounted.current || ticket !== generation.current) return
      setError(messageOf(err))
    } finally {
      if (mounted.current && ticket === generation.current) setBusy(false)
    }
  }, [apply])

  const reload = useCallback(() => load(true), [load])
  const refresh = useCallback((opts?: { clearError?: boolean }) => load(false, opts?.clearError ?? false), [load])
  const recover = useCallback(async (loader?: () => Promise<T>): Promise<void> => {
    const ticket = generation.current
    try {
      const next = await (loader?.() ?? loadRef.current())
      if (!mounted.current || ticket !== generation.current) return
      apply(next)
    } catch {
      if (mounted.current && ticket === generation.current) setState(null)
    }
  }, [apply])

  const perform = useCallback<StageReview<T>['perform']>(async (action, opts = {}) => {
    const ticket = ++generation.current
    setBusy(true)
    setExecuting(!!opts.executing)
    setError('')
    try {
      const next = await action()
      if (!mounted.current || ticket !== generation.current) return 'stale'
      apply(next)
      // 仍在运行时交给轮询收尾，保证一轮运行只发一次 onRecorded。
      if (opts.recorded && !isRunningRef.current(next, false)) {
        recordedNotified.current = true
        callbacks.current.onRecorded?.()
      }
      return 'ok'
    } catch (err) {
      if (!mounted.current || ticket !== generation.current) return 'stale'
      setError(messageOf(err))
      return 'failed'
    } finally {
      if (mounted.current && ticket === generation.current) {
        setBusy(false)
        setExecuting(false)
      }
    }
  }, [apply])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      generation.current++
    }
  }, [])

  // 首次与依赖变化：清空身份相关状态后重新读取。
  useEffect(() => {
    void reload()
    // deps 由调用方声明；load/reload 通过 ref 与稳定回调读取最新实现。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, options.deps)

  // 每轮运行只在开始后允许一次结束信号。
  useEffect(() => {
    if (running) recordedNotified.current = false
    callbacks.current.onRunning?.(running)
    return () => callbacks.current.onRunning?.(false)
  }, [running])

  useEffect(() => {
    if (!running || busy) return
    let cancelled = false
    let timer: number | undefined
    // 连续失败时按指数退避并最终停止，避免后端故障期间每秒无休止打请求。
    let failures = 0
    const MAX_FAILURES = 5
    const tick = async () => {
      const ticket = generation.current
      try {
        const next = await (pollLoadRef.current?.(state) ?? loadRef.current())
        if (cancelled || !mounted.current || ticket !== generation.current) return
        apply(next)
        setError('')
        failures = 0
        if (isRunningRef.current(next, false)) {
          timer = window.setTimeout(() => void tick(), pollMs)
        } else if (recordOnPoll && !recordedNotified.current) {
          recordedNotified.current = true
          callbacks.current.onRecorded?.()
        }
      } catch (err) {
        if (cancelled || !mounted.current || ticket !== generation.current) return
        setError(messageOf(err))
        failures += 1
        if (failures >= MAX_FAILURES) return
        // 首次失败仍按常规间隔重试，连续失败后再指数退避。
        const delay = Math.min(pollMs * 2 ** (failures - 1), pollMs * 8)
        timer = window.setTimeout(() => void tick(), delay)
      }
    }
    timer = window.setTimeout(() => void tick(), pollMs)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [running, busy, pollMs, recordOnPoll, state, executing, apply])

  return { state, setState, busy, executing, error, reload, refresh, recover, perform }
}
