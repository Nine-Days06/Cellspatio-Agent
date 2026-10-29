import { useId } from 'react'
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

/** 知识问答的证据补充区：正文由 MarkdownMessage 渲染，这里只放论断引用与原文。 */
export function KnowledgeAnswer({ data }: { data: KnowledgeQAResult }) {
  const detailPrefix = useId()
  const outcome = data.outcome ? OUTCOME_LABEL[data.outcome] : null

  return (
    <section aria-label="知识问答证据" className="mt-3 space-y-3 border-t border-border pt-3 text-sm">
      <div className="flex flex-wrap items-center gap-2 text-xs">
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
        {data.status !== 'interrupted' && <span className="text-muted-foreground">未复核</span>}
        {data.evidence.length > 0 && (
          <span className="text-muted-foreground">证据片段 {data.evidence.length} 条</span>
        )}
      </div>

      {data.status === 'error' ? (
        <p role="alert" className="text-destructive">{data.answer}</p>
      ) : data.status === 'interrupted' ? (
        <p role="status" className="text-muted-foreground">{data.answer}</p>
      ) : (
        <>
          {data.claims.length > 0 && (
            <div>
              <h4 className="mb-1 font-medium">主要论断</h4>
              <ul className="space-y-1">
                {data.claims.map((claim) => (
                  <li key={claim.claim_id} className="flex flex-wrap gap-1">
                    {claim.basis && (
                      <span className="rounded bg-muted px-1 text-[10px] leading-5 text-muted-foreground">
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
                            className="ml-1 rounded border border-border px-1 text-xs hover:bg-muted"
                            aria-label={`查看证据 ${id}${target?.source_id ? `：${target.source_id}` : ''}`}
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
              <h4 className="mb-1 font-medium">需要补充</h4>
              <ul className="list-disc space-y-0.5 pl-5">
                {data.clarifications.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}

          {data.limitations.length > 0 && (
            <div>
              <h4 className="mb-1 font-medium">限制</h4>
              <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
                {data.limitations.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}

          {data.sources.length > 0 && (
            <div>
              <h4 className="mb-1 font-medium">来源</h4>
              <ul className="space-y-1 text-xs">
                {data.sources.map((source) => {
                  const href = safeHttpUrl(source.url)
                  const background = [source.species, source.tissue, source.technology]
                    .filter(Boolean)
                    .join(' · ')
                  return (
                    <li key={source.source_id} className="flex flex-wrap gap-1">
                      <span className="font-medium">{source.source_id}</span>
                      {source.year && <span className="text-muted-foreground">{source.year}</span>}
                      {source.title && <span>{source.title}</span>}
                      {background && <span className="text-muted-foreground">[{background}]</span>}
                      {href && (
                        <a href={href} target="_blank" rel="noopener noreferrer" className="underline">
                          原文
                        </a>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}

          {data.evidence.length > 0 && (
            <details>
              <summary className="cursor-pointer text-xs">查看检索原文</summary>
              <ol className="mt-2 space-y-2">
                {data.evidence.map((item) => (
                  <li key={item.evidence_id} id={`${detailPrefix}-${item.evidence_id}`}>
                    <p className="text-xs font-medium">
                      {item.evidence_id} · {item.source_id}
                      {item.title ? ` · ${item.title}` : ''}
                    </p>
                    <p className="mt-0.5 whitespace-pre-wrap text-xs text-muted-foreground">{item.content}</p>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </>
      )}
    </section>
  )
}
