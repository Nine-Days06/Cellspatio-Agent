import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PcaReview } from '@/components/PcaReview'
import { executePca, fetchPca, fetchPcaDimensions } from '@/lib/api'
import type { PcaState } from '@/lib/types'

vi.mock('@/lib/api', () => ({ fetchPca: vi.fn(), executePca: vi.fn(), fetchPcaDimensions: vi.fn(), recordPcaDimensions: vi.fn(), pcaDiagnosticPath: () => '/local/diagnostic' }))
const initial: PcaState = { run_id: 'r1', filter_attempt_id: 'f1', protocol: { normalize: 'LogNormalize' }, tool_version: '1.0.0', binding_sha256: 'binding', source_summary: { cells: 3, features: 3 }, blocked: [], attempts: [] }
const completed: PcaState = { ...initial, attempts: [{ attempt_id: 'a1', binding_sha256: 'binding', status: 'completed', created_at: 'now', finished_at: 'later', result: { cells: 3, usable_features: 3, actual_pcs: 2 } }] }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(fetchPca).mockResolvedValue(initial)
  vi.mocked(executePca).mockResolvedValue(completed)
  vi.mocked(fetchPcaDimensions).mockResolvedValue({ run_id: 'r1', filter_attempt_id: 'f1', attempt_id: 'a1', record_version: '1', stage: 'pca_review', scientific_status: 'unreviewed', revision: 4, binding_sha256: 'dimensions', evidence: { actual_pcs: 2, stdev: [{ pc: 'PC_1', stdev: 2 }, { pc: 'PC_2', stdev: 1 }] }, blocked: [], current: null, history: [] })
})

describe('PCA independent authorization', () => {
  it('GET only until separate nonempty authorization, then restores without POST', async () => {
    const recorded = vi.fn()
    const view = render(<PcaReview sessionId="s1" runId="r1" filterAttemptId="f1" revision={4} onRecorded={recorded} />)
    await screen.findByText(/父过滤：3 细胞/)
    expect(executePca).not.toHaveBeenCalled()
    const button = screen.getByRole('button', { name: '独立授权并执行归一化与 PCA' })
    expect(button).toBeDisabled()
    const reason = screen.getByLabelText('PCA 授权依据（仅本地记录）')
    fireEvent.change(reason, { target: { value: ' ' } })
    expect(button).toBeDisabled()
    fireEvent.change(reason, { target: { value: ' 技术验证 ' } })
    expect(button).toBeEnabled()
    fireEvent.click(button)
    await screen.findByText('PCA 已保存，等待维度选择复核')
    expect(executePca).toHaveBeenCalledWith('s1', 'r1', 'f1', 'binding', '技术验证')
    expect(recorded).toHaveBeenCalledOnce()
    expect(button).toBeDisabled()
    expect(screen.getByRole('img')).toHaveAttribute('src', '/local/diagnostic')
    view.unmount()
    vi.mocked(fetchPca).mockResolvedValue(completed)
    render(<PcaReview sessionId="s1" runId="r1" filterAttemptId="f1" revision={4} />)
    await screen.findByText('PCA 已保存，等待维度选择复核')
    expect(screen.getByRole('button', { name: '独立授权并执行归一化与 PCA' })).toBeDisabled()
    expect(executePca).toHaveBeenCalledOnce()
  })

  it.each(['failed', 'interrupted'] as const)('never offers retry after %s', async (status) => {
    vi.mocked(fetchPca).mockResolvedValue({ ...completed, attempts: [{ ...completed.attempts[0], status, result: { error: '人工复核' } }] })
    render(<PcaReview sessionId="s1" runId="r1" filterAttemptId="f1" revision={4} />)
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: '独立授权并执行归一化与 PCA' })).toBeDisabled()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(executePca).not.toHaveBeenCalled()
  })

  it('blocks missing identities and scientific gates, never auto-POSTs', async () => {
    vi.mocked(fetchPca).mockResolvedValue({ ...initial, binding_sha256: null, blocked: ['父文件变化'] })
    render(<PcaReview sessionId="s1" runId="r1" filterAttemptId="f1" revision={4} />)
    await screen.findByText('父文件变化')
    fireEvent.change(screen.getByLabelText('PCA 授权依据（仅本地记录）'), { target: { value: '技术验证' } })
    expect(screen.getByRole('button', { name: '独立授权并执行归一化与 PCA' })).toBeDisabled()
    expect(executePca).not.toHaveBeenCalled()
  })

  it('signals running immediately and clears stale response when switching source', async () => {
    let resolve!: (value: PcaState) => void
    vi.mocked(executePca).mockImplementationOnce(() => new Promise((done) => { resolve = done }))
    const running = vi.fn()
    const view = render(<PcaReview sessionId="s1" runId="r1" filterAttemptId="f1" revision={4} onRunning={running} />)
    await screen.findByText(/父过滤：3 细胞/)
    fireEvent.change(screen.getByLabelText('PCA 授权依据（仅本地记录）'), { target: { value: '技术验证' } })
    fireEvent.click(screen.getByRole('button', { name: '独立授权并执行归一化与 PCA' }))
    expect(screen.getByRole('button', { name: 'PCA 运行中…' })).toBeDisabled()
    expect(running).toHaveBeenLastCalledWith(true)
    view.rerender(<PcaReview sessionId="s2" runId="r2" filterAttemptId="f2" revision={4} onRunning={running} />)
    await waitFor(() => expect(fetchPca).toHaveBeenCalledWith('s2', 'r2', 'f2'))
    await act(async () => { resolve(completed) })
    expect(screen.queryByText('PCA 已保存，等待维度选择复核')).not.toBeInTheDocument()
    expect(screen.getByLabelText('PCA 授权依据（仅本地记录）')).toHaveValue('')
  })

  it('recovers unknown POST outcome using GET, never retries the POST', async () => {
    vi.mocked(executePca).mockRejectedValue(new Error('连接断开'))
    vi.mocked(fetchPca).mockResolvedValueOnce(initial).mockResolvedValue(completed)
    render(<PcaReview sessionId="s1" runId="r1" filterAttemptId="f1" revision={4} />)
    await screen.findByText(/父过滤：3 细胞/)
    fireEvent.change(screen.getByLabelText('PCA 授权依据（仅本地记录）'), { target: { value: '技术验证' } })
    fireEvent.click(screen.getByRole('button', { name: '独立授权并执行归一化与 PCA' }))
    await screen.findByText('PCA 已保存，等待维度选择复核')
    expect(executePca).toHaveBeenCalledOnce()
    expect(fetchPca).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: '独立授权并执行归一化与 PCA' })).toBeDisabled()
  })
})
