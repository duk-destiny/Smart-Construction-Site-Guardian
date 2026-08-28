/** 应用骨架：按角色注入侧边栏菜单；顶栏用户信息/改密/退出。 */
import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Dropdown, Layout, Menu, Modal, Form, Input, App as AntApp } from 'antd'
import {
  CameraOutlined, FileTextOutlined, LogoutOutlined, KeyOutlined,
  LineChartOutlined, ToolOutlined, UploadOutlined, UserOutlined,
  AuditOutlined, RobotOutlined,
} from '@ant-design/icons'
import { changePassword } from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import type { MenuItem } from '../router'
import { menuItemsFor } from '../router'

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

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider theme="dark" breakpoint="lg" collapsedWidth="0">
        <div style={{ color: '#fff', padding: 16, fontWeight: 600 }}>
          🏗 智护工地
        </div>
        <Menu
          theme="dark" mode="inline" selectedKeys={[selected]}
          items={items as never}
          onClick={(e) => navigate(String(e.key))}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header style={{
          background: '#fff', display: 'flex',
          justifyContent: 'flex-end', alignItems: 'center',
          paddingInline: 24,
        }}>
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
            <span style={{ cursor: 'pointer' }}>
              <UserOutlined /> {user.username}（{user.role}）
            </span>
          </Dropdown>
        </Layout.Header>
        <Layout.Content style={{ margin: 16 }}>
          <Outlet />
        </Layout.Content>
      </Layout>

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
    </Layout>
  )
}

export const MENU_ICONS = {
  report: <UploadOutlined />, agents: <RobotOutlined />,
  orders: <FileTextOutlined />, history: <LineChartOutlined />,
  realtime: <CameraOutlined />, my_orders: <ToolOutlined />,
  admin: <AuditOutlined />,
}
