import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert, App as AntApp, Button, Descriptions, Space, Spin, Tag, Typography,
} from 'antd'
import { motion, AnimatePresence } from 'framer-motion'
import { useParams } from 'react-router-dom'
import * as ep from '../api/endpoints'
import type { AgentRunRow, TaskDetail } from '../api/types'
import { RiskTag } from '../components/Tags'
import MediaUploadForm from '../components/MediaUploadForm'
import PageHeader from '../components/PageHeader'

const AGENT_CN: Record<string, string> = {
  vision: '视觉检测', rule: '规范合规', fusion: '融合定级',
  action: '处置工单', review: '人工复核',
  llm_assist: 'LLM 辅助研判',
}

const AGENT_ICONS: Record<string, string> = {
  vision: '👁', rule: '📜', fusion: '⚖️', action: '📋', review: '🔍', llm_assist: '🧠',
}

const AGENT_ORDER = ['vision', 'rule', 'fusion', 'action']

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
  const actionPayload = action?.['payload'] as Record<string, unknown> | undefined
  const wo = actionPayload?.['work_order'] || action?.['work_order']
    || payload['work_order'] || {}
  return wo as Record<string, string>
}

export default function AgentRun() {
  const { taskId = '' } = useParams()
  const { message } = AntApp.useApp()
  const [progress, setProgress] = useState<Record<string, { status: string; cost_ms: number }>>({})
  const [result, setResult] = useState<RunResult | null>(null)
  const [runs, setRuns] = useState<AgentRunRow[]>([])
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [starting, setStarting] = useState(false)
  // 乐观渲染：后端热态下研判可能在首个轮询 tick 前就完成，先置位保证进度块可见
  const [optimistic, setOptimistic] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval>>()

  const finish = useCallback(async () => {
    try { setResult(await ep.getResult(taskId)) } catch { /* 404 */ }
    try { setRuns(await ep.getAgentRuns(taskId)) } catch { /* 文字单无视觉链路 */ }
    try { setDetail(await ep.getTaskDetail(taskId)) } catch { /* 任务不存在 */ }
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
          setOptimistic(false)
          clearInterval(timer.current)
          await finish()
        }
      } catch { /* 轮询 404 属正常态 */ }
    }
    // 结果是取走即删语义：回看已消费任务时轮询永远等不到 result，
    // 证据链/风险信息必须在挂载时主动加载一次
    void finish()
    void tick()
    timer.current = setInterval(tick, 1500)
    return () => { cancelled = true; clearInterval(timer.current) }
  }, [taskId, finish])

  async function startRun() {
    setStarting(true)
    try {
      await ep.startRun(taskId, { permit_info: {}, scene_id: 'hot_work' })
      message.info('后台研判已启动')
      setOptimistic(true)
    } finally {
      setStarting(false)
    }
  }

  const entries = Object.entries(progress)
  const anyRunning = entries.some(([, v]) => v.status === "running")
  const wo = payloadWorkOrder(result?.payload)

  return (
    <>
      <PageHeader
        title="影像研判"
        subtitle={taskId ? `任务 ${taskId.slice(0, 16)}...` : '取证上传 · 五段流水线 · 证据链可回溯'}
      />

      {/* 影像研判窗口（v2.2）：无任务时显示发起表单（原统一上报影像 Tab 迁入） */}
      {!taskId && <MediaUploadForm />}

      {taskId && !result && (entries.length === 0 || !anyRunning) && (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          {entries.length > 0 && (
            <Alert style={{ maxWidth: 520, margin: '0 auto 16px' }}
              type="warning"
              message="上一轮研判结果已被取走或已过期，可重新发起研判" />
          )}
          <Button type="primary" loading={starting} onClick={startRun}
            style={{
              height: 44, paddingInline: 32, borderRadius: 12, fontWeight: 600,
              background: 'linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-primary-deep) 100%)',
            }}>
            开始 / 重试影像研判
          </Button>
        </div>
      )}

      {taskId && !result && (anyRunning || optimistic) && (
        <div style={{ padding: '32px 0' }}>
          <div style={{ textAlign: 'center', marginBottom: 32 }}>
            <Spin style={{ marginBottom: 16 }} />
            <Typography.Text type="secondary">
              后台研判进行中，每 1.5 秒自动轮询…
            </Typography.Text>
          </div>
          <div style={{
            display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap',
          }}>
            {AGENT_ORDER.map((agent, i) => {
              const p = progress[agent]
              const isRunning = p?.status === 'running'
              const isDone = p && p.status !== 'running'
              return (
                <motion.div
                  key={agent}
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: i * 0.1 }}
                  style={{
                    padding: '16px 24px',
                    borderRadius: 14,
                    background: isRunning ? 'rgba(var(--accent-primary-rgb),0.08)' : isDone ? 'rgba(0,212,170,0.06)' : 'rgba(var(--fg-rgb),0.03)',
                    border: `1px solid ${isRunning ? 'rgba(var(--accent-primary-rgb),0.2)' : isDone ? 'rgba(0,212,170,0.15)' : 'rgba(var(--fg-rgb),0.06)'}`,
                    textAlign: 'center',
                    minWidth: 120,
                    position: 'relative',
                    overflow: 'hidden',
                  }}
                >
                  {isRunning && (
                    <motion.div
                      animate={{ x: ['-100%', '200%'] }}
                      transition={{ duration: 1.5, repeat: Infinity, ease: 'linear' }}
                      style={{
                        position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                        background: 'linear-gradient(90deg, transparent, rgba(var(--accent-primary-rgb),0.06), transparent)',
                      }}
                    />
                  )}
                  <div style={{ fontSize: 24, marginBottom: 8 }}>{AGENT_ICONS[agent]}</div>
                  <div style={{
                    fontSize: 13, fontWeight: 600,
                    color: isRunning ? 'var(--accent-primary)' : isDone ? '#00d4aa' : 'rgba(var(--fg-rgb),0.3)',
                  }}>{AGENT_CN[agent]}</div>
                  {p && (
                    <div className="mono" style={{
                      fontSize: 11, marginTop: 6,
                      color: 'rgba(var(--fg-rgb),0.3)',
                    }}>{p.cost_ms}ms</div>
                  )}
                  <div style={{
                    position: 'absolute', top: 8, right: 8,
                    width: 6, height: 6, borderRadius: 3,
                    background: isRunning ? 'var(--accent-primary)' : isDone ? '#00d4aa' : 'rgba(var(--fg-rgb),0.1)',
                    boxShadow: isRunning ? '0 0 8px rgba(var(--accent-primary-rgb),0.5)' : isDone ? '0 0 6px rgba(0,212,170,0.4)' : 'none',
                  }} />
                </motion.div>
              )
            })}
          </div>
        </div>
      )}

      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            {result.status === 'failed'
              ? <div style={{
                  padding: 24, borderRadius: 14, textAlign: 'center',
                  background: 'rgba(var(--accent-primary-rgb),0.06)', border: '1px solid rgba(var(--accent-primary-rgb),0.2)',
                }}>
                  <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--accent-primary)', marginBottom: 8 }}>
                    研判失败
                  </div>
                  <div style={{ color: 'rgba(var(--fg-rgb),0.5)' }}>
                    {String(result.payload?.['error'] ?? '请重试或检查模型配置')}
                  </div>
                </div>
              : <>
            {result.status === 'degraded' && (
              <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                message="部分能力降级（研判结论仍有效）"
                description={String(result.payload?.['error']
                  ?? '个别辅助环节（如 LLM 辅助/规范检索）不可用，已自动降级；检测、定级与工单结论不受影响。')} />
            )}
            <div style={{
                  padding: 24, borderRadius: 14,
                  background: 'rgba(0,212,170,0.04)', border: '1px solid rgba(0,212,170,0.15)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
                    <div style={{
                      width: 40, height: 40, borderRadius: 20,
                      background: 'rgba(0,212,170,0.1)', display: 'flex',
                      alignItems: 'center', justifyContent: 'center', fontSize: 20,
                    }}>✓</div>
                    <div>
                      <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-strong)' }}>研判完成</div>
                      <div style={{ fontSize: 13, color: 'rgba(var(--fg-rgb),0.4)' }}>
                        风险等级：<RiskTag level={payloadRisk(result.payload)} />
                      </div>
                    </div>
                    <Button type="primary" href="/orders" style={{ marginLeft: 'auto' }}>
                      去工单页派发
                    </Button>
                  </div>
                </div>
            </>}
            {(() => {
              const vision = result?.payload?.['vision'] as Record<string, unknown> | undefined
              const dets = (vision?.['payload'] as Record<string, unknown> | undefined)?.['detections']
              if ((result?.status === 'success' || result?.status === 'degraded')
                  && Array.isArray(dets) && dets.length === 0) {
                return <Alert style={{ marginTop: 12 }} type="info" showIcon
                  message="本帧未检出隐患目标"
                  description="模型识别范围有限（火花/烟雾/灭火器/安全帽/反光衣等），普通场景照片没有可识别对象属正常现象。" />
              }
              return null
            })()}
            {Object.keys(wo).length > 0 && (
              <div style={{
                marginTop: 16, padding: 20, borderRadius: 14,
                background: 'rgba(var(--fg-rgb),0.02)', border: '1px solid rgba(var(--fg-rgb),0.06)',
              }}>
                <div style={{
                  fontSize: 12, fontWeight: 600, color: 'rgba(var(--fg-rgb),0.4)',
                  textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 14,
                }}>处置工单</div>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="隐患">{wo['hazard_desc']}</Descriptions.Item>
                  <Descriptions.Item label="违反规范">{wo['clause'] || '—'}</Descriptions.Item>
                  <Descriptions.Item label="整改要求">{wo['requirement']}</Descriptions.Item>
                </Descriptions>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {detail?.risk && (
        <Alert style={{ marginTop: 16 }} type="info" showIcon
          message={<>当前风险：<RiskTag level={detail.risk['override_level'] as string
            || detail.risk['risk_level'] as string} />
            {detail.risk['override_reason']
              ? `（改判原因：${detail.risk['override_reason']}）` : ''}</>} />
      )}

      {(() => {
        const assist = runs.find((r) => r.agent === 'llm_assist')
        if (!assist) return null
        let advice = ''
        try {
          advice = String((JSON.parse(assist.output_json) as { advice?: string }).advice ?? '')
        } catch { /* 证据链摘要格式 */ }
        if (!advice) return null
        return (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
            style={{
              marginTop: 20, padding: 20, borderRadius: 14,
              background: 'rgba(124,77,255,0.04)',
              border: '1px solid rgba(124,77,255,0.15)',
            }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
            }}>
              <span style={{ fontSize: 16 }}>🧠</span>
              <span style={{ fontSize: 13, fontWeight: 600, color: '#7c4dff' }}>
                AI 辅助研判意见
              </span>
              <Tag color="purple" style={{ marginLeft: 'auto' }}>仅辅助理解 · 不改变定级</Tag>
            </div>
            <pre style={{
              whiteSpace: 'pre-wrap', margin: 0,
              fontFamily: 'var(--font-mono)', fontSize: 12,
              color: 'rgba(var(--fg-rgb),0.6)', lineHeight: 1.7,
            }}>{advice}</pre>
          </motion.div>
        )
      })()}

      {runs.length > 0 && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.4 }}
          style={{
            marginTop: 20, padding: 20, borderRadius: 14,
            background: 'rgba(var(--fg-rgb),0.02)', border: '1px solid rgba(var(--fg-rgb),0.06)',
          }}>
          <div style={{
            fontSize: 12, fontWeight: 600, color: 'rgba(var(--fg-rgb),0.4)',
            textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16,
          }}>Agent 运行证据链</div>
          <div style={{ position: 'relative', paddingLeft: 20 }}>
            <div style={{
              position: 'absolute', left: 5, top: 0, bottom: 0, width: 1,
              background: 'rgba(var(--fg-rgb),0.06)',
            }} />
            {runs.map((r, i) => (
              <motion.div
                key={r.agent}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.1 }}
                style={{
                  position: 'relative', marginBottom: 20, paddingLeft: 16,
                }}>
                <div style={{
                  position: 'absolute', left: -18, top: 4,
                  width: 10, height: 10, borderRadius: 5,
                  background: r.status === 'success' ? '#00d4aa' : r.status === 'failed' ? 'var(--accent-primary)' : '#3b82f6',
                  boxShadow: `0 0 8px ${r.status === 'success' ? 'rgba(0,212,170,0.4)' : r.status === 'failed' ? 'rgba(var(--accent-primary-rgb),0.4)' : 'rgba(59,130,246,0.4)'}`,
                }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span style={{ fontWeight: 600, color: 'var(--text-strong)' }}>
                    {AGENT_ICONS[r.agent]} {AGENT_CN[r.agent] || r.agent}
                  </span>
                  <Tag style={{
                    background: r.status === 'success' ? 'rgba(0,212,170,0.1)' : 'rgba(var(--accent-primary-rgb),0.1)',
                    border: 'none',
                    color: r.status === 'success' ? '#00d4aa' : 'var(--accent-primary)',
                    fontSize: 10,
                  }}>{r.status}</Tag>
                  <span className="mono" style={{ fontSize: 11, color: 'rgba(var(--fg-rgb),0.3)' }}>
                    {r.cost_ms}ms
                  </span>
                </div>
                {r.error && (
                  <div style={{ fontSize: 12, color: 'var(--accent-primary)', marginBottom: 4 }}>{r.error}</div>
                )}
                <pre style={{
                  margin: 0, padding: 12, borderRadius: 8,
                  background: 'rgba(0,0,0,0.3)',
                  border: '1px solid rgba(var(--fg-rgb),0.04)',
                  fontSize: 11, maxHeight: 140, overflow: 'auto',
                  fontFamily: 'var(--font-mono)',
                  color: 'rgba(0,212,170,0.7)',
                  lineHeight: 1.6,
                }}>{r.output_json}</pre>
              </motion.div>
            ))}
          </div>
        </motion.div>
      )}
    </>
  )
}
