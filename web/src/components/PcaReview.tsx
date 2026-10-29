import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { executePca, fetchPca, pcaDiagnosticPath } from '@/lib/api'
import type { PcaState } from '@/lib/types'
import { PcaDimensions } from '@/components/PcaDimensions'
import { ClusterReview } from '@/components/ClusterReview'

/** PCA 是独立授权；恢复和轮询只有 GET，不会重跑失败或中断。 */
export function PcaReview({ sessionId, runId, filterAttemptId, revision, onRunning, onRecorded }: {
  sessionId: string; runId: string; filterAttemptId: string
  revision: number
  onRunning?: (running: boolean) => void; onRecorded?: () => void
}) {
  const [state, setState] = useState<PcaState | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(true)
  const [executing, setExecuting] = useState(false)
  const [clusterRunning, setClusterRunning] = useState<string[]>([])
  const [error, setError] = useState('')
  const generation = useRef(0)
  const callbacks = useRef({ onRunning, onRecorded })
  callbacks.current = { onRunning, onRecorded }
  const pcaRunning = executing || (state?.attempts.some((a) => a.status === 'running') ?? false)
  const running = pcaRunning || clusterRunning.length > 0

  useEffect(() => {
    const ticket = ++generation.current
    setState(null); setReason(''); setError(''); setBusy(true); setExecuting(false)
    setClusterRunning([])
    fetchPca(sessionId, runId, filterAttemptId).then((next) => {
      if (ticket === generation.current) setState(next)
    }).catch((err: Error) => {
      if (ticket === generation.current) setError(err.message)
    }).finally(() => {
      if (ticket === generation.current) setBusy(false)
    })
    return () => { generation.current++ }
  }, [sessionId, runId, filterAttemptId, revision])

  useEffect(() => {
    callbacks.current.onRunning?.(running)
    return () => callbacks.current.onRunning?.(false)
  }, [running])

  useEffect(() => {
    if (!pcaRunning || busy) return
    const ticket = generation.current
    const timer = window.setTimeout(() => {
      fetchPca(sessionId, runId, filterAttemptId).then((next) => {
        if (ticket !== generation.current) return
        setState(next)
        if (!next.attempts.some((a) => a.status === 'running')) callbacks.current.onRecorded?.()
      }).catch((err: Error) => {
        if (ticket === generation.current) setError(err.message)
      })
    }, 1500)
    return () => window.clearTimeout(timer)
  }, [pcaRunning, busy, state, sessionId, runId, filterAttemptId])

  async function execute() {
    if (!state?.binding_sha256) return
    const ticket = ++generation.current
    setBusy(true); setExecuting(true); setError('')
    try {
      const next = await executePca(sessionId, runId, filterAttemptId, state.binding_sha256, reason.trim())
      if (ticket !== generation.current) return
      setState(next)
      callbacks.current.onRecorded?.()
    } catch (err) {
      if (ticket !== generation.current) return
      setError(err instanceof Error ? err.message : 'PCA 请求失败；请只读刷新，不要重复执行')
      // 请求失联后先恢复服务端状态，不能把未知执行当作未执行。
      try { const next = await fetchPca(sessionId, runId, filterAttemptId); if (ticket === generation.current) setState(next) }
      catch { if (ticket === generation.current) setState(null) }
    } finally {
      if (ticket === generation.current) { setBusy(false); setExecuting(false) }
    }
  }

  const attempted = state?.attempts.some((a) => a.binding_sha256 === state.binding_sha256) ?? false
  const canExecute = !!state?.binding_sha256 && !busy && !running && !attempted && state.blocked.length === 0 && reason.trim().length > 0
  return <section aria-label="归一化与 PCA 独立授权" className="space-y-3 rounded-lg border border-border p-3 text-sm">
    <h4 className="font-medium">归一化与 PCA · 独立授权</h4>
    <p>固定 LogNormalize（10,000）→ vst（最多 2,000 features）→ 仅变异特征 Scale（不回归）→ PCA（上限 30，seed 42）。</p>
    <p className="text-xs text-muted-foreground">实际 PC 数 = min(30, 细胞数 − 1, 可用特征数 − 1)。至少 3 细胞和 3 可用特征；不足失败，不放宽 QC。完成停在 pca_review / unreviewed；下游维度与聚类须分别明确授权，本工具不执行 marker 或注释。</p>
    {error && <p role="alert">{error}</p>}
    {state?.blocked.map((text) => <p role="alert" key={text}>{text}</p>)}
    {state?.source_summary && <p>父过滤：{state.source_summary.cells.toLocaleString()} 细胞 · {state.source_summary.features.toLocaleString()} features</p>}
    {state && <details><summary>完整固定协议与绑定</summary><pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(state.protocol, null, 2)}</pre><code className="block break-all text-xs">工具 {state.tool_version} · 父过滤 {filterAttemptId} · 绑定 {state.binding_sha256 ?? '不可授权'}</code></details>}
    <label className="block space-y-1"><span>PCA 授权依据（仅本地记录）</span><Textarea maxLength={2000} value={reason} disabled={busy || running || attempted || !state} onChange={(event) => setReason(event.target.value)} /></label>
    <Button type="button" disabled={!canExecute} onClick={() => void execute()}>{clusterRunning.length > 0 && !pcaRunning ? '聚类或 marker 运行中…' : running ? 'PCA 运行中…' : '独立授权并执行归一化与 PCA'}</Button>
    <p className="text-xs text-muted-foreground">授权不是科学认可；刷新不执行，失败和中断不自动重跑。原始 counts 与父 QC 不改写；诊断图仅本地读取。</p>
    {running && <p role="status">{clusterRunning.length > 0 && !pcaRunning ? '聚类或 marker 运行中；该 run 的阈值、过滤与维度记录暂不可用。' : 'PCA 运行中；该 run 的阈值与过滤修改暂不可用。'}</p>}
    {state?.attempts.map((a) => <div key={a.attempt_id} className="space-y-2">
      <p role="status">{({ running: 'PCA 运行中', completed: 'PCA 已保存，等待维度选择复核', failed: 'PCA 失败，需要人工处理', interrupted: 'PCA 中断，需要人工处理' })[a.status]}</p>
      <code className="block break-all text-xs">{a.attempt_id}</code>
      {a.result?.error && <p role="alert">{a.result.error}</p>}
      {a.result?.actual_pcs !== undefined && <p>实际 {a.result.actual_pcs} PCs · {a.result.usable_features} 可用 features · {a.result.cells} 细胞</p>}
      {a.result?.pc_reduction_reason && <p>{a.result.pc_reduction_reason}</p>}
      {a.result?.versions && <p className="break-all text-xs">{a.result.versions.join(' / ')}</p>}
      {a.status === 'completed' && <img className="h-auto max-w-full" src={pcaDiagnosticPath(sessionId, runId, filterAttemptId, a.attempt_id)} alt="PCA 标准差肘部图与前两主成分诊断；不代表已选择下游维度" />}
      {a.status === 'completed' && <>
        <PcaDimensions key={`${sessionId}:${runId}:${filterAttemptId}:${a.attempt_id}`} sessionId={sessionId} runId={runId} filterAttemptId={filterAttemptId} attemptId={a.attempt_id} revision={revision} disabled={running} onRecorded={onRecorded} />
        <ClusterReview key={`cluster:${sessionId}:${runId}:${filterAttemptId}:${a.attempt_id}`} sessionId={sessionId} runId={runId} filterAttemptId={filterAttemptId} pcaAttemptId={a.attempt_id} revision={revision} disabled={running} onRecorded={onRecorded} onRunning={(active) => setClusterRunning((ids) => active ? [...ids.filter((id) => id !== a.attempt_id), a.attempt_id] : ids.filter((id) => id !== a.attempt_id))} />
      </>}
    </div>)}
  </section>
}
