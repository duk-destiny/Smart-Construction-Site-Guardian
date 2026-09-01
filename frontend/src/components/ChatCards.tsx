/** 对话消息卡片（v2.2 从统一上报页迁出，供 AI 助手窗口复用）。
 *
 * renderChat 承接 /agent/chat 双层响应：认知路径 → CognitiveRun（2s 轮询
 * + 确认卡）；快路径五分支（工单详情/列表/消歧/逾期/统计）渲染零改动。
 */
import { useEffect, useState } from 'react'
import {
  Alert, App as AntApp, Button, Descriptions, Input, Modal, Spin, Table, Tag,
  Typography,
} from 'antd'
import * as ep from '../api/endpoints'
import type { AgentChatCognitive, AgentChatReply, AgentRunProgress } from '../api/types'

export const STATUS_CN: Record<string, string> = {
  open: '待整改', rejected: '驳回重改', submitted: '待验收', closed: '已销项',
}

export function OrderCard({ o }: { o: Record<string, unknown> }) {
  return <Descriptions bordered column={1} size="small" style={{ marginBottom: 12 }}>
    <Descriptions.Item label="工单">{String(o['id'])}</Descriptions.Item>
    <Descriptions.Item label="隐患">{String(o['hazard_desc'] ?? '—')}</Descriptions.Item>
    <Descriptions.Item label="等级">{String(o['risk_level'] ?? '—')}</Descriptions.Item>
    <Descriptions.Item label="状态">{STATUS_CN[String(o['status'])] ?? String(o['status'] ?? '—')}</Descriptions.Item>
    <Descriptions.Item label="责任人">{String(o['assignee_name'] ?? '未派发')}</Descriptions.Item>
    <Descriptions.Item label="截止">{String(o['deadline'] ?? '—')}</Descriptions.Item>
    <Descriptions.Item label="整改要求">{String(o['requirement'] ?? '—')}</Descriptions.Item>
  </Descriptions>
}

const RUN_STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '排队中', color: 'default' },
  running: { label: '执行中', color: 'processing' },
  pending_confirm: { label: '待人工确认', color: 'warning' },
  completed: { label: '已完成', color: 'success' },
  degraded: { label: '已完成（降级）', color: 'gold' },
  failed: { label: '执行失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
}
export const TERMINAL_STATUSES = ['completed', 'degraded', 'failed', 'cancelled']
const STEP_STATUS_ICON: Record<string, string> = {
  pending: '⏳', success: '✅', degraded: '⚠️', failed: '❌',
}

