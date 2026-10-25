import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QcReview } from '@/components/QcReview'
import { approveQc, fetchQc, filterQc, previewQc } from '@/lib/api'
import type { QcReviewState } from '@/lib/types'

vi.mock('@/lib/api', () => ({ fetchQc: vi.fn(), previewQc: vi.fn(), approveQc: vi.fn(), filterQc: vi.fn() }))
vi.mock('@/components/PcaReview', () => ({ PcaReview: () => <div>PCA 独立授权</div> }))
const initial: QcReviewState = {
  run_id: 'r1', revision: 0, qc: {} as QcReviewState['qc'], active_preview: null, approvals: [], blocked: [],
}
const preview: QcReviewState = {
  ...initial, revision: 1,
  active_preview: {
    preview_id: 'p1', params: { n_feature_min: 1, n_feature_max: 2, percent_mt_max: '25', n_count_min: null, n_count_max: null },
    summary: { total: 2, kept: 1, excluded: 1, excluded_percent: 50, reason_counts: { mt_above_max: 1 }, blocked: [], filtered: false },
    status: 'pending', binding_sha256: 'hash', reason: null, approved_at: null,
  },
}
const approved: QcReviewState = { ...preview, revision: 2, active_preview: { ...preview.active_preview!, status: 'approved', reason: '科学选择依据', approved_at: '2026-10-25' }, approvals: [{ ...preview.active_preview!, status: 'approved', reason: '科学选择依据', approved_at: '2026-10-25' }] }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(fetchQc).mockResolvedValue(initial)
  vi.mocked(previewQc).mockResolvedValue(preview)
  vi.mocked(approveQc).mockResolvedValue(approved)
})

