import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { executeMarkers, fetchClusterAcceptance, fetchMarkerCandidates, fetchMarkers, markerDiagnosticPath, recordClusterAcceptance } from '@/lib/api'
import type { ClusterAcceptanceState, MarkerPage, MarkerState } from '@/lib/types'
import { AnalysisImage } from '@/components/AnalysisImage'

type Source = { sessionId: string; runId: string; filterAttemptId: string; pcaAttemptId: string; clusterAttemptId: string }

/** 只读分页与绘图始终绑定确切 attempt；切换身份丢弃旧响应。 */
function MarkerTable({ source, attemptId }: { source: Source; attemptId: string }) {
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<MarkerPage | null>(null)
  const [error, setError] = useState('')
  const { sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId } = source
  useEffect(() => {
    let ignore = false
    setPage(null); setError('')
    fetchMarkerCandidates({ sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId }, attemptId, offset)
      .then((next) => { if (!ignore) setPage(next) })
      .catch((err: Error) => { if (!ignore) setError(err.message) })
    return () => { ignore = true }
  }, [sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId, attemptId, offset])
  return <div className="space-y-2">
    {error && <p role="alert">{error}</p>}
    {!page && !error && <p role="status">读取候选表…</p>}
    {page && <>
      <p>候选 {page.total.toLocaleString()} 条 · 显示 {page.rows.length ? offset + 1 : 0}–{offset + page.rows.length}</p>
      {page.rows.length > 0 && <p className="text-xs text-muted-foreground">表内可横向滚动查看统计列；表头随纵向滚动保留。</p>}
      <div className="max-h-96 overflow-auto" tabIndex={0} role="region" aria-label="未复核 marker 候选表，可横向滚动">
        <table className="w-full whitespace-nowrap text-left text-xs"><caption className="sr-only">未复核 marker 候选，双向检验结果</caption><thead><tr>{['簇', '原 feature ID', 'symbol', '分析键', 'avg_log2FC', 'pct.1', 'pct.2', 'raw p', 'Bonferroni p'].map((h) => <th scope="col" className="p-2" key={h}>{h}</th>)}</tr></thead>
          <tbody>{page.rows.map((r) => <tr className="border-t border-border" key={`${r.cluster_id}:${r.feature_key}`}>
            <td className="p-2">{r.cluster_id}</td><td className="p-2">{r.feature_id}</td><td className="p-2">{r.symbol}</td><td className="p-2">{r.feature_key}</td>
            {[r.avg_log2FC, r['pct.1'], r['pct.2'], r.p_val, r.p_val_adj].map((value, i) => <td className="p-2" key={i}>{value.toPrecision(4)}</td>)}
          </tr>)}</tbody></table>
      </div>
      <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>上一页候选</Button><Button variant="outline" disabled={offset + page.rows.length >= page.total} onClick={() => setOffset(offset + 100)}>下一页候选</Button></div>
    </>}
  </div>
}

