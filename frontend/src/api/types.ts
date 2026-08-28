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
