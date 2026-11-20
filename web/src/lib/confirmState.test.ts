import { describe, expect, it } from 'vitest'
import { confirmReducer, initialConfirmState } from '@/lib/confirmState'

describe('confirmReducer', () => {
  it('submit 进入 submitting 并记录 action', () => {
    expect(confirmReducer(initialConfirmState, { type: 'submit', action: 'script_confirm' })).toEqual(
      { phase: 'submitting', action: 'script_confirm' },
    )
  })

  it('submitting 收到 success → succeeded，保留 action', () => {
    const submitting = confirmReducer(initialConfirmState, {
      type: 'submit',
      action: 'data_confirm',
    })
    expect(confirmReducer(submitting, { type: 'success' })).toEqual({
      phase: 'succeeded',
      action: 'data_confirm',
    })
  })

  it('submitting 收到 failure → failed，携带 error', () => {
    const submitting = confirmReducer(initialConfirmState, {
      type: 'submit',
      action: 'script_cancel',
    })
    expect(confirmReducer(submitting, { type: 'failure', error: '该会话已有任务在进行' })).toEqual({
      phase: 'failed',
      action: 'script_cancel',
      error: '该会话已有任务在进行',
    })
  })

  it('submitting 中重复 submit 保持原状态', () => {
    const submitting = confirmReducer(initialConfirmState, {
      type: 'submit',
      action: 'script_confirm',
    })
    expect(
      confirmReducer(submitting, { type: 'submit', action: 'script_cancel' }),
    ).toEqual(submitting)
  })

  it('reset 回到 idle，并忽略旧请求迟到的终态', () => {
    const reset = confirmReducer(
      { phase: 'submitting', action: 'script_confirm' },
      { type: 'reset' },
    )
    expect(reset).toEqual(initialConfirmState)
    expect(confirmReducer(reset, { type: 'success' })).toEqual(initialConfirmState)
    expect(confirmReducer(reset, { type: 'failure', error: '过期错误' })).toEqual(
      initialConfirmState,
    )
  })
})