/** 人工接受与 marker 是两次独立明确动作；恢复、刷新与失败只 GET。 */
export function MarkerReview({ sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId, revision, disabled = false, onRunning, onRecorded }: Source & {
  revision: number; disabled?: boolean; onRunning?: (active: boolean) => void; onRecorded?: () => void
}) {
  const source = { sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId }
  const [review, setReview] = useState<ClusterAcceptanceState | null>(null)
  const [state, setState] = useState<MarkerState | null>(null)
  const [accepted, setAccepted] = useState(false)
  const [reviewReason, setReviewReason] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(true)
  const [executing, setExecuting] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const callbacks = useRef({ onRunning, onRecorded })
  callbacks.current = { onRunning, onRecorded }
  const running = executing || (state?.attempts.some((a) => a.status === 'running') ?? false)

  useEffect(() => {
    const ticket = ++generation.current
    setReview(null); setState(null); setAccepted(false); setReviewReason(''); setReason(''); setError(''); setBusy(true); setExecuting(false)
    Promise.all([fetchClusterAcceptance({ sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId }), fetchMarkers({ sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId })])
      .then(([nextReview, next]) => { if (ticket === generation.current) { setReview(nextReview); setState(next) } })
      .catch((err: Error) => { if (ticket === generation.current) setError(err.message) })
      .finally(() => { if (ticket === generation.current) setBusy(false) })
    return () => { generation.current++ }
  }, [sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId, revision])

  useEffect(() => {
    callbacks.current.onRunning?.(running)
    return () => callbacks.current.onRunning?.(false)
  }, [running])

  useEffect(() => {
    if (!running || busy) return
    const ticket = generation.current
    let ignore = false
    let timer: number
    async function poll() {
      let again = true
      try {
        const next = await fetchMarkers({ sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId })
        if (ignore || ticket !== generation.current) return
        setState(next); setError('')
        again = next.attempts.some((a) => a.status === 'running')
        if (!again) callbacks.current.onRecorded?.()
      } catch (err) {
        if (!ignore && ticket === generation.current) setError(err instanceof Error ? err.message : '状态读取失败')
      } finally {
        if (again && !ignore && ticket === generation.current) timer = window.setTimeout(() => void poll(), 1500)
      }
    }
    timer = window.setTimeout(() => void poll(), 1500)
    return () => { ignore = true; window.clearTimeout(timer) }
  }, [running, busy, sessionId, runId, filterAttemptId, pcaAttemptId, clusterAttemptId])

  async function refresh() {
    const ticket = ++generation.current
    setBusy(true); setError(''); setReview(null); setState(null); setAccepted(false); setReviewReason(''); setReason('')
    try {
      const [nextReview, next] = await Promise.all([fetchClusterAcceptance(source), fetchMarkers(source)])
      if (ticket === generation.current) { setReview(nextReview); setState(next) }
    } catch (err) { if (ticket === generation.current) setError(err instanceof Error ? err.message : '读取失败') }
    finally { if (ticket === generation.current) setBusy(false) }
  }

  async function submit(kind: 'accept' | 'execute') {
    if (kind === 'accept' ? !review?.binding_sha256 || !accepted || !reviewReason.trim() : !state?.binding_sha256 || !state.review || !reason.trim()) return
    const ticket = ++generation.current
    setBusy(true); setError(''); setExecuting(kind === 'execute')
    try {
      if (kind === 'accept' && review?.binding_sha256) {
        const nextReview = await recordClusterAcceptance(source, review.binding_sha256, review.revision, reviewReason.trim())
        if (ticket !== generation.current) return
        setReview(nextReview); setAccepted(false); setReviewReason('')
        const next = await fetchMarkers(source)
        if (ticket !== generation.current) return
        setState(next)
      } else if (state?.binding_sha256 && state.review) {
        const next = await executeMarkers(source, state.binding_sha256, state.review.review_id, state.revision, reason.trim())
        if (ticket !== generation.current) return
        setState(next); setReason('')
      }
      callbacks.current.onRecorded?.()
    } catch (err) {
      if (ticket !== generation.current) return
      setError(err instanceof Error ? err.message : '请求失败；请只读刷新，不要重复执行')
      try {
        const [nextReview, next] = await Promise.all([fetchClusterAcceptance(source), fetchMarkers(source)])
        if (ticket === generation.current) { setReview(nextReview); setState(next) }
      } catch { if (ticket === generation.current) { setReview(null); setState(null) } }
    } finally { if (ticket === generation.current) { setBusy(false); setExecuting(false) } }
  }

  const canAccept = !!review?.binding_sha256 && !review.current && !review.blocked.length && !busy && !running && !disabled
  const attempted = state?.attempts.some((a) => a.binding_sha256 === state.binding_sha256) ?? false
  const canExecute = !!state?.binding_sha256 && !!state.review && !state.blocked.length && !attempted && !busy && !running && !disabled
  return <section aria-label="聚类接受与 marker 独立授权" className="analysis-stage space-y-3">
    <h6 className="font-medium">探索性聚类接受 → marker · 两次独立记录</h6>
    <p>接受只记录本地事实，不执行 R，也不代表科学认可。marker 比较每簇与其余所有细胞；不含细胞类型注释。</p>
    {error && <p role="alert">{error}</p>}
    {review?.blocked.map((text) => <p role="alert" key={text}>{text}</p>)}
    <details open={!review?.current}><summary>探索性聚类接受记录</summary>
    <label className="flex items-start gap-2"><input type="checkbox" checked={accepted} disabled={!canAccept} onChange={(event) => setAccepted(event.target.checked)} /><span>我接受当前探索性聚类继续寻找候选 marker（不是科学认可）</span></label>
    <label className="block space-y-1"><span>探索性聚类接受依据（仅本地）</span><Textarea maxLength={2000} disabled={!canAccept} value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} /></label>
    <Button disabled={!canAccept || !accepted || !reviewReason.trim()} onClick={() => void submit('accept')}>仅保存探索性聚类接受</Button>
    {!!review?.history.length && <details><summary>本地聚类接受历史（不进入对话或知识库）</summary>{review.history.map((r) => <div className="break-words py-1" key={r.review_id}><p>{r.reason}</p><p className="text-xs">{r.created_at} · {r.invalidated_reason ?? '当前新授权依据'}</p></div>)}</details>}
    </details>
    {review?.current && <p role="status">已记录探索性接受；marker 仍需独立授权。</p>}
    {state?.blocked.map((text) => <p role="alert" key={text}>{text}</p>)}
    <details open={!attempted || running}><summary>marker 协议与独立授权</summary>
    <p>固定 Seurat + Presto 1.1.0 全细胞 Wilcoxon：min.pct 0.01、|log2FC| ≥ 0.1、raw p &lt; 0.01，双向候选；Bonferroni 按全部 RNA 基因校正。seed 42、单线程、最多 300 秒；不会抽样、调参或后备。</p>
    <p className="text-xs text-muted-foreground">至少两簇，每簇及其余各至少 3 细胞。重复使用聚类数据的 p 值仅供探索，不能替代生物学重复。正向每簇 top 5 用于点图；无候选不是检验失败。</p>
    {state && <details><summary>marker 固定协议与授权绑定</summary><pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(state.protocol, null, 2)}</pre><code className="block break-all text-xs">{state.binding_sha256 ?? '不可新授权'}</code></details>}
    <label className="block space-y-1"><span>marker 授权依据（仅本地）</span><Textarea maxLength={2000} disabled={!canExecute} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
    <Button disabled={!canExecute || !reason.trim()} onClick={() => void submit('execute')}>{running ? 'marker 运行中…' : '独立授权并执行 marker'}</Button>
    </details>
    <Button variant="outline" disabled={busy} onClick={() => void refresh()}>只读刷新 marker 与接受记录</Button>
    {running && <p role="status">marker 运行中；该 run 上游修改、再次接受与删除暂不可用。</p>}
    <p className="text-xs text-muted-foreground">停在 marker_review / unreviewed。失败、中断、刷新不自动重跑；新执行必须重新明确记录聚类接受并独立授权。不调用 LLM 或 KG，不改写父对象。</p>
    {state?.attempts.map((a) => <div key={a.attempt_id} className="space-y-2">
      <p role="status">{({ running: 'marker 运行中', completed: 'marker 候选已保存，科学未复核', failed: 'marker 失败，需要人工处理', interrupted: 'marker 中断，需要人工处理' })[a.status]}</p>
      {a.error && <p role="alert">{a.error}</p>}{a.invalidated_reason && <p role="alert">{a.invalidated_reason}</p>}
      {a.result && !a.invalidated_reason && <>
        <p>{a.result.marker_rows.toLocaleString()} 条候选（正向 {a.result.positive_rows.toLocaleString()} / 负向 {a.result.negative_rows.toLocaleString()}） · {a.result.cells.toLocaleString()} 全细胞</p>
        <details><summary>查看每簇候选摘要</summary><ul className="space-y-2 text-sm">{a.result.cluster_summary.map((g) => <li key={g.cluster_id}>簇 {g.cluster_id} · {g.cells} vs 其余 {g.rest_cells} · 正 {g.positive_markers} / 负 {g.negative_markers} · {g.result_status === 'no_returned_candidates' ? '检验完成，无通过固定阈值候选' : '已返回候选'}</li>)}</ul></details>
        <details><summary>marker 执行身份与版本</summary><code className="block break-all text-xs">{a.attempt_id}</code><p className="break-all text-xs">{a.result.versions.join(' / ')}</p></details>
        <AnalysisImage src={markerDiagnosticPath(source, a.attempt_id)} alt="未复核正向 marker 候选点图：面积为表达比例，颜色为平均归一化表达的 log1p；不是细胞类型注释" />
        <MarkerTable key={`${sessionId}:${runId}:${clusterAttemptId}:${a.attempt_id}`} source={source} attemptId={a.attempt_id} />
      </>}
    </div>)}
  </section>
}
