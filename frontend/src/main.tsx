import { ConfigProvider, App as AntApp, theme as antdTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import AppLayout from './layouts/AppLayout'
import Admin from './pages/Admin'
import AgentRun from './pages/AgentRun'
import ChangePassword from './pages/ChangePassword'
import ChatWindow from './pages/ChatWindow'
import History from './pages/History'
import Login from './pages/Login'
import MyOrders from './pages/MyOrders'
import Orders from './pages/Orders'
import Realtime from './pages/Realtime'
import { homeFor, RequireRole } from './router'
import { ThemeProvider, useTheme } from './theme'
import './styles/global.css'

dayjs.locale('zh-cn')

function HomeRedirect() {
  const { user } = useAuth()
  return <Navigate to={user ? homeFor(user.role) : '/login'} replace />
}

function AppRoutes() {
  const staff = ['admin', 'safety'] as const
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/change-password" element={<ChangePassword />} />
      <Route element={<RequireRole roles={[...staff, 'responsible']}>
        <AppLayout />
      </RequireRole>}>
        <Route path="/chat" element={
          <RequireRole roles={[...staff]}><ChatWindow /></RequireRole>} />
        <Route path="/agents" element={
          <RequireRole roles={[...staff]}><AgentRun /></RequireRole>} />
        <Route path="/agents/:taskId" element={
          <RequireRole roles={[...staff]}><AgentRun /></RequireRole>} />
        <Route path="/orders" element={
          <RequireRole roles={[...staff]}><Orders /></RequireRole>} />
        <Route path="/history" element={
          <RequireRole roles={[...staff]}><History /></RequireRole>} />
        <Route path="/realtime" element={
          <RequireRole roles={[...staff]}><Realtime /></RequireRole>} />
        <Route path="/my-orders" element={<MyOrders />} />
        <Route path="/admin" element={
          <RequireRole roles={['admin']}><Admin /></RequireRole>} />
        <Route path="*" element={<HomeRedirect />} />
      </Route>
    </Routes>
  )
}

/** 主题色接入 AntD token：colorPrimary 随 ThemeProvider 动态变化。 */
function ThemedRoot() {
  const { theme, mode } = useTheme()
  const light = mode === 'light'
  return (
    <ConfigProvider locale={zhCN} theme={{
      algorithm: light ? antdTheme.defaultAlgorithm : antdTheme.darkAlgorithm,
      token: {
        colorPrimary: theme.primary,
        colorBgBase: light ? '#f4f6fa' : '#0a0e1a',
        colorBgContainer: light ? '#ffffff' : 'rgba(17, 24, 39, 0.7)',
        colorBgElevated: light ? '#ffffff' : '#1a2235',
        colorBorder: light ? 'rgba(15, 23, 42, 0.12)' : 'rgba(255, 255, 255, 0.06)',
        colorText: light ? '#1f2937' : '#e5e7eb',
        colorTextSecondary: light ? '#4b5563' : '#9ca3af',
        borderRadius: 10,
        fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
      },
    }}>
      <AntApp>
        <BrowserRouter>
          <AuthProvider>
            <AppRoutes />
          </AuthProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <ThemedRoot />
    </ThemeProvider>
  </React.StrictMode>,
)
