/** 与 src/api/* 实际响应逐字段对齐的线路类型。 */

export interface SessionSummary {
  id: string
  title: string
  updated_at: string
  message_count: number
}

export interface Session {
  id: string
  title: string
  created_at: string
  updated_at: string
  summary: string | null
  summary_upto: number
  downloaded_assets: DownloadedAsset[]
}

/** 后端只保证它是 dict；前端只在需要时读 access_path / asset_id / source。 */
export interface DownloadedAsset {
  asset_id?: string
  source?: string
  access_path?: string
  [key: string]: unknown
}

/** 历史消息里的图表项：plotly 字段名是 figure_json（区别于 chart 事件的 plotly_json）。 */
export type ChartItem =
  | { type: 'plotly'; figure_json: string }
  | { type: 'image'; image_b64: string }

/** results 是个 dict：charts 之外还可能有 statistics/data/returncode 等（一期不渲染）。 */
export interface MessageResults {
  charts?: ChartItem[]
  qc?: QcEvidence
  qc_artifacts?: Record<string, string>
  qc_run_id?: string
  [key: string]: unknown
}

export interface QcEvidence {
  tool_version: string
  stage: 'qc_review'
  scientific_status: 'unreviewed'
  filtered: false
  cells: number
  features: number
  zero_count_cells: number
  mt_symbol_resolved: boolean
  warnings: string[]
  versions: string[]
  metrics: Record<string, { available: number; min: number | null; median: number | null; max: number | null }>
}

export interface QcThresholds {
  n_feature_min: number
  n_feature_max: number
  percent_mt_max: number
  n_count_min: number | null
  n_count_max: number | null
}

export interface QcPreview {
  preview_id: string
  params: Omit<QcThresholds, 'percent_mt_max'> & { percent_mt_max: string }
  summary: { total: number; kept: number; excluded: number; excluded_percent: number; reason_counts: Record<string, number>; blocked: string[]; filtered: false }
  status: 'pending' | 'approved'
  binding_sha256: string
  approved_at: string | null
  reason: string | null
}

export interface QcReviewState {
  run_id: string
  revision: number
  qc: QcEvidence
  active_preview: QcPreview | null
  approvals: QcPreview[]
  blocked: string[]
  attempts?: FilterAttempt[]
  pca_running?: boolean
}

export interface PcaState {
  run_id: string
  filter_attempt_id: string
  protocol: Record<string, unknown>
  tool_version: string
  binding_sha256: string | null
  source_summary: { cells: number; features: number } | null
  blocked: string[]
  attempts: {
    attempt_id: string
    binding_sha256: string
    status: FilterAttempt['status']
    created_at: string
    finished_at: string | null
    result: { cells?: number; features?: number; selected_features?: number; usable_features?: number; actual_pcs?: number; pc_reduction_reason?: string; versions?: string[]; error?: string; elapsed_seconds?: number; scientific_status?: string } | null
  }[]
}

export interface PcaDimensionReview {
  review_id: string
  pca_attempt_id: string
  binding_sha256: string
  record_version: string
  source_revision: number
  dims: number[]
  reason: string
  created_at: string
  message_id: number | null
  invalidated_reason?: string | null
}

export interface PcaDimensionsState {
  run_id: string
  filter_attempt_id: string
  attempt_id: string
  record_version: string
  stage: 'pca_review'
  scientific_status: 'unreviewed'
  revision: number
  binding_sha256: string | null
  evidence: { actual_pcs: number; stdev: { pc: string; stdev: number }[] } | null
  blocked: string[]
  current: PcaDimensionReview | null
  history: PcaDimensionReview[]
}

export interface FilterAttempt {
  attempt_id: string
  preview_id: string
  status: 'running' | 'completed' | 'failed' | 'interrupted'
  directory: string
  created_at: string
  finished_at: string | null
  result: { cells?: number; features?: number; error?: string; versions?: string[]; artifacts?: Record<string, { path: string; bytes: number; sha256: string }> } | null
}

export type PendingScriptStatus = 'pending' | 'confirmed' | 'cancelled' | 'expired'

export interface PendingScript {
  script: string
  analysis_type: string | null
  params: Record<string, unknown>
  method_context: unknown
  user_request: string
  status: PendingScriptStatus
}

export type Role = 'user' | 'assistant'

export interface WireMessage {
  id: number
  session_id: string
  seq: number
  role: Role
  content: string
  timestamp: string
  results: MessageResults | null
  pending_script: PendingScript | null
}

export interface KbStats {
  initialized: boolean
  error?: string
  [key: string]: unknown
}

export type OllamaState =
  | 'ready'
  | 'starting'
  | 'downloading'
  | 'idle'
  | 'stopped'
  | 'unavailable'
  | 'failed'
  | 'needs_model'
  | 'error'

export interface OllamaStatus {
  state: OllamaState
  managed: boolean
  detail: string
}

export interface EnvStatus {
  rscript: string | null
  kb_path: string
  kb_ok: boolean
  /** 运行态：恰好三键（state/managed/detail），不得加第 4 个键 */
  ollama: OllamaStatus
  /**
   * 启动模式（配置态，故与 ollama 平级而非嵌进去）：
   * - `eager` = 随程序启动预热，不做空闲自动关闭
   * - `lazy`  = 按需唤起 + 空闲自动关闭（默认）
   */
  ollama_start_mode: 'eager' | 'lazy'
}

export interface SidebarResponse {
  kb_stats: KbStats
  env: EnvStatus
  soft_warn: boolean
}

export interface MessagesResponse {
  session: Session
  messages: WireMessage[]
  /** GET ?restore=1 时：pending → expired；其余状态 → null */
  pending_script: PendingScript | null
  soft_warn: boolean
}

export type ConfirmAction = 'script_confirm' | 'script_cancel' | 'data_confirm'

export interface ConfirmRequestBody {
  session_id: string
  action: ConfirmAction
  source?: string
  asset_id?: string
  query?: string
}

export interface DataCandidate {
  source: string
  asset_id: string
  title: string
  asset_type: string
  reason: string
  description: string
  metadata: Record<string, unknown>
}

/** SSE chart 事件载荷（字段名 plotly_json，与 ChartItem.figure_json 不同）。 */
export interface ChartEventPayload {
  type: 'chart'
  plotly_json?: string
  image_b64?: string
}

/** chart 事件 → 历史消息用的 ChartItem（统一渲染入口）。 */
export function chartFromEvent(event: ChartEventPayload): ChartItem {
  if (event.plotly_json !== undefined) {
    return { type: 'plotly', figure_json: event.plotly_json }
  }
  return { type: 'image', image_b64: event.image_b64 ?? '' }
}

export interface ScriptCardPayload {
  message_id: number
  script: string
  analysis_type: string | null
  params: Record<string, unknown>
  method_context: unknown
  user_request: string
  status: PendingScriptStatus
}

export interface DataCardPayload {
  message_id: number
  candidates: DataCandidate[]
  query: string
  message: string
}
