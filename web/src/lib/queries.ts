import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { fetchMessages, fetchSidebar, listSessions } from '@/lib/api'
import type { MessagesResponse, SessionSummary, SidebarResponse } from '@/lib/types'

export const queryKeys = {
  sessions: () => ['sessions'] as const,
  messages: (sessionId: string) => ['messages', sessionId] as const,
  sidebar: (sessionId?: string) => ['sidebar', sessionId ?? ''] as const,
}

export function useSessions(): UseQueryResult<{ sessions: SessionSummary[] }> {
  return useQuery({
    queryKey: queryKeys.sessions(),
    queryFn: listSessions,
    staleTime: 15_000,
  })
}

export function useMessages(
  sessionId: string | null,
): UseQueryResult<MessagesResponse> {
  return useQuery({
    queryKey: queryKeys.messages(sessionId ?? ''),
    queryFn: () => fetchMessages(sessionId as string),
    enabled: Boolean(sessionId),
  })
}

export function useSidebar(
  sessionId?: string,
): UseQueryResult<SidebarResponse> {
  return useQuery({
    queryKey: queryKeys.sidebar(sessionId),
    queryFn: () => fetchSidebar(sessionId),
    refetchInterval: 30_000,
  })
}