/** 认知路径渲染：2s 轮询 progress，终态停轮询；挂起时渲染确认卡。 */
export function CognitiveRun({ reply }: { reply: AgentChatCognitive }) {
  const { message } = AntApp.useApp()
  const [view, setView] = useState<AgentRunProgress | null>(null)
  const [busyAct, setBusyAct] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [planText, setPlanText] = useState('')
  const runId = reply.run_id

  useEffect(() => {
    if (!runId) return undefined
    let timer: number | undefined
    let stopped = false
    const poll = async () => {
      try {
        const v = await ep.agentProgress(runId)
        if (stopped) return
        setView(v)
        if (!TERMINAL_STATUSES.includes(v.status)) {
          timer = window.setTimeout(() => void poll(), 2000)
        }
      } catch {
        if (!stopped) timer = window.setTimeout(() => void poll(), 2000)
      }
    }
    void poll()
    return () => {
      stopped = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [runId])

  async function doConfirm(modified: Record<string, unknown> | null) {
    if (!runId) return
    setBusyAct(true)
    try {
      await ep.agentConfirm(runId, 'confirm', modified)
      message.success(modified ? '已按修改后的计划续跑' : '已确认，继续执行')
    } catch {
      message.error('确认失败，请重试')
    } finally {
      setBusyAct(false)
    }
  }

  async function doCancel() {
    if (!runId) return
    setBusyAct(true)
    try {
      await ep.agentCancel(runId)
      message.info('已请求取消任务')
    } catch {
      message.error('取消失败，请重试')
    } finally {
      setBusyAct(false)
    }
  }

  if (reply.status === 'busy' || !runId) {
    return <Alert type="warning" showIcon message="认知任务通道繁忙，请稍后重试" />
  }

  const meta = RUN_STATUS_META[view?.status ?? 'pending']
    ?? { label: view?.status ?? '准备中', color: 'default' }
  const stepBy = new Map((view?.steps ?? []).map((s) => [Number(s['step_idx']), s]))

  return (
    <div style={{
      padding: 16, borderRadius: 12,
      background: 'rgba(0,212,170,0.03)', border: '1px solid rgba(0,212,170,0.15)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ fontSize: 15 }}>🧠</span>
        <b style={{ color: 'var(--text-strong)' }}>认知分析已启动</b>
        <Tag color={meta.color}>{meta.label}</Tag>
        {!view && <Spin size="small" />}
        <code style={{
          marginLeft: 'auto', color: 'rgba(var(--fg-rgb),0.3)',
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>{runId}</code>
      </div>
      {view?.plan?.goal ? (
        <div style={{ color: 'rgba(var(--fg-rgb),0.6)', fontSize: 13, marginBottom: 10 }}>
          目标：{view.plan.goal}
        </div>
      ) : null}
      {(view?.plan?.steps ?? []).map((s, i) => {
        const done = stepBy.get(i)
        const st = String(done?.['status'] ?? 'pending')
        return (
          <div key={i} style={{
            display: 'flex', gap: 8, alignItems: 'baseline', padding: '6px 0',
            fontSize: 13, borderTop: '1px solid rgba(var(--fg-rgb),0.05)',
          }}>
            <span>{STEP_STATUS_ICON[st] ?? '⏳'}</span>
            <span style={{ color: 'rgba(var(--fg-rgb),0.75)' }}>
              {s.reason || s.tool}
              <code style={{
                marginLeft: 8, color: 'rgba(var(--fg-rgb),0.3)',
                fontFamily: 'var(--font-mono)', fontSize: 11,
              }}>{s.tool}</code>
            </span>
            {done?.['result_digest'] ? (
              <span style={{
                color: 'rgba(var(--fg-rgb),0.4)', marginLeft: 'auto',
                maxWidth: '45%', textAlign: 'right',
              }}>{String(done['result_digest'])}</span>
            ) : null}
          </div>
        )
      })}

      {view?.status === 'pending_confirm' && (
        <div style={{
          marginTop: 12, padding: 12, borderRadius: 10,
          background: 'rgba(250,173,20,0.06)', border: '1px solid rgba(250,173,20,0.25)',
        }}>
          <div style={{ fontSize: 13, color: '#faad14', fontWeight: 600, marginBottom: 8 }}>
            计划含副作用操作，需人工确认后才执行（可修改计划或取消）
          </div>
          {view.confirm_payload ? (
            <pre style={{
              fontSize: 11, color: 'rgba(var(--fg-rgb),0.5)', whiteSpace: 'pre-wrap',
              fontFamily: 'var(--font-mono)', margin: '0 0 10px',
            }}>{JSON.stringify(view.confirm_payload, null, 2)}</pre>
          ) : null}
          <div style={{ display: 'flex', gap: 8 }}>
            <Button type="primary" size="small" loading={busyAct}
              onClick={() => void doConfirm(null)}>确认执行</Button>
            <Button size="small" onClick={() => {
              setPlanText(JSON.stringify(view.plan ?? {}, null, 2))
              setEditOpen(true)
            }}>修改计划</Button>
            <Button size="small" danger loading={busyAct}
              onClick={() => void doCancel()}>取消任务</Button>
          </div>
        </div>
      )}

      {view && TERMINAL_STATUSES.includes(view.status) && (
        <div style={{
          marginTop: 12, padding: 12, borderRadius: 10,
          background: 'rgba(var(--fg-rgb),0.03)', border: '1px solid rgba(var(--fg-rgb),0.08)',
        }}>
          {view.status === 'failed'
            && <Alert type="error" showIcon message={view.error || '执行失败，已留痕可查证据链'} />}
          {view.status === 'cancelled'
            && <Alert type="info" showIcon message={view.error || '任务已取消，副作用零执行'} />}
          {(view.status === 'completed' || view.status === 'degraded') && (
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, color: 'rgba(var(--fg-rgb),0.85)' }}>
              {view.status === 'degraded' && (
                <Tag color="gold" style={{ marginBottom: 8 }}>
                  {String(view.result?.['degraded_reason'] ?? '').includes('模板档')
                    || String(view.error ?? '').includes('模板档')
                    ? '已完成（确定性模板档，数字带来源标注）' : '降级完成'}
                </Tag>
              )}
              {String(view.result?.['answer'] ?? view.result?.['digest'] ?? '')
                || JSON.stringify(view.result ?? {}, null, 2)}
            </div>
          )}
        </div>
      )}

      <Modal title="修改执行计划（JSON：可删步/改参数）" open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => {
          try {
            const obj = JSON.parse(planText) as Record<string, unknown>
            setEditOpen(false)
            void doConfirm(obj)
          } catch {
            message.error('计划 JSON 格式无效')
          }
        }}>
        <Input.TextArea rows={10} value={planText}
          onChange={(e) => setPlanText(e.target.value)}
          style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }} />
      </Modal>
    </div>
  )
}

export function renderChat(route: AgentChatReply, ask: (t: string) => void) {
  if (route.path === 'cognitive') {
    return <CognitiveRun reply={route} />
  }
  const data = route.data
  if (route.action === 'greeting') {
    return <Alert type="success" showIcon message="你好！我是智护工地安全助手 🤖"
      description={
        <div style={{ lineHeight: 1.9 }}>
          可以帮你：
          <div>· 查工单进度 / 逾期情况 / 本周安全统计（试试快捷提问）</div>
          <div>· 上传影像 / 视频做 AI 分析（工具 → 影像 AI 分析）</div>
          <div>· 生成并解读安全周报</div>
          <div>· 文字线索一键建单，进入派发闭环</div>
          <div style={{ marginTop: 6, fontSize: 12, opacity: 0.7 }}>
            判定与数据链路全本地运行；写操作需人工确认，只读问询绝不写入。
          </div>
        </div>
      } />
  }
  if (route.action === 'order_detail' && data && !Array.isArray(data)) {
    return <OrderCard o={data as Record<string, unknown>} />
  }
  if (route.action === 'order_list' && Array.isArray(data)) {
    const rows = data as Record<string, unknown>[]
    if (!rows.length) return <Alert type="success" message="该范围内暂无工单" />
    return <Table size="small" rowKey={(r) => String(r['id'])} dataSource={rows}
      pagination={{ pageSize: 8 }}
      onRow={(r) => ({ onClick: () => void ask(`#${String(r['id'])} 的进度`),
        style: { cursor: 'pointer' } })}
      columns={[
        { title: '工单', dataIndex: 'id' },
        { title: '隐患', dataIndex: 'hazard_desc', ellipsis: true },
        { title: '等级', dataIndex: 'risk_level', width: 90 },
        { title: '状态', dataIndex: 'status', width: 100,
          render: (s: string) => STATUS_CN[s] ?? s },
        { title: '责任人', dataIndex: 'assignee_name', width: 100,
          render: (v: string | null) => v ?? '未派发' },
        { title: '截止', dataIndex: 'deadline', width: 170 },
      ]} />
  }
  if (route.action === 'confirm_list' && Array.isArray(data)) {
    const rows = data as Record<string, unknown>[]
    return <>
      <Typography.Text type="secondary">匹配到多张，点击选择：</Typography.Text>
      {rows.map((r) => (
        <div key={String(r['id'])} style={{
          padding: '10px 14px', marginBottom: 8, borderRadius: 10,
          background: 'rgba(var(--fg-rgb),0.03)', border: '1px solid rgba(var(--fg-rgb),0.06)',
          cursor: 'pointer', transition: 'all 0.2s',
        }}
          onClick={() => void ask(`#${String(r['id'])} 的进度`)}>
          <b style={{ color: 'var(--text-strong)' }}>{String(r['id'])}</b>
          <span style={{ color: 'rgba(var(--fg-rgb),0.5)', marginLeft: 12 }}>
            {String(r['hazard_desc'] ?? '')}
          </span>
        </div>
      ))}
    </>
  }
  if (route.action === 'overdue_stats' && data) {
    const rows = (data as { rows?: Record<string, unknown>[] }).rows ?? []
    return <>
      <Alert type={rows.length ? 'warning' : 'success'} showIcon
        message={`存量逾期未整改：${rows.length} 张`} />
      {rows.map((r) => (
        <div key={String(r['id'])} style={{ margin: '6px 0' }}>
          <Tag color="volcano">{String(r['risk_level'] ?? '—')}</Tag>
          <code style={{ color: 'rgba(var(--fg-rgb),0.6)', fontFamily: 'var(--font-mono)' }}>
            {String(r['id'])}
          </code>
          <span style={{ color: 'rgba(var(--fg-rgb),0.4)', marginLeft: 8 }}>
            {(String(r['deadline'] ?? '')).slice(0, 19)}
             {String(r['assignee_name'] ?? '未派发')}
          </span>
        </div>
      ))}
    </>
  }
  if (route.action === 'weekly_stats' && data) {
    const st = data as Record<string, unknown>
    return <Descriptions bordered column={2} size="small">
      <Descriptions.Item label="检测帧">{String(st['frames'] ?? 0)}</Descriptions.Item>
      <Descriptions.Item label="不合规帧">{String(st['bad'] ?? 0)}</Descriptions.Item>
      <Descriptions.Item label="新增工单">{String(st['orders_total'] ?? 0)}</Descriptions.Item>
      <Descriptions.Item label="存量逾期">{String(st['overdue_open_now'] ?? 0)}</Descriptions.Item>
    </Descriptions>
  }
  return <Alert type="warning" showIcon
    message={route.hint || '未能理解这个问题'}
    description={route.candidates?.length
      ? `您是想问：${route.candidates.join('、')}` : undefined} />
}
