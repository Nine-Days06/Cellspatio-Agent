import { describe, expect, it } from 'vitest'
import { stageActivity } from '@/lib/stageActivity'

describe('stageActivity', () => {
  it('无 attempts、未执行、无下游时全部空闲', () => {
    expect(stageActivity({ executing: false })).toEqual({
      selfRunning: false,
      downstreamRunning: false,
      running: false,
    })
    expect(stageActivity({ attempts: null, executing: false, downstream: null })).toEqual({
      selfRunning: false,
      downstreamRunning: false,
      running: false,
    })
  })

  it('attempts 中命中 running 视为自身运行', () => {
    expect(stageActivity({
      attempts: [{ status: 'completed' }, { status: 'running' }],
      executing: false,
    })).toEqual({ selfRunning: true, downstreamRunning: false, running: true })
  })

  it('attempts 无 running 且未执行时自身空闲', () => {
    expect(stageActivity({
      attempts: [{ status: 'completed' }, { status: 'failed' }, { status: 'interrupted' }],
      executing: false,
    })).toEqual({ selfRunning: false, downstreamRunning: false, running: false })
  })

  it('executing 单独即触发自身运行', () => {
    expect(stageActivity({ attempts: [], executing: true })).toEqual({
      selfRunning: true,
      downstreamRunning: false,
      running: true,
    })
  })

  it('下游 id 列表非空视为下游运行', () => {
    expect(stageActivity({ executing: false, downstream: ['attempt-1'] })).toEqual({
      selfRunning: false,
      downstreamRunning: true,
      running: true,
    })
  })

  it('下游 id 列表为空视为无下游', () => {
    expect(stageActivity({ executing: false, downstream: [] })).toEqual({
      selfRunning: false,
      downstreamRunning: false,
      running: false,
    })
  })

  it('下游布尔值继续生效', () => {
    expect(stageActivity({ executing: false, downstream: true })).toEqual({
      selfRunning: false,
      downstreamRunning: true,
      running: true,
    })
    expect(stageActivity({ executing: false, downstream: false })).toEqual({
      selfRunning: false,
      downstreamRunning: false,
      running: false,
    })
  })

  it('自身与下游同时运行', () => {
    expect(stageActivity({
      attempts: [{ status: 'running' }],
      executing: false,
      downstream: ['attempt-2'],
    })).toEqual({ selfRunning: true, downstreamRunning: true, running: true })
  })
})
