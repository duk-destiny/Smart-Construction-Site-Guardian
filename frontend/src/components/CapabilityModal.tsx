/** 能力提示弹窗（v2.2）：模型/服务不具备某能力时的统一解释口径。
 *
 * 设计约定（对齐"能力检测式降级"）：用户点了当前环境不具备的能力
 * （语音识别未配置、语音合成未配置、无可用 LLM 档位…），不再静默或
 * 只 toast 一闪而过，而是弹窗说明「缺什么、怎么配上」，关闭即回原位。
 */
import { Modal } from 'antd'

export interface CapabilityInfo {
  open: boolean
  title: string
  description: string
}

export function capabilityFor(kind: 'asr' | 'tts' | 'llm'): CapabilityInfo {
  if (kind === 'asr') {
    return {
      open: true, title: '模型暂未拥有语音识别能力',
      description: '当前部署未配置语音转写服务（asr.*）。'
        + '请联系管理员在 config.yaml 的 asr 段配置 OpenAI 兼容转写端点后，'
        + '即可使用按住说话 / 上传语音转文字。',
    }
  }
  if (kind === 'tts') {
    return {
      open: true, title: '模型暂未拥有语音合成能力',
      description: '当前部署未配置语音合成服务（tts.*）。'
        + '请联系管理员在 config.yaml 的 tts 段配置 OpenAI 兼容语音端点后，'
        + '即可朗读 AI 回答。',
    }
  }
  return {
    open: true, title: 'AI 通道当前不可用',
    description: '认知模型通道未配置或全部不可达（llm.providers）。'
      + '规则快路径查询仍可正常使用；认知类能力请检查网络与模型配置。',
  }
}

export default function CapabilityModal({ info, onClose }: {
  info: CapabilityInfo; onClose: () => void
}) {
  return (
    <Modal open={info.open} onCancel={onClose} onOk={onClose}
      okText="知道了" cancelButtonProps={{ style: { display: 'none' } }}
      title={<span>🈚 {info.title}</span>}>
      <div style={{ color: 'rgba(var(--fg-rgb),0.65)', fontSize: 13, lineHeight: 1.8 }}>
        {info.description}
      </div>
    </Modal>
  )
}
