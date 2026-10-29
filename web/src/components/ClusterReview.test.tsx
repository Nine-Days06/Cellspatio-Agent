import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ClusterReview } from '@/components/ClusterReview'
import { executeClusters, fetchClusters } from '@/lib/api'
import type { ClusterState } from '@/lib/types'

vi.mock('@/lib/api', () => ({ fetchClusters: vi.fn(), executeClusters: vi.fn(), clusterDiagnosticPath: () => '/local/cluster.png' }))
vi.mock('@/components/MarkerReview', () => ({ MarkerReview: ({ onRunning }: { onRunning: (active: boolean) => void }) => <button onClick={() => onRunning(true)}>模拟 marker 运行</button> }))
const initial: ClusterState = { run_id: 'r1', pca_attempt_id: 'p1', revision: 7, tool_version: '1.0.0', protocol: { seed: 42 }, stage: 'cluster_review', scientific_status: 'unreviewed', binding_sha256: 'binding', selection: { review_id: 'review1', dims: [1, 2], record_version: '1' }, source_summary: { cells: 40, actual_pcs: 30, n_epochs: 500 }, blocked: [], attempts: [] }
const completed: ClusterState = { ...initial, revision: 9, binding_sha256: null, blocked: [], attempts: [{ attempt_id: 'a1', review_id: 'review1', dims: [1, 2], binding_sha256: 'binding', status: 'completed', created_at: 'now', finished_at: 'later', source_revision: 7, claim_revision: 8, error: null, invalidated_reason: null, result: { cells: 40, clusters: 2, cluster_sizes: [{ cluster_id: '0', cells: 22, proportion: 0.55 }, { cluster_id: '1', cells: 18, proportion: 0.45 }], n_epochs: 500, versions: ['R 4.6.1'], seurat_threads: 1 } }] }
const props = { sessionId: 's1', runId: 'r1', filterAttemptId: 'f1', pcaAttemptId: 'p1', revision: 7 }
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(fetchClusters).mockResolvedValue(initial)
  vi.mocked(executeClusters).mockResolvedValue(completed)
})
afterEach(() => { vi.useRealTimers() })

