type AttemptLike = { status: string }

export function stageActivity({ attempts, executing, downstream }: {
  attempts?: readonly AttemptLike[] | null
  executing: boolean
  downstream?: boolean | readonly string[] | null
}): { selfRunning: boolean; downstreamRunning: boolean; running: boolean } {
  const selfRunning = executing || (attempts?.some((attempt) => attempt.status === 'running') ?? false)
  const downstreamRunning = Array.isArray(downstream) ? downstream.length > 0 : !!downstream
  return { selfRunning, downstreamRunning, running: selfRunning || downstreamRunning }
}
