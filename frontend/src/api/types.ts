/** 与 api/routers 响应对齐的轻量类型（结构化主要实体，杂项用宽松字段）。 */

export interface UserInfo {
  user_id: string
  username: string
  role: 'admin' | 'safety' | 'responsible'
  must_change_password: boolean
}

export interface HazardOption {
  key: string
  label: string
  severity: 'critical' | 'warning'
}

export interface Capabilities {
  asr_available: boolean
  enhance_available: boolean
  hazard_options: HazardOption[]
}

export interface OrderRow {
  id: string
  task_id: string
  hazard_desc: string
  clause: string
  requirement: string
  risk_level: string
  worker_notice: string
  created_at: string
  assignee_id: string | null
  status: 'open' | 'submitted' | 'closed' | 'rejected'
  dispatched_at: string | null
  deadline: string | null
  submitted_note: string | null
  submitted_imgs: string | null
  review_reason: string | null
  auto_level?: string | null
  override_level?: string | null
  override_reason?: string | null
  source?: string | null
  assignee_name?: string | null
  submitted_img_paths?: string[]
}

export interface AlarmRow {
  id: string
  session_id: string | null
  task_id: string | null
  scene_id: string | null
  cls: string | null
  conf: number | null
  status: 'new' | 'confirmed' | 'false_alarm' | 'resolved'
  source: string | null
  clause: string | null
  image_path: string | null
  created_at: string
}

export interface DispatchPanel {
  order: OrderRow
  assignee_name: string | null
  responsible_names: string[]
  suggestion: string | null
  default_hours: number
}

export interface TaskDetail {
  task: Record<string, unknown> | null
  risk: Record<string, unknown> | null
  detections: Record<string, unknown>[]
  compliances: Record<string, unknown>[]
}

export interface AgentRunRow {
  agent: string
  status: string
  cost_ms: number
  input_json: string
  output_json: string
  error: string | null
}

export interface ChatRoute {
  action: string
  tier: string
  order_id: string | null
  status: string | null
  days: number
  hint: string | null
  candidates: string[]
  data?: unknown
}

// ---------- 认知层（§5.11 双层响应 / §5.12 六端点） ----------

/** /agent/chat 快路径：旧 ChatRoute 结构 + path:"fast"（空文本契约响应无 path 字段，按 fast 处理）。 */
export interface AgentChatFast extends ChatRoute {
  path?: 'fast'
}

/** /agent/chat 认知路径：异步 run，前端轮询 progress。status: pending | busy。 */
export interface AgentChatCognitive {
  path: 'cognitive'
  run_id?: string
  session_id?: string
  status: string
}

/** 双层判别联合：以 path === 'cognitive' 为判别字段。 */
export type AgentChatReply = AgentChatFast | AgentChatCognitive

export interface AgentPlanStep {
  tool: string
  args?: Record<string, unknown>
  reason?: string
}

/** /agent/runs/{id}/progress 与 /trace 的响应视图。 */
export interface AgentRunProgress {
  run_id: string
  session_id: string
  status: string
  intent?: string | null
  current_step_idx?: number | null
  need_confirm?: boolean
  plan?: { goal?: string; steps?: AgentPlanStep[]; need_confirm?: boolean } | null
  confirm_payload?: Record<string, unknown> | null
  result?: Record<string, unknown> | null
  error?: string | null
  task_id?: string | null
  steps?: Record<string, unknown>[]
  created_at?: string
  updated_at?: string
}

/** /agent/sessions/{id}/history 的消息行（只存摘要）。 */
export interface AgentMessageRow {
  role: string
  content?: string | null
  intent?: string | null
  run_id?: string | null
  digest?: string | null
  attachments?: string[] | string | null
  created_at?: string
}

export interface UserRow {
  id: string
  username: string
  role: string
  disabled?: number | boolean
  must_change_password?: number | boolean
  created_at?: string
}

export interface ModelRow {
  id: string
  name: string
  version: string
  path: string
  active: number | boolean
  notes?: string | null
}
