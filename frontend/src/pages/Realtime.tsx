/** 实时监测页（Phase 4 前占位）：告警只读列表 + 误报/转工单操作。
 *
 * Phase 4 完成后此页替换为 WebSocket 帧 + 告警弹窗 + 声音
 * （后端单推理循环，推理成本 O(1)）；告警表格届时复用。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Image, Popconfirm, Select, Space,
  Table, Tag,
} from 'antd'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import { mediaUrl } from '../shared/media'
import type { AlarmRow } from '../api/types'
import { AlarmStatusTag } from '../components/Tags'
import { useAuth } from '../auth/AuthContext'

const STATUSES = ['new', 'confirmed', 'false_alarm', 'resolved']
  .map((v) => ({ value: v }))

export default function Realtime() {
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
    <Card title="📷 实时监测">
      <Alert style={{ marginBottom: 16 }} type="warning" showIcon
        message="实时帧监测将在 Phase 4 上线：后端单推理循环 + WebSocket 帧广播（N 个观看者共享一路推理）。当前为告警只读列表占位。" />
      <Table<AlarmRow> size="small" rowKey="id" dataSource={rows}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: '告警', dataIndex: 'id', width: 150 },
          { title: '类别', dataIndex: 'cls', width: 110 },
          { title: '置信度', dataIndex: 'conf', width: 90,
            render: (v: number) => v && `${Math.round(v * 100)}%` },
          { title: '来源', dataIndex: 'source', width: 130, ellipsis: true },
          { title: '条款', dataIndex: 'clause', ellipsis: true },
          { title: '证据', dataIndex: 'image_path', width: 110,
            render: (p: string) => p
              ? <Image width={72} height={54} src={mediaUrl(p)} />
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
    </Card>
  )
}
