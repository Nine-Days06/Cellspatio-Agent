import { useId, useRef } from 'react'
import { MarkdownMessage } from '@/components/MarkdownMessage'
import type { KnowledgeQAOutcome, KnowledgeQAResult } from '@/lib/types'
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

/** 论断与来源并排阅读；引用展开确切原文，不执行新检索。 */
export function KnowledgeAnswer({ data, text }: { data: KnowledgeQAResult; text?: string }) {
  const detailPrefix = useId()
  const evidenceRefs = useRef(new Map<string, HTMLDetailsElement>())
  const outcome = data.outcome ? OUTCOME_LABEL[data.outcome] : null

  return (
    <section aria-label="知识问答证据" className="knowledge-answer min-w-0 space-y-5 text-[15px] leading-7">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span
          className={cn(
            'rounded-full border px-2 py-0.5',
            data.status === 'error'
              ? 'border-destructive/40 text-destructive'
              : data.status === 'interrupted'
                ? 'border-amber-500/40 text-amber-700'
                : 'border-border text-muted-foreground',
          )}
        >
          {data.status === 'error'
            ? `问答失败 · ${data.error?.stage ?? 'unknown'}`
            : data.status === 'interrupted'
              ? '已中断'
              : outcome}
        </span>
        {data.status !== 'interrupted' && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[color:var(--review)]">未复核</span>}
        {data.evidence.length > 0 && (
          <a href={`#${detailPrefix}-excerpts`} className="inline-flex min-h-9 items-center text-primary underline underline-offset-4 max-md:min-h-11">证据片段 {data.evidence.length} 条</a>
        )}
      </div>

      {data.status === 'error' ? (
        <p role="alert" className="text-destructive">{data.answer}</p>
      ) : data.status === 'interrupted' ? (
        <p role="status" className="text-muted-foreground">{data.answer}</p>
      ) : (
        <div className="knowledge-grid">
          <div className="min-w-0 space-y-6">
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
            <div className="knowledge-limitations border-l-2 border-[color:var(--review)] bg-amber-50/50 py-3 pl-4 pr-3">
              <h4 className="mb-2 font-semibold">限制</h4>
              <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
                {data.limitations.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}
          {data.evidence.length > 0 && (
            <section id={`${detailPrefix}-excerpts`} aria-label="检索原文" className="evidence-excerpts space-y-2">
              <h4 className="text-base font-semibold">查看检索原文</h4>
              {data.evidence.map((item) => (
                <details key={item.evidence_id} id={`${detailPrefix}-${item.evidence_id}`} className="border-b border-border py-3"
                  ref={(element) => { if (element) evidenceRefs.current.set(item.evidence_id, element); else evidenceRefs.current.delete(item.evidence_id) }}>
                  <summary className="cursor-pointer text-sm text-primary">{item.evidence_id} · {item.source_id}{item.title ? ` · ${item.title}` : ''}</summary>
                  <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7">{item.content}</p>
                  <details className="mt-3 text-xs text-muted-foreground"><summary>片段身份与校验</summary><p className="mt-2 break-all">片段 {item.chunk_id} · 路径 {item.file_path}</p><p className="break-all">SHA256 {item.content_sha256}</p></details>
                </details>
              ))}
            </section>
          )}
          </div>

          {data.sources.length > 0 && (
            <aside aria-label="回答来源" className="min-w-0 border-t border-border pt-4 min-[1101px]:border-t-0 min-[1101px]:border-l min-[1101px]:pl-5 min-[1101px]:pt-0">
              <h4 className="mb-3 text-base font-semibold">来源 <span className="ml-1 text-xs font-normal text-muted-foreground">{data.sources.length} 篇</span></h4>
              <ul className="divide-y divide-border text-sm">
                {data.sources.map((source) => {
                  const href = safeHttpUrl(source.url)
                  const background = [source.species, source.tissue, source.technology]
                    .filter(Boolean)
                    .join(' · ')
                  return (
                    <li key={source.source_id} className="space-y-1 py-3">
                      <p className="font-medium">{source.source_id} {source.year && <span className="ml-1 font-normal text-muted-foreground">{source.year}</span>}</p>
                      {source.title && <p>{source.title}</p>}
                      {background && <p className="text-xs text-muted-foreground">[{background}]</p>}
                      {href && (
                        <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-8 items-center text-primary underline underline-offset-4 max-md:min-h-11">
                          原文
                        </a>
                      )}
                    </li>
                  )
                })}
              </ul>
            </aside>
          )}
        </div>
      )}
    </section>
  )
}
