/** 主题色切换（v2.2）：纯前端本地偏好，无后端状态。
 *
 * 预设四套主色（赤焰为默认）：切换时同步三处——
 * 1. AntD token.colorPrimary（所有 antd 组件主色）；
 * 2. CSS 变量（--accent-primary / -deep / -rgb 与派生的 --accent-red*、
 *    --border-glow），业务组件的内联样式经全局替换已全部走变量；
 * 3. localStorage（zhg_theme），刷新与下次登录保持。
 * 风险语义色（RiskTag 的红/橙/绿）与成功色（--accent-cyan）不随主题变。
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

export interface ThemePreset {
  key: string
  label: string
  primary: string
  deep: string
  rgb: string
}

export const THEME_PRESETS: ThemePreset[] = [
  { key: 'crimson', label: '赤焰', primary: '#c8102e', deep: '#9b0a22', rgb: '200, 16, 46' },
  { key: 'ocean', label: '沧海', primary: '#1e6fd9', deep: '#1554a8', rgb: '30, 111, 217' },
  { key: 'jade', label: '翡翠', primary: '#0f9d76', deep: '#0b7a5c', rgb: '15, 157, 118' },
  { key: 'violet', label: '紫霄', primary: '#7c4dff', deep: '#5b2ed6', rgb: '124, 77, 255' },
]

const THEME_KEY = 'zhg_theme'
const MODE_KEY = 'zhg_mode'

function initialPreset(): ThemePreset {
  const key = localStorage.getItem(THEME_KEY)
  return THEME_PRESETS.find((t) => t.key === key) ?? THEME_PRESETS[0]
}

function initialMode(): 'dark' | 'light' {
  return localStorage.getItem(MODE_KEY) === 'light' ? 'light' : 'dark'
}

function applyPreset(t: ThemePreset) {
  const root = document.documentElement
  root.style.setProperty('--accent-primary', t.primary)
  root.style.setProperty('--accent-primary-deep', t.deep)
  root.style.setProperty('--accent-primary-rgb', t.rgb)
  root.style.setProperty('--accent-red', t.primary)
  root.style.setProperty('--accent-red-glow', `rgba(${t.rgb}, 0.4)`)
  root.style.setProperty('--border-glow', `rgba(${t.rgb}, 0.3)`)
  root.dataset.theme = t.key
}

function applyMode(mode: 'dark' | 'light') {
  document.documentElement.dataset.mode = mode
}

interface ThemeCtx {
  theme: ThemePreset
  mode: 'dark' | 'light'
  setThemeKey: (key: string) => void
  setMode: (mode: 'dark' | 'light') => void
}

const Ctx = createContext<ThemeCtx>({
  theme: THEME_PRESETS[0], mode: 'dark',
  setThemeKey: () => undefined, setMode: () => undefined,
})

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<ThemePreset>(initialPreset)
  const [mode, setModeState] = useState<'dark' | 'light'>(initialMode)

  useEffect(() => { applyPreset(theme) }, [theme])
  useEffect(() => { applyMode(mode) }, [mode])

  const setThemeKey = useCallback((key: string) => {
    const next = THEME_PRESETS.find((t) => t.key === key)
    if (!next) return
    localStorage.setItem(THEME_KEY, key)
    setTheme(next)
  }, [])

  const setMode = useCallback((m: 'dark' | 'light') => {
    localStorage.setItem(MODE_KEY, m)
    setModeState(m)
  }, [])

  const value = useMemo(
    () => ({ theme, mode, setThemeKey, setMode }), [theme, mode, setThemeKey, setMode])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useTheme(): ThemeCtx {
  return useContext(Ctx)
}
