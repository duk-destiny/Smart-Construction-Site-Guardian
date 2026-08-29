import { useEffect, useState } from 'react'
import {
  Alert, App as AntApp, Button, Descriptions, Form, Input, Select,
  Table, Tag, Typography, Upload,
} from 'antd'
import { motion as Motion } from 'framer-motion'
import { InboxOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import type { ChatRoute, HazardOption } from '../api/types'
import { RiskTag } from '../components/Tags'
import PageHeader from '../components/PageHeader'

const SCENES = [
  { value: 'hot_work', label: '动火作业安全' },
  { value: 'construction_ppe', label: '施工 PPE / 危险检测' },
]

const TAB_ITEMS = [
  { key: 'media', label: '影像研判', icon: '📷' },
  { key: 'text', label: '文字线索', icon: '📝' },
  { key: 'chat', label: '对话查询', icon: '💬' },
]

function MediaTab() {
  const navigate = useNavigate()
  const { message } = AntApp.useApp()
  const [file, setFile] = useState<File | null>(null)
  const [autoRun, setAutoRun] = useState(true)
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()

  async function submit() {
    const v = await form.validateFields()
    if (!file) {
      message.warning('请先选择图片或视频')
      return
    }
    const permit = {
      scene: v.scene, fire_level: v.fire_level || '—', watcher: v.watcher || '',
      valid_until: v.valid_until || '', area: v.area || '',
      extinguisher: v.extinguisher || '', fire_blanket: v.fire_blanket || '',
      approval: v.approval || '已审批',
    }
    const fd = new FormData()
    fd.append('file', file)
    fd.append('scene_id', v.scene)
    fd.append('permit_info', JSON.stringify(permit))
    fd.append('auto_run', String(autoRun))
    setLoading(true)
    try {
      const res = await ep.uploadMedia(fd)
      message.success(`任务已创建：${res.task_id}`)
      navigate(`/agents/${res.task_id}`)
    } finally {
      setLoading(false)
    }
  }

  const isHot = Form.useWatch('scene', form) !== 'construction_ppe'
  return (
    <Motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}>
      <Form form={form} layout="vertical" initialValues={{ scene: 'hot_work', approval: '已审批' }}
        style={{ maxWidth: 640 }}>
        <Form.Item name="scene" label="归属场景" rules={[{ required: true }]}>
          <Select options={SCENES} />
        </Form.Item>
        <Form.Item label="影像文件" required>
          <Upload.Dragger
            maxCount={1} beforeUpload={() => false}
            onChange={({ fileList }) => setFile(fileList[0]?.originFileObj ?? null)}
            onRemove={() => setFile(null)}
            accept=".jpg,.jpeg,.png,.mp4,.mov"
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">点击或拖拽上传</p>
          </Upload.Dragger>
        </Form.Item>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20,
          padding: '12px 16px', borderRadius: 10,
          background: autoRun ? 'rgba(0,212,170,0.06)' : 'rgba(255,255,255,0.03)',
          border: `1px solid ${autoRun ? 'rgba(0,212,170,0.15)' : 'rgba(255,255,255,0.06)'}`,
          cursor: 'pointer', transition: 'all 0.2s',
        }} onClick={() => setAutoRun(!autoRun)}>
          <div style={{
            width: 10, height: 10, borderRadius: 5,
            background: autoRun ? '#00d4aa' : 'rgba(255,255,255,0.2)',
            boxShadow: autoRun ? '0 0 8px rgba(0,212,170,0.5)' : 'none',
            transition: 'all 0.2s',
          }} />
          <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.6)' }}>
            {autoRun ? '提交后自动启动多 Agent 研判' : '仅创建任务，不自动研判'}
          </span>
        </div>
        <div style={{
          padding: 20, borderRadius: 14,
          background: 'rgba(255,255,255,0.02)',
          border: '1px solid rgba(255,255,255,0.06)',
          marginBottom: 20,
        }}>
          <div style={{
            fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.4)',
            textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16,
          }}>作业票信息</div>
          {isHot
            ? <Form.Item name="fire_level" label="动火级别" style={{ marginBottom: 8 }}>
                <Select options={[{ value: '一级' }, { value: '二级' }]} />
              </Form.Item>
            : <Form.Item name="watcher" label="安全员" initialValue="已指定"
                style={{ marginBottom: 8 }}><Input /></Form.Item>}
          {isHot && <Form.Item name="watcher" label="监火人" style={{ marginBottom: 8 }}><Input /></Form.Item>}
          <Form.Item name="valid_until" label="有效期限" style={{ marginBottom: 8 }}><Input placeholder="如 2026-08-30 18:00" /></Form.Item>
          <Form.Item name="area" label="作业区域" style={{ marginBottom: 8 }}><Input placeholder="如 3号楼西侧" /></Form.Item>
          <Form.Item name="extinguisher" label={isHot ? '灭火器配置' : '防护装备确认'}
            initialValue={isHot ? '已配备' : '已确认'} style={{ marginBottom: 8 }}><Input /></Form.Item>
          <Form.Item name="fire_blanket" label={isHot ? '防火毯' : '现场清理确认'}
            initialValue={isHot ? '已设置' : '已完成'} style={{ marginBottom: 8 }}><Input /></Form.Item>
          <Form.Item name="approval" label="作业审批" style={{ marginBottom: 0 }}><Input /></Form.Item>
        </div>
        <Button type="primary" size="large" block loading={loading} onClick={submit}
          style={{
            height: 48, borderRadius: 12, fontWeight: 600,
            background: 'linear-gradient(135deg, #c8102e 0%, #9b0a22 100%)',
            boxShadow: '0 4px 16px rgba(200,16,46,0.25)',
          }}>
          开始智能研判
        </Button>
      </Form>
    </Motion.div>
  )
}

