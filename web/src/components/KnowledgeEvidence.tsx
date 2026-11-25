import type { ChatKnowledgeResult } from '@/lib/types'
import { dedupeSources } from '@/lib/knowledge'

/** 稳定的证据锚点：同一页面多条消息各自引用 E1 时不串消息。 */
export function knowledgeAnchor(messageId: number, evidenceId: string): string {
  return `evidence-${messageId}-${evidenceId}`
}

/** 把正文里的 [E1] 标记链接到本消息的证据条目；已有链接保持原样。 */
export function linkKnowledgeCitations(
  text: string,
  citations: string[],
  messageId: number,
): string {
  const cited = new Set(citations)
  return text.replace(/\[(E\d+)\](?!\()/g, (marker, evidenceId: string) =>
    cited.has(evidenceId)
      ? `[${evidenceId}](#${knowledgeAnchor(messageId, evidenceId)})`
      : marker,
  )
}

/**
 * 本轮文献证据折叠区：只展示已落库的 evidence/sources/claims 事实。
 * 历史刷新后直接从 message.results.knowledge_evidence 重建，不依赖内存状态。
 */
export function KnowledgeEvidence({
  data,
  messageId,
}: {
  data: ChatKnowledgeResult
  messageId: number
}) {
  const evidence = data.evidence
  const sources = dedupeSources(data.sources)
  const claims = data.claims
  if (evidence.length === 0 && sources.length === 0 && claims.length === 0) return null

  return (
    <details
      data-testid="knowledge-evidence"
      className="group mt-3 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs"
    >
      <summary className="cursor-pointer select-none text-muted-foreground">
        文献证据（{evidence.length} 条）
      </summary>
      {evidence.length > 0 && (
        <ul className="mt-2 space-y-2">
          {evidence.map((item) => (
            <li
              key={item.evidence_id}
              id={knowledgeAnchor(messageId, item.evidence_id)}
              className="min-w-0"
            >
              <div className="flex flex-wrap items-baseline gap-1.5">
                <span className="font-mono text-[11px] text-primary">[{item.evidence_id}]</span>
                <span className="font-medium">{item.title || '证据'}</span>
                {item.year && <span className="text-muted-foreground">{item.year}</span>}
                {item.url && (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary underline-offset-2 hover:underline"
                  >
                    原文
                  </a>
                )}
              </div>
              {item.content && (
                <p className="mt-1 line-clamp-4 whitespace-pre-wrap break-words text-muted-foreground group-open:line-clamp-none">
                  {item.content}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {sources.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <div className="mb-1 text-muted-foreground">来源</div>
          <ul className="space-y-1">
            {sources.map((source) => (
              <li key={source.source_id} className="truncate">
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary underline-offset-2 hover:underline"
                  >
                    {source.title || source.source_id}
                  </a>
                ) : (
                  (source.title || source.source_id)
                )}
                {source.year && `（${source.year}）`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {claims.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <div className="mb-1 text-muted-foreground">结构化论断</div>
          <ul className="space-y-1">
            {claims.map((claim) => (
              <li key={`${claim.text}-${claim.evidence_ids.join('-')}`}>
                {claim.text}
                {claim.evidence_ids.length > 0 && (
                  <span className="ml-1 font-mono text-[11px] text-primary">
                    [{claim.evidence_ids.join('][')}]
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </details>
  )
}
