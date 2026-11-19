import type { QcPreview } from '@/lib/types'

export type QcPreviewStatus = QcPreview['status']

export type GateBlocker =
  | 'busy'
  | 'running'
  | 'disabled'
  | 'blocked'
  | 'preview-not-pending'
  | 'preview-edited'
  | 'already-attempted'
  | 'missing-binding'
  | 'missing-selection'
  | 'missing-cluster-review'
  | 'missing-reason'
  | 'cluster-already-accepted'
  | 'acceptance-required'
  | 'invalid-dimensions'

const gateBlockerText: Record<GateBlocker, string> = {
  busy: '正在读取或执行',
  running: '当前阶段或下游正在运行',
  disabled: '上游运行中，暂不可操作',
  blocked: '阶段前置条件未满足',
  'preview-not-pending': '预览不在可操作状态',
  'preview-edited': '阈值已编辑，需重新生成预览',
  'already-attempted': '当前身份已有执行记录',
  'missing-binding': '缺少当前授权绑定',
  'missing-selection': '缺少当前维度选择记录',
  'missing-cluster-review': '缺少当前聚类接受记录',
  'missing-reason': '缺少书面依据',
  'cluster-already-accepted': '当前聚类已有接受记录',
  'acceptance-required': '尚未勾选接受当前探索性聚类',
  'invalid-dimensions': '维度数不在证据范围内',
}

export interface StageGateResult {
  /** 阶段事实允许操作；不含尚未填写的依据等用户输入。 */
  available: boolean
  /** 阶段事实与本次动作所需输入均满足。 */
  allowed: boolean
  blockers: GateBlocker[]
  /** 可直接展示的阻断原因，按 blockers 顺序拼接。 */
  blockerText: string
}

type CommonFacts = {
  blocked: readonly string[]
  busy: boolean
  running?: boolean
  reasonProvided: boolean
}

type QcFacts = {
  stage: 'qc'
  previewStatus: QcPreviewStatus | null
  previewAttempted: boolean
}

type StageFacts =
  | (QcFacts & { action: 'approve'; unchanged: boolean })
  | (QcFacts & { action: 'reject' })
  | (QcFacts & { action: 'filter'; unchanged: boolean })
  | { stage: 'pca'; bindingExists: boolean; bindingAttempted: boolean }
  | { stage: 'cluster'; bindingExists: boolean; selectionExists: boolean; disabled: boolean; bindingAttempted: boolean }
  | { stage: 'marker'; action: 'accept'; bindingExists: boolean; hasClusterAcceptance: boolean; accepted: boolean; disabled: boolean }
  | { stage: 'marker'; action: 'reject'; bindingExists: boolean; disabled: boolean }
  | { stage: 'marker'; action: 'execute'; bindingExists: boolean; hasClusterAcceptance: boolean; disabled: boolean; bindingAttempted: boolean }
  | { stage: 'pca-dimensions'; bindingExists: boolean; dimensionsValid: boolean; disabled: boolean }

export type StageGateInput = CommonFacts & StageFacts

export function evaluateStageGate(input: StageGateInput): StageGateResult {
  const blockers: GateBlocker[] = []
  const add = (code: GateBlocker, when: boolean) => {
    if (when) blockers.push(code)
  }
  const unblocked = input.blocked.length === 0
  const idle = !input.busy && !input.running
  const result = (available: boolean, allowed: boolean): StageGateResult => ({
    available,
    allowed,
    blockers,
    blockerText: blockers.map((code) => gateBlockerText[code]).join('；'),
  })

  add('busy', input.busy)
  add('running', !!input.running)

  if (input.stage === 'qc') {
    if (input.action === 'reject') {
      add('preview-not-pending', input.previewStatus !== 'pending')
      add('missing-reason', !input.reasonProvided)
      const available = input.previewStatus === 'pending' && idle
      return result(available, available && input.reasonProvided)
    }

    const status = input.action === 'approve' ? 'pending' : 'approved'
    add('preview-not-pending', input.previewStatus !== status)
    add('preview-edited', !input.unchanged)
    add('blocked', !unblocked)
    if (input.action === 'filter') add('already-attempted', input.previewAttempted)
    else add('missing-reason', !input.reasonProvided)

    const available = input.previewStatus === status && input.unchanged
      && idle && unblocked
      && (input.action === 'approve' || !input.previewAttempted)
    return result(available, available && (input.action === 'filter' || input.reasonProvided))
  }

  if (input.stage === 'pca') {
    add('missing-binding', !input.bindingExists)
    add('blocked', !unblocked)
    add('already-attempted', input.bindingAttempted)
    add('missing-reason', !input.reasonProvided)
    const available = input.bindingExists && idle && !input.bindingAttempted && unblocked
    return result(available, available && input.reasonProvided)
  }

  if (input.stage === 'cluster') {
    add('missing-binding', !input.bindingExists)
    add('missing-selection', !input.selectionExists)
    add('disabled', input.disabled)
    add('blocked', !unblocked)
    add('already-attempted', input.bindingAttempted)
    add('missing-reason', !input.reasonProvided)
    const available = input.bindingExists && input.selectionExists && !input.disabled
      && idle && !input.bindingAttempted && unblocked
    return result(available, available && input.reasonProvided)
  }

  if (input.stage === 'marker') {
    add('missing-binding', !input.bindingExists)
    add('disabled', input.disabled)
    add('blocked', !unblocked)
    if (input.action === 'accept') {
      add('cluster-already-accepted', input.hasClusterAcceptance)
      add('acceptance-required', !input.accepted)
    }
    if (input.action === 'execute') {
      add('missing-cluster-review', !input.hasClusterAcceptance)
      add('already-attempted', input.bindingAttempted)
    }
    add('missing-reason', !input.reasonProvided)

    const available = input.bindingExists && !input.disabled && idle && unblocked
      && (input.action !== 'accept' || !input.hasClusterAcceptance)
      && (input.action !== 'execute' || (input.hasClusterAcceptance && !input.bindingAttempted))
    const hasRequiredInput = input.action === 'accept' ? input.accepted && input.reasonProvided : input.reasonProvided
    return result(available, available && hasRequiredInput)
  }

  if (input.stage === 'pca-dimensions') {
    add('missing-binding', !input.bindingExists)
    add('disabled', input.disabled)
    add('blocked', !unblocked)
    add('invalid-dimensions', !input.dimensionsValid)
    add('missing-reason', !input.reasonProvided)
    const available = input.bindingExists && !input.disabled && idle && unblocked
    return result(available, available && input.dimensionsValid && input.reasonProvided)
  }

  const unreachableStage: never = input
  throw new Error(`未处理的阶段：${String(unreachableStage)}`)
}

export function isAttemptedByPreview(
  attempts: readonly { preview_id: string }[],
  previewId: string | null | undefined,
): boolean {
  return !!previewId && attempts.some((attempt) => attempt.preview_id === previewId)
}

export function isAttemptedByBinding(
  attempts: readonly { binding_sha256: string }[],
  binding: string | null | undefined,
): boolean {
  return !!binding && attempts.some((attempt) => attempt.binding_sha256 === binding)
}