function TextTab({ hazards, enhance }: { hazards: HazardOption[]; enhance: boolean }) {
  const { message } = AntApp.useApp()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [created, setCreated] = useState<{
    task_id: string; risk_level: string; work_order: Record<string, string>;
    worker_notice: string
  } | null>(null)
  const [form] = Form.useForm()

  async function submit() {
    const v = await form.validateFields()
    setLoading(true)
    try {
      const res = await ep.createTextHazard({
        description: v.description, hazard_key: v.hazard_key,
        scene_id: v.scene, location: v.location,
      })
      message.success('文字隐患单已创建，直接进入派发闭环')
      setCreated(res)
      form.resetFields()
    } finally {
      setLoading(false)
    }
  }

  return (
    <Motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}
      style={{ maxWidth: 640 }}>
      <Alert style={{ marginBottom: 16 }} type="info" showIcon
        message="适合摄像头拍不到的隐患（无证上岗、无交底、通道占用等）。跳过视觉研判，按规则查表定级，直接进入派发闭环。" />
      <Form form={form} layout="vertical" initialValues={{ scene: 'hot_work' }}>
        <Form.Item name="scene" label="归属场景" rules={[{ required: true }]}>
          <Select options={SCENES} />
        </Form.Item>
        <Form.Item name="hazard_key" label="隐患类别" rules={[{ required: true }]}>
          <Select options={hazards.map((h) => ({
            value: h.key,
            label: <>{(h.severity === 'critical' ? '🔴 高危｜' : '🟡 关注｜') + h.label}（{h.key}）</>,
          }))} showSearch optionFilterProp="label" />
        </Form.Item>
        <Form.Item name="location" label="位置（可选）">
          <Input placeholder="如：3号楼西侧 / 地库B区" />
        </Form.Item>
        <Form.Item label="隐患描述"
          extra={enhance ? "点「AI 提取预填」可自动拆出类别/位置等字段,结果需人工确认" : undefined}
          rules={[{ required: true }]}>
          <Form.Item name="description" noStyle rules={[{ required: true, message: "请填写隐患描述" }]}>
            <Input.TextArea rows={3} placeholder="例：3号楼西侧电焊机旁堆着纸箱没人清理，也没有监火人" />
          </Form.Item>
          {enhance && (
            <Button size="small" icon={<ThunderboltOutlined />} loading={extracting}
              style={{ marginTop: 8 }}
              onClick={async () => {
                const raw = form.getFieldValue("description")
                if (!raw || !String(raw).trim()) {
                  message.warning("请先描述情况,再点 AI 提取")
                  return
                }
                setExtracting(true)
                try {
                  const out = await ep.enhanceExtract(String(raw))
                  form.setFieldsValue({
                    description: out.description,
                    hazard_key: out.hazard_key,
                    location: out.location || undefined,
                    scene_id: out.scene_id || undefined,
                  })
                  message.success("AI 预填完成，请人工确认后提交")
                } finally {
                  setExtracting(false)
                }
              }}>
              AI 提取预填
            </Button>
          )}
        </Form.Item>
        <Button type="primary" block loading={loading} onClick={submit}
          style={{
            height: 44, borderRadius: 12, fontWeight: 600,
            background: 'linear-gradient(135deg, #c8102e 0%, #9b0a22 100%)',
          }}>
          创建文字隐患单
        </Button>
      </Form>
      {created && (
        <div style={{
          marginTop: 20, padding: 20, borderRadius: 14,
          background: 'rgba(0,212,170,0.04)',
          border: '1px solid rgba(0,212,170,0.15)',
        }}>
          <div style={{ fontSize: 12, color: '#00d4aa', fontWeight: 600, marginBottom: 12 }}>
            已建单 {created.task_id}
          </div>
          <Descriptions column={1} size="small">
            <Descriptions.Item label="风险等级">
              <RiskTag level={created.risk_level} />
            </Descriptions.Item>
            <Descriptions.Item label="隐患">{created.work_order?.hazard_desc}</Descriptions.Item>
            <Descriptions.Item label="整改要求">{created.work_order?.requirement}</Descriptions.Item>
            <Descriptions.Item label="工人提示">{created.worker_notice}</Descriptions.Item>
          </Descriptions>
          <Button size="small" style={{ marginTop: 12 }} onClick={() => navigate('/orders')}>
            去工单页派发
          </Button>
        </div>
      )}
    </Motion.div>
  )
}

