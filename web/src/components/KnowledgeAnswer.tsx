import { useId, useRef } from 'react'
import { MarkdownMessage } from '@/components/MarkdownMessage'
import type {
  KnowledgeQAEvidence,
  KnowledgeQAOutcome,
  KnowledgeQAResult,
} from '@/lib/types'
import { dedupeSources } from '@/lib/knowledge'
import { cn } from '@/lib/utils'

const OUTCOME_LABEL: Record<KnowledgeQAOutcome, string> = {
  supported_answer: '有证据支持',
  insufficient_evidence: '证据不足',
  needs_context: '需要补充背景',
  context_mismatch: '背景不匹配',
  conflicting_evidence: '来源冲突',
  out_of_scope: '超出问答范围',
}

function safeHttpUrl(url: string): string | undefined {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.toString() : undefined
  } catch {
    return undefined
  }
}

/** 论断与来源同屏阅读；引用展开确切原文，不执行新检索。 */
export function KnowledgeAnswer({ data, text }: { data: KnowledgeQAResult; text?: string }) {
  const detailPrefix = useId()
  const excerptsRef = useRef<HTMLDetailsElement>(null)
  const evidenceRefs = useRef(new Map<string, HTMLDetailsElement>())
  const outcome = data.outcome ? OUTCOME_LABEL[data.outcome] : null
  const sources = dedupeSources(data.sources)

  const renderEvidence = (item: KnowledgeQAEvidence) => (
    <details
      key={item.evidence_id}
      id={`${detailPrefix}-${item.evidence_id}`}
      className="evidence-item min-w-0"
      ref={(element) => {
        if (element) evidenceRefs.current.set(item.evidence_id, element)
        else evidenceRefs.current.delete(item.evidence_id)
      }}
    >
      <summary className="cursor-pointer break-words text-sm text-primary">
        {item.evidence_id} · 片段 {item.chunk_id}
      </summary>
      <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7">{item.content}</p>
      <dl className="mt-3 space-y-1 text-xs text-muted-foreground">
        <div>
          <dt className="inline">片段编号</dt>
          <dd className="ml-1 inline break-all">{item.chunk_id}</dd>
        </div>
        <div>
          <dt className="inline">文件路径</dt>
          <dd className="ml-1 inline break-all">{item.file_path || '（未记录）'}</dd>
        </div>
        <div>
          <dt className="inline">SHA256</dt>
          <dd className="ml-1 inline break-all">{item.content_sha256}</dd>
        </div>
      </dl>
    </details>
  )

  return (
    <section aria-label="知识问答证据" className="knowledge-answer min-w-0 space-y-5 text-[15px] leading-7">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span
          className={cn(
            'rounded-full border px-2 py-0.5',
            data.status === 'error'
              ? 'border-destructive/40 text-destructive'
              : data.status === 'interrupted'
                ? 'border-amber-500/40 text-[color:var(--review)]'
                : 'border-border text-muted-foreground',
          )}
        >
          {data.status === 'error'
            ? `问答失败 · ${data.error?.stage ?? 'unknown'}`
            : data.status === 'interrupted'
              ? '已中断'
              : outcome}
        </span>
      </div>

      {data.status === 'error' ? (
        <p role="alert" className="text-destructive">{data.answer}</p>
      ) : data.status === 'interrupted' ? (
        <p role="status" className="text-muted-foreground">{data.answer}</p>
      ) : (
        <div className="knowledge-answer-body min-w-0 space-y-6">
          <MarkdownMessage text={text?.trim() ? text : data.answer} restrictExternalContent className="reading-prose" />
          {data.claims.length > 0 && (
            <div className="knowledge-claims">
              <h4 className="mb-3 text-base font-semibold">主要论断</h4>
              <ul className="divide-y divide-border">
                {data.claims.map((claim) => (
                  <li key={claim.claim_id} className="py-3">
                    {claim.basis && (
                      <span className="mr-2 rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                        {claim.basis === 'inference' ? '推断' : '事实'}
                      </span>
                    )}
                    <span>{claim.text}</span>
                    <span className="text-muted-foreground">
                      {claim.evidence_ids.map((id) => {
                        const target = data.evidence.find((item) => item.evidence_id === id)
                        return (
                          <a
                            key={id}
                            href={`#${detailPrefix}-${id}`}
                            className="ml-2 inline-flex min-h-8 min-w-8 items-center justify-center rounded border border-border px-2 text-xs text-primary hover:bg-muted max-md:min-h-11 max-md:min-w-11"
                            aria-label={`查看证据 ${id}${target?.source_id ? `：${target.source_id}` : ''}`}
                            onClick={(event) => {
                              const detail = evidenceRefs.current.get(id)
                              if (!detail) return
                              event.preventDefault()
                              if (excerptsRef.current) excerptsRef.current.open = true
                              detail.open = true
                              detail.querySelector('summary')?.focus()
                              detail.scrollIntoView?.({ block: 'nearest' })
                            }}
                          >
                            {id}
                          </a>
                        )
                      })}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.clarifications.length > 0 && (
            <div>
              <h4 className="mb-2 font-semibold">需要补充</h4>
              <ul className="list-disc space-y-0.5 pl-5">
                {data.clarifications.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}

          {data.limitations.length > 0 && (
            <div className="knowledge-limitations border-l-2 border-[color:var(--review)] bg-[color:var(--review-surface)]/50 py-3 pl-4 pr-3">
              <h4 className="mb-2 font-semibold">限制</h4>
              <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
                {data.limitations.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}

          {data.evidence.length > 0 && (
            <details ref={excerptsRef} className="evidence-excerpts min-w-0" aria-label="检索原文">
              <summary className="cursor-pointer break-words text-base font-semibold">
                检索原文
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {data.evidence.length} 段
                </span>
              </summary>
              <div className="mt-3 min-w-0 space-y-2">{data.evidence.map(renderEvidence)}</div>
            </details>
          )}
          {sources.length > 0 && (
            <div className="evidence-source-list min-w-0">
              <h4 className="mb-2 text-base font-semibold">来源</h4>
              <ul className="space-y-2">
                {sources.map((source) => {
                  const href = safeHttpUrl(source.url)
                  const background = [source.species, source.tissue, source.technology]
                    .filter(Boolean)
                    .join(' · ')
                  return (
                    <li key={source.source_id} className="break-words text-sm">
                      <span className="text-primary">{source.source_id}</span>
                      {source.year && (
                        <span className="ml-1 text-muted-foreground">{source.year}</span>
                      )}
                      {source.title && <p>{source.title}</p>}
                      {background && (
                        <p className="text-xs text-muted-foreground">[{background}]</p>
                      )}
                      {href && (
                        <a
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex min-h-8 items-center text-primary underline underline-offset-4 max-md:min-h-11"
                        >
                          原文
                        </a>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
