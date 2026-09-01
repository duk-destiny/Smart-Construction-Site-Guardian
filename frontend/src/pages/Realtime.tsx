import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert, App as AntApp, Badge, Button, Popconfirm, Select, Space,
  Table, Tag,
} from 'antd'
import { motion } from 'framer-motion'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import { getToken } from '../api/client'
import { mediaUrl } from '../shared/media'
import { ALARM_WAV_B64 } from '../shared/alarmAudio'
import type { AlarmRow } from '../api/types'
import { AlarmStatusTag } from '../components/Tags'
import { useAuth } from '../auth/AuthContext'
import PageHeader from '../components/PageHeader'

const STATUSES = [
  { value: 'new', label: '新建' },
  { value: 'confirmed', label: '已确认' },
  { value: 'false_alarm', label: '误报' },
  { value: 'resolved', label: '已关闭' },
]

interface FrameMsg {
  type: 'frame'
  seq: number
  jpeg: string
  status: string
  level: 'safe' | 'warning' | 'critical'
  boxes: { label: string; conf: number; severity: string; track_id?: number }[]
  alarms: { id: string; cls: string; conf: number; label: string }[]
  cost_ms: number
  ts: number
}

function playAlarm(cooldownRef: { current: number }) {
  const now = Date.now()
  if (now - cooldownRef.current < 5000) return
  cooldownRef.current = now
  try {
    new Audio(`data:audio/wav;base64,${ALARM_WAV_B64}`).play()
      .catch(() => {/* 自动播放被拦截 */})
  } catch { /* 音频失败不影响监测 */ }
}

