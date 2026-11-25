import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MessageList, EMPTY_FALLBACK } from '@/components/MessageList'
import type { WireMessage } from '@/lib/types'

describe('QC evidence', () => {
  it('renders persisted read-only evidence without confirmation buttons or empty fallback', () => {
    const message: WireMessage = {
      id: 1, session_id: 's1', seq: 1, role: 'assistant', content: '', timestamp: '', pending_script: null,
      results: {
        qc: { tool_version: '1.0.0', stage: 'qc_review', scientific_status: 'unreviewed', filtered: false, cells: 2700, features: 32738,
          zero_count_cells: 1, mt_symbol_resolved: false, warnings: ['percent.mt 为 NA'], versions: ['R 4.6.1'],
          metrics: { percent_mt: { available: 0, min: null, median: null, max: null } } },
        qc_artifacts: { 'cell_qc.csv': 'D:/local/cell_qc.csv' },
      },
    }
    render(<MessageList messages={[message]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)
    expect(screen.getByRole('region', { name: '过滤前 QC 证据' })).toBeInTheDocument()
    expect(screen.getByText(/2,700/)).toBeInTheDocument()
    expect(screen.getByText('percent.mt 为 NA')).toBeInTheDocument()
    expect(screen.getByText('QC 1.0.0 · R 4.6.1')).toBeInTheDocument()
    expect(screen.getByText('D:/local/cell_qc.csv')).toBeInTheDocument()
    expect(screen.queryByText(EMPTY_FALLBACK)).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('does not present failed QC as completed or awaiting scientific review', () => {
    const message: WireMessage = {
      id: 2, session_id: 's1', seq: 2, role: 'assistant', content: '', timestamp: '', pending_script: null,
      results: { qc_artifacts: { 'stderr.txt': 'D:/local/stderr.txt' } },
    }
    render(<MessageList messages={[message]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)
    expect(screen.getByText('QC 未完成 · 本地证据已保留')).toBeInTheDocument()
    expect(screen.queryByText(/等待科学审阅/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
