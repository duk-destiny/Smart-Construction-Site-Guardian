/** 端点封装：仅调用 api/routers 暴露的接口，前端零业务逻辑零 SQL。
 *
 * 约定：函数与后端路由一一对应；分页参数原样透传；上传用 FormData。
 */
import { api } from './client'
import type {
  AgentRunRow, AlarmRow, Capabilities, ChatRoute, DispatchPanel,
  ModelRow, OrderRow, TaskDetail, UserRow,
} from './types'

// ---------- auth ----------

export async function login(username: string, password: string) {
  const r = await api.post('/auth/login', { username, password })
  return r.data as {
    token: string; user_id: string; username: string; role: string;
    must_change_password: boolean; expires_in: number
  }
}

export async function changePassword(oldPassword: string, newPassword: string) {
  const r = await api.post('/auth/change-password', {
    old_password: oldPassword, new_password: newPassword })
  return r.data
}

export async function me() {
  const r = await api.get('/auth/me')
  return r.data
}

// ---------- tasks ----------

export async function getCapabilities() {
  const r = await api.get('/tasks/capabilities')
  return r.data as Capabilities
}

export async function uploadMedia(form: FormData) {
  const r = await api.post('/tasks/media', form)
  return r.data as { task_id: string; media_path: string | null; async_started: boolean }
}

export async function createTextHazard(body: {
  description: string; hazard_key: string; scene_id?: string; location?: string
}) {
  const r = await api.post('/tasks/text', body)
  return r.data as {
    ok: boolean; task_id: string; risk_level: string;
    work_order: Record<string, string>; worker_notice: string
  }
}

export async function startRun(taskId: string, body: {
  images?: string[]; permit_info?: Record<string, unknown>; scene_id?: string
}) {
  const r = await api.post(`/tasks/${taskId}/run`, body)
  return r.data
}

export async function getProgress(taskId: string) {
  const r = await api.get(`/tasks/${taskId}/progress`)
  return r.data as Record<string, { status: string; cost_ms: number }>
}

export async function getResult(taskId: string) {
  const r = await api.get(`/tasks/${taskId}/result`)
  return r.data as { status: string; payload: Record<string, unknown> }
}

export async function getTaskDetail(taskId: string) {
  const r = await api.get(`/tasks/${taskId}/detail`)
  return r.data as TaskDetail
}

export async function getAgentRuns(taskId: string) {
  const r = await api.get(`/tasks/${taskId}/agents`)
  return r.data as AgentRunRow[]
}

export async function overrideTask(taskId: string, newLevel: string, reason: string) {
  const r = await api.post(`/tasks/${taskId}/override`, {
    new_level: newLevel, reason })
  return r.data
}

export async function enhanceExtract(text: string) {
  const r = await api.post('/tasks/enhance-extract', { text })
  return r.data as {
    description: string; hazard_key: string;
    location?: string | null; scene_id?: string
  }
}

export async function queryChat(text: string) {
  const r = await api.post('/tasks/query-chat', { text })
  return r.data as ChatRoute
}

// ---------- alarms ----------

export async function listAlarms(limit = 200) {
  const r = await api.get('/alarms', { params: { limit } })
  return r.data as AlarmRow[]
}

export async function updateAlarmStatus(alarmId: string, status: string) {
  const r = await api.patch(`/alarms/${alarmId}/status`, { status })
  return r.data
}

export async function convertAlarm(alarmId: string) {
  const r = await api.post(`/alarms/${alarmId}/convert-order`)
  return r.data as { ok: boolean; order_id: string }
}

// ---------- orders ----------

export async function listOrders() {
  const r = await api.get('/orders')
  return r.data as OrderRow[]
}

export async function listMyOrders() {
  const r = await api.get('/orders/mine')
  return r.data as OrderRow[]
}

export async function listPendingReview() {
  const r = await api.get('/orders/pending-review')
  return r.data as OrderRow[]
}

export async function listOverdue(asOf?: string) {
  const r = await api.get('/orders/overdue', { params: asOf ? { as_of: asOf } : {} })
  return r.data as OrderRow[]
}

export async function getDispatchPanel(taskId: string) {
  const r = await api.get(`/orders/by-task/${taskId}/panel`)
  return r.data as DispatchPanel
}

export async function dispatchOrder(taskId: string, assignee: string, hours: number) {
  const r = await api.post(`/orders/by-task/${taskId}/dispatch`,
    { assignee, hours })
  return r.data as { ok: boolean; message: string }
}

