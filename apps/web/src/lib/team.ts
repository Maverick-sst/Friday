/** Typed client for the strategy-team API (/api/v1/team/*) + SSE helper. */

export interface MissionSummary {
  id: string
  name: string
  mission_type: string
  status: string
  priority: string
  budget_runs: number
  created_at: string
}

export interface MissionDetail extends MissionSummary {
  merchant_id: string
  objective: string
  runs_used: number
  tool_calls_used: number
  cancel_requested: boolean
  started_at: string | null
  completed_at: string | null
  result_summary_json: Record<string, unknown>
  error_json: Record<string, unknown>
}

export interface OnboardResponse {
  merchant_id: string
  mission_id?: string
  slug?: string
  created: boolean
  next?: string
}

export interface ExperimentResult {
  SIMULATED: boolean
  control_selection_rate: number
  treatment_selection_rate: number
  simulated_relative_lift_pct: number
  note: string
}

export interface ExperimentDetail {
  id: string
  hypothesis: string
  status: string
  cohort_size: number
  is_simulated: boolean
  mission_id: string | null
  result_json: Partial<ExperimentResult>
  runs_summary: {
    control: number
    treatment: number
    control_selected: number
    treatment_selected: number
  }
}

export const MERCHANT_KEY = 'acs.merchant_id'

export function currentMerchantId(): string | null {
  return localStorage.getItem(MERCHANT_KEY)
}

export function setMerchant(id: string): void {
  localStorage.setItem(MERCHANT_KEY, id)
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail?.message) message = body.detail.message
      else if (body?.detail) message =
        typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep default */
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

const base = '/api/v1/team'
const json = { 'Content-Type': 'application/json' }

export const team = {
  onboard: (url: string, goal?: string) =>
    fetch(`${base}/onboard`, { method: 'POST', headers: json, body: JSON.stringify({ url, goal }) }).then((r) =>
      handle<OnboardResponse>(r),
    ),

  createMission: (body: {
    merchant_id: string
    name: string
    objective: string
    mission_type?: string
    budget_runs?: number
    agent_assignments?: string[]
    shopping?: Record<string, unknown>
  }) => fetch(`${base}/missions`, { method: 'POST', headers: json, body: JSON.stringify(body) }).then((r) => handle<{ mission_id: string; status: string }>(r)),

  getMission: (id: string) => fetch(`${base}/missions/${id}`).then((r) => handle<MissionDetail>(r)),

  listMissions: (merchantId: string) =>
    fetch(`${base}/merchants/${merchantId}/missions?limit=50`).then((r) => handle<MissionSummary[]>(r)),

  cancelMission: (id: string) => fetch(`${base}/missions/${id}/cancel`, { method: 'POST' }).then((r) => handle<{ status: string }>(r)),

  meta: () => fetch(`${base}/meta`).then((r) => handle<{
    queue_driver: string
    llm_configured: boolean
    composio_ready: boolean
    mem0_ready: boolean
    registered_mission_types: string[]
    limits: Record<string, number>
    langfuse_ui: string | null
  }>(r)),

  createExperiment: (body: {
    merchant_id: string
    hypothesis: string
    control_variant_json: Record<string, string>
    treatment_variant_json: Record<string, string>
    cohort_size: number
  }) => fetch(`${base}/experiments`, { method: 'POST', headers: json, body: JSON.stringify(body) }).then((r) => handle<{ experiment_id: string }>(r)),

  startExperiment: (id: string) =>
    fetch(`${base}/experiments/${id}/start`, { method: 'POST' }).then((r) => handle<{ mission_id: string; status: string }>(r)),

  getExperiment: (id: string) => fetch(`${base}/experiments/${id}`).then((r) => handle<ExperimentDetail>(r)),
}

/** Subscribe to a mission's SSE event stream. Returns a disposer. */
export function streamMissionEvents(
  missionId: string,
  onEvent: (event: { kind: string; [k: string]: unknown }) => void,
  onStateChange?: (state: 'connecting' | 'open' | 'closed') => void,
): () => void {
  const source = new EventSource(`${base}/missions/${missionId}/events`)
  source.onopen = () => onStateChange?.('open')
  source.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data))
    } catch {
      /* ignore malformed frames */
    }
  }
  source.onerror = () => {
    // EventSource auto-reconnects and sends Last-Event-ID; the server replays
    // the missed backlog, so reconnects are gap-free (Fleet PRD A3).
    onStateChange?.('connecting')
  }
  return () => {
    source.close()
    onStateChange?.('closed')
  }
}
