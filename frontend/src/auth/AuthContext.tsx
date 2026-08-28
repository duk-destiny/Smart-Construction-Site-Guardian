/** 登录态上下文：token/用户信息持久化于 localStorage，供路由守卫与菜单使用。 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import * as ep from '../api/endpoints'
import { clearAuth, getToken, storedUser } from '../api/client'
import type { UserInfo } from '../api/types'

interface AuthState {
  user: UserInfo | null
  token: string
  login: (username: string, password: string) => Promise<UserInfo>
  refresh: () => Promise<void>
  logout: () => void
}

const AuthCtx = createContext<AuthState>({
  user: null, token: '',
  login: async () => { throw new Error('AuthProvider missing') },
  refresh: async () => {},
  logout: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState(getToken())
  const [user, setUser] = useState<UserInfo | null>(() =>
    storedUser<UserInfo>())

  const login = useCallback(async (username: string, password: string) => {
    const res = await ep.login(username, password)
    const info: UserInfo = {
      user_id: res.user_id, username: res.username,
      role: res.role as UserInfo['role'],
      must_change_password: res.must_change_password,
    }
    localStorage.setItem('zhg_token', res.token)
    localStorage.setItem('zhg_user', JSON.stringify(info))
    setToken(res.token)
    setUser(info)
    return info
  }, [])

  const refresh = useCallback(async () => {
    try {
      const info = await ep.me()
      localStorage.setItem('zhg_user', JSON.stringify(info))
      setUser(info as UserInfo)
    } catch { /* 401 已由拦截器统一跳登录 */ }
  }, [])

  const logout = useCallback(() => {
    clearAuth()
    setToken('')
    setUser(null)
  }, [])

  // 起始挂载时若本地有用户快照则与 DB 对齐（角色/改密标记取实时值）
  useEffect(() => {
    if (getToken()) void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <AuthCtx.Provider value={{ user, token, login, refresh, logout }}>
      {children}
    </AuthCtx.Provider>
  )
}

export function useAuth() {
  return useContext(AuthCtx)
}
