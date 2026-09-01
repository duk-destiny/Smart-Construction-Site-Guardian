/** 工单 AI 弹窗（v2.2）：仅携带当前工单上下文的只读问答抽屉。
 *
 * 面向责任人（我的整改单）与管理/安全员（工单台账）：
 * - 服务端只喂当前这条工单的上下文 + RAG 条款，权限在服务层强制
 *   （本单责任人本人或 admin/safety）；零写入、不进认知内核；
 * - 预设问法 chips：规范依据 / 要求解读 / 验收要点；
 * - LLM 不可用时后端返回可读降级提示（不编造、不断链路）。
 */
import { useState } from 'react'
import { App as AntApp, Button, Drawer, Input, Space, Tag } from 'antd'
import { SendOutlined } from '@ant-design/icons'
import * as ep from '../api/endpoints'

interface QA {
  q: string
  a: string
  status: string
}

const PRESETS = [
  '这条单的规范依据是什么？',
  '整改要求具体是什么意思？',
  '验收时需要准备/注意什么？',
]

export default function OrderAskDrawer({ orderId, open, onClose }: {
  orderId: string | null
  open: boolean
  onClose: () => void
}) {
  const { message } = AntApp.useApp()
  const [qa, setQa] = useState<QA[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  async function ask(q: string) {
    const question = q.trim()
    if (!question || !orderId) return
    setBusy(true)
    setInput('')
    try {
      const res = await ep.askOrder(orderId, question)
      setQa((prev) => [...prev, { q: question, a: res.answer, status: res.status }])
    } catch (e) {
      message.error(e instanceof Error ? e.message : '问询失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer title={<>🤖 工单 AI 助手 <Tag color="purple" style={{ marginLeft: 8 }}>仅本单上下文 · 只读</Tag></>}
      open={open} onClose={() => { setQa([]); onClose() }} width={420}>
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {qa.length === 0 && (
            <div style={{ color: 'rgba(var(--fg-rgb),0.5)', fontSize: 13, lineHeight: 1.9 }}>
              围绕<b style={{ color: 'rgba(var(--fg-rgb),0.8)' }}>当前这条工单</b>提问，
              例如规范依据、整改要求解读、验收要点。AI 只能基于本单内容与规范条款回答，
              不会改变定级，也不会写入任何数据。
            </div>
          )}
          {qa.map((item, i) => (
            <div key={i} style={{ marginBottom: 16 }}>
              <div style={{
                textAlign: 'right', margin: '6px 0', fontSize: 13,
                color: 'rgba(var(--fg-rgb),0.85)',
              }}>
                <span style={{
                  display: 'inline-block', padding: '8px 12px', borderRadius: 12,
                  background: 'rgba(var(--accent-primary-rgb),0.15)',
                  border: '1px solid rgba(var(--accent-primary-rgb),0.25)', textAlign: 'left',
                }}>{item.q}</span>
              </div>
              <div style={{
                padding: '10px 12px', borderRadius: 12, fontSize: 13, lineHeight: 1.7,
                background: 'rgba(var(--fg-rgb),0.04)',
                border: '1px solid rgba(var(--fg-rgb),0.08)',
                color: item.status === 'failed'
                  ? 'rgba(250,173,20,0.9)' : 'rgba(var(--fg-rgb),0.85)',
                whiteSpace: 'pre-wrap',
              }}>{item.a}</div>
            </div>
          ))}
        </div>
        <div style={{ paddingTop: 10, borderTop: '1px solid rgba(var(--fg-rgb),0.06)' }}>
          <Space wrap size={6} style={{ marginBottom: 8 }}>
            {PRESETS.map((p) => (
              <Button key={p} size="small" onClick={() => void ask(p)}>{p}</Button>
            ))}
          </Space>
          <div style={{ display: 'flex', gap: 8 }}>
            <Input value={input} onChange={(e) => setInput(e.target.value)}
              placeholder="问点关于这张单的问题…"
              onPressEnter={(e) => { e.preventDefault(); void ask(input) }} />
            <Button type="primary" icon={<SendOutlined />} loading={busy}
              onClick={() => void ask(input)} />
          </div>
        </div>
      </div>
    </Drawer>
  )
}
