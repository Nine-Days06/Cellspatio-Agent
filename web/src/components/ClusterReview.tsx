import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { clusterDiagnosticPath, executeClusters, fetchClusters } from '@/lib/api'
import type { ClusterState } from '@/lib/types'

/** 显式独立授权；切换身份丢弃旧异步结果，恢复与轮询只 GET。 */
export function ClusterReview({ sessionId, runId, filterAttemptId, pcaAttemptId, revision, onRunning, onRecorded }: {
  sessionId: string; runId: string; filterAttemptId: string; pcaAttemptId: string; revision: number
  onRunning?: (running: boolean) => void; onRecorded?: () => void
}) {
  const [state, setState] = useState<ClusterState | null>(null)
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
    setState(null); setReason(''); setError(''); setBusy(true); setExecuting(false)
    fetchClusters(sessionId, runId, filterAttemptId, pcaAttemptId).then((next) => {
      if (ticket === generation.current) setState(next)
    }).catch((err: Error) => {
      if (ticket === generation.current) setError(err.message)
    }).finally(() => { if (ticket === generation.current) setBusy(false) })
    return () => { generation.current++ }
  }, [sessionId, runId, filterAttemptId, pcaAttemptId, revision])

  useEffect(() => {
    callbacks.current.onRunning?.(running)
    return () => callbacks.current.onRunning?.(false)
  }, [running])

  useEffect(() => {
    if (!running || busy) return
    const ticket = generation.current
    const timer = window.setTimeout(() => {
      fetchClusters(sessionId, runId, filterAttemptId, pcaAttemptId).then((next) => {
        if (ticket !== generation.current) return
        setState(next)
        if (!next.attempts.some((a) => a.status === 'running')) callbacks.current.onRecorded?.()
      }).catch((err: Error) => { if (ticket === generation.current) setError(err.message) })
    }, 1500)
    return () => window.clearTimeout(timer)
  }, [running, busy, state, sessionId, runId, filterAttemptId, pcaAttemptId])

  async function refresh() {
    const ticket = ++generation.current
    setBusy(true); setError(''); setState(null); setReason('')
    try {
      const next = await fetchClusters(sessionId, runId, filterAttemptId, pcaAttemptId)
      if (ticket === generation.current) setState(next)
    } catch (err) {
      if (ticket === generation.current) setError(err instanceof Error ? err.message : '读取失败')
    } finally { if (ticket === generation.current) setBusy(false) }
  }

  async function execute() {
    if (!state?.binding_sha256 || !state.selection) return
    const ticket = ++generation.current
    setBusy(true); setExecuting(true); setError('')
    try {
      const next = await executeClusters(sessionId, runId, filterAttemptId, pcaAttemptId, state.binding_sha256, state.selection.review_id, state.revision, reason.trim())
      if (ticket !== generation.current) return
      setState(next); setReason(''); callbacks.current.onRecorded?.()
    } catch (err) {
      if (ticket !== generation.current) return
      setError(err instanceof Error ? err.message : '执行请求失败；请只读刷新，不要重复执行')
      try {
        const next = await fetchClusters(sessionId, runId, filterAttemptId, pcaAttemptId)
        if (ticket === generation.current) setState(next)
      } catch { if (ticket === generation.current) setState(null) }
    } finally { if (ticket === generation.current) { setBusy(false); setExecuting(false) } }
  }

  const attempted = state?.attempts.some((a) => a.binding_sha256 === state.binding_sha256) ?? false
  const available = !!state?.binding_sha256 && !busy && !running && !attempted && state.blocked.length === 0
  return <section aria-label="聚类与 UMAP 独立授权" className="space-y-3 rounded-lg border border-border p-3 text-sm">
    <h5 className="font-medium">邻居图 → 聚类 → UMAP · 独立授权</h5>
    <p>固定 Annoy（20-NN，euclidean）→ Louvain（resolution 0.5）→ uwot UMAP（30-NN，cosine，2D，seed 42）。单线程，最多 300 秒；不调参、不自动补维或放宽 QC。</p>
    <p className="text-xs text-muted-foreground">至少 31 细胞、人工记录至少 2 个 PCs。UMAP epochs：≤10,000 细胞 500，否则 200。图与簇大小仅供探索，不代表细胞类型或科学认可。</p>
    {error && <p role="alert">{error}</p>}
    {state?.blocked.map((text) => <p role="alert" key={text}>{text}</p>)}
    {state?.selection && <p className="break-words">此次来源维度：[{state.selection.dims.join(', ')}] · {state.source_summary?.cells.toLocaleString()} 细胞 · {state.source_summary?.n_epochs} epochs</p>}
    {state && <details><summary>完整固定协议与来源绑定</summary><pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(state.protocol, null, 2)}</pre><code className="block break-all text-xs">工具 {state.tool_version} · 记录 {state.selection?.review_id ?? '无当前记录'} · 绑定 {state.binding_sha256 ?? '不可新授权'}</code></details>}
    <label className="block space-y-1"><span>聚类与 UMAP 授权依据（仅本地）</span><Textarea maxLength={2000} value={reason} disabled={!available} onChange={(event) => setReason(event.target.value)} /></label>
    <div className="flex flex-wrap gap-2">
      <Button type="button" disabled={!available || !reason.trim()} onClick={() => void execute()}>{running ? '聚类与 UMAP 运行中…' : '独立授权并执行聚类与 UMAP'}</Button>
      <Button type="button" variant="outline" disabled={busy} onClick={() => void refresh()}>只读刷新聚类记录</Button>
    </div>
    {running && <p role="status">聚类与 UMAP 运行中；该 run 上游选择、维度记录与删除暂不可用。</p>}
    <p className="text-xs text-muted-foreground">完成停在 cluster_review / unreviewed。刷新、失败、中断均不自动重跑；若确需新执行，须回上游重新明确记录维度，再独立授权。原始 counts、父 QC/过滤/PCA 不改写，不调用 LLM 或 KG，不执行 markers 或注释。</p>
    {state?.attempts.map((a) => <div key={a.attempt_id} className="space-y-2">
      <p role="status">{({ running: '聚类运行中', completed: '聚类与 UMAP 已保存，科学未复核', failed: '聚类失败，需要人工处理', interrupted: '聚类中断，需要人工处理' })[a.status]} · 来源维度 [{a.dims.join(', ')}]</p>
      <code className="block break-all text-xs">{a.attempt_id}</code>
      {a.error && <p role="alert">{a.error}</p>}
      {a.invalidated_reason && <p role="alert">{a.invalidated_reason}</p>}
      {a.result && !a.invalidated_reason && <>
        <p>{a.result.cells.toLocaleString()} 细胞 · {a.result.clusters} 个探索簇 · 科学未复核</p>
        <ul className="text-xs">{a.result.cluster_sizes.map((c) => <li key={c.cluster_id}>簇 {c.cluster_id}：{c.cells.toLocaleString()} ({(c.proportion * 100).toFixed(2)}%)</li>)}</ul>
        <p className="break-all text-xs">{a.result.versions.join(' / ')}</p>
        <img className="h-auto max-w-full" src={clusterDiagnosticPath(sessionId, runId, filterAttemptId, pcaAttemptId, a.attempt_id)} alt="探索性 UMAP 按未复核簇着色；不是细胞类型注释或科学认可" />
      </>}
    </div>)}
  </section>
}
