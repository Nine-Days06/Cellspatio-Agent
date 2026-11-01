import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PcaDimensions } from '@/components/PcaDimensions'
import { executePca, fetchPcaDimensions, recordPcaDimensions } from '@/lib/api'
import type { PcaDimensionReview, PcaDimensionsState } from '@/lib/types'

vi.mock('@/lib/api', () => ({ fetchPcaDimensions: vi.fn(), recordPcaDimensions: vi.fn(), executePca: vi.fn() }))
const initial: PcaDimensionsState = { run_id: 'r1', filter_attempt_id: 'f1', attempt_id: 'a1', record_version: '1', stage: 'pca_review', scientific_status: 'unreviewed', revision: 4, binding_sha256: 'binding', evidence: { actual_pcs: 2, stdev: [{ pc: 'PC_1', stdev: 2 }, { pc: 'PC_2', stdev: 1 }] }, blocked: [], current: null, history: [] }
const review: PcaDimensionReview = { review_id: 'd1', pca_attempt_id: 'a1', binding_sha256: 'binding', record_version: '1', source_revision: 4, dims: [1, 2], reason: '技术依据', created_at: 'now', message_id: 2 }
const recorded: PcaDimensionsState = { ...initial, revision: 5, current: review, history: [{ ...review, invalidated_reason: null }] }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(fetchPcaDimensions).mockResolvedValue(initial)
  vi.mocked(recordPcaDimensions).mockResolvedValue(recorded)
})

function fill() {
  fireEvent.change(screen.getByLabelText('维度数 k（手工填写）'), { target: { value: '2' } })
  fireEvent.change(screen.getByLabelText('维度选择依据（仅本地）'), { target: { value: ' 技术依据 ' } })
}

