import { describe, expect, it } from 'vitest'
import { nextSessionAfterDelete } from '@/lib/sessions'
import type { Project, SessionSummary } from '@/lib/types'

function summary(id: string, overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id,
    title: id,
    updated_at: '2026-11-23T00:00:00Z',
    message_count: 0,
    project_id: null,
    pinned: false,
    archived_at: null,
    ...overrides,
  }
}

function project(id: string, archived: boolean): Project {
  return {
    id,
    label: id,
    kind: 'local',
    root_path: '',
    created_at: '2026-11-23T00:00:00Z',
    updated_at: '2026-11-23T00:00:00Z',
    archived_at: archived ? '2026-11-23T00:00:00Z' : null,
    session_count: 0,
  }
}

describe('nextSessionAfterDelete', () => {
  it('返回首个未删除且活动可用的会话，没有剩余时返回 null', () => {
    const sessions = [
      summary('deleted'),
      summary('archived', { archived_at: '2026-11-23T00:00:00Z' }),
      summary('in-archived-project', { project_id: 'p-archived' }),
      summary('next'),
    ]

    expect(
      nextSessionAfterDelete(sessions, [project('p-archived', true)], 'deleted'),
    ).toBe('next')
    expect(nextSessionAfterDelete([summary('deleted')], [], 'deleted')).toBeNull()
  })
})