describe('受控聚类独立授权', () => {
  it('marker 运行只传播互斥状态，不轮询或误报聚类完成', async () => {
    vi.useFakeTimers()
    vi.mocked(fetchClusters).mockResolvedValue(completed)
    const recorded = vi.fn(); const running = vi.fn()
    render(<ClusterReview {...props} onRecorded={recorded} onRunning={running} />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: '模拟 marker 运行' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(fetchClusters).toHaveBeenCalledTimes(1)
    expect(recorded).not.toHaveBeenCalled()
    expect(running).toHaveBeenLastCalledWith(true)
    expect(screen.getByRole('button', { name: '下游 marker 运行中…' })).toBeDisabled()
  })

  it('空初值、明确确切绑定/review/revision，刷新恢复只有 GET', async () => {
    const recorded = vi.fn()
    const view = render(<ClusterReview {...props} onRecorded={recorded} />)
    await screen.findByText(/此次来源维度/)
    const reason = screen.getByLabelText('聚类与 UMAP 授权依据（仅本地）')
    const button = screen.getByRole('button', { name: '独立授权并执行聚类与 UMAP' })
    expect(reason).toHaveValue('')
    expect(button).toBeDisabled()
    fireEvent.change(reason, { target: { value: ' ' } })
    expect(button).toBeDisabled()
    fireEvent.change(reason, { target: { value: ' 技术验证 ' } })
    fireEvent.click(button)
    await screen.findByText(/40 细胞 · 2 个探索簇/)
    expect(executeClusters).toHaveBeenCalledWith('s1', 'r1', 'f1', 'p1', 'binding', 'review1', 7, '技术验证')
    expect(recorded).toHaveBeenCalledOnce()
    expect(screen.getByRole('img')).toHaveAttribute('src', '/local/cluster.png')
    expect(reason).toHaveValue('')
    expect(button).toBeDisabled()
    view.unmount()
    vi.mocked(fetchClusters).mockResolvedValue(completed)
    render(<ClusterReview {...props} revision={9} />)
    await screen.findByRole('img')
    fireEvent.click(screen.getByRole('button', { name: '只读刷新聚类记录' }))
    await screen.findByRole('img')
    expect(executeClusters).toHaveBeenCalledOnce()
  })

  it.each(['failed', 'interrupted'] as const)('%s 不重试、不出图、不自动 POST', async (status) => {
    vi.mocked(fetchClusters).mockResolvedValue({ ...completed, attempts: [{ ...completed.attempts[0], status, result: null, error: '本地证据保留' }] })
    render(<ClusterReview {...props} />)
    await screen.findByText('本地证据保留')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '独立授权并执行聚类与 UMAP' })).toBeDisabled()
    expect(executeClusters).not.toHaveBeenCalled()
  })

  it.each(['需要至少 31 细胞', '需要至少 2 PCs', '所选 PCs 零范数'])('阻断 %s，不补维降参', async (message) => {
    vi.mocked(fetchClusters).mockResolvedValue({ ...initial, binding_sha256: null, selection: null, blocked: [message] })
    render(<ClusterReview {...props} />)
    await screen.findByText(message)
    expect(screen.getByLabelText('聚类与 UMAP 授权依据（仅本地）')).toBeDisabled()
    expect(executeClusters).not.toHaveBeenCalled()
  })

  it('旧结果明确历史，禁止显示旧图和可信聚合', async () => {
    vi.mocked(fetchClusters).mockResolvedValue({ ...completed, attempts: [{ ...completed.attempts[0], invalidated_reason: '父身份已变化，仅历史' }] })
    render(<ClusterReview {...props} />)
    await screen.findByText('父身份已变化，仅历史')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByText(/40 细胞 · 2 个探索簇/)).not.toBeInTheDocument()
  })

  it('运行立即通知上游，切换身份丢旧 GET/POST 和未保存依据', async () => {
    let resolve!: (state: ClusterState) => void
    vi.mocked(executeClusters).mockImplementationOnce(() => new Promise((done) => { resolve = done }))
    const running = vi.fn()
    const recorded = vi.fn()
    const view = render(<ClusterReview {...props} onRunning={running} onRecorded={recorded} />)
    await screen.findByText(/此次来源维度/)
    fireEvent.change(screen.getByLabelText('聚类与 UMAP 授权依据（仅本地）'), { target: { value: '私有依据' } })
    fireEvent.click(screen.getByRole('button', { name: '独立授权并执行聚类与 UMAP' }))
    expect(running).toHaveBeenLastCalledWith(true)
    expect(screen.getByRole('button', { name: '聚类与 UMAP 运行中…' })).toBeDisabled()
    view.rerender(<ClusterReview {...props} sessionId="s2" runId="r2" onRunning={running} onRecorded={recorded} />)
    await waitFor(() => expect(fetchClusters).toHaveBeenCalledWith('s2', 'r2', 'f1', 'p1'))
    await act(async () => { resolve(completed) })
    expect(recorded).not.toHaveBeenCalled()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByLabelText('聚类与 UMAP 授权依据（仅本地）')).toHaveValue('')
  })

  it('POST 失联只 GET 恢复，不重复执行', async () => {
    vi.mocked(executeClusters).mockRejectedValue(new Error('连接断开'))
    vi.mocked(fetchClusters).mockResolvedValueOnce(initial).mockResolvedValue(completed)
    render(<ClusterReview {...props} />)
    await screen.findByText(/此次来源维度/)
    fireEvent.change(screen.getByLabelText('聚类与 UMAP 授权依据（仅本地）'), { target: { value: '技术验证' } })
    fireEvent.click(screen.getByRole('button', { name: '独立授权并执行聚类与 UMAP' }))
    await screen.findByRole('img')
    expect(executeClusters).toHaveBeenCalledOnce()
    expect(fetchClusters).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: '独立授权并执行聚类与 UMAP' })).toBeDisabled()
  })

  it('revision 变化清除旧表单，重新读取当前绑定', async () => {
    const view = render(<ClusterReview {...props} />)
    await screen.findByText(/此次来源维度/)
    fireEvent.change(screen.getByLabelText('聚类与 UMAP 授权依据（仅本地）'), { target: { value: '未保存' } })
    vi.mocked(fetchClusters).mockResolvedValue({ ...initial, revision: 8, binding_sha256: 'new', selection: { ...initial.selection!, review_id: 'review2' } })
    view.rerender(<ClusterReview {...props} revision={8} />)
    await screen.findByText(/记录 review2/)
    expect(screen.getByLabelText('聚类与 UMAP 授权依据（仅本地）')).toHaveValue('')
    expect(executeClusters).not.toHaveBeenCalled()
  })
})
