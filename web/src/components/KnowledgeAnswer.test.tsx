import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MessageList, EMPTY_FALLBACK } from '@/components/MessageList'
import type { KnowledgeQAResult, WireMessage } from '@/lib/types'

function qa(overrides: Partial<KnowledgeQAResult> = {}): KnowledgeQAResult {
  return {
    answer_id: 'qa-1',
    schema_version: 'knowledge-qa/1.0.0',
    status: 'success',
    outcome: 'supported_answer',
    answer: '该文献支持结论。',
    claims: [{ claim_id: 'C1', text: '主要论断', evidence_ids: ['E1'], basis: 'fact' }],
    limitations: ['仅限摘要'],
    clarifications: [],
    evidence: [
      {
        evidence_id: 'E1',
        chunk_id: 'chunk-1',
        reference_id: '1',
        content: '标题：A paper\n摘要：Some evidence.',
        content_sha256: 'abc',
        file_path: '38947524',
        source_id: 'PMID 38947524',
        title: 'A paper',
        year: '2024',
        kind: 'abstract',
        url: 'https://pubmed.ncbi.nlm.nih.gov/38947524/',
      },
    ],
    sources: [
      {
        source_id: 'PMID 38947524',
        pmid: '38947524',
        title: 'A paper',
        year: '2024',
        url: 'https://pubmed.ncbi.nlm.nih.gov/38947524/',
        evidence_ids: ['E1'],
      },
    ],
    review_status: 'unreviewed',
    ...overrides,
  }
}

function message(data: KnowledgeQAResult): WireMessage {
  return {
    id: 1,
    session_id: 's1',
    seq: 1,
    role: 'assistant',
    content: data.answer,
    timestamp: '',
    pending_script: null,
    results: { knowledge_qa: data },
  }
}

describe('KnowledgeAnswer', () => {
  it('opens the exact cited excerpt, focuses its summary and renders the answer only once', () => {
    render(<MessageList messages={[message(qa())]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)
    const citation = screen.getByRole('link', { name: /查看证据 E1/ })
    const target = document.getElementById(citation.getAttribute('href')!.slice(1)) as HTMLDetailsElement
    expect(target.open).toBe(false)
    fireEvent.click(citation)
    expect(target.open).toBe(true)
    expect(target.querySelector('summary')).toHaveFocus()
    expect(screen.getAllByText('该文献支持结论。')).toHaveLength(1)
    expect(target.querySelector('details')?.open).toBe(false)
  })
  it('shows outcome, citation, source link and unreviewed status', () => {
    render(<MessageList messages={[message(qa())]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)

    expect(screen.getByRole('region', { name: '知识问答证据' })).toBeInTheDocument()
    expect(screen.getByText('有证据支持')).toBeInTheDocument()
    expect(screen.getByText('未复核')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /查看证据 E1/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '原文' })).toHaveAttribute(
      'href',
      'https://pubmed.ncbi.nlm.nih.gov/38947524/',
    )
  })

  it('renders a failed answer without exposing an unsafe link', () => {
    const data = qa({
      status: 'error',
      outcome: null,
      answer: '知识检索失败，请稍后重试。',
      claims: [],
      evidence: [],
      sources: [],
      error: { stage: 'retrieval', message: '知识检索失败，请稍后重试。' },
    })
    render(<MessageList messages={[message(data)]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)

    expect(screen.getByRole('alert')).toHaveTextContent('知识检索失败')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByText(EMPTY_FALLBACK)).not.toBeInTheDocument()
  })

  it('does not render javascript: urls as links', () => {
    const data = qa({
      sources: [
        {
          source_id: 'PMID 1',
          pmid: '1',
          title: '',
          year: '',
          url: 'javascript:alert(1)',
          evidence_ids: ['E1'],
        },
      ],
    })
    render(<MessageList messages={[message(data)]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)

    expect(screen.queryByRole('link', { name: '原文' })).not.toBeInTheDocument()
  })

  it('shows species/tissue/technology background when the source carries it', () => {
    const data = qa({
      sources: [
        {
          source_id: 'DOI 10.1038/s41586-024-00001-2',
          pmid: '',
          doi: '10.1038/s41586-024-00001-2',
          species: 'human',
          tissue: 'brain',
          technology: '10x Visium',
          title: 'DOI paper',
          year: '2024',
          url: 'https://doi.org/10.1038/s41586-024-00001-2',
          evidence_ids: ['E1'],
        },
      ],
    })
    render(<MessageList messages={[message(data)]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)

    expect(screen.getByText('[human · brain · 10x Visium]')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '原文' })).toHaveAttribute(
      'href',
      'https://doi.org/10.1038/s41586-024-00001-2',
    )
  })
  it('labels inference claims and blocks non-http markdown links in the answer body', async () => {
    const data = qa({
      answer:
        '[点我](javascript:alert(1)) 和 [文献](https://pubmed.ncbi.nlm.nih.gov/1/) ![插图](https://evil.example/i.png)',
      claims: [{ claim_id: 'C1', text: '由证据推出的判断', evidence_ids: ['E1'], basis: 'inference' }],
    })
    render(<MessageList messages={[message(data)]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)

    expect(screen.getByText('推断')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: '文献' })).toHaveAttribute(
      'href',
      'https://pubmed.ncbi.nlm.nih.gov/1/',
    )
    expect(screen.queryByRole('link', { name: '点我' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders an interrupted turn without unreviewed badge', () => {
    const data = qa({
      status: 'interrupted',
      outcome: null,
      answer: '本轮已中断，未生成回答。',
      claims: [],
      evidence: [],
      sources: [],
    })
    render(<MessageList messages={[message(data)]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)

    expect(screen.getByText('已中断')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('本轮已中断')
    expect(screen.queryByText('未复核')).not.toBeInTheDocument()
  })

  it('保留结构化回答，空消息正文不会把回答隐藏', () => {
    const item = message(qa({ answer: '结构化回答仍可阅读' }))
    item.content = ' '
    render(<MessageList messages={[item]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />)
    expect(screen.getByText('结构化回答仍可阅读')).toBeInTheDocument()
  })
})

