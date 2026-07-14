import type {
  ApprovalDecision,
  ApprovalPending,
  BatchEntryDetail,
  EodEntrySummary,
  EodPublishResult,
  HealthResponse,
  StateResponse,
  TaskCurrentResponse,
  TaskQueueResponse,
} from './types'

const BASE = import.meta.env.BASE_URL || '/'

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}api${path}`, {
    headers: { Accept: 'application/json' },
  })
  if (!resp.ok) {
    throw new Error(`GET ${path} failed: ${resp.status} ${resp.statusText}`)
  }
  return resp.json() as Promise<T>
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    throw new Error(`POST ${path} failed: ${resp.status} ${resp.statusText}`)
  }
  return resp.json() as Promise<T>
}

// --- Health & state ---
export const getHealth = () => getJson<HealthResponse>('/health')
export const getState = () => getJson<StateResponse>('/state')

// --- Tasks ---
export const getTasksCurrent = () => getJson<TaskCurrentResponse>('/tasks/current')
export const getTasksQueue = () => getJson<TaskQueueResponse>('/tasks/queue')
export const getTasksDone = () => getJson<EodEntrySummary[]>('/tasks/done')
export const getTaskDetail = (taskId: string) =>
  getJson<BatchEntryDetail>(`/tasks/${encodeURIComponent(taskId)}`)

// --- Approvals ---
export const getApprovals = () => getJson<ApprovalPending[]>('/approvals')
export const resolveApproval = (threadId: string, decision: ApprovalDecision) =>
  postJson<{ status: string; thread_id: string }>(
    `/approvals/${encodeURIComponent(threadId)}`,
    decision,
  )

// --- EOD ---
export const getEod = () => getJson<EodEntrySummary[]>('/eod')
export const eodFinalize = () => postJson<{ pending_count: number }>('/eod/finalize')
export const eodPublish = (taskIds: string[]) =>
  postJson<EodPublishResult>('/eod/publish', { task_ids: taskIds })
export const getEodEntry = (entryId: number) =>
  getJson<BatchEntryDetail>(`/eod/entries/${entryId}`)
