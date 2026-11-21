import { describe, expect, it } from 'vitest'
import type {
  ConfirmRequestBody,
  DataConfirmRequest,
  ScriptConfirmRequest,
} from './types'

const scriptBody = {
  session_id: 's1',
  action: 'script_cancel',
} satisfies ScriptConfirmRequest

const dataBody = {
  session_id: 's1',
  action: 'data_confirm',
  source: 'geo',
  asset_id: 'GSE1',
} satisfies DataConfirmRequest

describe('ConfirmRequestBody 判别联合', () => {
  it('两个分支均可赋给联合，并按 action 收窄', () => {
    const bodies = [scriptBody, dataBody] satisfies ConfirmRequestBody[]
    const source = (body: ConfirmRequestBody): string | undefined =>
      body.action === 'data_confirm' ? body.source : undefined

    expect(bodies.map((body) => body.action)).toEqual(['script_cancel', 'data_confirm'])
    expect(bodies.map(source)).toEqual([undefined, 'geo'])
  })

  it('data_confirm 缺少 source/asset_id 时无法赋给联合', () => {
    // @ts-expect-error data_confirm 必须携带 source 与 asset_id
    const invalid: ConfirmRequestBody = { session_id: 's1', action: 'data_confirm' }
    expect(invalid.action).toBe('data_confirm')
  })
})
