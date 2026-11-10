export type Status = 'queued' | 'running' | 'cancelling' | 'recovering' | 'passed' | 'failed' | 'error' | 'timed_out' | 'cancelled'
export type Counts = Record<'total' | 'passed' | 'failed' | 'error' | 'skipped', number>
export interface Suite {
  id: string; name: string; description: string
  requires_postgres: boolean; expected_failure: boolean
}
export interface CatalogCase {
  id: string; name: string; function: string; title: string; description: string; expected: string
  source_file: string; source_line: number; parameters: Record<string, string>; requires_postgres: boolean
}
export interface CaseDirectory { suite_id: string; catalog_revision: string; collected_at: string; count: number; cases: CatalogCase[] }
export interface CaseSelection { mode: 'all' | 'selected'; catalog_revision: string; collected_at: string; cases: CatalogCase[] }
export interface CaseChoice { ids: string[] | null; revision?: string; count?: number }
export interface Run {
  id: string; suite_id: string; status: Status
  case_selection: CaseSelection | null
  rerun_of_id: string | null
  created_at: string; started_at: string | null; finished_at: string | null
  exit_code: number | null; total_count: number | null; passed_count: number | null
  failed_count: number | null; error_count: number | null; skipped_count: number | null
  error_message: string | null
  idempotency_key: string; timeout_seconds: number; max_attempts: number; attempt_count: number
  worker_id: string | null; lease_until: string | null; cancel_requested_at: string | null
  active_attempt_id: string | null; report_attempt_id: string | null; replayed?: boolean
}
export interface Case {
  case_id?: string | null
  name: string; classname: string; outcome: 'passed' | 'failed' | 'error' | 'skipped'
  duration_seconds: number; details: string
  source_file?: string | null; source_line?: number | null; evidence?: Evidence[]
}
export interface Evidence {
  version: number; label: string
  request: { method: string; url: string; path: string; headers: Record<string, string>; body: unknown }
  response: { status_code: number | null; headers: Record<string, string>; body: unknown }
  database: { before: unknown; after: unknown }
  checks: { name: string; expected: unknown; actual: unknown }[]
  hint?: string | null; related_code?: { file: string; line: number } | null
  transport?: { mode: 'network'; base_url?: string; service_id?: string; timeout_seconds?: number; elapsed_seconds?: number }
  transport_error?: { type: string; phase: string; message: string }
}
export interface Attempt {
  id: string; number: number; worker_id: string; status: string
  started_at: string; finished_at: string | null; process_id: number | null
  isolation_schema: string | null; stop_reason: string | null; cleanup_done: boolean; exit_code: number | null
}
export interface HTTPService {
  id: string; attempt_id: string; case_id: string; base_url: string; process_id: number | null; isolation_schema: string
  status: 'starting' | 'ready' | 'stopped' | 'error'; started_at: string; ready_at: string | null; stopped_at: string | null
  exit_code: number | null; cleanup_verified: boolean; stop_reason: string | null; log_available: boolean
}
export interface Detail extends Run { report: { counts: Counts; cases: Case[] } | null; attempts: Attempt[]; http_services: HTTPService[]; http_service_notice: string | null }
export interface System { online_workers: number; queue: string; lease_seconds: number }
export type CheckStatus = 'ok' | 'waiting' | 'unavailable' | 'unknown'
export interface EnvironmentCheck { id: string; name: string; status: CheckStatus; code: string; message: string; action: string; details: Record<string, unknown>; checked_at: string; duration_ms: number }
export interface EnvironmentSuite { id: string; name: string; requires_postgres: boolean; requires_platform_test: boolean; execution_ready: boolean; blocked_by: string[] }
export interface EnvironmentReport { checked_at: string; request_id: string; status: 'ready' | 'partial' | 'waiting' | 'blocked'; can_submit: boolean; checks: EnvironmentCheck[]; suites: EnvironmentSuite[] }
export interface RunList { items: Run[]; total: number; summary: Record<Status, number>; matched_total: number; limit: number; offset: number }
export interface Log { content: string; truncated: boolean }

export interface DataSource { kind: string; database: string; schema: string; captured_at: string; readonly: boolean; row_limit: number }
export interface DataProduct { id: number; name: string; price_cents: string; stock: number; is_active?: boolean }
export interface ProductEditing { enabled: boolean; reason: string; can_create?: boolean; can_delete?: boolean; can_restore?: boolean }
export interface ProductDetail { product: DataProduct; product_editing: ProductEditing }
export interface ProductSaved { product: DataProduct; request_id: string }
export interface DataOrder { id: string; product_id: number; product_name: string; quantity: number; total_amount_cents: string; status: 'pending' | 'paid' | 'cancelled'; created_at: string; updated_at: string; payment_event_count: number }
export interface DataPayment { event_id: string; order_id: string; product_id: number; amount_cents: string; created_at: string; order_status: string }
export interface OrderDataset { source: DataSource; product_editing?: ProductEditing; demo_orders?: ProductEditing & { base_url: string }; product_status_counts?: { active: number; deleted: number }; counts: { products: number; orders: number; payment_events: number }; status_counts: Record<'pending' | 'paid' | 'cancelled', number>; products: DataProduct[]; orders: DataOrder[]; payment_events: DataPayment[] }
export type DataRun = Pick<Run, 'id' | 'suite_id' | 'status' | 'created_at' | 'started_at' | 'finished_at' | 'exit_code' | 'total_count' | 'passed_count' | 'failed_count' | 'error_count' | 'skipped_count' | 'attempt_count' | 'timeout_seconds'>
export interface DataWorker { id: string; process_id: number; heartbeat_at: string; last_status: string; online: boolean }
export interface PlatformDataset { source: DataSource; counts: { runs: number; attempts: number; workers: number }; online_workers: number; runs: DataRun[]; attempts: (Attempt & { run_id: string })[]; workers: DataWorker[] }

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError' }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = typeof body.detail === 'string' ? body.detail : `请求失败（HTTP ${response.status}）`
    throw new ApiError(detail, response.status)
  }
  return response.json() as Promise<T>
}
