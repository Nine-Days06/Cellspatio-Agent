import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MarkdownContent from '@/components/MarkdownContent'
import {
  KnowledgeEvidence,
  linkKnowledgeCitations,
} from '@/components/KnowledgeEvidence'
import type { ChatKnowledgeResult } from '@/lib/types'

function packageData(overrides: Partial<ChatKnowledgeResult> = {}): ChatKnowledgeResult {
  return {
    outcome: 'supported_answer',
    evidence: [
      {
        evidence_id: 'E1',
        title: 'TP53 in cancer',
        year: '2024',
        content: 'TP53 是抑癌基因。',
        url: 'https://pubmed.ncbi.nlm.nih.gov/1/',
      },
    ],
    sources: [
      {
        source_id: 'PMID 1',
        title: 'TP53 in cancer',
        year: '2024',
        url: 'https://pubmed.ncbi.nlm.nih.gov/1/',
        evidence_ids: ['E1'],
      },
    ],
    citations: ['E1'],
    claims: [{ text: 'TP53 是抑癌基因', evidence_ids: ['E1'] }],
    ...overrides,
  }
}

describe('KnowledgeEvidence', () => {
  it('展开后可查看证据、来源与显式论断', () => {
    render(<KnowledgeEvidence data={packageData()} messageId={7} />)

    expect(screen.getByText('文献证据（1 条）')).toBeInTheDocument()
    expect(screen.getAllByText('[E1]')).toHaveLength(2)
    expect(screen.getAllByText('TP53 in cancer')).toHaveLength(2)
    expect(screen.getByText('TP53 是抑癌基因。')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'TP53 in cancer' })[0]).toHaveAttribute(
      'href',
      'https://pubmed.ncbi.nlm.nih.gov/1/',
    )
    expect(screen.getByText('结构化论断')).toBeInTheDocument()
    expect(screen.getByText('TP53 是抑癌基因')).toBeInTheDocument()
  })

  it('没有证据时不渲染空折叠区', () => {
    const { container } = render(
      <KnowledgeEvidence
        data={packageData({ evidence: [], sources: [], claims: [], citations: [] })}
        messageId={7}
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('引用标记链接到本消息的证据锚点，不存在的编号保持文字', () => {
    expect(linkKnowledgeCitations('答 [E1] 与 [E9]', ['E1'], 7)).toBe(
      '答 [E1](#evidence-7-E1) 与 [E9]',
    )
  })

  it('点击引用展开证据折叠区并滚动定位，展开后正文不再截断', () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView

    render(
      <>
        <MarkdownContent text="答 [E1](#evidence-7-E1)" />
        <KnowledgeEvidence data={packageData()} messageId={7} />
      </>,
    )

    const details = screen.getByTestId('knowledge-evidence') as HTMLDetailsElement
    const content = screen.getByText('TP53 是抑癌基因。')
    expect(details.open).toBe(false)
    expect(content.className).toContain('group-open:line-clamp-none')

    fireEvent.click(screen.getByRole('link', { name: 'E1' }))

    expect(details.open).toBe(true)
    expect(scrollIntoView).toHaveBeenCalled()
  })
})

afterEach(() => {
  delete (Element.prototype as unknown as Record<string, unknown>).scrollIntoView
})
