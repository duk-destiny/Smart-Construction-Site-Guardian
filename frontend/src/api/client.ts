/** axios 封装：token 注入 + 401/403 统一处理 + 错误消息规范化。
 *
 * 401 → 清登录态并跳登录页；403/4xx/5xx → antd message 提示后端 detail
 * 后调用方 reject（页面无需各自 try/catch 提示）。
 */
import axios from 'axios'
import { message } from 'antd'

export const TOKEN_KEY = 'zhg_token'
export const USER_KEY = 'zhg_user'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setAuth(token: string, user: unknown) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function storedUser<T>(): T | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

export const api = axios.create({ baseURL: '/api', timeout: 120_000 })

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const status = error?.response?.status
    const detail: string = error?.response?.data?.detail || error?.message || '请求失败'
    if (status === 401) {
      clearAuth()
      // 硬跳转：清空一切页面状态，回登录页
      if (!location.pathname.startsWith('/login')) {
        message.warning(detail)
        location.href = '/login'
      }
    } else {
      message.error(detail)
    }
    return Promise.reject(new Error(detail))
  },
)

/** 下载二进制（导出 CSV/Excel/PDF）：走同一鉴权，成功后触发浏览器保存。 */
export async function downloadFile(url: string, fallbackName: string) {
  const resp = await api.get(url, { responseType: 'blob' })
  const dispo: string = resp.headers['content-disposition'] || ''
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(dispo)
  const name = m ? decodeURIComponent(m[1]) : fallbackName
  const href = URL.createObjectURL(resp.data as Blob)
  const a = document.createElement('a')
  a.href = href
  a.download = name
  a.click()
  URL.revokeObjectURL(href)
}