describe('PCA dimension facts without execution', () => {
  it('starts empty, shows exact prefix, records and refreshes without execution', async () => {
    const callback = vi.fn()
    const view = render(<PcaDimensions sessionId="s1" runId="r1" filterAttemptId="f1" attemptId="a1" revision={4} onRecorded={callback} />)
    const button = await screen.findByRole('button', { name: '记录选择（不执行）' })
    await waitFor(() => expect(screen.getByLabelText('维度数 k（手工填写）')).toBeEnabled())
    expect(screen.getByLabelText('维度数 k（手工填写）')).toHaveValue(null)
    expect(button).toBeDisabled()
    expect(recordPcaDimensions).not.toHaveBeenCalled()
    fill()
    expect(screen.getByText('将记录：[1, 2]')).toBeInTheDocument()
    fireEvent.click(button)
    await screen.findByText('当前维度：[1, 2] · 已记录，科学未复核')
    expect(recordPcaDimensions).toHaveBeenCalledWith('s1', 'r1', 'f1', 'a1', 'binding', 4, 2, '技术依据')
    expect(callback).toHaveBeenCalledOnce()
    expect(executePca).not.toHaveBeenCalled()
    expect(screen.getByLabelText('维度数 k（手工填写）')).toHaveValue(null)
    view.unmount()
    vi.mocked(fetchPcaDimensions).mockResolvedValue(recorded)
    render(<PcaDimensions sessionId="s1" runId="r1" filterAttemptId="f1" attemptId="a1" revision={4} />)
    await screen.findByText('当前维度：[1, 2] · 已记录，科学未复核')
    expect(recordPcaDimensions).toHaveBeenCalledOnce()
  })

  it('blocks fractions, out of range and blank reasons, shows invalidated history', async () => {
    render(<PcaDimensions sessionId="s1" runId="r1" filterAttemptId="f1" attemptId="a1" revision={4} />)
    await waitFor(() => expect(screen.getByLabelText('维度数 k（手工填写）')).toBeEnabled())
    fill()
    for (const value of ['0', '3', '1.5', '']) {
      fireEvent.change(screen.getByLabelText('维度数 k（手工填写）'), { target: { value } })
      expect(screen.getByRole('button', { name: '记录选择（不执行）' })).toBeDisabled()
    }
    fill()
    fireEvent.change(screen.getByLabelText('维度选择依据（仅本地）'), { target: { value: ' ' } })
    expect(screen.getByRole('button', { name: '记录选择（不执行）' })).toBeDisabled()
    vi.mocked(fetchPcaDimensions).mockResolvedValue({ ...initial, binding_sha256: null, blocked: ['父选择已变化'], history: [{ ...review, invalidated_reason: '需重新明确记录' }] })
    fireEvent.click(screen.getByRole('button', { name: '只读刷新维度记录' }))
    await screen.findByText('父选择已变化')
    expect(screen.getByText('需重新明确记录')).toBeInTheDocument()
    expect(screen.getByLabelText('维度数 k（手工填写）')).toBeDisabled()
    expect(recordPcaDimensions).not.toHaveBeenCalled()
  })

  it('drops old GET and POST responses when switching sessions', async () => {
    let finishGet!: (value: PcaDimensionsState) => void
    vi.mocked(fetchPcaDimensions).mockImplementationOnce(() => new Promise((resolve) => { finishGet = resolve }))
    const callback = vi.fn()
    const view = render(<PcaDimensions sessionId="s1" runId="r1" filterAttemptId="f1" attemptId="a1" revision={4} onRecorded={callback} />)
    view.rerender(<PcaDimensions sessionId="s2" runId="r2" filterAttemptId="f2" attemptId="a2" revision={4} onRecorded={callback} />)
    await waitFor(() => expect(screen.getByLabelText('维度数 k（手工填写）')).toBeEnabled())
    await act(async () => { finishGet(recorded) })
    expect(screen.queryByText(/当前维度：/)).not.toBeInTheDocument()
    let finishPost!: (value: PcaDimensionsState) => void
    vi.mocked(recordPcaDimensions).mockImplementationOnce(() => new Promise((resolve) => { finishPost = resolve }))
    fill()
    fireEvent.click(screen.getByRole('button', { name: '记录选择（不执行）' }))
    view.rerender(<PcaDimensions sessionId="s3" runId="r3" filterAttemptId="f3" attemptId="a3" revision={4} onRecorded={callback} />)
    await waitFor(() => expect(fetchPcaDimensions).toHaveBeenCalledWith('s3', 'r3', 'f3', 'a3'))
    await act(async () => { finishPost(recorded) })
    expect(callback).not.toHaveBeenCalled()
    expect(screen.queryByText(/当前维度：/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('维度选择依据（仅本地）')).toHaveValue('')
  })

  it('rechecks after an upstream preview/revision change in the same session', async () => {
    vi.mocked(fetchPcaDimensions).mockResolvedValueOnce(recorded).mockResolvedValue({ ...initial, binding_sha256: null, blocked: ['新预览使父选择失效'], history: [{ ...review, invalidated_reason: '需重新记录' }] })
    const view = render(<PcaDimensions sessionId="s1" runId="r1" filterAttemptId="f1" attemptId="a1" revision={5} />)
    await screen.findByText('当前维度：[1, 2] · 已记录，科学未复核')
    view.rerender(<PcaDimensions sessionId="s1" runId="r1" filterAttemptId="f1" attemptId="a1" revision={6} />)
    await screen.findByText('新预览使父选择失效')
    expect(screen.queryByText(/当前维度：/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '记录选择（不执行）' })).toBeDisabled()
    expect(recordPcaDimensions).not.toHaveBeenCalled()
  })

  it('recovers unknown POST with only GET, permits explicit new record using latest revision', async () => {
    vi.mocked(recordPcaDimensions).mockRejectedValueOnce(new Error('连接断开'))
    vi.mocked(fetchPcaDimensions).mockResolvedValueOnce(initial).mockResolvedValue(recorded)
    render(<PcaDimensions sessionId="s1" runId="r1" filterAttemptId="f1" attemptId="a1" revision={4} />)
    await waitFor(() => expect(screen.getByLabelText('维度数 k（手工填写）')).toBeEnabled())
    fill()
    fireEvent.click(screen.getByRole('button', { name: '记录选择（不执行）' }))
    await screen.findByText('当前维度：[1, 2] · 已记录，科学未复核')
    expect(recordPcaDimensions).toHaveBeenCalledOnce()
    expect(fetchPcaDimensions).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(screen.getByLabelText('维度数 k（手工填写）')).toBeEnabled())
    fireEvent.change(screen.getByLabelText('维度数 k（手工填写）'), { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: '记录选择（不执行）' }))
    await waitFor(() => expect(recordPcaDimensions).toHaveBeenLastCalledWith('s1', 'r1', 'f1', 'a1', 'binding', 5, 1, '技术依据'))
    expect(executePca).not.toHaveBeenCalled()
  })
})
