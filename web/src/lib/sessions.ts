import type { Project, SessionSummary } from '@/lib/types'

/** 已归档项目 id：其下会话随项目整组隐藏。 */
export function collectArchivedProjectIds(projects: Project[]): Set<string> {
  return new Set(
    projects.filter((project) => project.archived_at).map((project) => project.id),
  )
}

export function isActiveSession(
  session: SessionSummary,
  archivedProjectIds: Set<string>,
): boolean {
  return (
    !session.archived_at &&
    !(session.project_id && archivedProjectIds.has(session.project_id))
  )
}
