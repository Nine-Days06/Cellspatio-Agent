import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MarkerReview } from '@/components/MarkerReview'
import { executeMarkers, fetchClusterAcceptance, fetchMarkerCandidates, fetchMarkers, recordClusterAcceptance } from '@/lib/api'
import type { ClusterAcceptanceState, MarkerState } from '@/lib/types'

vi.mock('@/lib/api', () => ({ executeMarkers: vi.fn(), fetchClusterAcceptance: vi.fn(), fetchMarkerCandidates: vi.fn(), fetchMarkers: vi.fn(), recordClusterAcceptance: vi.fn(), markerDiagnosticPath: () => '/local/marker.png' }))
const source = { sessionId: 's1', runId: 'r1', filterAttemptId: 'f1', pcaAttemptId: 'p1', clusterAttemptId: 'c1' }
const initialReview: ClusterAcceptanceState = { revision: 9, record_version: '1', binding_sha256: 'cluster-binding', blocked: [], current: null, history: [] }
const record = { review_id: 'review1', cluster_attempt_id: 'c1', binding_sha256: 'cluster-binding', record_version: '1', source_revision: 9, accepted: true as const, reason: '私有接受', created_at: 'now' }
const acceptedReview = { ...initialReview, revision: 10, current: record, history: [record] }
const initial: MarkerState = { revision: 9, binding_sha256: null, review: null, protocol: { full: true }, tool_version: '1', blocked: ['需要独立聚类接受'], attempts: [] }
const authorized: MarkerState = { ...initial, revision: 10, binding_sha256: 'marker-binding', review: { review_id: 'review1', record_version: '1', accepted: true }, blocked: [] }
const completed: MarkerState = { ...authorized, revision: 12, binding_sha256: null, review: null, attempts: [{ attempt_id: 'm1', binding_sha256: 'marker-binding', status: 'completed', invalidated_reason: null, error: null, result: { cells: 31, features: 3, clusters: 2, marker_rows: 1, positive_rows: 1, negative_rows: 0, versions: ['presto 1.1.0'], cluster_summary: [{ cluster_id: '0', cells: '16', rest_cells: '15', result_status: 'candidates_returned', positive_markers: '1', negative_markers: '0' }, { cluster_id: '1', cells: '15', rest_cells: '16', result_status: 'no_returned_candidates', positive_markers: '0', negative_markers: '0' }] } }] }
const page = { attempt_id: 'm1', total: 1, offset: 0, limit: 100, rows: [{ cluster_id: '0', feature_key: 'F00000002', feature_id: 'original-A', symbol: 'A_B', p_val: 0.0001, avg_log2FC: 1, 'pct.1': 0.8, 'pct.2': 0.2, p_val_adj: 0.0003 }] }
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(fetchClusterAcceptance).mockResolvedValue(initialReview)
  vi.mocked(fetchMarkers).mockResolvedValue(initial)
  vi.mocked(recordClusterAcceptance).mockResolvedValue(acceptedReview)
  vi.mocked(executeMarkers).mockResolvedValue(completed)
  vi.mocked(fetchMarkerCandidates).mockResolvedValue(page)
})
afterEach(() => { vi.useRealTimers() })
const acceptButton = () => screen.getByRole('button', { name: '仅保存探索性聚类接受' })
const executeButton = () => screen.getByRole('button', { name: '独立授权并执行 marker' })
async function authorize() {
  await waitFor(() => expect(screen.getByLabelText('marker 授权依据（仅本地）')).not.toBeDisabled())
  fireEvent.change(screen.getByLabelText('marker 授权依据（仅本地）'), { target: { value: ' 技术验收 ' } })
  fireEvent.click(executeButton())
}

