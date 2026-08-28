/** 实时监测页（Phase 4）：WebSocket 帧广播 + canvas 渲染 + 告警弹窗/声音。
 *
 * 后端单推理循环（api.realtime_hub）：N 个观看者共享同一路推理；
 * 本页仅消费广播（最新帧 seq 去重），推理成本与观看人数无关。
 * Hub 未启用时降级为告警只读列表（Phase 3 占位行为保留）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert, App as AntApp, Badge, Button, Card, Image as AntImage,
  Popconfirm, Select, Space, Statistic, Table, Tag,
} from 'antd'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import { getToken } from '../api/client'
import { mediaUrl } from '../shared/media'
import { ALARM_WAV_B64 } from '../shared/alarmAudio'
import type { AlarmRow } from '../api/types'
import { AlarmStatusTag } from '../components/Tags'
import { useAuth } from '../auth/AuthContext'

const STATUSES = ['new', 'confirmed', 'false_alarm', 'resolved']
  .map((v) => ({ value: v }))

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

/** 800Hz 警报音：base64 WAV（与 Streamlit 实时页同源生成）。
 *  自动播放受浏览器策略限制时，首次点击画面任意处即解锁。 */
function playAlarm(cooldownRef: { current: number }) {
  const now = Date.now()
  if (now - cooldownRef.current < 5000) return  // 客户端 5s 冷却防轰炸
  cooldownRef.current = now
  try {
    new Audio(`data:audio/wav;base64,${ALARM_WAV_B64}`).play()
      .catch(() => {/* 自动播放被拦截：用户点击画面后即解锁 */})
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
          message: `🚨 高危告警：${a.label}`,
          description: `置信度 ${Math.round((a.conf || 0) * 100)}% · 告警 ${a.id} · 已推送`,
          duration: 6,
        })
        playAlarm(alarmCooldown)
      }
    }
    return () => { ws.close(); wsRef.current = null }
  }, [sources, sourceIdx, notification])

  const levelColor = meta?.level === 'critical' ? '#c8102e'
    : meta?.level === 'warning' ? '#faad14' : '#52c41a'

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select value={sourceIdx} style={{ minWidth: 220 }}
          onChange={(v) => setSourceIdx(v)}
          options={sources.map((s) => ({ value: s.index, label: s.source }))} />
        <Badge status={connected ? 'processing' : 'error'}
          text={connected ? 'WebSocket 已连接（共享后端单路推理）' : '未连接'} />
        {meta && <>
          <Tag color={meta.level === 'critical' ? 'red'
            : meta.level === 'warning' ? 'gold' : 'green'}>{meta.status}</Tag>
          <span style={{ color: '#888', fontSize: 12 }}>
            帧序 #{meta.seq} · 本帧 {meta.cost_ms}ms
          </span>
        </>}
      </Space>
      <div style={{
        background: '#111', borderRadius: 8, minHeight: 320,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: `3px solid ${meta ? levelColor : '#333'}`,
      }}>
        <canvas ref={canvasRef} style={{ maxWidth: '100%', maxHeight: 560 }} />
        {!meta && <span style={{ color: '#666' }}>等待视频帧…</span>}
      </div>
      <div style={{ marginTop: 8, color: '#888', fontSize: 12 }}>
        红框=不合规（当帧告警+声音） / 黄框=警告 / 绿框=合规；声音被浏览器拦截时，点击画面即可解锁。
        <Button size="small" type="link" onClick={() => playAlarm({ current: 0 })}>
          手动试听警报
        </Button>
      </div>
    </div>
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
          render: (v: number) => v && `${Math.round(v * 100)}%` },
        { title: '来源', dataIndex: 'source', width: 130, ellipsis: true },
        { title: '条款', dataIndex: 'clause', ellipsis: true },
        { title: '证据', dataIndex: 'image_path', width: 110,
          render: (p: string) => p
            ? <AntImage width={72} height={54} src={mediaUrl(p)} />
            : <Tag>无截图</Tag> },
        { title: '状态', dataIndex: 'status', width: 100,
          render: (_, r) => <AlarmStatusTag status={r.status} /> },
        { title: '时间', dataIndex: 'created_at', width: 160,
          render: (d: string) => d && dayjs(d).format('MM-DD HH:mm:ss') },
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
    <Card title="📷 实时监测">
      <Space style={{ marginBottom: 12 }} size={24}>
        <Statistic title="Hub 状态" valueRender={() => (
          <span>
            {hubStatus?.running
              ? <Tag color="success">运行中</Tag>
              : <Tag>未启用（config.realtime.enabled）</Tag>}
          </span>
        )} />
        <Statistic title="观看者" value={hubStatus?.viewers ?? 0} />
        <Statistic title="累计推理帧" value={hubStatus?.polls ?? 0} />
        <Statistic title="产生告警" value={hubStatus?.alarms ?? 0} />
        {hubStatus?.running && (
          <Statistic title="当前帧率" value={`${hubStatus.target_fps ?? '—'} fps`} />
        )}
      </Space>
      {hubStatus?.enabled
        ? <LiveView />
        : <Alert style={{ marginBottom: 16 }} type="warning" showIcon
            message="实时 Hub 未启用：在 config.yaml 设 realtime.enabled=true 并重启 API 后，本页展示共享推理的实时画面（后端单推理循环，多端观看零额外推理成本）。当前为告警列表模式。" />}
      <AlarmsTable />
    </Card>
  )
}
