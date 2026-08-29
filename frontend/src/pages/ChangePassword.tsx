import { App as AntApp, Button, Form, Input, Spin } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { changePassword } from '../api/endpoints'
import { getToken } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { homeFor } from '../router'
import ParticleField from '../components/ParticleField'

export default function ChangePassword() {
  const { user, refresh, logout } = useAuth()
  const navigate = useNavigate()
  const { message } = AntApp.useApp()
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    if (!getToken()) navigate('/login', { replace: true })
  }, [navigate])

  if (!user) {
    return (
      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center',
        justifyContent: 'center', background: 'var(--bg-deep)',
      }}>
        <Spin tip="正在同步登录态…"><div style={{ minWidth: 320, minHeight: 120 }} /></Spin>
      </div>
    )
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
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg-deep)',
      position: 'relative',
      overflow: 'hidden',
    }}>
      <ParticleField />

      <div style={{
        display: 'flex',
        width: 820,
        minHeight: 480,
        position: 'relative',
        zIndex: 1,
      }}>
        {/* Left panel */}
        <motion.div
          initial={{ opacity: 0, x: -30 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.6 }}
          style={{
            flex: 1,
            padding: '60px 48px',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
          }}
        >
          <div style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: '0.2em',
            color: 'var(--accent-red)',
            textTransform: 'uppercase',
            marginBottom: 16,
          }}>Security</div>
          <h1 style={{
            fontSize: 36,
            fontWeight: 800,
            color: '#fff',
            margin: 0,
            lineHeight: 1.2,
            letterSpacing: '-0.02em',
          }}>
            {forced ? '首次登录' : '修改密码'}
          </h1>
          <p style={{
            fontSize: 14,
            color: 'rgba(255,255,255,0.35)',
            margin: '16px 0 0',
            lineHeight: 1.6,
          }}>
            {forced
              ? '当前账号仍使用初始密码，修改后才能进入系统。'
              : '定期更换密码有助于保护账号安全。'}
          </p>

          <div style={{ marginTop: 48, display: 'flex', gap: 32 }}>
            <div>
              <div className="mono" style={{ fontSize: 22, fontWeight: 700, color: '#fff' }}>AES-256</div>
              <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', marginTop: 4 }}>加密传输</div>
            </div>
            <div>
              <div className="mono" style={{ fontSize: 22, fontWeight: 700, color: '#fff' }}>≥8位</div>
              <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', marginTop: 4 }}>密码强度</div>
            </div>
          </div>
        </motion.div>

        {/* Right panel — form */}
        <motion.div
          initial={{ opacity: 0, x: 30 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.6, delay: 0.15 }}
          style={{
            width: 380,
            padding: 40,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            borderRadius: 20,
            background: 'rgba(17, 24, 39, 0.6)',
            backdropFilter: 'blur(24px)',
            WebkitBackdropFilter: 'blur(24px)',
            border: '1px solid rgba(255,255,255,0.08)',
            boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
          }}
        >
          <Form form={form} layout="vertical" onFinish={onFinish}>
            <Form.Item name="old" label={<span style={{ color: 'rgba(255,255,255,0.5)' }}>原密码</span>}
              rules={[{ required: true }]}>
              <Input.Password placeholder="输入当前密码" />
            </Form.Item>
            <Form.Item name="new1" label={<span style={{ color: 'rgba(255,255,255,0.5)' }}>新密码</span>}
              rules={[{ required: true }, { min: 8, message: '至少 8 位' }]}>
              <Input.Password placeholder="至少 8 位" />
            </Form.Item>
            <Form.Item name="new2" label={<span style={{ color: 'rgba(255,255,255,0.5)' }}>确认新密码</span>}
              rules={[{ required: true }]}>
              <Input.Password placeholder="再次输入新密码" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}
              style={{
                height: 48, borderRadius: 12, fontWeight: 600, marginTop: 8,
                background: 'linear-gradient(135deg, #c8102e 0%, #9b0a22 100%)',
                boxShadow: '0 4px 16px rgba(200,16,46,0.25)',
              }}>
              提交修改
            </Button>
            {!forced && (
              <Button block style={{
                marginTop: 12, borderRadius: 12,
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.08)',
                color: 'rgba(255,255,255,0.5)',
              }} onClick={() => {
                logout()
                navigate('/login', { replace: true })
              }}>
                返回登录
              </Button>
            )}
          </Form>
        </motion.div>
      </div>
    </div>
  )
}
