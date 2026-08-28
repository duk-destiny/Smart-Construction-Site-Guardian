/** 媒体 URL 助手：<img> 无法带 header，统一以 ?token= 携带 JWT
 * （后端 api.deps.media_auth 支持；仅内网部署语义）。 */
import { getToken } from '../api/client'

export function mediaUrl(rel: string): string {
  return `/api/media/${rel}?token=${encodeURIComponent(getToken())}`
}
