/** 组件冒烟：登录页渲染、风险标签映射、axios 拦截器鉴权与 401 处理。 */
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { api, clearAuth, getToken, setAuth } from '../api/client'
import Login from '../pages/Login'
import { RiskTag, OrderStatusTag } from '../components/Tags'

describe('Tags', () => {
  it('风险等级映射颜色', () => {
    render(<RiskTag level="重大" />)
    expect(screen.getByText('重大')).toBeInTheDocument()
  })
  it('工单状态显示中文', () => {
    render(<OrderStatusTag status="submitted" />)
    expect(screen.getByText('待验收')).toBeInTheDocument()
  })
})

describe('Login', () => {
  it('渲染用户名/密码表单与登录按钮', () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <Login />
        </AuthProvider>
      </MemoryRouter>,
    )
    expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('密码')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '进入系统' })).toBeInTheDocument()
  })
})

describe('axios client', () => {
  it('请求自动携带 Bearer token', async () => {
    setAuth('jwt-test-token', { username: 'x' })
    let seen: string | undefined
    // 用自定义适配器捕获请求头，不发真实网络
    api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
      seen = String(config.headers?.Authorization ?? '')
      return {
        data: {}, status: 200, statusText: 'OK',
        headers: {}, config,
      } as never
    }
    await api.get('/anything')
    expect(seen).toBe('Bearer jwt-test-token')
    clearAuth()
  })

  it('401 清空登录态', async () => {
    setAuth('expired', { username: 'x' })
    // 自定义 adapter 需按 axios 语义抛 AxiosError（带 response）才会进响应拦截器
    api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
      throw new AxiosError('Unauthorized', AxiosError.ERR_BAD_REQUEST, config,
        null, {
          data: { detail: '登录已过期' }, status: 401,
          statusText: 'Unauthorized', headers: {}, config,
        } as never)
    }
    await expect(api.get('/auth/me')).rejects.toThrow()
    await waitFor(() => expect(getToken()).toBe(''))
  })
})
