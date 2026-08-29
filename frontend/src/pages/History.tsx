import { useCallback, useEffect, useState } from 'react'
import { DatePicker, Table } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import * as ep from '../api/endpoints'
import EChart from '../components/EChart'
import PageHeader from '../components/PageHeader'

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
    legend: { data: ['合规率%', '不合规帧', '警告帧', '合规帧'], textStyle: { color: '#9ca3af' } },
    grid: { left: 48, right: 24, top: 40, bottom: 32 },
    xAxis: {
      type: 'category' as const, data: stats.map((s) => s.day),
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
      axisLabel: { color: '#6b7280' },
    },
    yAxis: [
      {
        type: 'value' as const, name: '帧数',
        nameTextStyle: { color: '#6b7280' },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
        axisLabel: { color: '#6b7280' },
      },
      {
        type: 'value' as const, name: '合规率%', max: 100,
        nameTextStyle: { color: '#6b7280' },
        axisLabel: { formatter: '{value}%', color: '#6b7280' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '合规率%', type: 'line' as const, yAxisIndex: 1, smooth: true,
        data: stats.map((s) => {
          const total = (s.compliant || 0) + (s.warning || 0) + (s.non_compliant || 0)
          return total ? Math.round((s.compliant / total) * 1000) / 10 : 0
        }),
        lineStyle: { color: '#00d4aa', width: 2 },
        itemStyle: { color: '#00d4aa' },
        areaStyle: { color: 'rgba(0,212,170,0.08)' },
      },
      { name: '不合规帧', type: 'bar' as const, stack: 'f',
        data: stats.map((s) => s.non_compliant), itemStyle: { color: '#c8102e' } },
      { name: '警告帧', type: 'bar' as const, stack: 'f',
        data: stats.map((s) => s.warning), itemStyle: { color: '#f59e0b' } },
      { name: '合规帧', type: 'bar' as const, stack: 'f',
        data: stats.map((s) => s.compliant), itemStyle: { color: '#00d4aa' } },
    ],
  }

  const sevOption = {
    tooltip: {},
    grid: { left: 48, right: 24, top: 24, bottom: 64 },
    xAxis: {
      type: 'category' as const,
      data: sev.map((s) => s.cls),
      axisLabel: { rotate: 30, color: '#6b7280' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
    },
    yAxis: {
      type: 'value' as const,
      nameTextStyle: { color: '#6b7280' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
      axisLabel: { color: '#6b7280' },
    },
    series: [{
      type: 'bar' as const, name: '命中次数',
      data: sev.map((s) => s.cnt),
      itemStyle: {
        color: 'rgba(200,16,46,0.6)',
        borderRadius: [4, 4, 0, 0],
      },
    }],
  }

  return (
    <>
      <PageHeader
        title="历史分析"
        subtitle="合规率趋势 · 隐患类别分布"
        action={
          <DatePicker.RangePicker
            value={range} onChange={(v) => setRange(v as Range)} allowEmpty={[true, true]} />
        }
      />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div style={{
          padding: 20, borderRadius: 16,
          background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
        }}>
          <div style={{
            fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.3)',
            textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16,
          }}>合规率趋势</div>
          <EChart option={trendOption} height={300} />
        </div>
        <div style={{
          padding: 20, borderRadius: 16,
          background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
        }}>
          <div style={{
            fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.3)',
            textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16,
          }}>隐患类别分布</div>
          <EChart option={sevOption} height={260} />
        </div>
        <div style={{
          padding: 20, borderRadius: 16,
          background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
        }}>
          <div style={{
            fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.3)',
            textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16,
          }}>任务风险记录</div>
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
                render: (d: string) => d && <span className="mono">{dayjs(d).format('MM-DD HH:mm')}</span> },
            ]} />
        </div>
      </div>
    </>
  )
}
