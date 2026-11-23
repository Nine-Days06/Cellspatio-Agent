import { fetchMessages } from '@/lib/api'
import type {
  ConfirmAction,
  DataCardPayload,
  MessagesResponse,
  ScriptCardPayload,
} from '@/lib/types'

export type HistoryLoader = (
  sessionId: string,
  restore: boolean,
) => Promise<MessagesResponse>

export interface HistoryAcceptance {
  data: MessagesResponse
  busy: boolean
  currentSessionId: string | null
}

export interface SessionCards {
  scriptCard: ScriptCardPayload | null
  dataCard: DataCardPayload | null
}

export interface ConfirmationIdentity {
  sessionId: string
  kind: 'script' | 'data'
  messageId: number | null
}

/** 活动会话的异步结果接纳规则。 */
export class SessionWorkspace {
  private readonly restoredSessions = new Set<string>()
  private activeSessionId: string | null = null
  private acceptedHistory: MessagesResponse | null = null

  constructor(private readonly loadHistory: HistoryLoader) {}

  /** 开始一次会话激活；切回缓存会话时允许重新接纳其历史。 */
  activateSession(sessionId: string | null): void {
    if (sessionId === this.activeSessionId) return
    this.activeSessionId = sessionId
    this.acceptedHistory = null
  }

  /** 每会话首次成功读取前使用 restore；失败时不消费标记。 */
  async loadMessages(sessionId: string): Promise<MessagesResponse> {
    const restore = !this.restoredSessions.has(sessionId)
    const data = await this.loadHistory(sessionId, restore)
    this.restoredSessions.add(sessionId)
    return data
  }

  /** 仅允许匹配会话、非 busy 且本次激活尚未接纳的响应落地。 */
  acceptsHistory({ data, busy, currentSessionId }: HistoryAcceptance): boolean {
    if (busy || currentSessionId !== data.session.id || this.acceptedHistory === data) {
      return false
    }
    this.activeSessionId = currentSessionId
    this.acceptedHistory = data
    return true
  }

  /** 锁定提交动作对应的会话、卡类型与卡 id。 */
  captureConfirmation(
    action: ConfirmAction,
    sessionId: string,
    { scriptCard, dataCard }: SessionCards,
  ): ConfirmationIdentity {
    const kind = action === 'data_confirm' ? 'data' : 'script'
    const card = kind === 'data' ? dataCard : scriptCard
    return { sessionId, kind, messageId: card?.message_id ?? null }
  }

  /** 仅在提交身份仍等于当前活动会话与同类卡时接纳确认结果。 */
  acceptsConfirmation(
    identity: ConfirmationIdentity,
    currentSessionId: string | null,
    { scriptCard, dataCard }: SessionCards,
  ): boolean {
    const card = identity.kind === 'data' ? dataCard : scriptCard
    return (
      currentSessionId === identity.sessionId &&
      (card?.message_id ?? null) === identity.messageId
    )
  }
}

export const sessionWorkspace = new SessionWorkspace((sessionId, restore) =>
  fetchMessages(sessionId, restore),
)
