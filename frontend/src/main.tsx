import { ConfigProvider, App as AntApp, theme } from 'antd'
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
import History from './pages/History'
import Login from './pages/Login'
import MyOrders from './pages/MyOrders'
import Orders from './pages/Orders'
import Realtime from './pages/Realtime'
import Report from './pages/Report'
import { homeFor, RequireRole } from './router'
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
        <Route path="/report" element={
          <RequireRole roles={[...staff]}><Report /></RequireRole>} />
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

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{
      algorithm: theme.darkAlgorithm,
      token: {
        colorPrimary: '#c8102e',
        colorBgBase: '#0a0e1a',
        colorBgContainer: 'rgba(17, 24, 39, 0.7)',
        colorBgElevated: '#1a2235',
        colorBorder: 'rgba(255, 255, 255, 0.06)',
        colorText: '#e5e7eb',
        colorTextSecondary: '#9ca3af',
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
  </React.StrictMode>,
)
