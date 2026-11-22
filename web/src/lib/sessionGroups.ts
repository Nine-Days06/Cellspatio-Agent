import type { Project, SessionSummary } from '@/lib/types'
import { collectArchivedProjectIds, isActiveSession } from '@/lib/sessions'

export interface SessionGroups {
  query: string
  visibleSessions: SessionSummary[]
  activeSessions: SessionSummary[]
  archivedSessions: SessionSummary[]
  activeProjects: Project[]
  archivedProjects: Project[]
  pinnedSessions: SessionSummary[]
  ungroupedSessions: SessionSummary[]
  projectSessions: (projectId: string) => SessionSummary[]
}

/** 搜索过滤与侧栏分组；纯派生，不改变输入顺序。 */
export function groupSessions(
  sessions: SessionSummary[],
  projects: Project[],
  search: string,
): SessionGroups {
  const archivedProjectIds = collectArchivedProjectIds(projects)
  const query = search.trim().toLocaleLowerCase()
  const visibleSessions = sessions.filter((session) =>
    session.title.toLocaleLowerCase().includes(query),
  )
  const activeSessions = visibleSessions.filter((session) =>
    isActiveSession(session, archivedProjectIds),
  )
  const archivedSessions = visibleSessions.filter(
    (session) =>
      Boolean(session.archived_at) &&
      !(session.project_id && archivedProjectIds.has(session.project_id)),
  )
  const activeProjects = projects.filter((project) => !project.archived_at)
  const archivedProjects = projects.filter(
    (project) =>
      project.archived_at &&
      (!query || project.label.toLocaleLowerCase().includes(query)),
  )
  return {
    query,
    visibleSessions,
    activeSessions,
    archivedSessions,
    activeProjects,
    archivedProjects,
    pinnedSessions: activeSessions.filter((session) => session.pinned),
    ungroupedSessions: activeSessions.filter(
      (session) => !session.pinned && !session.project_id,
    ),
    projectSessions: (projectId) =>
      activeSessions.filter(
        (session) => !session.pinned && session.project_id === projectId,
      ),
  }
}
