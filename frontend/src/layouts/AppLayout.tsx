import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Dropdown, Form, Input, Modal, App as AntApp } from 'antd'
import {
  CameraOutlined, FileTextOutlined, LogoutOutlined, KeyOutlined,
  LineChartOutlined, ToolOutlined, UploadOutlined, UserOutlined,
  AuditOutlined, RobotOutlined,
} from '@ant-design/icons'
import { AnimatePresence } from 'framer-motion'
import { changePassword } from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import type { MenuItem } from '../router'
import { menuItemsFor } from '../router'
import Dock from '../components/Dock'
import PageTransition from '../components/PageTransition'

const ICON_MAP: Record<string, React.ReactNode> = {
  '/report': <UploadOutlined />,
  '/agents': <RobotOutlined />,
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
        background: 'rgba(7, 11, 20, 0.8)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        borderBottom: '1px solid rgba(255,255,255,0.04)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 8,
            height: 8,
            borderRadius: 4,
            background: '#c8102e',
            boxShadow: '0 0 8px rgba(200,16,46,0.5)',
            animation: 'pulse-glow 2s ease-in-out infinite',
          }} />
          <span style={{
            fontSize: 14,
            fontWeight: 700,
            color: '#fff',
            letterSpacing: '0.02em',
          }}>智护工地</span>
          <span style={{
            fontSize: 11,
            color: 'rgba(255,255,255,0.25)',
            fontFamily: 'var(--font-mono)',
            marginLeft: 8,
          }}>v2.0</span>
        </div>

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
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.06)',
            color: 'rgba(255,255,255,0.6)',
            fontSize: 13,
            transition: 'all 0.2s',
          }}>
            <UserOutlined style={{ fontSize: 14 }} />
            <span>{user.username}</span>
            <span style={{
              fontSize: 10,
              padding: '2px 6px',
              borderRadius: 4,
              background: user.role === 'admin' ? 'rgba(200,16,46,0.15)' : 'rgba(0,212,170,0.1)',
              color: user.role === 'admin' ? '#c8102e' : '#00d4aa',
              fontWeight: 600,
            }}>{user.role}</span>
          </div>
        </Dropdown>
      </header>

      <main style={{
        position: 'relative',
        zIndex: 1,
        padding: '72px 24px 100px',
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
