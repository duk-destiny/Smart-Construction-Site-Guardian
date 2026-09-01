/** 影像研判发起表单（v2.2 从统一上报页迁出）：上传 + 作业票 → 建任务。
 *
 * 由 /agents 影像研判窗口在无 taskId 时渲染（表单+结果一体的上半屏）；
 * 上传成功后跳 /agents/:taskId 进入研判结果区，链路与原先完全一致。
 */
import { useState } from 'react'
import { App as AntApp, Button, Form, Input, Select, Upload } from 'antd'
import { motion as Motion } from 'framer-motion'
import { InboxOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import * as ep from '../api/endpoints'
import { activateOnKey } from '../shared/keyboard'

const SCENES = [
  { value: 'hot_work', label: '动火作业安全' },
  { value: 'construction_ppe', label: '施工 PPE / 危险检测' },
]

export default function MediaUploadForm() {
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
            <p className="ant-upload-text">点击或拖拽上传取证照片 / 视频</p>
          </Upload.Dragger>
        </Form.Item>
        <div role="switch" aria-checked={autoRun} tabIndex={0}
          onKeyDown={activateOnKey}
          style={{
            display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20,
            padding: '12px 16px', borderRadius: 10,
            background: autoRun ? 'rgba(0,212,170,0.06)' : 'rgba(var(--fg-rgb),0.03)',
            border: `1px solid ${autoRun ? 'rgba(0,212,170,0.15)' : 'rgba(var(--fg-rgb),0.06)'}`,
            cursor: 'pointer', transition: 'all 0.2s',
          }} onClick={() => setAutoRun(!autoRun)}>
          <div style={{
            width: 10, height: 10, borderRadius: 5,
            background: autoRun ? '#00d4aa' : 'rgba(var(--fg-rgb),0.2)',
            boxShadow: autoRun ? '0 0 8px rgba(0,212,170,0.5)' : 'none',
            transition: 'all 0.2s',
          }} />
          <span style={{ fontSize: 13, color: 'rgba(var(--fg-rgb),0.6)' }}>
            {autoRun ? '提交后自动启动影像研判' : '仅创建任务，不自动研判'}
          </span>
        </div>
        <div style={{
          padding: 20, borderRadius: 14,
          background: 'rgba(var(--fg-rgb),0.02)',
          border: '1px solid rgba(var(--fg-rgb),0.06)',
          marginBottom: 20,
        }}>
          <div style={{
            fontSize: 12, fontWeight: 600, color: 'rgba(var(--fg-rgb),0.4)',
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
            background: 'linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-primary-deep) 100%)',
            boxShadow: '0 4px 16px rgba(var(--accent-primary-rgb),0.25)',
          }}>
          开始智能研判
        </Button>
      </Form>
    </Motion.div>
  )
}
