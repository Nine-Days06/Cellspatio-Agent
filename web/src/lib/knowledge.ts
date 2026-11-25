/** 同一文献可能出现在多段证据里；展示层按 source_id 只保留最后一条。 */
export function dedupeSources<T extends { source_id: string }>(sources: readonly T[]): T[] {
  return Array.from(new Map(sources.map((source) => [source.source_id, source])).values())
}

/** 分页盘点按来源只留一张证据卡；无来源 ID 时退回按证据 ID 去重。 */
export function dedupeEvidenceBySource<T extends { evidence_id: string; source_id?: string }>(
  evidence: readonly T[],
): T[] {
  const seen = new Set<string>()
  return evidence.filter((item) => {
    const key = item.source_id || item.evidence_id
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}
