import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp, Button, Empty, Form, Input, Tag, Upload,
} from 'antd'
import { CameraOutlined, SendOutlined } from '@ant-design/icons'
import { motion } from 'framer-motion'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import type { OrderRow } from '../api/types'
import { OrderStatusTag, RiskTag } from '../components/Tags'
import PageHeader from '../components/PageHeader'

function deadlineInfo(deadline: string | null): { text: string; color: string; pct: number } | null {
  if (!deadline) return null
  const total = 48
  const hours = dayjs(deadline).diff(dayjs(), 'hour')
  if (hours < 0) return { text: `已逾期 ${-hours} 小时`, color: '#c8102e', pct: 100 }
  if (hours < 24) return { text: `剩余 ${hours} 小时`, color: '#f59e0b', pct: ((24 - hours) / 24) * 100 }
  return { text: `剩余 ${Math.round(hours / 24)} 天`, color: '#00d4aa', pct: ((48 - hours) / 48) * 100 }
}

function RectifyForm({ order, onDone }: { order: OrderRow; onDone: () => void }) {
  const { message } = AntApp.useApp()
  const [note, setNote] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [loading, setLoading] = useState(false)

  async function submit() {
    if (!note.trim()) {
      message.warning('请填写整改说明')
      return
    }
    setLoading(true)
    try {
      const res = await ep.submitRectification(order.id, note, files)
      message.success(res.message)
      onDone()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      marginTop: 16, paddingTop: 16,
      borderTop: '1px solid rgba(255,255,255,0.06)',
    }}>
      <Input.TextArea
        value={note} onChange={(e) => setNote(e.target.value)}
        rows={2} placeholder="整改说明（必填，如：已清理现场并补充灭火器）" />
      <Upload
        listType="picture-card" maxCount={4} accept="image/*"
        beforeUpload={() => false}
        onChange={({ fileList }) => setFiles(fileList.map((f) => f.originFileObj as File))}
      >
        <div style={{ padding: 4 }}>
          <CameraOutlined />
          <div style={{ fontSize: 12 }}>拍照/传图</div>
        </div>
      </Upload>
      <Button type="primary" block icon={<SendOutlined />} loading={loading}
        onClick={submit} size="large"
        style={{
          height: 44, borderRadius: 12, fontWeight: 600, marginTop: 8,
          background: 'linear-gradient(135deg, #c8102e 0%, #9b0a22 100%)',
        }}>
        提交整改，等待验收
      </Button>
    </div>
  )
}

export default function MyOrders() {
  const [rows, setRows] = useState<OrderRow[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRows(await ep.listMyOrders())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <>
      <PageHeader title="我的整改单" subtitle="待处理的安全整改任务" />
      {loading && <div style={{ textAlign: 'center', padding: 40 }}>加载中...</div>}
      {!loading && rows.length === 0 && (
        <Empty description="当前没有待整改的工单" />
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 640, margin: '0 auto' }}>
        {rows.map((r, i) => {
          const dl = deadlineInfo(r.deadline)
          const canSubmit = ['open', 'rejected'].includes(r.status)
          const borderColor = dl?.color || 'rgba(255,255,255,0.06)'
          return (
            <motion.div
              key={r.id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
              style={{
                padding: 20,
                borderRadius: 16,
                background: 'rgba(255,255,255,0.02)',
                border: `1px solid ${borderColor}33`,
                borderLeft: `3px solid ${borderColor}`,
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                <RiskTag level={r.risk_level} />
                <OrderStatusTag status={r.status} />
                <span className="mono" style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)' }}>
                  {r.id.slice(0, 12)}...
                </span>
                {dl && (
                  <span style={{
                    marginLeft: 'auto', fontSize: 12, fontWeight: 600,
                    color: dl.color, fontFamily: 'var(--font-mono)',
                  }}>
                    {dl.text}
                  </span>
                )}
              </div>

              {dl && dl.pct > 0 && (
                <div style={{
                  height: 2, borderRadius: 1, marginBottom: 12,
                  background: 'rgba(255,255,255,0.04)',
                  overflow: 'hidden',
                }}>
                  <div style={{
                    height: '100%', borderRadius: 1,
                    width: `${Math.min(dl.pct, 100)}%`,
                    background: dl.color,
                    transition: 'width 0.5s ease',
                  }} />
                </div>
              )}

              <p style={{ margin: '6px 0', fontSize: 14, color: 'rgba(255,255,255,0.7)' }}>
                <span style={{ color: 'rgba(255,255,255,0.35)', fontSize: 12 }}>隐患 </span>
                {r.hazard_desc}
              </p>
              <p style={{ margin: '6px 0', fontSize: 13, color: 'rgba(255,255,255,0.5)' }}>
                <span style={{ color: 'rgba(255,255,255,0.35)', fontSize: 12 }}>要求 </span>
                {r.requirement || '—'}
              </p>
              {r.clause && (
                <p style={{ margin: '6px 0', fontSize: 12, color: 'rgba(255,255,255,0.3)' }}>
                  {r.clause}
                </p>
              )}
              {r.status === 'rejected' && (
                <div style={{
                  marginTop: 8, padding: '8px 12px', borderRadius: 8,
                  background: 'rgba(200,16,46,0.06)',
                  border: '1px solid rgba(200,16,46,0.15)',
                  fontSize: 13, color: '#c8102e',
                }}>
                  驳回原因：{r.review_reason || '—'}
                </div>
              )}
              {r.status === 'submitted' && (
                <div style={{
                  marginTop: 8, padding: '8px 12px', borderRadius: 8,
                  background: 'rgba(59,130,246,0.06)',
                  border: '1px solid rgba(59,130,246,0.15)',
                  fontSize: 13, color: '#3b82f6',
                }}>
                  已提交「{r.submitted_note}」，等待安全员验收
                </div>
              )}
              {canSubmit && <RectifyForm order={r} onDone={load} />}
            </motion.div>
          )
        })}
      </div>
    </>
  )
}
