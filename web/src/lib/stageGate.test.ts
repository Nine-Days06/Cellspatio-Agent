import { describe, expect, it } from 'vitest'
import {
  evaluateStageGate,
  isAttemptedByBinding,
  isAttemptedByPreview,
  type QcPreviewStatus,
} from '@/lib/stageGate'

type QcFacts = {
  previewStatus?: QcPreviewStatus | null
  unchanged?: boolean
  attempted?: boolean
  blocked?: string[]
  busy?: boolean
  running?: boolean
  reasonProvided?: boolean
}

function qc(action: 'approve' | 'reject' | 'filter', facts: QcFacts = {}) {
  const {
    previewStatus = 'pending',
    unchanged = true,
    attempted = false,
    blocked = [],
    busy = false,
    running = false,
    reasonProvided = true,
  } = facts
  const common = { blocked, busy, running, reasonProvided, previewStatus, previewAttempted: attempted }
  if (action === 'reject') return evaluateStageGate({ ...common, stage: 'qc', action: 'reject' })
  return evaluateStageGate({ ...common, stage: 'qc', action, unchanged })
}

function qcPreview(facts: Pick<QcFacts, 'blocked' | 'busy' | 'running'> = {}) {
  const { blocked = [], busy = false, running = false } = facts
  return evaluateStageGate({ stage: 'qc', action: 'preview', blocked, busy, running, reasonProvided: false })
}

type MarkerReviewFacts = {
  disabled?: boolean
  blocked?: string[]
  busy?: boolean
  running?: boolean
  reasonProvided?: boolean
}

function markerReview(facts: MarkerReviewFacts = {}) {
  const { disabled = false, blocked = [], busy = false, running = false, reasonProvided = true } = facts
  return evaluateStageGate({ stage: 'marker', action: 'review', disabled, blocked, busy, running, reasonProvided })
}

describe('QC preview gate', () => {
  it('previews whenever idle and unblocked, without a preview status or reason', () => {
    expect(qcPreview()).toMatchObject({ available: true, allowed: true, blockers: [] })
    expect(qcPreview({ blocked: ['参数不完整'] })).toMatchObject({ available: false, allowed: false, blockers: ['blocked'] })
    expect(qcPreview({ blocked: ['参数不完整'] }).blockerText).toBe('阶段前置条件未满足')
    expect(qcPreview({ busy: true })).toMatchObject({ available: false, allowed: false, blockers: ['busy'] })
    expect(qcPreview({ busy: true }).blockerText).toBe('正在读取或执行')
    expect(qcPreview({ running: true })).toMatchObject({ available: false, allowed: false, blockers: ['running'] })
    expect(qcPreview({ running: true }).blockerText).toBe('当前阶段或下游正在运行')
    expect(qcPreview({ busy: true, running: true }).blockerText).toBe('正在读取或执行；当前阶段或下游正在运行')
  })

  it('approves only a pending, unchanged preview with a reason and no block', () => {
    expect(qc('approve')).toMatchObject({ available: true, allowed: true, blockers: [] })
    expect(qc('approve', { previewStatus: 'approved' }).blockers).toContain('preview-not-pending')
    expect(qc('approve', { unchanged: false }).blockers).toContain('preview-edited')
    expect(qc('approve', { reasonProvided: false }).blockers).toContain('missing-reason')
    expect(qc('approve', { reasonProvided: false }).blockerText).toBe('缺少书面依据')
    expect(qc('approve', { blocked: ['NA 不能批准'] }).blockers).toContain('blocked')
    expect(qc('approve', { busy: true }).allowed).toBe(false)
    expect(qc('approve', { running: true }).allowed).toBe(false)
  })

  it('leaves rejection available when blocked, edited or already attempted, but still needs a reason', () => {
    expect(qc('reject', { blocked: ['NA'], unchanged: false, attempted: true })).toMatchObject({ allowed: true, blockers: [] })
    expect(qc('reject', { reasonProvided: false }).blockers).toContain('missing-reason')
    expect(qc('reject', { previewStatus: 'rejected' }).allowed).toBe(false)
  })

  it('filters only an approved, unchanged, unattempted preview without a block', () => {
    expect(qc('filter', { previewStatus: 'approved' })).toMatchObject({ allowed: true, blockers: [] })
    expect(qc('filter', { previewStatus: 'approved', attempted: true }).blockers).toContain('already-attempted')
    expect(qc('filter', { previewStatus: 'approved', unchanged: false }).blockers).toContain('preview-edited')
    expect(qc('filter', { previewStatus: 'pending' }).blockers).toContain('preview-not-pending')
    expect(qc('filter', { previewStatus: 'approved', blocked: ['父选择变化'] }).allowed).toBe(false)
  })
})

