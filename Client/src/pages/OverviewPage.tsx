import { useMemo } from 'react'
import { Activity, AlertTriangle, BarChart3, Check, ClipboardList, Cpu, Database, Plus, Wrench } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { roleLabel } from '../constants'

import { EmptyState } from '../components/EmptyState'
import { PageHeading } from '../components/PageHeading'
import { SourceTypeBadge } from '../components/SourceTypeControls'

import type { User, DashboardData, Page } from '../types'

function sourceBreakdownNote(
  counts: Record<'manual' | 'sensor' | 'simulated' | 'dataset', number> | undefined,
) {
  const manual = counts?.manual ?? 0
  const sensor = counts?.sensor ?? 0
  const simulated = counts?.simulated ?? 0
  const dataset = counts?.dataset ?? 0
  return `All sources · Real ${manual + sensor} (manual ${manual}, sensor ${sensor}) · Simulated ${simulated} · Dataset ${dataset}`
}

export function Overview({ user, dashboard, onNavigate }: { user: User; dashboard: DashboardData | null; onNavigate: (page: Page) => void }) {
  const metrics = dashboard?.metrics ?? {}
  const anomalyChartData = useMemo(
    () => (dashboard?.anomalies_by_device ?? []).slice(0, 6),
    [dashboard?.anomalies_by_device],
  )
  const chartSourceTypes = [...new Set(anomalyChartData.map((item) => item.source_type))]
  const cards = user.role === 'maintenance_technician'
    ? [
        { label: 'Open work orders', value: metrics.open_orders ?? 0, icon: ClipboardList, tone: 'bg-forest-50 text-forest-600', note: 'Work still in progress' },
        { label: 'Cases to review', value: metrics.unreviewed_anomalies ?? 0, icon: AlertTriangle, tone: 'bg-amber-50 text-amber-600', note: sourceBreakdownNote(dashboard?.source_breakdown.cases_to_review) },
        { label: 'Active equipment', value: metrics.active_devices ?? 0, icon: Cpu, tone: 'bg-lime-50 text-lime-700', note: sourceBreakdownNote(dashboard?.source_breakdown.active_devices) },
        { label: 'Completed work orders', value: dashboard?.maintenance_orders_by_status.completed ?? 0, icon: Check, tone: 'bg-emerald-50 text-emerald-600', note: 'Marked complete' },
      ]
    : [
        { label: 'Active equipment', value: metrics.active_devices ?? 0, icon: Cpu, tone: 'bg-forest-50 text-forest-600', note: sourceBreakdownNote(dashboard?.source_breakdown.active_devices) },
        { label: 'Datasets imported', value: metrics.datasets ?? 0, icon: Database, tone: 'bg-lime-50 text-lime-700', note: `${metrics.overdue_measurements ?? 0} equipment measurements overdue` },
        { label: 'Analyses complete', value: metrics.completed_analyses ?? 0, icon: BarChart3, tone: 'bg-emerald-50 text-emerald-600', note: sourceBreakdownNote(dashboard?.source_breakdown.completed_analyses) },
        { label: 'Cases to review', value: metrics.unreviewed_anomalies ?? 0, icon: AlertTriangle, tone: 'bg-amber-50 text-amber-600', note: sourceBreakdownNote(dashboard?.source_breakdown.cases_to_review) },
      ]
  const orderChart = Object.entries(dashboard?.maintenance_orders_by_status ?? {}).map(([name, value]) => ({ name: name.replace('_', ' '), value }))
  const palette = ['#35563b', '#71824a', '#d39a45', '#c9795d', '#8ba68a']
  const dashboardCopy: Record<string, { description: string; action: string; page: Page }> = {
    administrator: { description: 'Monitor equipment health, user access, and system activity.', action: 'Manage users', page: 'Users' },
    data_analyst: { description: 'Explore sensor data and run anomaly analyses across your equipment.', action: 'New analysis', page: 'Analysis' },
    maintenance_technician: { description: 'Review flagged equipment and keep maintenance work moving.', action: 'View work orders', page: 'Maintenance' },
  }
  const copy = dashboardCopy[user.role] ?? dashboardCopy.data_analyst
  return <>
    <PageHeading eyebrow={`${roleLabel[user.role] ?? user.role} dashboard`} title={`Welcome, ${user.full_name.split(' ')[0]}.`} description={copy.description} action={<button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 bg-[#182334] text-white hover:bg-slate-700" onClick={() => onNavigate(copy.page)}>{user.role === 'data_analyst' && <Plus size={16} />}{copy.action}</button>} />
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">{cards.map(({ label, value, icon: Icon, tone, note }) => <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm" key={label}><div className={`mb-5 grid size-10 place-items-center rounded-xl ${tone}`}><Icon size={19} /></div><span className="block text-xs font-medium text-slate-500">{label}</span><strong className="mt-1 block text-3xl font-semibold tracking-tight text-slate-900">{value.toLocaleString()}</strong><span className="mt-2 block text-[11px] text-slate-400">{note}</span></article>)}</div>
    <article className="mt-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">Computed severity distribution</h2>
      <p className="mt-1 text-xs text-slate-500">Unique measurement severity: physical limits first; statistical evidence only, never Critical. Original severity is retained.</p>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Object.entries(dashboard?.computed_severity_distribution ?? {}).map(([severity, count]) => (
          <div className="rounded-lg bg-slate-50 px-3 py-3" key={severity}>
            <span className="block text-xs capitalize text-slate-500">{severity}</span>
            <strong className="mt-1 block text-xl text-slate-900">{count.toLocaleString()}</strong>
          </div>
        ))}
      </div>
    </article>
    <div className="grid gap-5 xl:grid-cols-2">
      <article className="rounded-xl border border-slate-200 bg-white shadow-sm min-h-[330px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-100 px-5 py-4 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:text-slate-900 [&_p]:mt-1 [&_p]:text-xs [&_p]:text-slate-500"><div><h2>Anomalies by registered device</h2><p>Unique flagged measurement windows linked to registered equipment; unlinked dataset results are excluded.</p></div><button className="shrink-0 text-xs font-semibold text-forest-700 hover:text-forest-900" onClick={() => onNavigate('Anomalies')}>View cases <span>{'->'}</span></button></div>
        {anomalyChartData.length ? <><div className="h-64 px-4 py-5"><ResponsiveContainer width="100%" height="100%"><BarChart data={anomalyChartData} margin={{ top: 10, right: 10, bottom: 0, left: -18 }}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e9edf3" /><XAxis dataKey="device_name" tickLine={false} axisLine={false} tick={{ fill: '#738094', fontSize: 11 }} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: '#738094', fontSize: 11 }} /><Tooltip cursor={{ fill: '#f2f5fa' }} /><Bar dataKey="count" name="Unique flagged measurements" fill="#466b4c" radius={[5, 5, 0, 0]} maxBarSize={38} /></BarChart></ResponsiveContainer></div><div className="flex flex-wrap gap-2 px-5 pb-4">{chartSourceTypes.map((sourceType) => <SourceTypeBadge key={sourceType} sourceType={sourceType} />)}</div></> : <EmptyState icon={<BarChart3 size={20} />} title="No registered-device anomaly results" body="Registered-device anomalies will appear here after analysis." />}
      </article>
      <article className="rounded-xl border border-slate-200 bg-white shadow-sm min-h-[330px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:text-slate-900 [&_p]:mt-1 [&_p]:text-xs [&_p]:text-slate-500"><div><h2>Maintenance orders</h2><p>Current workload by status</p></div><button className="shrink-0 text-xs font-semibold text-forest-700 hover:text-forest-900" onClick={() => onNavigate('Maintenance')}>View orders <span>{'->'}</span></button></div>
        {orderChart.some((item) => item.value > 0) ? <div className="grid min-h-[260px] grid-cols-[1fr_1fr] items-center gap-2 px-4"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={orderChart} dataKey="value" nameKey="name" innerRadius={58} outerRadius={82} paddingAngle={3} stroke="none">{orderChart.map((entry, index) => <Cell key={entry.name} fill={palette[index % palette.length]} />)}</Pie><Tooltip /></PieChart></ResponsiveContainer><div className="grid gap-3 text-xs capitalize text-slate-500 [&>div]:flex [&>div]:items-center [&>div]:gap-2 [&>div>span]:size-2 [&>div>span]:rounded-full [&>div>strong]:ml-auto [&>div>strong]:text-slate-800">{orderChart.map((entry, index) => <div key={entry.name}><span style={{ backgroundColor: palette[index % palette.length] }} />{entry.name}<strong>{entry.value}</strong></div>)}</div></div> : <EmptyState icon={<Wrench size={20} />} title="Nothing scheduled" body="Maintenance activity will appear once work orders are created." />}
      </article>
    </div>
    <article className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"><div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:text-slate-900 [&_p]:mt-1 [&_p]:text-xs [&_p]:text-slate-500"><div><h2>Recent activity</h2><p>Latest changes made in your workspace</p></div><Activity size={18} className="text-slate-400" /></div>{dashboard?.recent_activity.length ? <div className="divide-y divide-slate-100">{dashboard.recent_activity.map((event) => <div className="flex items-center gap-3 px-5 py-3 [&>div]:min-w-0 [&_strong]:text-xs [&_strong]:capitalize [&_strong>span]:font-normal [&_strong>span]:text-slate-500 [&_small]:mt-1 [&_small]:block [&_small]:text-[11px] [&_small]:text-slate-400" key={event.id}><span className="size-2 shrink-0 rounded-full bg-forest-500" /><div><strong>{event.action.replaceAll('_', ' ')} <span>{event.entity_type}</span></strong><small>{event.entity_id ? `Record #${event.entity_id}  -  ` : ''}{new Date(event.created_at).toLocaleString()}</small></div><span className="ml-auto text-[11px] text-slate-400">User {event.user_id ?? 'system'}</span></div>)}</div> : <EmptyState icon={<ClipboardList size={20} />} title="No activity recorded" body="Actions such as imports and reviews will show up here." />}</article>
  </>
}
