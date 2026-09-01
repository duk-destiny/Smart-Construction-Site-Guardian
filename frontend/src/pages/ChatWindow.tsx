/** AI 助手对话窗口（v2.2 新增）：类豆包交互，取代原「统一上报」的
 *  文字线索 / 对话查询两个 Tab，并新增会话管理、附件、语音、工具抽屉。
 *
 * 设计要点（对齐"能力检测式降级 + 读写硬隔离"）：
 * - 会话侧栏：列表 / 新建 / 改名 / 归档 / 删除（含管理模式批量操作）；
 * - 消息流：快路径五分支卡片 + 认知层 CognitiveRun（轮询/确认卡）复用 ChatCards；
 * - 输入区：文本 + 附件（图片/视频，服务端强制绑定给视频分析工具）+ 语音转写；
 * - 工具抽屉：文字线索建单 / 视频分析 / 周报生成 / 快捷查询，按角色+能力渲染；
 * - 能力缺失（语音识别/合成未配置、LLM 全灭）统一弹 CapabilityModal 说明。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  App as AntApp, Button, Checkbox, DatePicker, Drawer, Empty, Form, Input,
  Modal, Popconfirm, Select, Space, Spin, Tag, Upload,
} from 'antd'
import {
  AudioOutlined, DeleteOutlined, EditOutlined, FolderOpenOutlined,
  InboxOutlined, PlusOutlined, SendOutlined, SoundOutlined, ToolOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import * as ep from '../api/endpoints'
import type { AgentChatReply, HazardOption } from '../api/types'
import { downloadFile } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { renderChat } from '../components/ChatCards'
import CapabilityModal, { capabilityFor } from '../components/CapabilityModal'
import type { CapabilityInfo } from '../components/CapabilityModal'
import PageHeader from '../components/PageHeader'

const { RangePicker } = DatePicker

interface Msg {
  id: number
  role: 'user' | 'assistant'
  text?: string
  reply?: AgentChatReply
  attachments?: string[]
}

let _mid = 0
const nid = () => ++_mid

export default function ChatWindow() {
  const { user } = useAuth()
  const { message } = AntApp.useApp()
  const role = user?.role ?? ''

  const [sessions, setSessions] = useState<ep.SessionRow[]>([])
  const [showArchived, setShowArchived] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [sending, setSending] = useState(false)
  const [modelInfo, setModelInfo] = useState<ep.ModelInfo | null>(null)
  const [cap, setCap] = useState<CapabilityInfo | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [hazards, setHazards] = useState<HazardOption[]>([])
  const [enhance, setEnhance] = useState(false)
  const [managing, setManaging] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [renaming, setRenaming] = useState<ep.SessionRow | null>(null)
  const [renameVal, setRenameVal] = useState('')
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const listRef = useRef<HTMLDivElement>(null)

  const asrOk = modelInfo?.asr_available ?? false
  const ttsOk = modelInfo?.tts_available ?? false

  const loadSessions = useCallback(async (archived = false) => {
    try {
      setSessions(await ep.listSessions(archived))
    } catch { /* 静默 */ }
  }, [])

  useEffect(() => {
    void loadSessions(showArchived)
    void ep.getModelInfo().then(setModelInfo).catch(() => undefined)
    void ep.getCapabilities().then((c) => {
      setHazards(c.hazard_options || [])
      setEnhance(c.enhance_available)
    }).catch(() => undefined)
  }, [loadSessions, showArchived])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [msgs])

  const loadHistory = useCallback(async (sid: string) => {
    try {
      const rows = await ep.agentHistory(sid)
      const next: Msg[] = []
      for (const r of rows) {
        let atts: string[] | undefined
        if (r.attachments) {
          try {
            atts = typeof r.attachments === 'string'
              ? JSON.parse(r.attachments) : r.attachments
          } catch { atts = undefined }
        }
        next.push({
          id: nid(), role: r.role as 'user' | 'assistant',
          text: r.content || r.digest || '', attachments: atts,
        })
      }
      // 上下文恢复（v2.2）：会话内最近未完结 run 恢复实时气泡——
      // 页面刷新后认知轮询与副作用确认卡不丢
      const runIds = rows.map((r) => r.run_id).filter(Boolean) as string[]
      const lastRun = runIds.length ? runIds[runIds.length - 1] : null
      if (lastRun) {
        try {
          const v = await ep.agentProgress(lastRun)
          if (!['completed', 'degraded', 'failed', 'cancelled'].includes(v.status)) {
            next.push({
              id: nid(), role: 'assistant',
              reply: { path: 'cognitive', run_id: lastRun,
                session_id: sid, status: v.status },
            })
          }
        } catch { /* 历史 run 不存在，忽略 */ }
      }
      setMsgs(next)
    } catch { setMsgs([]) }
  }, [])

  function openSession(sid: string) {
    setActiveId(sid)
    void loadHistory(sid)
  }

  async function newChat() {
    try {
      const s = await ep.createSession()
      await loadSessions(showArchived)
      setActiveId(s.id)
      setMsgs([])
    } catch { /* toast 已由拦截器处理 */ }
  }

  function patchTitle(sid: string, title: string) {
    void ep.patchSession(sid, { title }).then(() => loadSessions(showArchived))
  }
  function patchArchive(sid: string, archived: boolean) {
    void ep.patchSession(sid, { archived }).then(() => loadSessions(showArchived))
  }
  function removeSession(sid: string) {
    void ep.deleteSession(sid).then(() => {
      if (activeId === sid) { setActiveId(null); setMsgs([]) }
      loadSessions(showArchived)
    })
  }

  async function send(text: string, atts: string[] = []) {
    const q = text.trim()
    if (!q && atts.length === 0) return
    setSending(true)
    const userMsg: Msg = {
      id: nid(), role: 'user', text: q || '（附件）',
      attachments: atts.length ? atts : undefined,
    }
    setMsgs((m) => [...m, userMsg])
    setInput(''); setFiles([])
    try {
      let reply: AgentChatReply
      if (!q && atts.length === 0) {
        reply = await ep.queryChat('')
      } else {
        reply = await ep.agentChat(q, activeId, atts)
      }
      if (reply.path === 'cognitive' && reply.session_id && !activeId) {
        setActiveId(reply.session_id)
        void loadSessions(showArchived)
      }
      setMsgs((m) => [...m, { id: nid(), role: 'assistant', reply }])
    } catch (e) {
      setMsgs((m) => [...m, {
        id: nid(), role: 'assistant',
        text: `请求失败：${e instanceof Error ? e.message : String(e)}`,
      }])
    } finally {
      setSending(false)
    }
  }

  async function onSend() {
    // 先上传附件（服务端校验后返回路径），再发起对话
    if (files.length) {
      try {
        const paths: string[] = []
        for (const f of files) {
          paths.push((await ep.uploadChatAttachment(f)).path)
        }
        await send(input, paths)
      } catch {
        message.error('附件上传失败，请检查文件类型/大小')
      }
    } else {
      await send(input)
    }
  }

  // ---------- 语音输入（MediaRecorder → ASR 转写回填） ----------
  async function toggleRecord() {
    if (recording) {
      recorderRef.current?.stop()
      return
    }
    if (!asrOk) { setCap(capabilityFor('asr')); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data) }
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRecording(false)
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        if (!blob.size) return
        setTranscribing(true)
        try {
          const text = await ep.asrTranscribe(new File([blob], 'record.webm'))
          setInput((prev) => (prev ? prev + ' ' : '') + text)
        } catch (e) {
          // 能力已配置（asrOk=true）但调用失败 → 如实报错（如上游无语音路由 404），
          // 不伪装成「未配备」误导排查方向
          if (asrOk) { message.error(`语音转写失败：${e instanceof Error ? e.message : String(e)}`) }
          else { setCap(capabilityFor('asr')) }
        } finally { setTranscribing(false) }
      }
      rec.start()
      recorderRef.current = rec
      setRecording(true)
    } catch {
      setCap(capabilityFor('asr'))
    }
  }

  // ---------- 语音输出（TTS 朗读单条回答） ----------
  async function speak(text: string) {
    if (!ttsOk) { setCap(capabilityFor('tts')); return }
    try {
      const blob = await ep.agentTts(text)
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => URL.revokeObjectURL(url)
      void audio.play()
    } catch (e) {
      if (ttsOk) { message.error(`语音合成失败：${e instanceof Error ? e.message : String(e)}`) }
      else { setCap(capabilityFor('tts')) }
    }
  }

  const active = sessions.find((s) => s.id === activeId)

  return (
    <>
      <PageHeader title="AI 助手" subtitle="对话式查询 · 视频分析 · 周报解读 · 文字建单" />
      <div style={{ display: 'flex', gap: 16, height: 'calc(100vh - 280px)', minHeight: 420 }}>
        {/* 会话侧栏 */}
        <div className="glass" style={{
          width: 240, flexShrink: 0, display: 'flex', flexDirection: 'column',
          borderRadius: 14, overflow: 'hidden',
        }}>
          <div style={{ padding: 12, display: 'flex', gap: 8 }}>
            <Button type="primary" icon={<PlusOutlined />} block onClick={newChat}>
              新建对话
            </Button>
          </div>
          <div style={{
            padding: '0 12px 8px', display: 'flex', alignItems: 'center', gap: 8,
            fontSize: 12, color: 'rgba(var(--fg-rgb),0.5)',
          }}>
            <Checkbox
              checked={showArchived}
              onChange={(e) => { setShowArchived(e.target.checked); loadSessions(e.target.checked) }}
            >归档箱</Checkbox>
            <Button size="small" type={managing ? 'primary' : 'default'}
              icon={<DeleteOutlined />} style={{ marginLeft: 'auto' }}
              onClick={() => { setManaging(!managing); setSelected(new Set()) }}>
              {managing ? '完成' : '管理'}
            </Button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px' }}>
            {sessions.length === 0 && (
              <div style={{ padding: 24, textAlign: 'center', color: 'rgba(var(--fg-rgb),0.3)', fontSize: 12 }}>
                {showArchived ? '归档箱为空' : '暂无会话，点「新建对话」开始'}
              </div>
            )}
            {sessions.map((s) => {
              const on = s.id === activeId
              return (
                <div key={s.id}
                  onClick={() => (managing
                    ? setSelected((prev) => {
                        const nx = new Set(prev)
                        if (nx.has(s.id)) nx.delete(s.id); else nx.add(s.id)
                        return nx
                      })
                    : openSession(s.id))}
                  style={{
                    padding: '10px 12px', marginBottom: 4, borderRadius: 10,
                    cursor: 'pointer', position: 'relative',
                    background: on ? 'rgba(var(--accent-primary-rgb),0.1)' : 'transparent',
                    border: `1px solid ${on ? 'rgba(var(--accent-primary-rgb),0.25)' : 'transparent'}`,
                    transition: 'all 0.15s',
                  }}>
                  {managing && (
                    <Checkbox checked={selected.has(s.id)} style={{ pointerEvents: 'none', marginRight: 6 }} />
                  )}
                  <div style={{
                    fontSize: 13, color: on ? '#fff' : 'rgba(var(--fg-rgb),0.7)',
                    fontWeight: on ? 600 : 400,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>
                    {s.title || '新对话'}
                  </div>
                  <div style={{ fontSize: 11, color: 'rgba(var(--fg-rgb),0.3)', marginTop: 2 }}>
                    {(s.updated_at || s.created_at || '').slice(0, 16)}
                  </div>
                  {!managing && (
                    <div style={{
                      position: 'absolute', top: 8, right: 8, display: 'flex', gap: 4,
                      opacity: on ? 1 : 0, transition: 'opacity 0.15s',
                    }}>
                      <span title="改名" style={{ cursor: 'pointer' }}
                        onClick={(e) => { e.stopPropagation(); setRenaming(s); setRenameVal(s.title || '') }}>
                        <EditOutlined />
                      </span>
                      <span title={s.archived ? '移出归档' : '归档'} style={{ cursor: 'pointer' }}
                        onClick={(e) => { e.stopPropagation(); patchArchive(s.id, !s.archived) }}>
                        <FolderOpenOutlined />
                      </span>
                      <Popconfirm title="删除该会话？不可恢复" onConfirm={() => removeSession(s.id)}>
                        <span title="删除" style={{ cursor: 'pointer', color: 'var(--accent-primary)' }}
                          onClick={(e) => e.stopPropagation()}>
                          <DeleteOutlined />
                        </span>
                      </Popconfirm>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
          {managing && selected.size > 0 && (
            <div style={{ padding: 10, borderTop: '1px solid rgba(var(--fg-rgb),0.06)' }}>
              <Space size={8}>
                <Button size="small" onClick={() => {
                  selected.forEach((id) => patchArchive(id, true))
                  setSelected(new Set()); setManaging(false)
                }}>归档</Button>
                <Popconfirm title={`删除 ${selected.size} 个会话？不可恢复`}
                  onConfirm={() => {
                    selected.forEach((id) => removeSession(id))
                    setSelected(new Set()); setManaging(false)
                  }}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            </div>
          )}
        </div>

        {/* 对话主区 */}
        <div className="glass" style={{
          flex: 1, minWidth: 0, borderRadius: 14, display: 'flex',
          flexDirection: 'column', overflow: 'hidden',
        }}>
          <div style={{
            padding: '10px 16px', borderBottom: '1px solid rgba(var(--fg-rgb),0.06)',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ fontSize: 13, color: 'rgba(var(--fg-rgb),0.6)' }}>
              {active ? (active.title || '新对话') : '新对话'}
            </span>
            {modelInfo?.provider_available && (
              <Tag color="green" style={{ marginLeft: 4 }}>
                模型：{modelInfo.provider_available}
              </Tag>
            )}
            <Upload multiple beforeUpload={() => false}
              onChange={({ fileList }) => setFiles(fileList.map((f) => f.originFileObj as File).filter(Boolean))}
              accept=".jpg,.jpeg,.png,.mp4,.mov" showUploadList={false}>
              <Button size="small" icon={<InboxOutlined />} title="上传影像附件" />
            </Upload>
            <Button size="small" icon={<ToolOutlined />} style={{ marginLeft: 'auto' }}
              onClick={() => setDrawerOpen(true)}>
              工具
            </Button>
          </div>

          <div ref={listRef} style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
            {msgs.length === 0 && (
              <div style={{ textAlign: 'center', marginTop: 60 }}>
                <Empty description="问我工单进度、逾期情况、本周安全统计，或上传影像做 AI 分析" />
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 12, flexWrap: 'wrap' }}>
                  {['近7天有多少张未闭环工单', '目前有哪些逾期工单', '本周安全统计'].map((q) => (
                    <Button key={q} size="small" ghost onClick={() => void send(q)}>{q}</Button>
                  ))}
                </div>
              </div>
            )}
            {msgs.map((m) => (
              <div key={m.id} style={{
                display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                marginBottom: 14,
              }}>
                <div style={{
                  maxWidth: '78%', padding: '10px 14px', borderRadius: 14,
                  background: m.role === 'user' ? 'rgba(var(--accent-primary-rgb),0.15)' : 'rgba(var(--fg-rgb),0.04)',
                  border: `1px solid ${m.role === 'user' ? 'rgba(var(--accent-primary-rgb),0.25)' : 'rgba(var(--fg-rgb),0.08)'}`,
                }}>
                  {m.attachments && m.attachments.length > 0 && (
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                      {m.attachments.map((p) => (
                        <span key={p} className="mono" style={{
                          fontSize: 11, color: 'rgba(var(--fg-rgb),0.5)',
                          background: 'rgba(0,0,0,0.3)', padding: '2px 8px', borderRadius: 6,
                        }}>📎 {p.split('/').pop()}</span>
                      ))}
                    </div>
                  )}
                  {m.reply
                    ? <div style={{ minWidth: 320, maxWidth: 640 }}>{renderChat(m.reply, (t) => void send(t))}</div>
                    : <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, color: 'rgba(var(--fg-rgb),0.88)', lineHeight: 1.7 }}>
                      {m.text}
                      {m.role === 'assistant' && m.text && (
                        <span style={{ marginLeft: 10, cursor: 'pointer', opacity: 0.6 }}
                          title="朗读" onClick={() => void speak(m.text || '')}>
                          <SoundOutlined />
                        </span>
                      )}
                    </div>}
                </div>
              </div>
            ))}
            {sending && <div style={{ color: 'rgba(var(--fg-rgb),0.4)', fontSize: 12 }}>正在思考…</div>}
          </div>

          {/* 输入区 */}
          <div style={{ padding: 12, borderTop: '1px solid rgba(var(--fg-rgb),0.06)' }}>
            {files.length > 0 && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                {files.map((f, i) => (
                  <Tag key={i} closable onClose={() => setFiles((p) => p.filter((_, j) => j !== i))}>
                    📎 {f.name}
                  </Tag>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
              <Button icon={<AudioOutlined />} type="text"
                style={{ color: recording ? 'var(--accent-primary)' : undefined }}
                loading={transcribing}
                title={asrOk ? '语音输入' : '语音识别未配置'}
                onClick={() => void toggleRecord()}>
                {recording ? '停止录音' : '语音'}
              </Button>
              <Input.TextArea
                value={input} onChange={(e) => setInput(e.target.value)}
                autoSize={{ minRows: 1, maxRows: 4 }}
                placeholder="输入问题，或上传影像让 AI 分析（Enter 发送，Shift+Enter 换行）"
                onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); void onSend() } }}
              />
              <Button type="primary" icon={<SendOutlined />} loading={sending}
                onClick={() => void onSend()}
                style={{
                  height: 40, borderRadius: 10,
                  background: 'linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-primary-deep) 100%)',
                }}>
                发送
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* 工具抽屉 */}
      <Drawer title="工具" open={drawerOpen} onClose={() => setDrawerOpen(false)}
        width={380} styles={{ body: { background: 'transparent' } }}>
        <ToolCards
          hazards={hazards} enhance={enhance}
          onSend={(text, atts) => { setDrawerOpen(false); void send(text, atts) }}
          onCapability={(k) => setCap(capabilityFor(k))}
          onDownload={(url, name) => void downloadFile(url, name)}
        />
      </Drawer>

      {/* 改名弹窗 */}
      <Modal title="重命名会话" open={!!renaming}
        onCancel={() => setRenaming(null)}
        onOk={() => {
          if (renaming && renameVal.trim()) patchTitle(renaming.id, renameVal.trim())
          setRenaming(null)
        }} okText="保存">
        <Input value={renameVal} onChange={(e) => setRenameVal(e.target.value)}
          placeholder="会话标题" />
      </Modal>

      {cap && <CapabilityModal info={cap} onClose={() => setCap(null)} />}
    </>
  )
}

// ---------- 工具卡片（抽屉内） ----------

function ToolCards({ hazards, enhance, onSend, onCapability, onDownload }: {
  hazards: HazardOption[]; enhance: boolean
  onSend: (text: string, atts?: string[]) => void
  onCapability: (k: 'asr' | 'tts' | 'llm') => void
  onDownload: (url: string, name: string) => void
}) {
  const { message } = AntApp.useApp()
  const [week, setWeek] = useState<[dayjs.Dayjs, dayjs.Dayjs]>([
    dayjs().startOf('week'), dayjs().endOf('week'),
  ])
  const [genWeek, setGenWeek] = useState(false)
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [videoBusy, setVideoBusy] = useState(false)
  const [form] = Form.useForm()
  const [textBusy, setTextBusy] = useState(false)
  const [extracting, setExtracting] = useState(false)

  async function genWeekly() {
    setGenWeek(true)
    try {
      const res = await ep.generateWeekly(week[0].format('YYYY-MM-DD'), week[1].format('YYYY-MM-DD'))
      if (res.file?.download_url) {
        onDownload(res.file.download_url, res.file.name)
        message.success('周报 PDF 已生成并开始下载')
      }
    } finally {
      setGenWeek(false)
    }
  }

  async function submitVideo() {
    if (!videoFile) { message.warning('请先选择影像文件'); return }
    setVideoBusy(true)
    try {
      const { path } = await ep.uploadChatAttachment(videoFile)
      onSend('请分析这段影像/照片，指出安全隐患并给出规范依据', [path])
      setVideoFile(null)
    } catch {
      message.error('影像上传失败')
    } finally {
      setVideoBusy(false)
    }
  }

  async function submitText() {
    const v = await form.validateFields()
    setTextBusy(true)
    try {
      const res = await ep.createTextHazard({
        description: v.description, hazard_key: v.hazard_key,
        scene_id: v.scene, location: v.location,
      })
      onSend(`我已用文字线索建单：${res.task_id}（${res.risk_level}）。${res.work_order?.requirement || ''}`)
      form.resetFields()
      message.success('文字隐患单已创建，进入派发闭环')
    } finally {
      setTextBusy(false)
    }
  }

  const cardStyle: React.CSSProperties = {
    padding: 14, borderRadius: 12, marginBottom: 12,
    background: 'rgba(var(--fg-rgb),0.03)', border: '1px solid rgba(var(--fg-rgb),0.08)',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: 13, fontWeight: 600, color: 'var(--text-strong)', marginBottom: 10,
  }

  return (
    <div>
      <div style={cardStyle}>
        <div style={labelStyle}>⚡ 快捷查询</div>
        <Space wrap size={6}>
          {['近7天有多少张未闭环工单', '目前有哪些逾期工单', '本周安全统计', '帮我写一份本周周报并解读'].map((q) => (
            <Button key={q} size="small" onClick={() => onSend(q)}>{q}</Button>
          ))}
        </Space>
      </div>

      <div style={cardStyle}>
        <div style={labelStyle}>🎬 影像 AI 分析</div>
        <div style={{ fontSize: 12, color: 'rgba(var(--fg-rgb),0.5)', marginBottom: 8 }}>
          上传取证照片/视频，AI 走完整研判链路（检测+规范+定级），只读不建单。
        </div>
        <Upload.Dragger maxCount={1} beforeUpload={() => false}
          onChange={({ fileList }) => setVideoFile(fileList[0]?.originFileObj ?? null)}
          onRemove={() => setVideoFile(null)}
          accept=".jpg,.jpeg,.png,.mp4,.mov"
          style={{ padding: 8 }}>
          <p style={{ margin: 0, fontSize: 13 }}>点击或拖拽影像</p>
        </Upload.Dragger>
        <Button type="primary" block loading={videoBusy} style={{ marginTop: 10 }}
          onClick={() => void submitVideo()}>
          开始 AI 分析
        </Button>
      </div>

      <div style={cardStyle}>
        <div style={labelStyle}>📊 周报生成</div>
        <RangePicker value={week} size="small" style={{ width: '100%' }}
          onChange={(v) => { if (v && v[0] && v[1]) setWeek([v[0], v[1]]) }} />
        <Space style={{ marginTop: 10 }} wrap>
          <Button size="small" loading={genWeek} onClick={() => void genWeekly()}>生成 PDF</Button>
          <Button size="small" onClick={() => onSend(
            `解读 ${week[0].format('MM-DD')} 到 ${week[1].format('MM-DD')} 的安全周报`)}>
            在对话中解读
          </Button>
        </Space>
      </div>

      <div style={cardStyle}>
        <div style={labelStyle}>📝 文字线索建单</div>
        <Form form={form} layout="vertical" initialValues={{ scene: 'hot_work' }}>
          <Form.Item name="scene" label="场景" rules={[{ required: true }]}>
            <Select options={[
              { value: 'hot_work', label: '动火作业安全' },
              { value: 'construction_ppe', label: '施工 PPE' },
            ]} />
          </Form.Item>
          <Form.Item name="hazard_key" label="隐患类别" rules={[{ required: true }]}>
            <Select showSearch optionFilterProp="label"
              options={hazards.map((h) => ({
                value: h.key,
                label: `${h.severity === 'critical' ? '🔴' : '🟡'} ${h.label}`,
              }))} />
          </Form.Item>
          <Form.Item name="location" label="位置（可选）"><Input placeholder="如 3号楼西侧" /></Form.Item>
          <Form.Item name="description" label="隐患描述" rules={[{ required: true }]}>
            <Input.TextArea rows={2} placeholder="例：电焊机旁堆着纸箱没人清理" />
          </Form.Item>
          <Space>
            <Button type="primary" size="small" loading={textBusy}
              onClick={() => void submitText()}>创建隐患单</Button>
            {enhance && (
              <Button size="small" loading={extracting} onClick={async () => {
                const raw = form.getFieldValue('description')
                if (!raw) { message.warning('请先描述情况'); return }
                setExtracting(true)
                try {
                  const out = await ep.enhanceExtract(String(raw))
                  form.setFieldsValue({
                    description: out.description, hazard_key: out.hazard_key,
                    location: out.location || undefined,
                  })
                  message.success('AI 预填完成，请确认后创建')
                } finally { setExtracting(false) }
              }}>AI 预填</Button>
            )}
          </Space>
        </Form>
      </div>

      <div style={{ fontSize: 11, color: 'rgba(var(--fg-rgb),0.3)', marginTop: 4 }}>
        提示：语音输入/朗读能力按当前部署配置检测，未配置时点击会提示「模型暂未拥有该能力」。
      </div>
    </div>
  )
}
