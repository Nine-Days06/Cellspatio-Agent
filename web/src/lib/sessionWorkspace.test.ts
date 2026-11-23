import { describe, expect, it, vi } from 'vitest'
import { SessionWorkspace } from '@/lib/sessionWorkspace'
import type { MessagesResponse } from '@/lib/types'

function history(sessionId: string): MessagesResponse {
  return {
    session: {
      id: sessionId,
      title: '测试会话',
      created_at: '2026-11-23T00:00:00Z',
      updated_at: '2026-11-23T00:00:00Z',
      summary: null,
      summary_upto: 0,
      downloaded_assets: [],
      project_id: null,
      pinned: false,
      archived_at: null,
    },
    messages: [],
    pending_script: null,
    soft_warn: false,
  }
}

describe('SessionWorkspace history loading', () => {
  it('只在该会话首次成功后才消费 restore，失败重试仍恢复', async () => {
    const loader = vi
      .fn<(sessionId: string, restore: boolean) => Promise<MessagesResponse>>()
      .mockRejectedValueOnce(new Error('temporary'))
      .mockResolvedValueOnce(history('s1'))
      .mockResolvedValueOnce(history('s1'))
    const workspace = new SessionWorkspace(loader)

    await expect(workspace.loadMessages('s1')).rejects.toThrow('temporary')
    await workspace.loadMessages('s1')
    await workspace.loadMessages('s1')

    expect(loader.mock.calls).toEqual([
      ['s1', true],
      ['s1', true],
      ['s1', false],
    ])
  })

  it('restore 成功标记按会话隔离', async () => {
    const loader = vi
      .fn<(sessionId: string, restore: boolean) => Promise<MessagesResponse>>()
      .mockResolvedValueOnce(history('s1'))
      .mockResolvedValueOnce(history('s2'))
    const workspace = new SessionWorkspace(loader)

    await workspace.loadMessages('s1')
    await workspace.loadMessages('s2')

    expect(loader.mock.calls).toEqual([
      ['s1', true],
      ['s2', true],
    ])
  })
})

describe('SessionWorkspace history acceptance', () => {
  it('busy 或 session 不匹配时不接纳，结束后可接纳同一响应且只接纳一次', () => {
    const workspace = new SessionWorkspace(vi.fn())
    const data = history('s1')

    expect(
      workspace.acceptsHistory({
        data,
        busy: true,
        currentSessionId: 's1',
      }),
    ).toBe(false)
    expect(
      workspace.acceptsHistory({
        data,
        busy: false,
        currentSessionId: 's2',
      }),
    ).toBe(false)
    expect(
      workspace.acceptsHistory({
        data,
        busy: false,
        currentSessionId: 's1',
      }),
    ).toBe(true)
    expect(
      workspace.acceptsHistory({
        data,
        busy: false,
        currentSessionId: 's1',
      }),
    ).toBe(false)
  })

  it('切回同一缓存会话时开启新接纳边界，可重新 hydrate 同一响应对象', () => {
    const workspace = new SessionWorkspace(vi.fn())
    const data = history('s1')

    expect(
      workspace.acceptsHistory({ data, busy: false, currentSessionId: 's1' }),
    ).toBe(true)
    workspace.activateSession('s2')
    workspace.activateSession('s1')
    expect(
      workspace.acceptsHistory({ data, busy: false, currentSessionId: 's1' }),
    ).toBe(true)
  })
})

describe('SessionWorkspace confirmation acceptance', () => {
  it('按动作锁定同类卡 id，迟到响应在换会话或换卡后失效', () => {
    const workspace = new SessionWorkspace(vi.fn())
    const scriptCard = {
      message_id: 7,
      script: 'x <- 1',
      analysis_type: null,
      params: {},
      method_context: null,
      user_request: '跑分析',
      status: 'pending' as const,
    }
    const dataCard = {
      message_id: 4,
      candidates: [],
      query: 'GSE1',
      message: '候选',
    }

    const script = workspace.captureConfirmation('script_cancel', 's1', {
      scriptCard,
      dataCard,
    })
    expect(script).toEqual({ sessionId: 's1', kind: 'script', messageId: 7 })
    expect(
      workspace.acceptsConfirmation(script, 's1', {
        scriptCard: { ...scriptCard, status: 'cancelled' },
        dataCard,
      }),
    ).toBe(true)
    expect(
      workspace.acceptsConfirmation(script, 's2', {
        scriptCard,
        dataCard,
      }),
    ).toBe(false)
    expect(
      workspace.acceptsConfirmation(script, 's1', {
        scriptCard: { ...scriptCard, message_id: 8 },
        dataCard,
      }),
    ).toBe(false)

    expect(workspace.captureConfirmation('data_confirm', 's1', { scriptCard, dataCard })).toEqual({
      sessionId: 's1',
      kind: 'data',
      messageId: 4,
    })
  })
})
