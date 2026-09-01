import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp, Button, Descriptions, Form, Image, Input, Modal,
  Popconfirm, Select, Space, Table, Tag, Upload,
} from 'antd'
import { DownloadOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import { motion } from 'framer-motion'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import { downloadFile } from '../api/client'
import { mediaUrl } from '../shared/media'
import type { ModelRow, UserRow } from '../api/types'
import PageHeader from '../components/PageHeader'

const TAB_ITEMS = [
  { key: 'users', label: '用户治理', icon: '👥' },
  { key: 'models', label: '模型版本', icon: '🧠' },
  { key: 'kb', label: '知识库', icon: '📚' },
  { key: 'notify', label: '推送通道', icon: '📡' },
  { key: 'selfcheck', label: '系统自检', icon: '🔍' },
  { key: 'audit', label: '审计日志', icon: '📋' },
  { key: 'feedback', label: '纠偏样本', icon: '🔧' },
]

function UsersTab() {
  const { message } = AntApp.useApp()
  const [rows, setRows] = useState<UserRow[]>([])
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState<UserRow | null>(null)
  const [form] = Form.useForm()
  const [resetForm] = Form.useForm()

  const load = useCallback(async () => setRows(await ep.listUsers()), [])
  useEffect(() => { void load() }, [load])

  async function create() {
    const v = await form.validateFields()
    await ep.createUser(v)
    message.success(`用户 ${v.username} 已创建（首登强制改密）`)
    setCreating(false)
    form.resetFields()
    await load()
  }

  async function reset() {
    if (!resetting) return
    const v = await resetForm.validateFields()
    await ep.resetPassword(resetting.id, v.password)
    message.success('已重置，对方下次登录将强制改密')
    setResetting(null)
  }

  async function toggleDisabled(r: UserRow) {
    await ep.setUserDisabled(r.id, !r.disabled)
    message.success(r.disabled ? '已启用' : '已停用')
    await load()
  }

  return (
    <>
      <Button type="primary" style={{ marginBottom: 16, borderRadius: 10 }}
        onClick={() => setCreating(true)}>＋ 新建用户</Button>
      <Table<UserRow> size="small" rowKey="id" dataSource={rows}
        pagination={{ pageSize: 8 }}
        columns={[
          { title: '用户名', dataIndex: 'username' },
          { title: '角色', dataIndex: 'role', width: 110,
            render: (r: string) => <Tag color={{ admin: 'red', safety: 'blue', responsible: 'green' }[r]}>{r}</Tag> },
          { title: '初始密码未改', dataIndex: 'must_change_password', width: 120,
            render: (v: number) => v ? <Tag color="warning">是</Tag> : '—' },
          { title: '状态', dataIndex: 'disabled', width: 90,
            render: (v: number) => v ? <Tag color="error">停用</Tag> : <Tag color="success">正常</Tag> },
          { title: '操作', width: 190, render: (_, r) => (
            <Space>
              <Button size="small" onClick={() => { setResetting(r); resetForm.resetFields() }}>
                重置密码
              </Button>
              <Popconfirm title={r.disabled ? '确认启用该账号？' : '确认停用该账号？'}
                onConfirm={() => void toggleDisabled(r)}>
                <Button size="small" danger={!r.disabled}>
                  {r.disabled ? '启用' : '停用'}
                </Button>
              </Popconfirm>
            </Space>
          ) },
        ]} />
      <Modal title="新建用户" open={creating} onOk={create} okText="创建"
        onCancel={() => setCreating(false)} destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{
          role: 'responsible', must_change_password: true }}>
          <Form.Item name="username" label="用户名（2-32 字符）" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="初始密码（至少 8 位）" rules={[{ required: true }, { min: 8 }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={[
              { value: 'responsible', label: 'responsible · 整改责任人' },
              { value: 'safety', label: 'safety · 安全员' },
              { value: 'admin', label: 'admin · 管理员' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal title={`重置 ${resetting?.username ?? ''} 的密码`} open={!!resetting}
        onOk={reset} okText="重置" onCancel={() => setResetting(null)}>
        <Form form={resetForm} layout="vertical">
          <Form.Item name="password" label="新密码（至少 8 位，重置后强制对方首登改密）"
            rules={[{ required: true }, { min: 8 }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

function ModelsTab() {
  const { message } = AntApp.useApp()
  const [data, setData] = useState<Awaited<ReturnType<typeof ep.listModels>> | null>(null)

  const load = useCallback(async () => setData(await ep.listModels()), [])
  useEffect(() => { void load() }, [load])

  if (!data) return null
  return (
    <>
      <Table<ModelRow> size="small" rowKey="id" dataSource={data.models}
        pagination={{ pageSize: 8 }}
        rowClassName={(r) => (r.active ? 'ant-table-row-selected' : '')}
        columns={[
          { title: '族', dataIndex: 'name', width: 90 },
          { title: '版本', dataIndex: 'version' },
          { title: '权重路径', dataIndex: 'path', ellipsis: true },
          { title: '活跃', dataIndex: 'active', width: 80,
            render: (v: number) => v ? <Tag color="success">active</Tag> : '' },
          { title: '操作', width: 100, render: (_, r) =>
            r.active ? <Tag>使用中</Tag> :
              <Popconfirm title={`切换 ${r.name} 族到 ${r.version}？运行中引擎将热加载。`}
                onConfirm={async () => {
                  await ep.switchModel(r.name, r.id)
                  message.success('已切换（运行中引擎热加载拾取）')
                  await load()
                }}>
                <Button size="small">切换</Button>
              </Popconfirm> },
        ]} />
      <Descriptions size="small" column={2} style={{ marginTop: 16 }} title="当前活跃版本">
        <Descriptions.Item label="fire">{data.active.fire?.version || '未注册'}</Descriptions.Item>
        <Descriptions.Item label="ppe">{data.active.ppe?.version || '未注册'}</Descriptions.Item>
      </Descriptions>
    </>
  )
}

function KbTab() {
  const { message } = AntApp.useApp()
  const [rows, setRows] = useState<Awaited<ReturnType<typeof ep.listKbDocs>>>([])
  const [importing, setImporting] = useState(false)

  const load = useCallback(async () => setRows(await ep.listKbDocs()), [])
  useEffect(() => { void load() }, [load])

  return (
    <>
      <Upload showUploadList={false} accept=".pdf"
        beforeUpload={async (file) => {
          setImporting(true)
          try {
            const res = await ep.importKbPdf(file)
            message.success(`导入成功：${res.chunks} 个语义块`)
            await load()
          } finally {
            setImporting(false)
          }
          return false
        }}>
        <Button icon={<UploadOutlined />} loading={importing} style={{ borderRadius: 10 }}>导入规范 PDF</Button>
      </Upload>
      <Table size="small" rowKey="id" style={{ marginTop: 16 }} dataSource={rows}
        columns={[
          { title: '文档', dataIndex: 'filename' },
          { title: '语义块', dataIndex: 'chunk_count', width: 90 },
          { title: '导入人', dataIndex: 'imported_by', width: 100 },
          { title: '时间', dataIndex: 'created_at', width: 170 },
        ]} />
    </>
  )
}

function NotifyTab() {
  const { message } = AntApp.useApp()
  const [status, setStatus] = useState<Awaited<ReturnType<typeof ep.notifyStatus>> | null>(null)
  const [capture, setCapture] = useState<Record<string, unknown>[]>([])
  const [testing, setTesting] = useState(false)

  const load = useCallback(async () => {
    setStatus(await ep.notifyStatus())
  }, [])
  useEffect(() => { void load() }, [load])

  return (
    <>
      {status && <Descriptions size="small" column={2} bordered>
        <Descriptions.Item label="推送开关">
          {status.enabled ? <Tag color="success">已启用</Tag> : <Tag>关闭</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="演示模式">
          {status.demo_mode ? <Tag color="processing">开启（payload 捕获到 mock_capture）</Tag> : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="通道">{status.channel}</Descriptions.Item>
        <Descriptions.Item label="webhook">
          {status.webhook_configured ? <Tag color="success">已配置</Tag> : <Tag color="warning">未配置</Tag>}
        </Descriptions.Item>
      </Descriptions>}
      <Space style={{ margin: '16px 0' }}>
        <Button type="primary" loading={testing} onClick={async () => {
          setTesting(true)
          try {
            const res = await ep.notifyTest()
            message.info(`测试推送结果：${JSON.stringify(res)}`)
            setCapture(await ep.listMockCapture().catch(() => []))
          } finally {
            setTesting(false)
          }
        }}>发送测试推送</Button>
        <Button icon={<ReloadOutlined />} onClick={() =>
          void ep.listMockCapture().then(setCapture)}>刷新捕获</Button>
      </Space>
      {capture.length > 0 && (
        <pre style={{
          maxHeight: 220, overflow: 'auto', padding: 16, fontSize: 12,
          background: 'rgba(0,0,0,0.3)', borderRadius: 10,
          border: '1px solid rgba(var(--fg-rgb),0.06)',
          fontFamily: 'var(--font-mono)', color: 'rgba(var(--fg-rgb),0.6)',
        }}>
          {JSON.stringify(capture, null, 2)}
        </pre>
      )}
    </>
  )
}

function SelfCheckTab() {
  const [items, setItems] = useState<Awaited<ReturnType<typeof ep.selfCheck>> | null>(null)
  const [loading, setLoading] = useState(false)
  return (
    <>
      <Button type="primary" loading={loading} onClick={async () => {
        setLoading(true)
        try { setItems(await ep.selfCheck()) } finally { setLoading(false) }
      }} style={{ borderRadius: 10 }}>运行系统自检</Button>
      {items && (
        <Table size="small" style={{ marginTop: 16 }} rowKey="item"
          dataSource={items.items} pagination={false}
          columns={[
            { title: '检查项', dataIndex: 'item', width: 160 },
            { title: '结果', dataIndex: 'ok', width: 90,
              render: (v: boolean) => v
                ? <Tag color="success">通过</Tag> : <Tag color="error">异常</Tag> },
            { title: '详情', dataIndex: 'message', ellipsis: true },
          ]} />
      )}
    </>
  )
}

function AuditTab() {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof ep.listAudit>>>([])
  useEffect(() => { void ep.listAudit().then(setRows) }, [])
  return (
    <>
      <Button icon={<DownloadOutlined />} style={{ marginBottom: 16, borderRadius: 10 }}
        onClick={() => void downloadFile('/admin/audit/export', 'audit.csv')}>
        导出审计 CSV
      </Button>
      <Table size="small" rowKey={(r, i) => `${r.created_at}-${i}`} dataSource={rows}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: '时间', dataIndex: 'created_at', width: 170 },
          { title: '用户', dataIndex: 'user_id', width: 110 },
          { title: '动作', dataIndex: 'action', width: 160,
            render: (a: string) => <Tag>{a}</Tag> },
          { title: '明细', dataIndex: 'detail_json', ellipsis: true },
        ]} />
    </>
  )
}

function FeedbackTab() {
  const { message } = AntApp.useApp()
  const [rows, setRows] = useState<Record<string, unknown>[]>([])

  const load = useCallback(async () => setRows(await ep.listFeedback()), [])
  useEffect(() => { void load() }, [load])

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<DownloadOutlined />} style={{ borderRadius: 10 }}
          onClick={() => void downloadFile('/admin/feedback/export', 'feedback.csv')}>
          导出纠偏 CSV
        </Button>
      </Space>
      <Table size="small" rowKey={(r) => String(r['id'])} dataSource={rows}
        pagination={{ pageSize: 8 }}
        expandable={{
          expandedRowRender: (r) => (r['image_abs']
            ? <Image width={160} src={mediaUrl(String(r['image_path']))} />
            : null),
        }}
        columns={[
          { title: '时间', dataIndex: 'created_at', width: 160,
            render: (d: string) => d && dayjs(d).format('MM-DD HH:mm') },
          { title: '任务', dataIndex: 'task_id', width: 150, ellipsis: true },
          { title: '自动→改判', width: 130,
            render: (_: unknown, r: Record<string, unknown>) =>
              `${r['auto_risk_level'] ?? '—'} → ${r['corrected_risk_level']}` },
          { title: '类型', dataIndex: 'feedback_type', width: 120 },
          { title: '原因', dataIndex: 'reason', ellipsis: true },
          { title: '状态', dataIndex: 'status', width: 110,
            render: (s: string, r: Record<string, unknown>) => (
              <Select size="small" value={s} style={{ width: 100 }}
                options={['pending', 'confirmed', 'rejected'].map((v) => ({ value: v }))}
                onChange={async (v) => {
                  await ep.reviewFeedback(String(r['id']), v)
                  message.success('已更新审核状态')
                  await load()
                }} />
            ) },
        ]} />
    </>
  )
}

const TAB_CONTENT: Record<string, () => React.JSX.Element | null> = {
  users: UsersTab,
  models: ModelsTab,
  kb: KbTab,
  notify: NotifyTab,
  selfcheck: SelfCheckTab,
  audit: AuditTab,
  feedback: FeedbackTab,
}

export default function Admin() {
  const [activeTab, setActiveTab] = useState('users')
  const ActiveComponent = TAB_CONTENT[activeTab]

  return (
    <>
      <PageHeader title="管理端" subtitle="用户治理 · 模型版本 · 知识库 · 推送 · 自检 · 审计 · 纠偏" />
      <div style={{ display: 'flex', gap: 24 }}>
        <div className="admin-tabnav" style={{ width: 180, flexShrink: 0 }}>
          {TAB_ITEMS.map((t) => (
            <motion.div key={t.key}
              whileHover={{ x: 4 }}
              onClick={() => setActiveTab(t.key)}
              className="admin-tab"
              style={{
                padding: '12px 16px',
                marginBottom: 4,
                borderRadius: 12,
                cursor: 'pointer',
                background: activeTab === t.key ? 'rgba(var(--accent-primary-rgb),0.08)' : 'transparent',
                border: `1px solid ${activeTab === t.key ? 'rgba(var(--accent-primary-rgb),0.2)' : 'transparent'}`,
                color: activeTab === t.key ? '#fff' : 'rgba(var(--fg-rgb),0.4)',
                fontWeight: activeTab === t.key ? 600 : 400,
                fontSize: 13,
                transition: 'background 0.2s, border-color 0.2s, color 0.2s',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}>
              <span style={{ fontSize: 15 }}>{t.icon}</span>
              {t.label}
            </motion.div>
          ))}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
          >
            <ActiveComponent />
          </motion.div>
        </div>
      </div>
    </>
  )
}