async function enterPreview() {
  await waitFor(() => expect(screen.getByRole('button', { name: '生成排除预览' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('RNA features 下限'), { target: { value: '1' } })
  fireEvent.change(screen.getByLabelText('RNA features 上限'), { target: { value: '2' } })
  fireEvent.change(screen.getByLabelText('percent.mt 上限 (%)'), { target: { value: '25' } })
  fireEvent.click(screen.getByRole('button', { name: '生成排除预览' }))
  await screen.findByRole('region', { name: '排除预览' })
}

describe('QC review', () => {
  it('disables QC changes while PCA is running in another view', async () => {
    vi.mocked(fetchQc).mockResolvedValue({ ...preview, pca_running: true })
    render(<QcReview sessionId="s1" runId="r1" />)
    await screen.findByRole('region', { name: '排除预览' })
    expect(screen.getByLabelText('RNA features 下限')).toBeDisabled()
    expect(screen.getByLabelText('选择依据（仅本地记录）')).toBeDisabled()
    expect(screen.getByRole('button', { name: '生成排除预览' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '记录此过滤选择' })).toBeDisabled()
  })
  it('starts blank, previews explicit parameters, disables edited selection, records and restores approval', async () => {
    const recorded = vi.fn()
    const view = render(<QcReview sessionId="s1" runId="r1" onRecorded={recorded} />)
    expect(screen.getByLabelText('RNA features 下限')).toHaveValue(null)
    await enterPreview()
    expect(previewQc).toHaveBeenCalledWith('s1', 'r1', { n_feature_min: 1, n_feature_max: 2, percent_mt_max: 25, n_count_min: null, n_count_max: null })
    expect(screen.getByText(/原因可重叠/)).toBeInTheDocument()
    const button = screen.getByRole('button', { name: '记录此过滤选择' })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('选择依据（仅本地记录）'), { target: { value: '科学选择依据' } })
    expect(button).toBeEnabled()
    fireEvent.change(screen.getByLabelText('RNA features 上限'), { target: { value: '3' } })
    expect(button).toBeDisabled()
    expect(screen.getByText(/阈值已编辑/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('RNA features 上限'), { target: { value: '2' } })
    fireEvent.click(button)
    await screen.findByRole('region', { name: '已批准选择' })
    expect(approveQc).toHaveBeenCalledWith('s1', 'r1', 'p1', '科学选择依据')
    expect(recorded).toHaveBeenCalledOnce()
    view.unmount()
    vi.mocked(fetchQc).mockResolvedValue(approved)
    render(<QcReview sessionId="s1" runId="r1" />)
    await screen.findByRole('region', { name: '已批准选择' })
    expect(screen.queryByRole('button', { name: '记录此过滤选择' })).not.toBeInTheDocument()
  })

  it('blocks malformed thresholds, stale evidence and NA selection', async () => {
    render(<QcReview sessionId="s1" runId="r1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: '生成排除预览' })).toBeEnabled())
    fireEvent.change(screen.getByLabelText('RNA features 下限'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('RNA features 上限'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('percent.mt 上限 (%)'), { target: { value: '25' } })
    fireEvent.submit(screen.getByRole('button', { name: '生成排除预览' }).closest('form')!)
    expect(screen.getByRole('alert')).toHaveTextContent('请输入合法阈值')
    expect(previewQc).not.toHaveBeenCalled()
    vi.mocked(previewQc).mockResolvedValue({ ...preview, active_preview: { ...preview.active_preview!, summary: { ...preview.active_preview!.summary, blocked: ['NA 不能批准'] } } })
    await enterPreview()
    fireEvent.change(screen.getByLabelText('选择依据（仅本地记录）'), { target: { value: 'review' } })
    expect(screen.getByRole('button', { name: '记录此过滤选择' })).toBeDisabled()
    vi.mocked(previewQc).mockRejectedValue(new Error('证据变化，请重新 QC'))
    fireEvent.click(screen.getByRole('button', { name: '生成排除预览' }))
    await screen.findByText('证据变化，请重新 QC')
    expect(screen.getByRole('button', { name: '记录此过滤选择' })).toBeDisabled()
  })

  it('discards async response after switching run', async () => {
    let resolveOld!: (value: QcReviewState) => void
    vi.mocked(fetchQc).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    const view = render(<QcReview sessionId="s1" runId="r1" />)
    view.rerender(<QcReview sessionId="s2" runId="r2" />)
    await waitFor(() => expect(screen.getByRole('button', { name: '生成排除预览' })).toBeEnabled())
    await act(async () => { resolveOld(preview) })
    expect(screen.queryByRole('region', { name: '排除预览' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('RNA features 下限')).toHaveValue(null)
  })

  it('clears previous run evidence and reason while loading another run', async () => {
    vi.mocked(fetchQc).mockResolvedValueOnce(preview)
    const view = render(<QcReview sessionId="s1" runId="r1" />)
    await screen.findByRole('region', { name: '排除预览' })
    fireEvent.change(screen.getByLabelText('选择依据（仅本地记录）'), { target: { value: 'old reason' } })
    vi.mocked(fetchQc).mockReturnValueOnce(new Promise(() => {}))
    view.rerender(<QcReview sessionId="s2" runId="r2" />)
    expect(screen.queryByRole('region', { name: '排除预览' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('RNA features 下限')).toHaveValue(null)
  })

  it('executes only a separately approved unchanged selection and restores actual outcome without rerunning', async () => {
    vi.mocked(fetchQc).mockResolvedValueOnce(approved)
    const completed: QcReviewState = { ...approved, attempts: [{ attempt_id: 'a1', preview_id: 'p1', status: 'completed', directory: '/local/a1', created_at: 'now', finished_at: 'later', result: { cells: 1, features: 2 } }] }
    vi.mocked(filterQc).mockResolvedValue(completed)
    const view = render(<QcReview sessionId="s1" runId="r1" />)
    const button = await screen.findByRole('button', { name: '执行此已记录选择的过滤' })
    expect(button).toBeEnabled()
    expect(filterQc).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('RNA features 上限'), { target: { value: '3' } })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('RNA features 上限'), { target: { value: '2' } })
    fireEvent.click(button)
    await screen.findByText('过滤产物已保存，等待审阅')
    expect(filterQc).toHaveBeenCalledOnce()
    expect(filterQc).toHaveBeenCalledWith('s1', 'r1', 'p1')
    expect(button).toBeDisabled()
    expect(screen.getByText(/实际保留 1 个 barcode/)).toBeInTheDocument()
    view.unmount()
    vi.mocked(fetchQc).mockResolvedValueOnce(completed)
    render(<QcReview sessionId="s1" runId="r1" />)
    await screen.findByRole('region', { name: '过滤执行记录' })
    expect(screen.getByRole('button', { name: '执行此已记录选择的过滤' })).toBeDisabled()
    expect(filterQc).toHaveBeenCalledOnce()
  })

  it('does not offer retry for interrupted execution', async () => {
    vi.mocked(fetchQc).mockResolvedValue({ ...approved, attempts: [{ attempt_id: 'a1', preview_id: 'p1', status: 'interrupted', directory: '/local/a1', created_at: 'now', finished_at: 'later', result: null }] })
    render(<QcReview sessionId="s1" runId="r1" />)
    await screen.findByText('过滤中断，需要新预览和批准')
    expect(screen.getByRole('button', { name: '执行此已记录选择的过滤' })).toBeDisabled()
    expect(filterQc).not.toHaveBeenCalled()
  })
})
