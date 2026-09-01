import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Button, Dropdown, Form, Input, Modal, Switch, App as AntApp } from 'antd'
import {
  BgColorsOutlined, CameraOutlined, CheckOutlined, FileTextOutlined,
  LogoutOutlined, KeyOutlined, LineChartOutlined, MoonOutlined,
  SunOutlined, ToolOutlined, UserOutlined,
  AuditOutlined, PictureOutlined, RobotOutlined,
} from '@ant-design/icons'
import { AnimatePresence } from 'framer-motion'
import { changePassword } from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import type { MenuItem } from '../router'
import { menuItemsFor } from '../router'
import { THEME_PRESETS, useTheme } from '../theme'
import Dock from '../components/Dock'
import PageTransition from '../components/PageTransition'

const ICON_MAP: Record<string, React.ReactNode> = {
  '/chat': <RobotOutlined />,
  '/agents': <PictureOutlined />,
  '/orders': <FileTextOutlined />,
  '/history': <LineChartOutlined />,
  '/realtime': <CameraOutlined />,
  '/my-orders': <ToolOutlined />,
  '/admin': <AuditOutlined />,
}

export default function AppLayout() {
  const { user, logout, refresh } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { message } = AntApp.useApp()
  const { theme, mode, setThemeKey, setMode } = useTheme()
  const [pwdOpen, setPwdOpen] = useState(false)
  const [form] = Form.useForm()

  if (!user) return null
  const items: MenuItem[] = menuItemsFor(user.role)
  const selected = '/' + (location.pathname.split('/')[1] || '')

  async function submitPwd() {
    const v = await form.validateFields()
    if (v.new1 !== v.new2) {
      message.error('两次输入的新密码不一致')
      return
    }
    await changePassword(v.old, v.new1)
    message.success('密码已更新')
    setPwdOpen(false)
    form.resetFields()
    await refresh()
  }

  const dockItems = items.map((m) => ({
    key: m.key,
    icon: ICON_MAP[m.key] || m.icon,
    label: m.label,
    active: selected === m.key,
    onClick: () => navigate(String(m.key)),
  }))

  return (
    <div style={{
      minHeight: '100vh',
      background: 'var(--bg-deep)',
      position: 'relative',
    }}>
      <div className="grid-bg" style={{
        position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none',
        opacity: 0.4,
      }} />

      <header style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 100,
        height: 56,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 24px',
        background: 'var(--topbar-bg)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        borderBottom: '1px solid rgba(var(--fg-rgb),0.04)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 8,
            height: 8,
            borderRadius: 4,
            background: 'var(--accent-primary)',
            boxShadow: '0 0 8px rgba(var(--accent-primary-rgb),0.5)',
            animation: 'pulse-glow 2s ease-in-out infinite',
          }} />
          <span style={{
            fontSize: 14,
            fontWeight: 700,
            color: 'var(--text-strong)',
            letterSpacing: '0.02em',
          }}>智护工地</span>
          <span style={{
            fontSize: 11,
            color: 'rgba(var(--fg-rgb),0.25)',
            fontFamily: 'var(--font-mono)',
            marginLeft: 8,
          }}>v2.2</span>
        </div>

        {/* 明暗模式切换（v2.2）：暗色默认，亮色为新增主题 */}
        <Switch
          checked={mode === 'light'}
          checkedChildren={<SunOutlined />}
          unCheckedChildren={<MoonOutlined />}
          onChange={(v) => setMode(v ? 'light' : 'dark')}
          title="暗色 / 亮色主题"
        />
        {/* 主题色切换（v2.2）：纯前端本地偏好，四预设 */}
        <Dropdown menu={{
          items: THEME_PRESETS.map((t) => ({
            key: t.key,
            label: (
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{
                  width: 12, height: 12, borderRadius: 6, display: 'inline-block',
                  background: t.primary,
                  boxShadow: `0 0 6px ${t.primary}`,
                }} />
                {t.label}
                {theme.key === t.key && (
                  <CheckOutlined style={{ marginLeft: 'auto', color: 'var(--accent-primary)' }} />
                )}
              </span>
            ),
          })),
          onClick: ({ key }) => setThemeKey(String(key)),
          selectedKeys: [theme.key],
        }}>
          <Button type="text" size="small" title="主题色"
            icon={<BgColorsOutlined style={{ fontSize: 16 }} />} />
        </Dropdown>

        <Dropdown menu={{
          items: [
            { key: 'pwd', icon: <KeyOutlined />, label: '修改密码' },
            { key: 'logout', icon: <LogoutOutlined />, label: '退出登录' },
          ],
          onClick: ({ key }) => {
            if (key === 'pwd') setPwdOpen(true)
            if (key === 'logout') { logout(); navigate('/login') }
          },
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            cursor: 'pointer',
            padding: '6px 12px',
            borderRadius: 10,
            background: 'rgba(var(--fg-rgb),0.04)',
            border: '1px solid rgba(var(--fg-rgb),0.06)',
            color: 'rgba(var(--fg-rgb),0.6)',
            fontSize: 13,
            transition: 'all 0.2s',
          }}>
            <UserOutlined style={{ fontSize: 14 }} />
            <span>{user.username}</span>
            <span style={{
              fontSize: 10,
              padding: '2px 6px',
              borderRadius: 4,
              background: user.role === 'admin' ? 'rgba(var(--accent-primary-rgb),0.15)' : 'rgba(0,212,170,0.1)',
              color: user.role === 'admin' ? 'var(--accent-primary)' : '#00d4aa',
              fontWeight: 600,
            }}>{user.role}</span>
          </div>
        </Dropdown>
      </header>

      <main style={{
        position: 'relative',
        zIndex: 1,
        padding: '72px 24px 130px',
        maxWidth: 1280,
        margin: '0 auto',
      }}>
        <AnimatePresence mode="wait">
          <PageTransition key={location.pathname}>
            <Outlet />
          </PageTransition>
        </AnimatePresence>
      </main>

      <Dock items={dockItems} />

      <Modal
        title="修改密码" open={pwdOpen} onOk={submitPwd}
        onCancel={() => setPwdOpen(false)} okText="提交" cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="old" label="原密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="new1" label="新密码（至少 8 位）"
            rules={[{ required: true }, { min: 8 }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="new2" label="确认新密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
