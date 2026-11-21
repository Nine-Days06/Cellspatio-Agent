import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { fetchPcaDimensions, recordPcaDimensions } from '@/lib/api'
import { evaluateStageGate } from '@/lib/stageGate'
import type { PcaDimensionsState } from '@/lib/types'
import { useStageReview } from '@/lib/useStageReview'

/** 人工选择是独立事实；本控件没有分析执行入口。 */
export function PcaDimensions({ sessionId, runId, filterAttemptId, attemptId, revision, disabled = false, onRecorded }: {
  sessionId: string; runId: string; filterAttemptId: string; attemptId: string; revision: number; disabled?: boolean; onRecorded?: () => void
}) {
  const [nDims, setNDims] = useState('')
  const [reason, setReason] = useState('')
  const review = useStageReview<PcaDimensionsState>({
    load: () => fetchPcaDimensions(sessionId, runId, filterAttemptId, attemptId),
    deps: [sessionId, runId, filterAttemptId, attemptId, revision],
    isRunning: () => false,
    onRecorded,
  })
  const { state, busy, error } = review

  useEffect(() => { setNDims(''); setReason('') }, [sessionId, runId, filterAttemptId, attemptId, revision])

  async function record() {
    const binding = state?.binding_sha256
    if (!binding) return
    let failed = false
    const ok = await review.perform(
      () => recordPcaDimensions(sessionId, runId, filterAttemptId, attemptId, binding, state.revision, Number(nDims), reason.trim())
        .catch((err) => { failed = true; throw err instanceof Error ? err : new Error('记录失败，请刷新') }),
      { recorded: true },
    )
    if (ok) { setNDims(''); setReason('') }
    // 失联或 CAS 冲突只能只读恢复，不能自动重发选择。
    else if (failed) await review.refresh()
  }

  const k = Number(nDims)
  const valid = /^\d+$/.test(nDims) && Number.isInteger(k) && k >= 1 && k <= (state?.evidence?.actual_pcs ?? 0)
  const gate = evaluateStageGate({
    stage: 'pca-dimensions',
    bindingExists: !!state?.binding_sha256,
    dimensionsValid: valid,
    disabled,
    blocked: state?.blocked ?? [],
    busy,
    reasonProvided: reason.trim().length > 0,
  })
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
    <label className="block space-y-1"><span>维度数 k（手工填写）</span><Input type="number" min={1} max={state?.evidence?.actual_pcs} step={1} value={nDims} disabled={!gate.available} onChange={(event) => setNDims(event.target.value)} /></label>
    {valid && <p className="break-words">将记录：[{Array.from({ length: k }, (_, i) => i + 1).join(', ')}]</p>}
    <label className="block space-y-1"><span>维度选择依据（仅本地）</span><Textarea maxLength={2000} value={reason} disabled={!gate.available} onChange={(event) => setReason(event.target.value)} /></label>
    <div className="flex flex-wrap gap-2">
      <span title={gate.blockerText || undefined}><Button type="button" disabled={!gate.allowed} onClick={() => void record()}>记录选择（不执行）</Button></span>
    </div>
    </details>
    <Button type="button" variant="outline" disabled={busy} onClick={() => void review.refresh()}>只读刷新维度记录</Button>
    {!!state?.history.length && <details><summary>人工记录历史（{state.history.length}）</summary>
      {state.history.map((entry) => <div key={entry.review_id} className="my-2 space-y-1 break-words">
        <p>[{entry.dims.join(', ')}] · {entry.created_at}</p>
        <p className="whitespace-pre-wrap">{entry.reason}</p>
        <p>{entry.invalidated_reason ?? '当前有效记录；科学未复核'}</p>
      </div>)}
    </details>}
  </section>
}
