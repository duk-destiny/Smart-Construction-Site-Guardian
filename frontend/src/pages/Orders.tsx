/** 工单闭环页（admin/safety）：台账 / 待验收 / 逾期 三视图。
 *
 * 台账行展开 → 派发面板（责任人下拉=接口下发候选+规则建议）+ 人工改判 +
 * 导出 Excel；待验收含整改照片预览（/api/media 带 token）与通过/驳回。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp, Button, Card, Descriptions, Drawer, Form, Input, InputNumber,
  Modal, Popconfirm, Select, Space, Table, Tabs, Tag, Typography, Image,
} from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import { downloadFile } from '../api/client'
import { mediaUrl } from '../shared/media'
import type { DispatchPanel, OrderRow } from '../api/types'
import { OrderStatusTag, RiskTag } from '../components/Tags'

const RISK_OPTIONS = ['重大', '较大', '一般', '低'].map((v) => ({ value: v }))

function DispatchPanelForm({ taskId, onDone }: {
  taskId: string
  onDone: () => void
}) {
  const { message } = AntApp.useApp()
  const [panel, setPanel] = useState<DispatchPanel | null>(null)
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    try {
      const p = await ep.getDispatchPanel(taskId)
      setPanel(p)
      form.setFieldsValue({
        assignee: p.assignee_name || p.suggestion || undefined,
        hours: p.default_hours,
      })
    } catch { /* 404 尚无工单 */ }
  }, [taskId, form])

  useEffect(() => { void load() }, [load])

  if (!panel) return <Typography.Text type="secondary">该任务尚未生成工单</Typography.Text>

  async function submit() {
    const v = await form.validateFields()
    setLoading(true)
    try {
      const res = await ep.dispatchOrder(taskId, v.assignee, v.hours)
      message.success(res.message)
      onDone()
      await load()
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Descriptions column={1} size="small">
        <Descriptions.Item label="工单">{panel.order.id}</Descriptions.Item>
        <Descriptions.Item label="隐患">{panel.order.hazard_desc}</Descriptions.Item>
        <Descriptions.Item label="等级">
          <RiskTag level={panel.order.risk_level} />
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <OrderStatusTag status={panel.order.status} />
        </Descriptions.Item>
        {panel.order.assignee_name && (
          <Descriptions.Item label="当前责任人">{panel.order.assignee_name}</Descriptions.Item>
        )}
        {panel.suggestion && (
          <Descriptions.Item label="规则建议">
            <Tag color="blue">{panel.suggestion}</Tag>
          </Descriptions.Item>
        )}
      </Descriptions>
      {['open', 'rejected'].includes(panel.order.status) && (
        <Form form={form} layout="inline" style={{ marginTop: 12, flexWrap: 'wrap' }}>
          <Form.Item name="assignee" label="责任人" rules={[{ required: true }]}>
            <Select style={{ width: 160 }}
              options={panel.responsible_names.map((n) => ({ value: n }))} />
          </Form.Item>
          <Form.Item name="hours" label="时限（小时）" rules={[{ required: true }]}>
            <InputNumber min={1} max={720} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" loading={loading} onClick={submit}>派发 / 改派</Button>
          </Form.Item>
        </Form>
      )}
    </>
  )
}

function OverrideForm({ taskId, onDone }: { taskId: string; onDone: () => void }) {
  const { message } = AntApp.useApp()
  const [level, setLevel] = useState<string>()
  const [reason, setReason] = useState('')

  async function submit() {
    if (!level || !reason.trim()) {
      message.warning('请选择等级并填写原因')
      return
    }
    await ep.overrideTask(taskId, level, reason)
    message.success('改判已记录')
    setReason('')
    onDone()
  }

  return (
    <Space.Compact style={{ width: '100%', marginTop: 12 }}>
      <Select placeholder="人工改判等级" style={{ width: 140 }} value={level}
        onChange={setLevel} options={RISK_OPTIONS} />
      <Input placeholder="改判原因（写入审计与纠偏样本）" value={reason}
        onChange={(e) => setReason(e.target.value)} />
      <Button onClick={submit}>改判</Button>
    </Space.Compact>
  )
}

