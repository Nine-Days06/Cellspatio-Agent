/** 确认卡显式状态机：ChatPage 持有，ConfirmCard 只消费。 */
import type { ConfirmAction, PendingScriptStatus } from './types'

export const ACTION_SCRIPT_CONFIRM = 'script_confirm'
export const ACTION_SCRIPT_CANCEL = 'script_cancel'
export const ACTION_DATA_CONFIRM = 'data_confirm'

/** 运行时可枚举的确认动作，与 ConfirmAction 字面量保持一一对应。 */
export const CONFIRM_ACTIONS = [
  ACTION_SCRIPT_CONFIRM,
  ACTION_SCRIPT_CANCEL,
  ACTION_DATA_CONFIRM,
] as const satisfies readonly ConfirmAction[]

export const STATUS_PENDING = 'pending'
export const STATUS_CONFIRMED = 'confirmed'
export const STATUS_CANCELLED = 'cancelled'
export const STATUS_EXPIRED = 'expired'

/** 脚本卡终态/过程态全集，与 PendingScriptStatus 字面量保持一一对应。 */
export const SCRIPT_STATUSES = [
  STATUS_PENDING,
  STATUS_CONFIRMED,
  STATUS_CANCELLED,
  STATUS_EXPIRED,
] as const satisfies readonly PendingScriptStatus[]

/** 动作 → 脚本卡状态；data_confirm 不改脚本卡，返回 null。 */
export const SCRIPT_STATUS_BY_ACTION = {
  [ACTION_SCRIPT_CONFIRM]: STATUS_CONFIRMED,
  [ACTION_SCRIPT_CANCEL]: STATUS_CANCELLED,
  [ACTION_DATA_CONFIRM]: null,
} as const satisfies Record<ConfirmAction, PendingScriptStatus | null>

export function scriptStatusForAction(action: ConfirmAction): PendingScriptStatus | null {
  return SCRIPT_STATUS_BY_ACTION[action]
}

/** 非 pending 脚本卡的状态文案；pending 由卡片自身的动作区表达。 */
export const STATUS_TEXT: Partial<Record<PendingScriptStatus, string>> = {
  [STATUS_CONFIRMED]: '已确认执行',
  [STATUS_CANCELLED]: '已取消',
  [STATUS_EXPIRED]: '已过期（会话已恢复，需重新发起）',
}

export type ConfirmPhase = 'idle' | 'submitting' | 'succeeded' | 'failed'

export type ConfirmState =
  | { phase: 'idle' }
  | { phase: 'submitting'; action: ConfirmAction }
  | { phase: 'succeeded'; action: ConfirmAction }
  | { phase: 'failed'; action: ConfirmAction; error: string }

export type ConfirmEvent =
  | { type: 'submit'; action: ConfirmAction }
  | { type: 'success' }
  | { type: 'failure'; error: string }
  | { type: 'reset' }

export const initialConfirmState: ConfirmState = { phase: 'idle' }

/**
 * 纯函数：只有 submitting 能转到终态；reset 可丢弃在途请求迟到的终态事件。
 * 重复 submit 保持原状态，与卡片的 locked 形成双保险。
 */
export function confirmReducer(state: ConfirmState, event: ConfirmEvent): ConfirmState {
  switch (event.type) {
    case 'submit':
      return state.phase === 'submitting' ? state : { phase: 'submitting', action: event.action }
    case 'success':
      return state.phase === 'submitting'
        ? { phase: 'succeeded', action: state.action }
        : state
    case 'failure':
      return state.phase === 'submitting'
        ? { phase: 'failed', action: state.action, error: event.error }
        : state
    case 'reset':
      return initialConfirmState
  }
}
