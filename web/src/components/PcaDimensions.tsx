import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { fetchPcaDimensions, recordPcaDimensions } from '@/lib/api'
import type { PcaDimensionsState } from '@/lib/types'

/** 人工选择是独立事实；本控件没有分析执行入口。 */
export function PcaDimensions({ sessionId, runId, filterAttemptId, attemptId, revision, disabled = false, onRecorded }: {
  sessionId: string; runId: string; filterAttemptId: string; attemptId: string; revision: number; disabled?: boolean; onRecorded?: () => void
}) {
  const [state, setState] = useState<PcaDimensionsState | null>(null)
  const [nDims, setNDims] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const generation = useRef(0)

  useEffect(() => {
    const ticket = ++generation.current
    setState(null); setNDims(''); setReason(''); setError(''); setBusy(true)
    fetchPcaDimensions(sessionId, runId, filterAttemptId, attemptId).then((next) => {
      if (ticket === generation.current) setState(next)
    }).catch((err: Error) => {
      if (ticket === generation.current) setError(err.message)
    }).finally(() => { if (ticket === generation.current) setBusy(false) })
    return () => { generation.current++ }
  }, [sessionId, runId, filterAttemptId, attemptId, revision])

  async function refresh() {
    const ticket = ++generation.current
    setBusy(true); setError(''); setState(null)
    try {
      const next = await fetchPcaDimensions(sessionId, runId, filterAttemptId, attemptId)
      if (ticket === generation.current) setState(next)
    } catch (err) {
      if (ticket === generation.current) setError(err instanceof Error ? err.message : '读取失败')
    } finally { if (ticket === generation.current) setBusy(false) }
  }

  async function record() {
    if (!state?.binding_sha256) return
    const ticket = ++generation.current
    setBusy(true); setError('')
    try {
      const next = await recordPcaDimensions(sessionId, runId, filterAttemptId, attemptId, state.binding_sha256, state.revision, Number(nDims), reason.trim())
      if (ticket !== generation.current) return
      setState(next); setNDims(''); setReason(''); onRecorded?.()
    } catch (err) {
      if (ticket !== generation.current) return
      setError(err instanceof Error ? err.message : '记录失败，请刷新')
      // 失联或 CAS 冲突只能只读恢复，不能自动重发选择。
      try {
        const next = await fetchPcaDimensions(sessionId, runId, filterAttemptId, attemptId)
        if (ticket === generation.current) setState(next)
      } catch { if (ticket === generation.current) setState(null) }
    } finally { if (ticket === generation.current) setBusy(false) }
  }

  const k = Number(nDims)
  const valid = /^\d+$/.test(nDims) && Number.isInteger(k) && k >= 1 && k <= (state?.evidence?.actual_pcs ?? 0)
  const available = !!state?.binding_sha256 && !busy && !disabled && state.blocked.length === 0
  return <section aria-label="PCA 维度选择记录" className="analysis-stage space-y-3">
    <h5 className="font-medium">记录下游维度选择 · 不执行</h5>
    <p>仅支持手工前缀 1..k，无默认或自动推荐。实际 PCs 是证据范围，不是科学适用性保证。</p>
    <p className="text-xs text-muted-foreground">标准差不是总方差百分比；肘部和 PC1/2 图不足以单独证明选择合理。科学未复核，未授权下游执行。</p>
    {error && <p role="alert">{error}</p>}
    {state?.blocked.map((text) => <p role="alert" key={text}>{text}</p>)}
    {state?.evidence && <details><summary>已计算 {state.evidence.actual_pcs} PCs · 标准差证据</summary>
      <ul>{state.evidence.stdev.map((row) => <li key={row.pc}>{row.pc} · {row.stdev.toPrecision(6)}</li>)}</ul>
    </details>}
    {state?.current ? <div role="status" className="space-y-1 break-words">
      <p>当前维度：[{state.current.dims.join(', ')}] · 已记录，科学未复核</p>
      <p className="whitespace-pre-wrap">依据：{state.current.reason}</p>
    </div> : <p role="status">暂无当前有效维度记录</p>}
    <details open={!state?.current}><summary>填写或修改维度选择</summary>
    <label className="block space-y-1"><span>维度数 k（手工填写）</span><Input type="number" min={1} max={state?.evidence?.actual_pcs} step={1} value={nDims} disabled={!available} onChange={(event) => setNDims(event.target.value)} /></label>
    {valid && <p className="break-words">将记录：[{Array.from({ length: k }, (_, i) => i + 1).join(', ')}]</p>}
    <label className="block space-y-1"><span>维度选择依据（仅本地）</span><Textarea maxLength={2000} value={reason} disabled={!available} onChange={(event) => setReason(event.target.value)} /></label>
    <div className="flex flex-wrap gap-2">
      <Button type="button" disabled={!available || !valid || !reason.trim()} onClick={() => void record()}>记录选择（不执行）</Button>
    </div>
    </details>
    <Button type="button" variant="outline" disabled={busy} onClick={() => void refresh()}>只读刷新维度记录</Button>
    {!!state?.history.length && <details><summary>人工记录历史（{state.history.length}）</summary>
      {state.history.map((review) => <div key={review.review_id} className="my-2 space-y-1 break-words">
        <p>[{review.dims.join(', ')}] · {review.created_at}</p>
        <p className="whitespace-pre-wrap">{review.reason}</p>
        <p>{review.invalidated_reason ?? '当前有效记录；科学未复核'}</p>
      </div>)}
    </details>}
  </section>
}
