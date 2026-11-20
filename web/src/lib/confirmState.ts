/** 确认卡显式状态机：ChatPage 持有，ConfirmCard 只消费。 */
import type { ConfirmAction } from './types'

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
