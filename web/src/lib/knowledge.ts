/** 同一文献可能出现在多段证据里；展示层按 source_id 只保留最后一条。 */
export function dedupeSources<T extends { source_id: string }>(sources: readonly T[]): T[] {
  return Array.from(new Map(sources.map((source) => [source.source_id, source])).values())
}
