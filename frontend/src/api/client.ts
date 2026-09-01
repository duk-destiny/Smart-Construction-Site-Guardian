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
      } else {
        // 登录页上的 401 = 账号密码错误，必须给用户可见反馈
        message.error(detail)
      }
    } else {
      // 任务轮询（/tasks/{id}/result|progress）的 404 属正常态（结果未就绪），静默不提示；仍 reject 由调用方处理
      const url: string = error?.config?.url || ''
      const method: string = (error?.config?.method || '').toUpperCase()
      // 轮询类 404 属正常态（任务/认知 run 未就绪或已随会话删除），静默不提示；仍 reject 由调用方处理
      const isPoll404 =
        status === 404 && method === 'GET' &&
        (/\/tasks\/[^/]+\/(result|progress)$/.test(url) ||
         /\/agent\/runs\/[^/]+\/(progress|trace)$/.test(url))
      // 501 = 能力未配置（asr/tts），由调用方弹「模型暂未拥有该能力」提示，此处不重复 toast
      if (!isPoll404 && status !== 501) message.error(detail)
    }
    return Promise.reject(new Error(detail))
  },
)

/** 下载二进制（导出 CSV/Excel/PDF）：走同一鉴权，成功后触发浏览器保存。 */
export async function downloadFile(url: string, fallbackName: string) {
  // 后端返回的 download_url 自带 /api 前缀，axios baseURL 又是 /api——归一化防 /api/api
  const path = url.startsWith('/api/') ? url.slice(4) : url
  const resp = await api.get(path, { responseType: 'blob' })
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
