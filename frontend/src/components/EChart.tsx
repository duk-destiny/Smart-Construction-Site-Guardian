/** echarts 轻封装：全量引入（离线打包，无 CDN）；option 变更即重渲染。 */
import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

export default function EChart({ option, height = 320 }: {
  option: echarts.EChartsOption
  height?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    chart.current = echarts.init(ref.current)
    const onResize = () => chart.current?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.current?.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option, true)
  }, [option])

  return <div ref={ref} style={{ width: '100%', height }} />
}