export async function submitRectification(orderId: string, note: string, files: File[]) {
  const form = new FormData()
  form.append('note', note)
  files.forEach((f) => form.append('photos', f))
  const r = await api.post(`/orders/${orderId}/rectification`, form)
  return r.data as { ok: boolean; message: string }
}

export async function reviewOrder(orderId: string, approve: boolean, reason = '') {
  const r = await api.post(`/orders/${orderId}/review`, { approve, reason })
  return r.data as { ok: boolean; message: string }
}

export async function exportOrderExcel(orderId: string) {
  const r = await api.post(`/orders/${orderId}/export`)
  return r.data as { ok: boolean; file: { name: string; download_url: string } }
}

// ---------- history / reports ----------

export async function historyStats(start?: string, end?: string) {
  const r = await api.get('/history/stats-by-date',
    { params: { start, end } })
  return r.data as { day: string; frames: number; non_compliant: number;
    warning: number; compliant: number }[]
}

export async function historySeverity(start?: string, end?: string) {
  const r = await api.get('/history/severity-breakdown',
    { params: { start, end } })
  return r.data as { cls: string; cnt: number }[]
}

export async function historyTaskRisks(start?: string, end?: string) {
  const r = await api.get('/history/task-risks', { params: { start, end } })
  return r.data as Record<string, unknown>[]
}

export async function generateWeekly(start: string, end: string) {
  const r = await api.post('/reports/weekly', { start, end })
  return r.data as { ok: boolean; stats: Record<string, unknown>;
    file: { name: string; download_url: string } }
}

export async function weeklyPreview(start?: string, end?: string) {
  const r = await api.get('/reports/weekly/preview', { params: { start, end } })
  return r.data as Record<string, unknown>
}

// ---------- realtime（Phase 4：Hub + WS 帧广播） ----------

export async function realtimeStatus() {
  const r = await api.get('/realtime/status')
  return r.data as {
    enabled: boolean; running: boolean;
    sources: { index: number; source: string }[];
    viewers: number; polls: number; alarms: number;
    active_fps?: number; idle_fps?: number; target_fps?: number;
    last_error?: string | null;
  }
}

// ---------- admin ----------

export async function listUsers() {
  const r = await api.get('/admin/users')
  return r.data as UserRow[]
}

export async function createUser(body: {
  username: string; password: string; role: string; must_change_password?: boolean
}) {
  const r = await api.post('/admin/users', body)
  return r.data
}

export async function resetPassword(userId: string, newPassword: string) {
  const r = await api.post(`/admin/users/${userId}/reset-password`,
    { new_password: newPassword })
  return r.data
}

export async function setUserDisabled(userId: string, disabled: boolean) {
  const r = await api.post(`/admin/users/${userId}/disabled`, { disabled })
  return r.data
}

export async function listModels() {
  const r = await api.get('/admin/models')
  return r.data as { models: ModelRow[]; active: Record<string, ModelRow | null> }
}

export async function switchModel(name: string, modelId: string) {
  const r = await api.post('/admin/models/switch', { name, model_id: modelId })
  return r.data
}

export async function listKbDocs() {
  const r = await api.get('/admin/kb/docs')
  return r.data as { id: string; filename: string; chunk_count: number;
    imported_by: string; created_at: string }[]
}

export async function importKbPdf(file: File) {
  const form = new FormData()
  form.append('file', file)
  const r = await api.post('/admin/kb/import', form)
  return r.data as { ok: boolean; chunks: number }
}

export async function notifyStatus() {
  const r = await api.get('/admin/notify/status')
  return r.data as { enabled: boolean; demo_mode: boolean; channel: string;
    webhook_configured: boolean }
}

export async function notifyTest() {
  const r = await api.post('/admin/notify/test')
  return r.data as Record<string, unknown>
}

export async function listMockCapture(n = 10) {
  const r = await api.get('/admin/mock-capture', { params: { n } })
  return r.data as Record<string, unknown>[]
}

export async function selfCheck() {
  const r = await api.post('/admin/self-check')
  return r.data as { ok: boolean; items: { item: string; ok: boolean; message: string }[] }
}

export async function listAudit(limit = 200) {
  const r = await api.get('/admin/audit', { params: { limit } })
  return r.data as { user_id: string | null; action: string;
    detail_json: string; created_at: string }[]
}

export async function listFeedback(limit = 500) {
  const r = await api.get('/admin/feedback', { params: { limit } })
  return r.data as Record<string, unknown>[]
}

export async function reviewFeedback(feedbackId: string, status: string) {
  const r = await api.post(`/admin/feedback/${feedbackId}/review`, { status })
  return r.data
}
