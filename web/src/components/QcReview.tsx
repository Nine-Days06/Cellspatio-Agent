import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { approveQc, fetchQc, filterQc, previewQc } from '@/lib/api'
import type { QcReviewState, QcThresholds } from '@/lib/types'

const fields = [
  ['n_feature_min', 'RNA features 下限', true],
  ['n_feature_max', 'RNA features 上限', true],
  ['percent_mt_max', 'percent.mt 上限 (%)', true],
  ['n_count_min', 'RNA counts 下限（可选）', false],
  ['n_count_max', 'RNA counts 上限（可选）', false],
] as const
type Values = Record<keyof QcThresholds, string>
const empty: Values = { n_feature_min: '', n_feature_max: '', percent_mt_max: '', n_count_min: '', n_count_max: '' }
const reasonNames: Record<string, string> = {
  n_feature_below_min: 'features 低于下限', n_feature_above_max: 'features 高于上限',
  n_count_below_min: 'counts 低于下限', n_count_above_max: 'counts 高于上限',
  mt_above_max: 'percent.mt 超过上限', mt_unavailable: 'percent.mt 不可定义',
}

/** 只批准已展示的精确预览；编辑阈值立即禁用旧批准。 */
export function QcReview({ sessionId, runId, onRecorded }: { sessionId: string; runId: string; onRecorded?: () => void }) {
  const [state, setState] = useState<QcReviewState | null>(null)
  const [values, setValues] = useState<Values>(empty)
  const [previewValues, setPreviewValues] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(true)
  const [filtering, setFiltering] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)

  function accept(next: QcReviewState) {
    setState(next)
    const params = next.active_preview?.params
    if (params) {
      const form = Object.fromEntries(fields.map(([name]) => [name, params[name] === null ? '' : String(params[name])])) as Values
      setValues(form)
      setPreviewValues(JSON.stringify(form))
    } else {
      setValues(empty)
      setPreviewValues('')
    }
  }

  useEffect(() => {
    const ticket = ++generation.current
    setState(null)
    setValues(empty)
    setPreviewValues('')
    setReason('')
    setError('')
    setBusy(true)
    setFiltering(false)
    fetchQc(sessionId, runId).then((next) => {
      if (ticket === generation.current) accept(next)
    }).catch((err: Error) => {
      if (ticket === generation.current) setError(err.message)
    }).finally(() => {
      if (ticket === generation.current) setBusy(false)
    })
    return () => { generation.current++ }
  }, [sessionId, runId])

  const running = state?.attempts?.some((attempt) => attempt.status === 'running') ?? false
  useEffect(() => {
    if (!running || busy) return
    const ticket = generation.current
    const timer = window.setTimeout(() => {
      fetchQc(sessionId, runId).then((next) => {
        if (ticket === generation.current) accept(next)
      }).catch((err: Error) => {
        if (ticket === generation.current) setError(err.message)
      })
    }, 1500)
    return () => window.clearTimeout(timer)
  }, [running, busy, state, sessionId, runId])

  async function perform(action: () => Promise<QcReviewState>, recorded = false) {
    const ticket = ++generation.current
    setBusy(true)
    setError('')
    try {
      const next = await action()
      if (ticket !== generation.current) return
      accept(next)
      if (recorded) onRecorded?.()
    } catch (err) {
      if (ticket !== generation.current) return
      setError(err instanceof Error ? err.message : 'QC 请求失败')
      setPreviewValues('')
    } finally {
      if (ticket === generation.current) setBusy(false)
    }
  }

  async function executeFilter(previewId: string) {
    setFiltering(true)
    const ticket = generation.current + 1
    await perform(() => filterQc(sessionId, runId, previewId), true)
    if (ticket === generation.current) setFiltering(false)
  }

  function preview(event: FormEvent) {
    event.preventDefault()
    const params: QcThresholds = {
      n_feature_min: Number(values.n_feature_min), n_feature_max: Number(values.n_feature_max), percent_mt_max: Number(values.percent_mt_max),
      n_count_min: values.n_count_min === '' ? null : Number(values.n_count_min), n_count_max: values.n_count_max === '' ? null : Number(values.n_count_max),
    }
    if (fields.some(([name, , required]) => (required && values[name] === '') || (params[name] !== null && (!Number.isFinite(params[name]) || params[name]! < 0 || (name !== 'percent_mt_max' && !Number.isSafeInteger(params[name]))))) || params.percent_mt_max > 100 || params.n_feature_min > params.n_feature_max || (params.n_count_min !== null && params.n_count_max !== null && params.n_count_min > params.n_count_max)) {
      setError('请输入合法阈值：非负整数上下限、0–100% 线粒体比例，下限不能超过上限。')
      return
    }
    void perform(() => previewQc(sessionId, runId, params))
  }

  const active = state?.active_preview
  const unchanged = previewValues === JSON.stringify(values)
  const blocked = [...(state?.blocked ?? []), ...(active?.summary.blocked ?? [])]
  const canApprove = active?.status === 'pending' && unchanged && !busy && blocked.length === 0 && reason.trim().length > 0
  const attempted = state?.attempts?.some((attempt) => attempt.preview_id === active?.preview_id) ?? false
  const canFilter = active?.status === 'approved' && unchanged && !busy && !running && !attempted && blocked.length === 0
  return (
    <section aria-label="QC 阈值选择" className="space-y-3 rounded-lg border border-border p-3">
      <h4 className="font-medium">阈值选择 · 记录与执行分开授权</h4>
      <p className="text-xs text-muted-foreground">所有条件 AND、边界包含等号。没有科学默认值；请自行审阅证据并填写依据。</p>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {filtering && <p role="status">过滤运行中；只保存细胞列选择，不执行下游分析。</p>}
      {state?.blocked.map((text) => <p role="alert" key={text}>{text}</p>)}
      <form onSubmit={preview} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          {fields.map(([name, label, required]) => <label key={name} className="space-y-1 text-xs">
            <span>{label}</span>
            <Input type="number" min="0" max={name === 'percent_mt_max' ? 100 : undefined} step={name === 'percent_mt_max' ? 'any' : 1} required={required} disabled={busy || running || !state || state.blocked.length > 0} value={values[name]} onChange={(event) => setValues({ ...values, [name]: event.target.value })} />
          </label>)}
        </div>
        <Button type="submit" disabled={busy || running || !state || state.blocked.length > 0}>{busy ? '正在读取或执行…' : '生成排除预览'}</Button>
      </form>
      {active && <section className="space-y-2" aria-label="排除预览">
        <p className="break-all text-xs">本预览参数：{JSON.stringify(active.params)}</p>
        <p>预计保留 {active.summary.kept.toLocaleString()} / {active.summary.total.toLocaleString()} · 排除 {active.summary.excluded.toLocaleString()} ({active.summary.excluded_percent.toFixed(2)}%)</p>
        <p className="text-xs text-muted-foreground">排除原因可重叠，不可直接相加。未实际过滤。</p>
        <ul className="text-xs">{Object.entries(active.summary.reason_counts).map(([name, count]) => <li key={name}>{reasonNames[name] ?? name}：{count.toLocaleString()}</li>)}</ul>
        {active.summary.blocked.map((text) => <p key={text} role="alert">{text}</p>)}
        <code className="block break-all text-xs text-muted-foreground">预览 {active.preview_id} · 绑定 {active.binding_sha256}</code>
        {!unchanged && <p role="status">阈值已编辑，请重新生成预览后批准。</p>}
        {active.status === 'pending' && <>
          <label className="block space-y-1"><span>选择依据（仅本地记录）</span><Textarea value={reason} maxLength={2000} disabled={busy} onChange={(event) => setReason(event.target.value)} /></label>
          <Button type="button" disabled={!canApprove} onClick={() => void perform(() => approveQc(sessionId, runId, active.preview_id, reason.trim()), true)}>记录此过滤选择</Button>
        </>}
        {active.status === 'approved' && <>
          <p className="text-xs">独立执行只筛选细胞列并保存内部稀疏 RDS；不归一化、聚类或注释。失败不会自动重跑。</p>
          <Button type="button" disabled={!canFilter} onClick={() => void executeFilter(active.preview_id)}>执行此已记录选择的过滤</Button>
        </>}
      </section>}
      {(state?.attempts?.length ?? 0) > 0 && <section aria-label="过滤执行记录" className="space-y-2">
        <h4 className="font-medium">过滤执行 · 科学未复核</h4>
        {state!.attempts!.map((attempt) => <div key={attempt.attempt_id} className="space-y-1 text-xs">
          <p role="status">{({ running: '过滤运行中', completed: '过滤产物已保存，等待审阅', failed: '过滤失败，需要人工处理', interrupted: '过滤中断，需要新预览和批准' })[attempt.status]}</p>
          <code className="block break-all">{attempt.attempt_id} · 预览 {attempt.preview_id}</code>
          {attempt.result?.cells !== undefined && <p>实际保留 {attempt.result.cells.toLocaleString()} 个 barcode · {attempt.result.features?.toLocaleString()} 个 RNA features</p>}
          {attempt.result?.error && <p role="alert">{attempt.result.error}</p>}
          {attempt.result?.versions && <p>{attempt.result.versions.join(' / ')}</p>}
          <p className="break-all">本地目录：{attempt.directory}</p>
          {attempt.result?.artifacts && <ul>{Object.entries(attempt.result.artifacts).map(([name, artifact]) => <li key={name} className="break-all">{name} · {artifact.bytes.toLocaleString()} bytes · SHA256 {artifact.sha256}</li>)}</ul>}
        </div>)}
      </section>}
      {(state?.approvals.length ?? 0) > 0 && <section aria-label="已批准选择" className="space-y-2">
        <h4 className="font-medium">已记录选择（不代表科学认可或下游授权）</h4>
        {state!.approvals.map((item) => <div key={item.preview_id} className="space-y-1 text-xs">
          <p>{item.approved_at} · 保留 {item.summary.kept.toLocaleString()} · {item.reason}</p>
          <code className="block break-all">{item.preview_id} · {JSON.stringify(item.params)}</code>
        </div>)}
      </section>}
    </section>
  )
}