describe('聚类接受与 marker 独立授权', () => {
  it('临时 GET 失败后继续只读轮询，到终态停止；不重发执行', async () => {
    vi.useFakeTimers()
    const running = { ...completed, attempts: [{ ...completed.attempts[0], status: 'running' as const, result: null }] }
    vi.mocked(fetchMarkers).mockResolvedValueOnce(running).mockRejectedValueOnce(new Error('临时断联')).mockResolvedValue(completed)
    const recorded = vi.fn()
    const view = render(<MarkerReview {...source} revision={11} onRecorded={recorded} />)
    await act(async () => {})
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByRole('alert')).toHaveTextContent('临时断联')
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(fetchMarkers).toHaveBeenCalledTimes(3)
    expect(screen.getByText('A_B')).toBeInTheDocument()
    expect(screen.queryByText('临时断联')).not.toBeInTheDocument()
    expect(recorded).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(fetchMarkers).toHaveBeenCalledTimes(3)
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
    expect(executeMarkers).not.toHaveBeenCalled()
  })

  it('轮询请求未返回时切换身份，不展示旧终态也不继续旧计时', async () => {
    vi.useFakeTimers()
    let resolve!: (next: MarkerState) => void
    const running = { ...completed, attempts: [{ ...completed.attempts[0], status: 'running' as const, result: null }] }
    vi.mocked(fetchMarkers).mockResolvedValueOnce(running).mockImplementationOnce(() => new Promise((done) => { resolve = done })).mockResolvedValue(authorized)
    const recorded = vi.fn()
    const view = render(<MarkerReview {...source} revision={11} onRecorded={recorded} />)
    await act(async () => {})
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    view.rerender(<MarkerReview {...source} sessionId="s2" revision={11} onRecorded={recorded} />)
    await act(async () => {})
    await act(async () => { resolve(completed) })
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(fetchMarkers).toHaveBeenCalledTimes(3)
    expect(fetchMarkers).toHaveBeenLastCalledWith({ ...source, sessionId: 's2' })
    expect(recorded).not.toHaveBeenCalled()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('空初值、checkbox 与接受依据都必需，接受不执行 R；第二次明确授权绑定与 revision', async () => {
    vi.mocked(fetchMarkers).mockResolvedValueOnce(initial).mockResolvedValue(authorized)
    const recorded = vi.fn()
    render(<MarkerReview {...source} revision={9} onRecorded={recorded} />)
    await waitFor(() => expect(screen.getByLabelText('探索性聚类接受依据（仅本地）')).not.toBeDisabled())
    expect(acceptButton()).toBeDisabled()
    expect(executeButton()).toBeDisabled()
    fireEvent.change(screen.getByLabelText('探索性聚类接受依据（仅本地）'), { target: { value: ' 私有接受 ' } })
    expect(acceptButton()).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(acceptButton())
    await screen.findByText('已记录探索性接受；marker 仍需独立授权。')
    expect(recordClusterAcceptance).toHaveBeenCalledWith(source, 'cluster-binding', 9, '私有接受')
    expect(executeMarkers).not.toHaveBeenCalled()
    expect(executeButton()).toBeDisabled()
    await authorize()
    await screen.findByText('A_B')
    expect(executeMarkers).toHaveBeenCalledWith(source, 'marker-binding', 'review1', 10, '技术验收')
    expect(recorded).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('img')).toHaveAttribute('src', '/local/marker.png')
    expect(screen.getByText(/检验完成，无通过固定阈值候选/)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '未复核 marker 候选表，可横向滚动' })).toHaveAttribute('tabindex', '0')
    expect(screen.getByLabelText('marker 授权依据（仅本地）')).toHaveValue('')
  })

  it('恢复与刷新只 GET，候选分页不触发执行', async () => {
    vi.mocked(fetchMarkers).mockResolvedValue(completed)
    vi.mocked(fetchMarkerCandidates).mockResolvedValueOnce({ ...page, total: 101, rows: Array.from({ length: 100 }, (_, i) => ({ ...page.rows[0], feature_key: `F${i}`, symbol: `gene${i}` })) }).mockResolvedValue({ ...page, total: 101, offset: 100 })
    render(<MarkerReview {...source} revision={12} />)
    await screen.findByText('gene99')
    fireEvent.click(screen.getByRole('button', { name: '下一页候选' }))
    await screen.findByText('A_B')
    expect(fetchMarkerCandidates).toHaveBeenLastCalledWith(source, 'm1', 100)
    expect(screen.getByRole('button', { name: '下一页候选' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '只读刷新 marker 与接受记录' }))
    await screen.findByText('A_B')
    expect(executeMarkers).not.toHaveBeenCalled()
    expect(recordClusterAcceptance).not.toHaveBeenCalled()
  })

  it.each(['failed', 'interrupted'] as const)('%s 无图、无表、不自动重试', async (status) => {
    vi.mocked(fetchMarkers).mockResolvedValue({ ...completed, attempts: [{ ...completed.attempts[0], status, result: null, error: '本地证据保留' }] })
    render(<MarkerReview {...source} revision={12} />)
    await screen.findByText('本地证据保留')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(fetchMarkerCandidates).not.toHaveBeenCalled()
    expect(executeButton()).toBeDisabled()
    expect(executeMarkers).not.toHaveBeenCalled()
  })

  it('source 漂移不展示可信图表', async () => {
    vi.mocked(fetchMarkers).mockResolvedValue({ ...completed, attempts: [{ ...completed.attempts[0], invalidated_reason: '来源已变化，只能历史' }] })
    render(<MarkerReview {...source} revision={12} />)
    await screen.findByText('来源已变化，只能历史')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(fetchMarkerCandidates).not.toHaveBeenCalled()
  })

  it('运行立即通知上游，切换身份清除未保存依据并丢弃旧 POST', async () => {
    vi.mocked(fetchClusterAcceptance).mockResolvedValue(acceptedReview)
    vi.mocked(fetchMarkers).mockResolvedValue(authorized)
    let resolve!: (next: MarkerState) => void
    vi.mocked(executeMarkers).mockImplementationOnce(() => new Promise((done) => { resolve = done }))
    const running = vi.fn(); const recorded = vi.fn()
    const view = render(<MarkerReview {...source} revision={10} onRunning={running} onRecorded={recorded} />)
    await authorize()
    expect(running).toHaveBeenLastCalledWith(true)
    view.rerender(<MarkerReview {...source} sessionId="s2" runId="r2" revision={10} onRunning={running} onRecorded={recorded} />)
    await waitFor(() => expect(fetchMarkers).toHaveBeenCalledWith({ ...source, sessionId: 's2', runId: 'r2' }))
    await act(async () => { resolve(completed) })
    expect(recorded).not.toHaveBeenCalled()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByLabelText('marker 授权依据（仅本地）')).toHaveValue('')
  })

  it('POST 失联仅 GET 恢复已完成结果，不重复执行', async () => {
    vi.mocked(fetchClusterAcceptance).mockResolvedValue(acceptedReview)
    vi.mocked(fetchMarkers).mockResolvedValueOnce(authorized).mockResolvedValue(completed)
    vi.mocked(executeMarkers).mockRejectedValue(new Error('连接断开'))
    render(<MarkerReview {...source} revision={10} />)
    await authorize()
    await screen.findByText('A_B')
    expect(executeMarkers).toHaveBeenCalledOnce()
    expect(fetchMarkers).toHaveBeenCalledTimes(2)
    expect(executeButton()).toBeDisabled()
  })

  it('上游运行 disabled 时不能接受或授权', async () => {
    vi.mocked(fetchClusterAcceptance).mockResolvedValue(acceptedReview)
    vi.mocked(fetchMarkers).mockResolvedValue(authorized)
    render(<MarkerReview {...source} revision={10} disabled />)
    await screen.findByText('已记录探索性接受；marker 仍需独立授权。')
    expect(acceptButton()).toBeDisabled()
    expect(screen.getByLabelText('marker 授权依据（仅本地）')).toBeDisabled()
  })
})