function LiveView() {
  const { notification } = AntApp.useApp()
  const [sources, setSources] = useState<{ index: number; source: string }[]>([])
  const [sourceIdx, setSourceIdx] = useState(0)
  const [connected, setConnected] = useState(false)
  const [meta, setMeta] = useState<{ status: string; level: string; cost_ms: number; seq: number } | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const alarmCooldown = useRef(0)

  useEffect(() => {
    void ep.realtimeStatus().then((s) => {
      if (s.enabled && s.sources.length) {
        setSources(s.sources)
        setSourceIdx(s.sources[0].index)
      }
    })
  }, [])

  useEffect(() => {
    if (!sources.length) return
    const ws = new WebSocket(
      `/api/ws/realtime?token=${encodeURIComponent(getToken())}&source=${sourceIdx}`)
    wsRef.current = ws
    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onerror = () => setConnected(false)
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data as string) as FrameMsg
      if (msg.type !== 'frame' || !msg.jpeg) return
      const img = new window.Image()
      img.onload = () => {
        const canvas = canvasRef.current
        if (!canvas) return
        canvas.width = img.width
        canvas.height = img.height
        canvas.getContext('2d')?.drawImage(img, 0, 0)
      }
      img.src = `data:image/jpeg;base64,${msg.jpeg}`
      setMeta({ status: msg.status, level: msg.level,
        cost_ms: msg.cost_ms, seq: msg.seq })
      if (msg.alarms?.length) {
        const a = msg.alarms[0]
        notification.error({
          message: `高危告警：${a.label}`,
          description: `置信度 ${Math.round((a.conf || 0) * 100)}% · 告警 ${a.id} · 已推送`,
          duration: 6,
        })
        playAlarm(alarmCooldown)
      }
    }
    return () => { ws.close(); wsRef.current = null }
  }, [sources, sourceIdx, notification])

  const borderColor = meta?.level === 'critical' ? 'var(--accent-primary)'
    : meta?.level === 'warning' ? '#f59e0b' : '#00d4aa'

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5 }}
      style={{ position: 'relative', marginBottom: 24 }}
    >
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16,
      }}>
        <Select value={sourceIdx} style={{ minWidth: 220 }}
          onChange={(v) => setSourceIdx(v)}
          options={sources.map((s) => ({ value: s.index, label: s.source }))} />
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '6px 12px', borderRadius: 8,
          background: connected ? 'rgba(0,212,170,0.06)' : 'rgba(var(--accent-primary-rgb),0.06)',
          border: `1px solid ${connected ? 'rgba(0,212,170,0.15)' : 'rgba(var(--accent-primary-rgb),0.15)'}`,
        }}>
          <div style={{
            width: 6, height: 6, borderRadius: 3,
            background: connected ? '#00d4aa' : 'var(--accent-primary)',
            animation: connected ? 'breathe 2s ease-in-out infinite' : 'none',
          }} />
          <span style={{ fontSize: 12, color: 'rgba(var(--fg-rgb),0.5)' }}>
            {connected ? '已连接' : '未连接'}
          </span>
        </div>
        {meta && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 12, marginLeft: 'auto',
          }}>
            <Tag style={{
              background: meta.level === 'critical' ? 'rgba(var(--accent-primary-rgb),0.15)' :
                meta.level === 'warning' ? 'rgba(245,158,11,0.1)' : 'rgba(0,212,170,0.1)',
              border: 'none',
              color: meta.level === 'critical' ? 'var(--accent-primary)' :
                meta.level === 'warning' ? '#f59e0b' : '#00d4aa',
              fontWeight: 600,
            }}>{meta.status}</Tag>
            <span className="mono" style={{ fontSize: 11, color: 'rgba(var(--fg-rgb),0.3)' }}>
              #{meta.seq} · {meta.cost_ms}ms
            </span>
          </div>
        )}
      </div>

      <div style={{
        position: 'relative',
        borderRadius: 16,
        overflow: 'hidden',
        background: '#000',
        border: `2px solid ${meta ? borderColor : 'rgba(var(--fg-rgb),0.06)'}`,
        boxShadow: meta ? `0 0 30px ${borderColor}33, 0 0 60px ${borderColor}11` : 'none',
        transition: 'border-color 0.3s, box-shadow 0.3s',
      }}>
        <canvas ref={canvasRef} style={{ display: 'block', width: '100%', maxHeight: 560 }} />
        {!meta && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            color: 'rgba(var(--fg-rgb),0.2)', fontSize: 14,
          }}>等待视频帧…</div>
        )}

        {meta && (
          <>
            <div style={{
              position: 'absolute', top: 12, left: 12,
              padding: '6px 10px', borderRadius: 8,
              background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(8px)',
              fontSize: 11, color: 'rgba(var(--fg-rgb),0.6)',
              fontFamily: 'var(--font-mono)',
            }}>
              LIVE
              <span style={{
                display: 'inline-block', width: 5, height: 5, borderRadius: 3,
                background: 'var(--accent-primary)', marginLeft: 6,
                animation: 'pulse-glow 1.5s ease-in-out infinite',
              }} />
            </div>
            <div style={{
              position: 'absolute', bottom: 12, right: 12,
              padding: '6px 10px', borderRadius: 8,
              background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(8px)',
              fontSize: 11, color: 'rgba(var(--fg-rgb),0.4)',
              fontFamily: 'var(--font-mono)',
            }}>
              {dayjs().format('HH:mm:ss')}
            </div>
          </>
        )}
      </div>

      <div style={{
        marginTop: 10, display: 'flex', alignItems: 'center', gap: 12,
        fontSize: 11, color: 'rgba(var(--fg-rgb),0.25)',
      }}>
        <span>红框=不合规 / 黄框=警告 / 绿框=合规</span>
        <Button size="small" type="link" onClick={() => playAlarm({ current: 0 })}
          style={{ color: 'rgba(var(--fg-rgb),0.3)', fontSize: 11, padding: 0 }}>
          试听警报
        </Button>
      </div>
    </motion.div>
  )
}

