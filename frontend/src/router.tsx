/** 路由与角色守卫：responsible 仅「我的整改单」；admin 独占管理端。
 *
 * must_change_password=true 时强制跳改密页（首登改密门控，v0.8 口径）。
 */
import { Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import type { UserInfo } from './api/types'
import {
  AuditOutlined, CameraOutlined, FileTextOutlined, LineChartOutlined,
  RobotOutlined, ToolOutlined, UploadOutlined,
} from '@ant-design/icons'

export interface MenuItem {
  key: string
  icon: ReactNode
  label: string
}

export function menuItemsFor(role: UserInfo['role']): MenuItem[] {
  if (role === 'responsible') {
    return [{ key: '/my-orders', icon: <ToolOutlined />, label: '我的整改单' }]
  }
  const items: MenuItem[] = [
    { key: '/report', icon: <UploadOutlined />, label: '统一上报' },
    { key: '/agents', icon: <RobotOutlined />, label: '多 Agent 研判' },
    { key: '/orders', icon: <FileTextOutlined />, label: '工单闭环' },
    { key: '/history', icon: <LineChartOutlined />, label: '历史分析' },
    { key: '/realtime', icon: <CameraOutlined />, label: '实时监测' },
  ]
  if (role === 'admin') {
    items.push({ key: '/admin', icon: <AuditOutlined />, label: '管理端' })
  }
  return items
}

/** 默认落地页按角色：responsible → 我的整改单；其余 → 统一上报。 */
export function homeFor(role: UserInfo['role']): string {
  return role === 'responsible' ? '/my-orders' : '/report'
}

export function RequireRole({ roles, children }: {
  roles: UserInfo['role'][]
  children: ReactNode
}) {
  const location = useLocation()
  const raw = localStorage.getItem('zhg_user')
  const user = raw ? (JSON.parse(raw) as UserInfo) : null
  const token = localStorage.getItem('zhg_token')
  if (!token || !user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }
  if (user.must_change_password && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />
  }
  if (roles.length && !roles.includes(user.role)) {
    return <Navigate to={homeFor(user.role)} replace />
  }
  return <>{children}</>
}