describe('binding-based gates', () => {
  it('executes PCA only with a current binding, unused attempt, no block and a reason', () => {
    const gate = evaluateStageGate({
      stage: 'pca',
      bindingExists: true,
      bindingAttempted: false,
      blocked: [],
      busy: false,
      running: false,
      reasonProvided: true,
    })
    expect(gate).toMatchObject({ available: true, allowed: true, blockers: [] })
    expect(evaluateStageGate({
      stage: 'pca',
      bindingExists: false,
      bindingAttempted: false,
      blocked: [],
      busy: false,
      running: false,
      reasonProvided: true,
    }).blockers).toContain('missing-binding')
    expect(evaluateStageGate({
      stage: 'pca',
      bindingExists: true,
      bindingAttempted: true,
      blocked: [],
      busy: false,
      running: false,
      reasonProvided: true,
    }).blockers).toContain('already-attempted')
  })

  it('separates cluster edit availability from final authorization by reason', () => {
    const base = {
      stage: 'cluster' as const,
      bindingExists: true,
      selectionExists: true,
      disabled: false,
      bindingAttempted: false,
      blocked: [],
      busy: false,
      running: false,
    }
    expect(evaluateStageGate({ ...base, reasonProvided: false })).toMatchObject({
      available: true,
      allowed: false,
      blockers: ['missing-reason'],
    })
    expect(evaluateStageGate({ ...base, reasonProvided: true }).allowed).toBe(true)
    expect(evaluateStageGate({ ...base, selectionExists: false, reasonProvided: true }).blockers).toContain('missing-selection')
    expect(evaluateStageGate({ ...base, bindingAttempted: true, reasonProvided: true }).blockers).toContain('already-attempted')
    expect(evaluateStageGate({ ...base, disabled: true, reasonProvided: true }).blockers).toContain('disabled')
    expect(evaluateStageGate({ ...base, blocked: ['需要至少 31 细胞'], reasonProvided: true }).available).toBe(false)
  })

  it('reviews marker results without a binding, needing only an idle unblocked stage and a reason', () => {
    expect(markerReview()).toMatchObject({ available: true, allowed: true, blockers: [] })
    expect(markerReview({ reasonProvided: false })).toMatchObject({
      available: true,
      allowed: false,
      blockers: ['missing-reason'],
    })
    expect(markerReview({ reasonProvided: false }).blockerText).toBe('缺少书面依据')
    expect(markerReview({ disabled: true })).toMatchObject({ available: false, allowed: false, blockers: ['disabled'] })
    expect(markerReview({ disabled: true }).blockerText).toBe('上游运行中，暂不可操作')
    expect(markerReview({ blocked: ['marker 结果缺失'] })).toMatchObject({ available: false, blockers: ['blocked'] })
    expect(markerReview({ blocked: ['marker 结果缺失'] }).blockerText).toBe('阶段前置条件未满足')
    expect(markerReview({ busy: true })).toMatchObject({ available: false, blockers: ['busy'] })
    expect(markerReview({ running: true })).toMatchObject({ available: false, blockers: ['running'] })
  })

  it('keeps marker cluster acceptance and marker execution as separate actions', () => {
    const accept = {
      stage: 'marker' as const,
      action: 'accept' as const,
      bindingExists: true,
      hasClusterAcceptance: false,
      accepted: true,
      disabled: false,
      blocked: [],
      busy: false,
      running: false,
      reasonProvided: true,
    }
    expect(evaluateStageGate(accept)).toMatchObject({ available: true, allowed: true, blockers: [] })
    expect(evaluateStageGate({ ...accept, accepted: false }).blockers).toContain('acceptance-required')
    expect(evaluateStageGate({ ...accept, reasonProvided: false }).blockers).toContain('missing-reason')
    expect(evaluateStageGate({ ...accept, hasClusterAcceptance: true }).blockers).toContain('cluster-already-accepted')

    const reject = {
      stage: 'marker' as const,
      action: 'reject' as const,
      bindingExists: true,
      disabled: false,
      blocked: [],
      busy: false,
      running: false,
      reasonProvided: false,
    }
    expect(evaluateStageGate(reject)).toMatchObject({ available: true, allowed: false, blockers: ['missing-reason'] })
    expect(evaluateStageGate({ ...reject, reasonProvided: true }).allowed).toBe(true)

    const execute = {
      stage: 'marker' as const,
      action: 'execute' as const,
      bindingExists: true,
      hasClusterAcceptance: true,
      disabled: false,
      bindingAttempted: false,
      blocked: [],
      busy: false,
      running: false,
      reasonProvided: true,
    }
    expect(evaluateStageGate(execute)).toMatchObject({ available: true, allowed: true, blockers: [] })
    expect(evaluateStageGate({ ...execute, hasClusterAcceptance: false }).blockers).toContain('missing-cluster-review')
    expect(evaluateStageGate({ ...execute, bindingAttempted: true }).blockers).toContain('already-attempted')
  })

  it('records PCA dimensions only within the computed range with a reason', () => {
    const base = {
      stage: 'pca-dimensions' as const,
      bindingExists: true,
      dimensionsValid: true,
      disabled: false,
      blocked: [],
      busy: false,
      running: false,
      reasonProvided: true,
    }
    expect(evaluateStageGate(base)).toMatchObject({ available: true, allowed: true, blockers: [] })
    expect(evaluateStageGate({ ...base, dimensionsValid: false }).blockers).toContain('invalid-dimensions')
    expect(evaluateStageGate({ ...base, reasonProvided: false }).blockers).toContain('missing-reason')
    expect(evaluateStageGate({ ...base, bindingExists: false, dimensionsValid: false }).blockers).toContain('missing-binding')
    expect(evaluateStageGate({ ...base, blocked: ['父选择已变化'] }).available).toBe(false)
  })
})

describe('attempt identity helpers', () => {
  it('compares QC attempts by preview id and later stages by binding hash', () => {
    expect(isAttemptedByPreview([{ preview_id: 'p1' }], 'p1')).toBe(true)
    expect(isAttemptedByPreview([{ preview_id: 'p2' }], 'p1')).toBe(false)
    expect(isAttemptedByPreview([{ preview_id: 'p1' }], undefined)).toBe(false)
    expect(isAttemptedByBinding([{ binding_sha256: 'h1' }], 'h1')).toBe(true)
    expect(isAttemptedByBinding([{ binding_sha256: 'h2' }], 'h1')).toBe(false)
    expect(isAttemptedByBinding([{ binding_sha256: 'h1' }], null)).toBe(false)
  })
})
