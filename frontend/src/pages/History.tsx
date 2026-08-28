/** 历史分析页（admin/safety）：日期筛选 + 合规率趋势/类别分布图表 + 任务风险表。 */
import { useCallback, useEffect, useState } from 'react'
import { Card, DatePicker, Space, Table } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import * as ep from '../api/endpoints'
import EChart from '../components/EChart'

type Range = [Dayjs | null, Dayjs | null] | null

export default function History() {
  const [range, setRange] = useState<Range>([
    dayjs().subtract(6, 'day'), dayjs(),
  ])
  const [stats, setStats] = useState<Awaited<ReturnType<typeof ep.historyStats>>>([])
  const [sev, setSev] = useState<Awaited<ReturnType<typeof ep.historySeverity>>>([])
  const [risks, setRisks] = useState<Awaited<ReturnType<typeof ep.historyTaskRisks>>>([])

  const params = useCallback(() => ({
    start: range?.[0]?.format('YYYY-MM-DD'),
    end: range?.[1]?.format('YYYY-MM-DD'),
  }), [range])

  useEffect(() => {
    const p = params()
    void ep.historyStats(p.start, p.end).then(setStats)
    void ep.historySeverity(p.start, p.end).then(setSev)
    void ep.historyTaskRisks(p.start, p.end).then(setRisks)
  }, [params])

  const trendOption = {
    tooltip: { trigger: 'axis' as const },
    legend: { data: ['合规率%', '不合规帧', '警告帧', '合规帧'] },
    grid: { left: 48, right: 24, top: 40, bottom: 32 },
    xAxis: { type: 'category' as const, data: stats.map((s) => s.day) },
    yAxis: [
      { type: 'value' as const, name: '帧数' },
      { type: 'value' as const, name: '合规率%', max: 100, axisLabel: { formatter: '{value}%' } },
    ],
    series: [
      {
        name: '合规率%', type: 'line' as const, yAxisIndex: 1, smooth: true,
        data: stats.map((s) => {
          const total = (s.compliant || 0) + (s.warning || 0) + (s.non_compliant || 0)
          return total ? Math.round((s.compliant / total) * 1000) / 10 : 0
        }),
      },
      { name: '不合规帧', type: 'bar' as const, stack: 'f',
        data: stats.map((s) => s.non_compliant), itemStyle: { color: '#c8102e' } },
      { name: '警告帧', type: 'bar' as const, stack: 'f',
        data: stats.map((s) => s.warning), itemStyle: { color: '#faad14' } },
      { name: '合规帧', type: 'bar' as const, stack: 'f',
        data: stats.map((s) => s.compliant), itemStyle: { color: '#52c41a' } },
    ],
  }

  const sevOption = {
    tooltip: {},
    grid: { left: 48, right: 24, top: 24, bottom: 64 },
    xAxis: {
      type: 'category' as const,
      data: sev.map((s) => s.cls),
      axisLabel: { rotate: 30 },
    },
    yAxis: { type: 'value' as const },
    series: [{
      type: 'bar' as const, name: '命中次数',
      data: sev.map((s) => s.cnt), itemStyle: { color: '#2c3e50' },
    }],
  }

  return (
    <Card title="📊 检测历史与分析" extra={
      <DatePicker.RangePicker
        value={range} onChange={(v) => setRange(v as Range)} allowEmpty={[true, true]} />
    }>
      <Space direction="vertical" style={{ width: '100%' }} size={16}>
        <EChart option={trendOption} height={300} />
        <EChart option={sevOption} height={260} />
        <Table size="small" rowKey={(r) => String(r['task_id'])} dataSource={risks}
          pagination={{ pageSize: 8 }}
          columns={[
            { title: '任务', dataIndex: 'task_id', width: 160 },
            { title: '隐患描述', dataIndex: 'hazard_desc', ellipsis: true },
            { title: '自动等级', dataIndex: 'auto_level', width: 100 },
            { title: '改判', dataIndex: 'override_level', width: 100,
              render: (v: string) => v || '—' },
            { title: '改判原因', dataIndex: 'override_reason', ellipsis: true },
            { title: '创建', dataIndex: 'created_at', width: 170,
              render: (d: string) => d && dayjs(d).format('MM-DD HH:mm') },
          ]} />
      </Space>
    </Card>
  )
}
