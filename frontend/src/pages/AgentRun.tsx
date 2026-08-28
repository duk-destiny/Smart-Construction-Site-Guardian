/** 多 Agent 研判页：进度轮询（1.5s）→ 结果面板 → Agent 证据链分步展示。
 *
 * 文字单（无视觉链路）直接显示工单卡；影像单在 auto_run 后轮询
 * progress/result；完成态展示各 Agent 耗时与输出摘要（agent_runs）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Descriptions, Result, Space, Spin,
  Steps, Tag, Timeline, Typography,
} from 'antd'
import { useParams } from 'react-router-dom'
import * as ep from '../api/endpoints'
import type { AgentRunRow, TaskDetail } from '../api/types'
import { RiskTag } from '../components/Tags'

const AGENT_CN: Record<string, string> = {
  vision: '👁 视觉检测', rule: '📜 规范合规', fusion: '⚖️ 融合定级',
  action: '📋 处置工单', review: '🔍 人工复核',
}

interface RunResult {
  status: string
  payload: Record<string, unknown>
}

function payloadRisk(payload?: Record<string, unknown>): string {
  if (!payload) return '—'
  const fusion = payload['fusion'] as Record<string, unknown> | undefined
  const action = payload['action'] as Record<string, unknown> | undefined
  return String(payload['risk_level'] || fusion?.['risk_level']
    || action?.['risk_level'] || '—')
}

function payloadWorkOrder(payload?: Record<string, unknown>): Record<string, string> {
  if (!payload) return {}
  const action = payload['action'] as Record<string, unknown> | undefined
  return ((action?.['work_order'] || {}) as Record<string, string>)
}

export default function AgentRun() {
  const { taskId = '' } = useParams()
  const { message } = AntApp.useApp()
  const [progress, setProgress] = useState<Record<string, { status: string; cost_ms: number }>>({})
  const [result, setResult] = useState<RunResult | null>(null)
  const [runs, setRuns] = useState<AgentRunRow[]>([])
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [starting, setStarting] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval>>()

  const finish = useCallback(async () => {
    try {
      setResult(await ep.getResult(taskId))
    } catch { /* 404 即未就绪 */ }
    try {
      setRuns(await ep.getAgentRuns(taskId))
    } catch { /* 文字单无运行链路 */ }
    try {
      setDetail(await ep.getTaskDetail(taskId))
    } catch { /* 任务不存在 */ }
  }, [taskId])

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    const tick = async () => {
      try {
        const prog = await ep.getProgress(taskId)
        if (!cancelled) setProgress(prog)
        const res = await ep.getResult(taskId)
        if (!cancelled && res) {
          setResult(res)
          clearInterval(timer.current)
          await finish()
        }
      } catch { /* 轮询 404 属正常态 */ }
    }
    void tick()
    timer.current = setInterval(tick, 1500)
    return () => {
      cancelled = true
      clearInterval(timer.current)
    }
  }, [taskId, finish])

  async function startRun() {
    setStarting(true)
    try {
      await ep.startRun(taskId, { permit_info: {}, scene_id: 'hot_work' })
      message.info('后台研判已启动')
    } finally {
      setStarting(false)
    }
  }

  const entries = Object.entries(progress)
  const running = entries.some(([, v]) => v.status === 'running')
  const wo = payloadWorkOrder(result?.payload)

  const stepIndex = (() => {
    if (result) return AGENT_ORDER.length
    const done = entries.filter(([, v]) => v.status !== 'running').length
    return Math.min(done, AGENT_ORDER.length - 1)
  })()

  return (
    <Card title={`🤖 多 Agent 研判 · ${taskId || '（无任务）'}`}>
      {!taskId && <Alert type="info" message="请从「统一上报 → 影像研判」发起任务" />}

      {taskId && !result && (
        <div style={{ textAlign: 'center', padding: '24px 0' }}>
          {entries.length === 0
            ? <>
                <Alert style={{ marginBottom: 16, maxWidth: 520, margin: '0 auto 16px' }}
                  type="info" message="尚未开始研判" />
                <Button type="primary" loading={starting} onClick={startRun}>
                  开始 / 重试多 Agent 研判
                </Button>
              </>
            : <>
                <Spin style={{ marginBottom: 16 }} />
                <Steps size="small" direction="horizontal"
                  current={running ? stepIndex : stepIndex + 1}
                  items={AGENT_ORDER.map((a) => ({
                    title: AGENT_CN[a] || a,
                    status: progress[a]?.status === 'running' ? 'process' as const
                      : progress[a] ? 'finish' as const : 'wait' as const,
                  }))} />
                <Typography.Text type="secondary">
                  后台研判进行中，页面每 1.5 秒自动轮询进度…
                </Typography.Text>
              </>}
        </div>
      )}

      {result && (
        <>
          {result.status !== 'success'
            ? <Result status="error" title="研判失败"
                subTitle={String(result.payload?.['error'] ?? '请重试或检查模型配置')} />
            : <Result status="success" title="研判完成"
                subTitle={`风险等级：${payloadRisk(result.payload)}`}
                extra={<Space>
                  <RiskTag level={payloadRisk(result.payload)} />
                  <Button type="primary" href="/orders">去工单页派发</Button>
                </Space>} />}
          {Object.keys(wo).length > 0 && (
            <Card size="small" title="处置工单" style={{ marginTop: 8 }}>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="隐患">{wo['hazard_desc']}</Descriptions.Item>
                <Descriptions.Item label="违反规范">{wo['clause'] || '—'}</Descriptions.Item>
                <Descriptions.Item label="整改要求">{wo['requirement']}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
        </>
      )}

      {detail?.risk && (
        <Alert style={{ marginTop: 12 }} type="info" showIcon
          message={<>当前风险：<RiskTag level={detail.risk['override_level'] as string
            || detail.risk['risk_level'] as string} />
            {detail.risk['override_reason']
              ? `（改判原因：${detail.risk['override_reason']}）` : ''}</>} />
      )}

      {runs.length > 0 && (
        <Card size="small" title="Agent 运行证据链" style={{ marginTop: 16 }}>
          <Timeline items={runs.map((r) => ({
            color: r.status === 'success' ? 'green' : r.status === 'failed' ? 'red' : 'blue',
            children: (
              <>
                <b>{AGENT_CN[r.agent] || r.agent}</b>
                <Tag style={{ marginLeft: 8 }}>{r.status}</Tag>
                <Tag>{r.cost_ms} ms</Tag>
                {r.error && <Typography.Text type="danger">{r.error}</Typography.Text>}
                <pre style={{
                  margin: '4px 0 0', fontSize: 12, maxHeight: 160,
                  overflow: 'auto', background: '#fafafa', padding: 8,
                }}>{r.output_json}</pre>
              </>
            ),
          }))} />
        </Card>
      )}
    </Card>
  )
}

const AGENT_ORDER = ['vision', 'rule', 'fusion', 'action']
