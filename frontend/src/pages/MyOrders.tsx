/** 我的整改单（responsible）：手机浏览器优先的响应式卡片布局。
 *
 * 卡片内联提交：说明 + 拍照/传图（accept 环境前置摄像头）→ 待验收；
 * 驳回单显示驳回原因；已销项单不在列表（list_by_assignee 排除 closed）。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp, Button, Card, Empty, Form, Input, Tag, Typography, Upload,
} from 'antd'
import { CameraOutlined, SendOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import type { OrderRow } from '../api/types'
import { OrderStatusTag, RiskTag } from '../components/Tags'

function deadlineInfo(deadline: string | null): { text: string; color: string } | null {
  if (!deadline) return null
  const hours = dayjs(deadline).diff(dayjs(), 'hour')
  if (hours < 0) return { text: `已逾期 ${-hours} 小时`, color: '#c8102e' }
  if (hours < 24) return { text: `剩余 ${hours} 小时`, color: '#fa8c16' }
  return { text: `剩余 ${Math.round(hours / 24)} 天`, color: '#52c41a' }
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
    <div style={{ marginTop: 12, borderTop: '1px dashed #eee', paddingTop: 12 }}>
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
        onClick={submit} size="large">
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
    <div style={{ maxWidth: 640, margin: '0 auto' }}>
      <Typography.Title level={4} style={{ textAlign: 'center' }}>
        🧰 我的整改单
      </Typography.Title>
      {loading && <Card loading />}
      {!loading && rows.length === 0 && (
        <Empty description="当前没有待整改的工单 🎉" />
      )}
      {rows.map((r) => {
        const dl = deadlineInfo(r.deadline)
        const canSubmit = ['open', 'rejected'].includes(r.status)
        return (
          <Card key={r.id} size="small" style={{ marginBottom: 12 }}
            title={<span style={{ fontSize: 14 }}>
              <RiskTag level={r.risk_level} />
              <OrderStatusTag status={r.status} />
              <Tag>{r.id.slice(0, 12)}…</Tag>
            </span>}
            extra={dl && <span style={{ color: dl.color, fontWeight: 600, fontSize: 13 }}>
              ⏰ {dl.text}</span>}>
            <p style={{ margin: '4px 0' }}><b>隐患：</b>{r.hazard_desc}</p>
            <p style={{ margin: '4px 0' }}><b>整改要求：</b>{r.requirement || '—'}</p>
            {r.clause && <p style={{ margin: '4px 0', color: '#666' }}>
              <b>依据：</b>{r.clause}</p>}
            {r.status === 'rejected' && (
              <p style={{ margin: '4px 0', color: '#c8102e' }}>
                <b>❌ 驳回原因：</b>{r.review_reason || '—'}</p>
            )}
            {r.status === 'submitted' && (
              <p style={{ margin: '4px 0', color: '#1677ff' }}>
                已提交「{r.submitted_note}」，等待安全员验收。</p>
            )}
            {canSubmit && (
              <RectifyForm order={r} onDone={load} />
            )}
          </Card>
        )
      })}
    </div>
  )
}
