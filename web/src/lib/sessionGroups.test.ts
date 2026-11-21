import { describe, expect, it } from 'vitest'

import { groupSessions } from './sessionGroups'
import type { Project, SessionSummary } from './types'

function session(overrides: Partial<SessionSummary>): SessionSummary {
  return {
    id: 's',
    title: '会话',
    updated_at: '2026-01-01',
    message_count: 1,
    project_id: null,
    pinned: false,
    archived_at: null,
    ...overrides,
  }
}

function project(overrides: Partial<Project>): Project {
  return {
    id: 'p',
    label: '项目',
    kind: 'local',
    root_path: null,
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
    archived_at: null,
    session_count: 0,
    ...overrides,
  }
}

describe('groupSessions', () => {
  it('把置顶、项目与未归类分开，并保留输入顺序', () => {
    const projects = [project({ id: 'a', label: 'Alpha' })]
    const sessions = [
      session({ id: 'u1', title: '未归类' }),
      session({ id: 'p1', title: '项目内', project_id: 'a' }),
      session({ id: 'pin', title: '置顶', pinned: true, project_id: 'a' }),
    ]
    const groups = groupSessions(sessions, projects, '')
    expect(groups.pinnedSessions.map((item) => item.id)).toEqual(['pin'])
    expect(groups.ungroupedSessions.map((item) => item.id)).toEqual(['u1'])
    expect(groups.projectSessions('a').map((item) => item.id)).toEqual(['p1'])
  })

  it('搜索过滤标题，并隐藏已归档项目', () => {
    const projects = [
      project({ id: 'old', label: '旧项目', archived_at: '2026-01-01' }),
      project({ id: 'new', label: '新项目' }),
    ]
    const sessions = [
      session({ id: 'keep', title: '肝脏 分析', project_id: 'new' }),
      session({ id: 'drop', title: '肾脏 分析' }),
      session({ id: 'old', title: '肝脏 归档', archived_at: '2026-01-01' }),
    ]
    const groups = groupSessions(sessions, projects, ' 肝脏 ')
    expect(groups.query).toBe('肝脏')
    expect(groups.activeSessions.map((item) => item.id)).toEqual(['keep'])
    expect(groups.archivedSessions.map((item) => item.id)).toEqual(['old'])
    expect(groups.activeProjects.map((item) => item.id)).toEqual(['new'])
  })
})
