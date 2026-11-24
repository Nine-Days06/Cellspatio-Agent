import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MessageList, EMPTY_FALLBACK } from '@/components/MessageList'
import type {
  KnowledgeQAEvidence,
  KnowledgeQAResult,
  KnowledgeQASource,
  WireMessage,
} from '@/lib/types'

function evidence(overrides: Partial<KnowledgeQAEvidence> = {}): KnowledgeQAEvidence {
  return {
    evidence_id: 'E1',
    chunk_id: 'chunk-1',
    reference_id: '1',
    content: '标题：A paper\n摘要：Some evidence.',
    content_sha256: 'a'.repeat(64),
    file_path: '38947524',
    source_id: 'PMID 38947524',
    title: 'A paper',
    year: '2024',
    kind: 'abstract',
    url: 'https://pubmed.ncbi.nlm.nih.gov/38947524/',
    ...overrides,
  }
}

function source(overrides: Partial<KnowledgeQASource> = {}): KnowledgeQASource {
  return {
    source_id: 'PMID 38947524',
    pmid: '38947524',
    title: 'A paper',
    year: '2024',
    url: 'https://pubmed.ncbi.nlm.nih.gov/38947524/',
    evidence_ids: ['E1'],
    ...overrides,
  }
}

function qa(overrides: Partial<KnowledgeQAResult> = {}): KnowledgeQAResult {
  return {
    answer_id: 'qa-1',
    schema_version: 'knowledge-qa/2.0.0',
    status: 'success',
    outcome: 'supported_answer',
    answer: '该文献支持结论。',
    claims: [{ claim_id: 'C1', text: '主要论断', evidence_ids: ['E1'], basis: 'fact' }],
    limitations: ['仅限摘要'],
    clarifications: [],
    evidence: [evidence()],
    sources: [source()],
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

function renderAnswer(data: KnowledgeQAResult) {
  return render(
    <MessageList messages={[message(data)]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />,
  )
}

function detailsOf(text: string | RegExp): HTMLDetailsElement {
  const target = screen.getByText(text).closest('details')
  expect(target).not.toBeNull()
  return target as HTMLDetailsElement
}

function excerptOf(citation: HTMLElement): HTMLDetailsElement {
  const id = citation.getAttribute('href')!.slice(1)
  const target = document.getElementById(id)
  expect(target).not.toBeNull()
  return target as HTMLDetailsElement
}

function mockScroll(): ReturnType<typeof vi.fn> {
  const scrollIntoView = vi.fn()
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    value: scrollIntoView,
    configurable: true,
  })
  return scrollIntoView
}

const originalScroll = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView')

afterEach(() => {
  vi.restoreAllMocks()
  if (originalScroll) Object.defineProperty(Element.prototype, 'scrollIntoView', originalScroll)
  else delete (Element.prototype as Partial<Element>).scrollIntoView
})

describe('KnowledgeAnswer', () => {
  it('keeps one collapsed source tree with a consistent count', () => {
    renderAnswer(qa())
    const citation = screen.getByRole('link', { name: /查看证据 E1/ })

    expect(screen.getByRole('region', { name: '知识问答证据' })).toBeInTheDocument()
    expect(screen.getAllByText(/来源与检索原文/)).toHaveLength(1)
    expect(screen.getByText('1 篇 · 1 段')).toBeInTheDocument()
    expect(screen.queryByText('查看检索原文')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /证据片段 1 条/ })).not.toBeInTheDocument()

    expect(detailsOf(/来源与检索原文/).open).toBe(false)
    expect(detailsOf('2024').open).toBe(false)
    expect(excerptOf(citation).open).toBe(false)
  })

  it('opens the excerpt tree at every level, focuses the excerpt and scrolls to it', () => {
    const scrollIntoView = mockScroll()
    renderAnswer(qa())
    const citation = screen.getByRole('link', { name: /查看证据 E1/ })
    fireEvent.click(citation)

    expect(detailsOf(/来源与检索原文/).open).toBe(true)
    expect(detailsOf('2024').open).toBe(true)
    const excerpt = excerptOf(citation)
    expect(excerpt.open).toBe(true)
    expect(excerpt.querySelector('summary')).toHaveFocus()
    expect(scrollIntoView).toHaveBeenCalled()
    expect(screen.getAllByText('该文献支持结论。')).toHaveLength(1)
  })

  it('opens the unassigned group when one of its excerpts is cited', () => {
    const scrollIntoView = mockScroll()
    const data = qa({
      claims: [{ claim_id: 'C1', text: '孤立论断', evidence_ids: ['E9'], basis: 'fact' }],
      evidence: [evidence({ evidence_id: 'E9', source_id: 'PMID 404', content: '孤立片段' })],
      sources: [source({ evidence_ids: [] })],
    })
    renderAnswer(data)
    const citation = screen.getByRole('link', { name: /查看证据 E9/ })
    fireEvent.click(citation)

    expect(detailsOf(/来源与检索原文/).open).toBe(true)
    expect(detailsOf(/^未归属来源/).open).toBe(true)
    expect(excerptOf(citation).open).toBe(true)
    expect(scrollIntoView).toHaveBeenCalled()
  })

  it('groups excerpts under their source and keeps repeated sources unique', () => {
    const data = qa({
      evidence: [
        evidence({ evidence_id: 'E1' }),
        evidence({ evidence_id: 'E2', chunk_id: 'chunk-2', content: '第二段证据' }),
        evidence({
          evidence_id: 'E3',
          chunk_id: 'chunk-3',
          source_id: 'PMID 2',
          content: '另一篇证据',
        }),
      ],
      sources: [
        source(),
        source(),
        source({ source_id: 'PMID 2', pmid: '2', title: 'B paper', year: '2023', evidence_ids: ['E3'] }),
      ],
    })
    renderAnswer(data)

    expect(screen.getByText('2 篇 · 3 段')).toBeInTheDocument()
    expect(screen.getAllByText(/· 片段 /)).toHaveLength(3)
    const firstPaper = detailsOf('2024')
    const secondPaper = detailsOf('2023')
    expect(firstPaper).not.toBe(secondPaper)
    expect(within(firstPaper).getAllByText(/· 片段 /)).toHaveLength(2)
    expect(within(firstPaper).getByText('第二段证据')).toBeInTheDocument()
    expect(within(firstPaper).queryByText('另一篇证据')).not.toBeInTheDocument()
    expect(within(secondPaper).getByText('另一篇证据')).toBeInTheDocument()
  })

  it('keeps excerpts without a matching source in an unassigned group', () => {
    const data = qa({
      evidence: [evidence({ evidence_id: 'E9', source_id: 'PMID 404', content: '孤立片段' })],
      sources: [source({ evidence_ids: [] })],
    })
    renderAnswer(data)

    expect(screen.getByText('2 篇 · 1 段')).toBeInTheDocument()
    const unassigned = detailsOf(/^未归属来源/)
    expect(unassigned.open).toBe(false)
    expect(unassigned).toHaveTextContent('孤立片段')
  })

  it('keeps a paper without excerpts and still exposes its link', () => {
    renderAnswer(qa({ evidence: [], sources: [source({ evidence_ids: [] })] }))

    expect(screen.getByText('1 篇 · 0 段')).toBeInTheDocument()
    expect(detailsOf('2024')).toHaveTextContent('A paper')
    expect(screen.getByText('暂无可展示的检索片段。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '原文' })).toHaveAttribute(
      'href',
      'https://pubmed.ncbi.nlm.nih.gov/38947524/',
    )
  })

  it('renders one row of metadata, background and link per paper', () => {
    const data = qa({
      sources: [
        source({
          source_id: 'DOI 10.1038/s41586-024-00001-2',
          pmid: '',
          doi: '10.1038/s41586-024-00001-2',
          species: 'human',
          tissue: 'brain',
          technology: '10x Visium',
          title: 'DOI paper',
          url: 'https://doi.org/10.1038/s41586-024-00001-2',
        }),
      ],
      evidence: [evidence({ source_id: 'DOI 10.1038/s41586-024-00001-2' })],
    })
    renderAnswer(data)

    expect(screen.getByText('[human · brain · 10x Visium]')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '原文' })).toHaveAttribute(
      'href',
      'https://doi.org/10.1038/s41586-024-00001-2',
    )
  })

  it('does not render a source tree for failed or interrupted answers', () => {
    const failed = qa({
      status: 'error',
      outcome: null,
      answer: '知识检索失败，请稍后重试。',
      claims: [],
      evidence: [],
      sources: [],
      error: { stage: 'retrieval', message: '知识检索失败，请稍后重试。' },
    })
    const { unmount } = renderAnswer(failed)
    expect(screen.getByRole('alert')).toHaveTextContent('知识检索失败')
    expect(screen.queryByText(/来源与检索原文/)).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByText(EMPTY_FALLBACK)).not.toBeInTheDocument()
    unmount()

    renderAnswer(
      qa({
        status: 'interrupted',
        outcome: null,
        answer: '本轮已中断，未生成回答。',
        claims: [],
        evidence: [],
        sources: [],
      }),
    )
    expect(screen.getByText('已中断')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('本轮已中断')
    expect(screen.queryByText(/来源与检索原文/)).not.toBeInTheDocument()
    expect(screen.queryByText('未复核')).not.toBeInTheDocument()
  })

  it('does not render javascript: urls as links', () => {
    renderAnswer(qa({ sources: [source({ url: 'javascript:alert(1)' })] }))

    expect(screen.queryByRole('link', { name: '原文' })).not.toBeInTheDocument()
  })

  it('labels inference claims and blocks non-http markdown links in the answer body', async () => {
    const data = qa({
      answer:
        '[点我](javascript:alert(1)) 和 [文献](https://pubmed.ncbi.nlm.nih.gov/1/) ![插图](https://evil.example/i.png)',
      claims: [{ claim_id: 'C1', text: '由证据推出的判断', evidence_ids: ['E1'], basis: 'inference' }],
    })
    renderAnswer(data)

    expect(screen.getByText('推断')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: '文献' })).toHaveAttribute(
      'href',
      'https://pubmed.ncbi.nlm.nih.gov/1/',
    )
    expect(screen.queryByRole('link', { name: '点我' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('保留结构化回答，空消息正文不会把回答隐藏', () => {
    const item = message(qa({ answer: '结构化回答仍可阅读' }))
    item.content = ' '
    render(
      <MessageList messages={[item]} draft="" busy={false} tools={[]} notice={null} softWarn={false} />,
    )
    expect(screen.getByText('结构化回答仍可阅读')).toBeInTheDocument()
  })
})