const STATUS_CN: Record<string, string> = {
  open: '待整改', rejected: '驳回重改', submitted: '待验收', closed: '已销项',
}

function OrderCard({ o }: { o: Record<string, unknown> }) {
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

function renderChat(route: ChatRoute, ask: (t: string) => void) {
  const data = route.data
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
          background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
          cursor: 'pointer', transition: 'all 0.2s',
        }}
          onClick={() => void ask(`#${String(r['id'])} 的进度`)}>
          <b style={{ color: '#fff' }}>{String(r['id'])}</b>
          <span style={{ color: 'rgba(255,255,255,0.5)', marginLeft: 12 }}>
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
          <code style={{ color: 'rgba(255,255,255,0.6)', fontFamily: 'var(--font-mono)' }}>
            {String(r['id'])}
          </code>
          <span style={{ color: 'rgba(255,255,255,0.4)', marginLeft: 8 }}>
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

function ChatTab() {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [route, setRoute] = useState<ChatRoute | null>(null)

  async function ask(q: string) {
    setLoading(true)
    try {
      setRoute(await ep.queryChat(q))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void ask('') }, [])

  return (
    <Motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}
      style={{ maxWidth: 760 }}>
      <Alert style={{ marginBottom: 16 }} type="info" showIcon
        message="对话式只读查询：问工单进度、逾期统计、本周情况。绝不写入。" />
      <Input.Search
        value={text} onChange={(e) => setText(e.target.value)}
        onSearch={(q) => void ask(q)} enterButton="查询" loading={loading}
        placeholder="如：近7天有多少张未闭环工单 / #w_xxx 的进度 / 最近有没有逾期的" />
      <div style={{ marginTop: 16 }}>
        {route && (
          <>
            <Tag color="blue">理解方式: {route.tier === 'llm' ? '本地模型' : route.tier === 'rule' ? '规则' : '人工点选'}</Tag>
            <div style={{ marginTop: 12 }}>{renderChat(route, ask)}</div>
          </>
        )}
      </div>
    </Motion.div>
  )
}

export default function Report() {
  const [activeTab, setActiveTab] = useState('media')
  const [hazardOpts, setHazardOpts] = useState<HazardOption[]>([])
  const [enhance, setEnhance] = useState(false)
  useEffect(() => {
    void ep.getCapabilities().then((c) => {
      setHazardOpts(c.hazard_options || [])
      setEnhance(c.enhance_available)
    })
  }, [])

  return (
    <>
      <PageHeader title="统一上报" subtitle="影像研判 / 文字线索 / 对话查询" />
      <div style={{ display: 'flex', gap: 24 }}>
        <div style={{ width: 180, flexShrink: 0 }}>
          {TAB_ITEMS.map((t) => (
            <div key={t.key}
              onClick={() => setActiveTab(t.key)}
              style={{
                padding: '14px 16px',
                marginBottom: 6,
                borderRadius: 12,
                cursor: 'pointer',
                background: activeTab === t.key ? 'rgba(200,16,46,0.08)' : 'transparent',
                border: `1px solid ${activeTab === t.key ? 'rgba(200,16,46,0.2)' : 'transparent'}`,
                color: activeTab === t.key ? '#fff' : 'rgba(255,255,255,0.4)',
                fontWeight: activeTab === t.key ? 600 : 400,
                fontSize: 14,
                transition: 'all 0.2s',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}>
              <span style={{ fontSize: 16 }}>{t.icon}</span>
              {t.label}
            </div>
          ))}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          {activeTab === 'media' && <MediaTab />}
          {activeTab === 'text' && <TextTab hazards={hazardOpts} enhance={enhance} />}
          {activeTab === 'chat' && <ChatTab />}
        </div>
      </div>
      <div style={{ marginTop: 24, color: 'rgba(255,255,255,0.2)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
        /api 后端接口 · 魔数校验 · {dayjs().format('YYYY-MM-DD HH:mm')}
      </div>
    </>
  )
}
