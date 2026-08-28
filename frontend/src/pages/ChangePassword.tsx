/** 首登强制改密页：must_change_password=true 时不可绕过（守卫 + 本页自锁）。 */
import { App as AntApp, Button, Card, Form, Input, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { changePassword } from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { homeFor } from '../router'

export default function ChangePassword() {
  const { user, refresh, logout } = useAuth()
  const navigate = useNavigate()
  const { message } = AntApp.useApp()
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()

  if (!user) {
    navigate('/login', { replace: true })
    return null
  }
  const forced = user.must_change_password

  async function onFinish(v: { old: string; new1: string; new2: string }) {
    if (v.new1 !== v.new2) {
      message.error('两次输入的新密码不一致')
      return
    }
    setLoading(true)
    try {
      await changePassword(v.old, v.new1)
      message.success('密码已更新，请牢记新密码')
      await refresh()
      navigate(homeFor(user!.role), { replace: true })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: '#f5f6f8',
    }}>
      <Card style={{ width: 420 }}>
        <Typography.Title level={4}>
          {forced ? '🔐 首次登录，请先修改初始密码' : '🔑 修改密码'}
        </Typography.Title>
        {forced && (
          <Typography.Paragraph type="warning">
            当前账号仍在使用初始密码，修改后才能进入系统。
          </Typography.Paragraph>
        )}
        <Form form={form} layout="vertical" onFinish={onFinish}>
          <Form.Item name="old" label="原密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="new1" label="新密码（至少 8 位）"
            rules={[{ required: true }, { min: 8, message: '至少 8 位' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="new2" label="确认新密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            提交修改
          </Button>
          {!forced && (
            <Button block style={{ marginTop: 8 }} onClick={() => {
              logout()
              navigate('/login', { replace: true })
            }}>
              返回登录
            </Button>
          )}
        </Form>
      </Card>
    </div>
  )
}
