// Hand-written types matching the FastAPI response models.
// Regenerate from OpenAPI via `npm run gen:types` when the daemon is running.

export interface HealthResponse {
  status: string
  scheduler: string
  uptime_seconds: number
  current_task: string | null
  pending_approvals: number
  batch_store_pending: number
  errors_last_24h: number
}

export interface StateResponse {
  hitl_strategy: string
  daemon: {
    enabled: boolean
    task_schedule: string
    eod_schedule: string
    port: number
    approval_timeout_hours: number
    approval_on_timeout: string
  }
  task_source: string
}

export interface TaskCurrentResponse {
  task_id: string | null
  node: string | null
}

export interface TaskQueueResponse {
  queue: Array<Record<string, unknown>>
  note: string
}

export interface EodEntrySummary {
  id: number
  task_id: string
  task_title: string
  branch_name: string
  final_verdict: string | null
  status: string
  created_at: string
}

export interface ReporterArtifacts {
  pr_title: string
  pr_description: string
  corporate_report: string
  commit_message: string
}

export interface CheckerReport {
  agent_name: string
  verdict: string
  summary: string
  findings: string[]
  suggestions: string[]
}

export interface BatchEntryDetail {
  id: number | null
  task_id: string
  task_title: string
  branch_name: string
  worktree_path: string
  diff: string
  plan_summary: string
  plan_steps: string[]
  checker_reports: CheckerReport[]
  self_review_notes: string
  final_verdict: string | null
  reporter_artifacts: ReporterArtifacts
  status: string
  created_at: string
  published_at: string | null
  mr_url: string | null
  pushed_sha: string | null
  rejection_reason: string | null
}

export interface ApprovalPending {
  thread_id: string
  payload: Record<string, unknown>
}

export interface ApprovalDecision {
  approved: boolean
  reason?: string
  requested_changes?: string[]
}

export interface EodPublishResult {
  published: string[]
  failed: string[]
  skipped: string[]
}

// SSE event shapes (from EventBus data dicts).
export interface SseEvent {
  event: string
  [key: string]: unknown
}