function LedgerTab({ refreshKey }: { refreshKey: number }) {
  const { message } = AntApp.useApp()
  const [rows, setRows] = useState<OrderRow[]>([])
  const [selected, setSelected] = useState<OrderRow | null>(null)

  const load = useCallback(async () => {
    setRows(await ep.listOrders())
  }, [])

  useEffect(() => { void load() }, [load, refreshKey])

  async function doExport(orderId: string) {
    const res = await ep.exportOrderExcel(orderId)
    await downloadFile(res.file.download_url, res.file.name)
    message.success('台账已导出')
  }

  return (
    <>
      <Table<OrderRow> size="small" rowKey="id" dataSource={rows}
        pagination={{ pageSize: 10 }} expandable={{
          expandedRowRender: (r) => (
            <div style={{ maxWidth: 720 }}>
              <p><b>整改要求：</b>{r.requirement || '—'}</p>
              <p><b>工人提示：</b>{r.worker_notice || '—'}</p>
              <p><b>违反规范：</b>{r.clause || '—'}</p>
              {r.override_level
                && <p><b>人工改判：</b>{r.override_level}（{r.override_reason}）</p>}
            </div>
          ),
        }}
        onRow={(r) => ({ onClick: () => setSelected(r), style: { cursor: 'pointer' } })}
        columns={[
          { title: '工单', dataIndex: 'id', width: 150 },
          { title: '隐患描述', dataIndex: 'hazard_desc', ellipsis: true },
          { title: '风险', width: 90, render: (_, r) =>
            <RiskTag level={r.override_level || r.risk_level} /> },
          { title: '状态', width: 100, render: (_, r) => <OrderStatusTag status={r.status} /> },
          { title: '来源', dataIndex: 'source', width: 90,
            render: (s: string) => ({ camera: '📷', upload: '📤', text: '📝' }[s] || s) },
          { title: '截止', dataIndex: 'deadline', width: 170,
            render: (d: string) => d && dayjs(d).format('MM-DD HH:mm') },
          { title: '创建', dataIndex: 'created_at', width: 170,
            render: (d: string) => d && dayjs(d).format('MM-DD HH:mm') },
        ]} />
      <Drawer title={`工单 ${selected?.id ?? ''}`} open={!!selected}
        width={520} onClose={() => setSelected(null)}>
        {selected && (
          <>
            <DispatchPanelForm taskId={selected.task_id} onDone={load} />
            <OverrideForm taskId={selected.task_id} onDone={load} />
            <Button style={{ marginTop: 12 }}
              icon={<DownloadOutlined />}
              onClick={() => void doExport(selected.id)}>
              导出台账 Excel
            </Button>
          </>
        )}
      </Drawer>
    </>
  )
}

function ReviewTab({ refreshKey, onChanged }: {
  refreshKey: number
  onChanged: () => void
}) {
  const { message } = AntApp.useApp()
  const [rows, setRows] = useState<OrderRow[]>([])
  const [rejecting, setRejecting] = useState<OrderRow | null>(null)
  const [reason, setReason] = useState('')
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setRows(await ep.listPendingReview())
  }, [])

  useEffect(() => { void load() }, [load, refreshKey])

  async function approve(r: OrderRow) {
    await ep.reviewOrder(r.id, true)
    message.success('已通过并关闭工单')
    await load()
    onChanged()
  }

  async function reject() {
    const v = await form.validateFields()
    if (!rejecting) return
    await ep.reviewOrder(rejecting.id, false, v.reason)
    message.success('已驳回，退回责任人整改')
    setRejecting(null)
    await load()
    onChanged()
  }

  return (
    <>
      <Table<OrderRow> size="small" rowKey="id" dataSource={rows}
        pagination={{ pageSize: 8 }}
        expandable={{
          expandedRowRender: (r) => (
            <div>
              <p><b>整改说明：</b>{r.submitted_note || '—'}</p>
              <Image.PreviewGroup>
                {(r.submitted_img_paths || []).map((p) => (
                  <Image key={p} width={120} height={90} style={{ objectFit: 'cover', marginRight: 8 }}
                    src={mediaUrl(p)} />
                ))}
              </Image.PreviewGroup>
            </div>
          ),
        }}
        columns={[
          { title: '工单', dataIndex: 'id', width: 150 },
          { title: '责任人', dataIndex: 'assignee_name', width: 110 },
          { title: '隐患描述', dataIndex: 'hazard_desc', ellipsis: true },
          { title: '提交时间', dataIndex: 'created_at', width: 170,
            render: (d: string) => d && dayjs(d).format('MM-DD HH:mm') },
          { title: '操作', width: 170, render: (_, r) => (
            <Space>
              <Popconfirm title="确认通过并销项该工单？"
                onConfirm={() => void approve(r)}>
                <Button size="small" type="primary">通过</Button>
              </Popconfirm>
              <Button size="small" danger onClick={() => setRejecting(r)}>驳回</Button>
            </Space>
          ) },
        ]} />
      <Modal title={`驳回 ${rejecting?.id ?? ''}`} open={!!rejecting}
        onOk={reject} onCancel={() => setRejecting(null)} okText="确认驳回">
        <Form form={form}>
          <Form.Item name="reason" label="驳回原因（必填，展示给责任人）"
            rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

function OverdueTab({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<OrderRow[]>([])
  useEffect(() => {
    void ep.listOverdue().then(setRows)
  }, [refreshKey])
  return (
    <Table<OrderRow> size="small" rowKey="id" dataSource={rows}
      pagination={{ pageSize: 8 }}
      columns={[
        { title: '工单', dataIndex: 'id', width: 150 },
        { title: '隐患描述', dataIndex: 'hazard_desc', ellipsis: true },
        { title: '等级', dataIndex: 'risk_level', width: 90,
          render: (l: string) => <RiskTag level={l} /> },
        { title: '责任人', dataIndex: 'assignee_id', width: 110 },
        { title: '截止', dataIndex: 'deadline', width: 170 },
      ]} />
  )
}

export default function Orders() {
  const [refreshKey, setRefreshKey] = useState(0)
  const bump = () => setRefreshKey((k) => k + 1)
  return (
    <Card title="📋 工单 / 派发 / 验收">
      <Tabs items={[
        { key: 'ledger', label: '台账与派发',
          children: <LedgerTab refreshKey={refreshKey} /> },
        { key: 'review', label: '待验收',
          children: <ReviewTab refreshKey={refreshKey} onChanged={bump} /> },
        { key: 'overdue', label: '逾期',
          children: <OverdueTab refreshKey={refreshKey} /> },
      ]} />
    </Card>
  )
}
