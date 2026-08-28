/** 风险等级/工单状态/告警状态标签（纯视图转换，颜色口径与 Streamlit 版一致）。 */
import { Tag } from 'antd'

const RISK_COLOR: Record<string, string> = {
  重大: 'volcano', 较大: 'orange', 一般: 'gold', 低: 'blue',
}

const ORDER_STATUS: Record<string, { text: string; color: string }> = {
  open: { text: '待整改', color: 'orange' },
  submitted: { text: '待验收', color: 'processing' },
  closed: { text: '已销项', color: 'success' },
  rejected: { text: '驳回重改', color: 'error' },
}

const ALARM_STATUS: Record<string, { text: string; color: string }> = {
  new: { text: '新建', color: 'error' },
  confirmed: { text: '已确认', color: 'processing' },
  false_alarm: { text: '误报', color: 'default' },
  resolved: { text: '已关闭', color: 'success' },
}

export function RiskTag({ level }: { level?: string | null }) {
  if (!level) return <Tag>—</Tag>
  return <Tag color={RISK_COLOR[level] || 'default'}>{level}</Tag>
}

export function OrderStatusTag({ status }: { status?: string | null }) {
  const it = status ? ORDER_STATUS[status] : undefined
  return <Tag color={it?.color || 'default'}>{it?.text || status || '—'}</Tag>
}

export function AlarmStatusTag({ status }: { status?: string | null }) {
  const it = status ? ALARM_STATUS[status] : undefined
  return <Tag color={it?.color || 'default'}>{it?.text || status || '—'}</Tag>
}
