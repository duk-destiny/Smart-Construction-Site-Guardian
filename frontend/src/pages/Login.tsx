import { App as AntApp, Form, Input, Button } from 'antd'
import { motion } from 'framer-motion'
import { useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { useAuth } from '../auth/AuthContext'
import { homeFor } from '../router'
import ParticleField from '../components/ParticleField'

export default function Login() {
  const { login, user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { message } = AntApp.useApp()
  const [loading, setLoading] = useState(false)

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
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--auth-bg)',
      position: 'relative',
      overflow: 'hidden',
    }}>
      <ParticleField />

      <div style={{
        position: 'relative',
        zIndex: 1,
        display: 'flex',
        alignItems: 'center',
        gap: 80,
        padding: '0 40px',
        maxWidth: 960,
        width: '100%',
      }}>
        <motion.div
          initial={{ opacity: 0, x: -30 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
          style={{ flex: 1, minWidth: 0 }}
        >
          <div style={{
            fontSize: 14,
            fontWeight: 600,
            color: 'var(--accent-primary)',
            letterSpacing: '0.15em',
            textTransform: 'uppercase',
            marginBottom: 16,
            fontFamily: 'var(--font-mono)',
          }}>
            CONSTRUCTION SAFETY AI
          </div>
          <h1 style={{
            fontSize: 52,
            fontWeight: 800,
            color: 'var(--text-strong)',
            lineHeight: 1.1,
            margin: '0 0 20px',
            letterSpacing: '-0.02em',
          }}>
            智护工地
          </h1>
          <p style={{
            fontSize: 16,
            color: 'rgba(var(--fg-rgb),0.4)',
            lineHeight: 1.7,
            margin: 0,
            maxWidth: 360,
          }}>
            影像研判 · 实时视觉监测 · 工单全链路闭环
          </p>
          <div style={{
            marginTop: 40,
            display: 'flex',
            gap: 24,
          }}>
            {[
              { label: '检测帧', value: '—' },
              { label: 'Agent 链路', value: '4 级' },
              { label: '响应时效', value: '<3s' },
            ].map((s) => (
              <div key={s.label}>
                <div style={{
                  fontSize: 22,
                  fontWeight: 700,
                  color: 'var(--text-strong)',
                  fontFamily: 'var(--font-mono)',
                }}>{s.value}</div>
                <div style={{
                  fontSize: 11,
                  color: 'rgba(var(--fg-rgb),0.3)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.1em',
                  marginTop: 4,
                }}>{s.label}</div>
              </div>
            ))}
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, x: 30 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.8, delay: 0.2, ease: [0.25, 0.46, 0.45, 0.94] }}
          style={{ width: 380, flexShrink: 0 }}
        >
          <div style={{
            padding: 32,
            borderRadius: 20,
            background: 'var(--glass-bg)',
            backdropFilter: 'blur(24px)',
            WebkitBackdropFilter: 'blur(24px)',
            border: '1px solid rgba(var(--fg-rgb),0.06)',
            boxShadow: '0 24px 64px rgba(0,0,0,0.3)',
          }}>
            <div style={{
              fontSize: 18,
              fontWeight: 700,
              color: 'var(--text-strong)',
              marginBottom: 4,
            }}>登录系统</div>
            <div style={{
              fontSize: 13,
              color: 'rgba(var(--fg-rgb),0.35)',
              marginBottom: 28,
            }}>使用您的授权账号访问监控平台</div>

            <Form onFinish={onFinish} layout="vertical" autoFocus size="large">
              <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
                <Input
                  placeholder="用户名"
                  autoComplete="username"
                  style={{
                    height: 48,
                    background: 'rgba(var(--fg-rgb),0.04)',
                    border: '1px solid rgba(var(--fg-rgb),0.08)',
                    borderRadius: 12,
                    color: 'var(--text-strong)',
                    fontSize: 15,
                  }} />
              </Form.Item>
              <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
                <Input.Password
                  placeholder="密码"
                  autoComplete="current-password"
                  style={{
                    height: 48,
                    background: 'rgba(var(--fg-rgb),0.04)',
                    border: '1px solid rgba(var(--fg-rgb),0.08)',
                    borderRadius: 12,
                    color: 'var(--text-strong)',
                    fontSize: 15,
                  }} />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0, marginTop: 8 }}>
                <Button
                  type="primary"
                  htmlType="submit"
                  block
                  loading={loading}
                  style={{
                    height: 48,
                    borderRadius: 12,
                    fontSize: 15,
                    fontWeight: 600,
                    background: 'linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-primary-deep) 100%)',
                    border: 'none',
                    boxShadow: '0 4px 16px rgba(var(--accent-primary-rgb),0.3)',
                  }}
                >
                  进入系统
                </Button>
              </Form.Item>
            </Form>

            <div style={{
              marginTop: 24,
              paddingTop: 20,
              borderTop: '1px solid rgba(var(--fg-rgb),0.06)',
              fontSize: 11,
              color: 'rgba(var(--fg-rgb),0.2)',
              textAlign: 'center',
              fontFamily: 'var(--font-mono)',
            }}>
              动火作业 / 施工 PPE · AI 安全监控
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  )
}
