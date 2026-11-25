import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { KnowledgeEvidence } from '@/components/KnowledgeEvidence'
import type { ChatKnowledgeResult } from '@/lib/types'

function result(overrides: Partial<ChatKnowledgeResult> = {}): ChatKnowledgeResult {
  return {
    outcome: 'supported_answer',
    evidence: [
      { evidence_id: 'E1', source_id: 'PMID 1', title: 'A paper', content: '第一条原文' },
      { evidence_id: 'E2', source_id: 'PMID 1', title: 'A paper', content: '第二条原文' },
    ],
    sources: [{ source_id: 'PMID 1', title: 'A paper', evidence_ids: ['E1', 'E2'] }],
    citations: ['E1'],
    claims: [],
    ...overrides,
  }
}

describe('KnowledgeEvidence', () => {
  it('展示范围、条数、覆盖说明与下一页，并按来源去重', () => {
    const data: ChatKnowledgeResult = {
      ...result(),
      scope: 'exhaustive',
      evidence_count: 2,
      source_count: 1,
      has_more: true,
      next_cursor: 'cursor-1',
      coverage_message: '这是当前知识库检索结果的有界一页，不代表已找全。',
    }
    render(<KnowledgeEvidence data={data} messageId={7} />)

    expect(
      screen.getByText('文献证据（2 条 / 1 个来源） · 分页盘点 · 还有下一页'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('knowledge-coverage')).toHaveTextContent('不代表已找全')
    expect(screen.getByText('第一条原文')).toBeInTheDocument()
    expect(screen.queryByText('第二条原文')).not.toBeInTheDocument()
  })

  it('旧消息没有新字段时保持原渲染，不出现空占位', () => {
    render(
      <KnowledgeEvidence
        data={result({
          evidence: [{ evidence_id: 'E1', source_id: 'PMID 1', title: 'A paper', content: '原文' }],
          sources: [{ source_id: 'PMID 1', title: 'A paper', evidence_ids: ['E1'] }],
        })}
        messageId={8}
      />,
    )

    expect(screen.getByText('文献证据（1 条）')).toBeInTheDocument()
    expect(screen.queryByTestId('knowledge-coverage')).not.toBeInTheDocument()
    expect(screen.queryByText(/分页盘点/)).not.toBeInTheDocument()
  })

  it('指定来源正文请求不按来源折叠多段正文', () => {
    const data: ChatKnowledgeResult = {
      ...result(),
      scope: 'exhaustive',
      source_id: 'PMID 1',
      evidence_count: 2,
      source_count: 1,
      coverage_message: '已返回指定来源的正文片段。',
    }
    render(<KnowledgeEvidence data={data} messageId={9} />)

    expect(screen.getByText('第一条原文')).toBeInTheDocument()
    expect(screen.getByText('第二条原文')).toBeInTheDocument()
  })
})