function AlarmsTable() {
  const { user } = useAuth()
  const { message } = AntApp.useApp()
  const [rows, setRows] = useState<AlarmRow[]>([])

  const load = useCallback(async () => {
    setRows(await ep.listAlarms())
  }, [])

  useEffect(() => { void load() }, [load])

  async function setStatus(a: AlarmRow, status: string) {
    await ep.updateAlarmStatus(a.id, status)
    message.success(`告警 ${a.id} 状态已更新`)
    await load()
  }

  async function convert(a: AlarmRow) {
    const res = await ep.convertAlarm(a.id)
    message.success(`已转为整改工单 ${res.order_id}`)
    await load()
  }

  return (
    <Table<AlarmRow> size="small" rowKey="id" dataSource={rows}
      pagination={{ pageSize: 8 }}
      columns={[
        { title: '告警', dataIndex: 'id', width: 150 },
        { title: '类别', dataIndex: 'cls', width: 110 },
        { title: '置信度', dataIndex: 'conf', width: 90,
          render: (v: number) => v ? <span className="mono">{Math.round(v * 100)}%</span> : null },
        { title: '来源', dataIndex: 'source', width: 130, ellipsis: true },
        { title: '条款', dataIndex: 'clause', ellipsis: true },
        { title: '证据', dataIndex: 'image_path', width: 110,
          render: (p: string) => p
            ? <img src={mediaUrl(p)} alt="" style={{
                width: 72, height: 54, objectFit: 'cover', borderRadius: 6,
                border: '1px solid rgba(var(--fg-rgb),0.06)',
              }} />
            : <Tag>无截图</Tag> },
        { title: '状态', dataIndex: 'status', width: 100,
          render: (_, r) => <AlarmStatusTag status={r.status} /> },
        { title: '时间', dataIndex: 'created_at', width: 160,
          render: (d: string) => d && <span className="mono">{dayjs(d).format('MM-DD HH:mm:ss')}</span> },
        ...(user?.role === 'admin' || user?.role === 'safety' ? [{
          title: '操作', width: 230, render: (_: unknown, r: AlarmRow) => (
            <Space>
              <Select size="small" value={r.status} style={{ width: 108 }}
                options={STATUSES} onChange={(v) => void setStatus(r, v)} />
              {['new', 'confirmed'].includes(r.status) && (
                <Popconfirm title="确认转为整改工单？"
                  onConfirm={() => void convert(r)}>
                  <Button size="small">转工单</Button>
                </Popconfirm>
              )}
            </Space>
          ),
        }] : []),
      ]} />
  )
}

export default function Realtime() {
  const [hubStatus, setHubStatus] = useState<Awaited<ReturnType<typeof ep.realtimeStatus>> | null>(null)

  useEffect(() => {
    void ep.realtimeStatus().then(setHubStatus)
    const t = setInterval(() => void ep.realtimeStatus().then(setHubStatus), 10_000)
    return () => clearInterval(t)
  }, [])

  return (
    <>
      <PageHeader title="实时监测" subtitle="WebSocket 视频流 · 共享推理后端" />

      <div style={{
        display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap',
      }}>
        {[
          { label: 'HUB', value: hubStatus?.running ? '运行中' : '未启用', color: hubStatus?.running ? '#00d4aa' : 'rgba(var(--fg-rgb),0.3)' },
          { label: '观看者', value: String(hubStatus?.viewers ?? 0), mono: true },
          { label: '推理帧', value: String(hubStatus?.polls ?? 0), mono: true },
          { label: '告警', value: String(hubStatus?.alarms ?? 0), mono: true },
          ...(hubStatus?.running ? [{ label: '帧率', value: `${hubStatus.target_fps ?? '—'} fps`, mono: true }] : []),
        ].map((s) => (
          <div key={s.label} style={{
            padding: '12px 18px', borderRadius: 12,
            background: 'rgba(var(--fg-rgb),0.02)',
            border: '1px solid rgba(var(--fg-rgb),0.06)',
            minWidth: 100,
          }}>
            <div style={{
              fontSize: 10, color: 'rgba(var(--fg-rgb),0.3)',
              textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6,
            }}>{s.label}</div>
            <div className={s.mono ? 'mono' : ''} style={{
              fontSize: s.mono ? 18 : 13, fontWeight: 600, color: s.color || '#fff',
            }}>{s.value}</div>
          </div>
        ))}
      </div>

      {hubStatus?.enabled
        ? <LiveView />
        : <Alert style={{ marginBottom: 20 }} type="warning" showIcon
            message="实时 Hub 未启用"
            description="在 config.yaml 设 realtime.enabled=true 并重启 API 后，本页展示共享推理的实时画面。当前为告警列表模式。" />}
      <AlarmsTable />
    </>
  )
}
