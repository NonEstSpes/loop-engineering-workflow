import type {
  AgentDetail,
  AgentSummary,
  AgentUpdate,
  ApprovalDecision,
  ApprovalPending,
  BatchEntryDetail,
  ConfigDiff,
  ConfigPatch,
  ConfigResponse,
  EodEntrySummary,
  EodPublishResult,
  EventLogEntry,
  HealthResponse,
  HitlStrategy,
  HitlSwitchResponse,
  ProviderSummary,
  QueueEntry,
  QueueReorderRequest,
  RunRequest,
  RunResponse,
  StateResponse,
  TaskCurrentResponse,
  TaskQueueResponse,
  TodoItem,
  TodoPatch,
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

async function patchJson<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}api${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    throw new Error(`PATCH ${path} failed: ${resp.status} ${resp.statusText}`)
  }
  return resp.json() as Promise<T>
}

async function putJson<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}api${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    throw new Error(`PUT ${path} failed: ${resp.status} ${resp.statusText}`)
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

// --- Control: run tasks ---
export const runTask = (req: RunRequest) =>
  postJson<RunResponse>('/tasks/run', req)

// --- Control: TODO ---
export const getTodo = () => getJson<TodoItem[]>('/todo')
export const patchTodo = (lineNo: number, patch: TodoPatch) =>
  patchJson<TodoItem>(`/todo/${lineNo}`, patch)

// --- Control: config ---
export const getConfig = () => getJson<ConfigResponse>('/config')
export const patchConfig = (patch: ConfigPatch) =>
  patchJson<ConfigResponse>('/config', patch)
export const getConfigDiff = () => getJson<ConfigDiff>('/config/diff')
export const saveConfig = () => postJson<{ path: string }>('/config/save')
export const switchHitl = (strategy: HitlStrategy) =>
  putJson<HitlSwitchResponse>('/config/hitl', { strategy })

// --- Control: agents ---
export const getAgents = () => getJson<AgentSummary[]>('/agents')
export const getAgent = (name: string) =>
  getJson<AgentDetail>(`/agents/${encodeURIComponent(name)}`)
export const updateAgentPrompt = (name: string, prompt: string) =>
  putJson<{ name: string; status: string }>(
    `/agents/${encodeURIComponent(name)}/prompt`,
    { system_prompt: prompt },
  )
export const saveAgent = (name: string) =>
  postJson<{ path: string }>(`/agents/${encodeURIComponent(name)}/save`)

// --- Enhancements A: providers + agent field update ---
export const getProviders = () => getJson<ProviderSummary[]>('/providers')
export const updateAgent = (name: string, update: AgentUpdate) =>
  putJson<{ name: string; status: string }>(
    `/agents/${encodeURIComponent(name)}`,
    update,
  )

// --- Control: execution queue (Enhancements B) ---
export const getQueue = () => getJson<QueueEntry[]>('/queue')
export const reorderQueue = (req: QueueReorderRequest) =>
  patchJson<QueueEntry[]>('/queue/reorder', req)
export const queueMoveUp = (taskId: string) =>
  postJson<QueueEntry[]>('/queue/move-up', { task_id: taskId })
export const queueMoveDown = (taskId: string) =>
  postJson<QueueEntry[]>('/queue/move-down', { task_id: taskId })

// --- Event history (P2) ---
export const getEventHistory = (limit = 100, eventType?: string) =>
  getJson<EventLogEntry[]>(
    `/events/history?limit=${limit}${eventType ? `&event_type=${eventType}` : ''}`,
  )
