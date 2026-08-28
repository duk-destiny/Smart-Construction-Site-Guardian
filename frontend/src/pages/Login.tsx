/** 登录页：成功后按 must_change_password 决定去改密页或角色首页。 */
import { App as AntApp } from 'antd'
import { Form, Input, Button, Card, Typography } from 'antd'
import { useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { useAuth } from '../auth/AuthContext'
import { homeFor } from '../router'

export default function Login() {
  const { login, user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { message } = AntApp.useApp()
  const [loading, setLoading] = useState(false)

  // 登录态驱动路由（单一事实源）：user 出现即跳改密页或角色首页。
  // 不在 onFinish 里手动 navigate——事件回调里 await 之后紧跟 navigate
  // 会被路由状态竞争吞掉（运维验收实测：token 已存但 URL 不变）。
  useEffect(() => {
    if (!user) return
    navigate(user.must_change_password
      ? '/change-password'
      : (location.state?.from as string) || homeFor(user.role),
      { replace: true })
  }, [user, navigate, location.state])

  async function onFinish(v: { username: string; password: string }) {
    setLoading(true)
    try {
      const info = await login(v.username, v.password)
      message.success(`欢迎，${info.username}`)
      void info
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(160deg, #1f2733 0%, #2c3e50 60%, #3b1f24 100%)',
    }}>
      <Card style={{ width: 380, boxShadow: '0 8px 32px rgba(0,0,0,.25)' }}>
        <Typography.Title level={3} style={{ textAlign: 'center' }}>
          🏗 智护工地
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
          施工安全 AI 监控系统 · 动火作业 / 施工 PPE
        </Typography.Paragraph>
        <Form onFinish={onFinish} layout="vertical" autoFocus>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="用户名" size="large" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password placeholder="密码" size="large" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}>
            登 录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
