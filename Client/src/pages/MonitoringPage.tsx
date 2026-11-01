import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { request } from '../api'

import type { Action, MonitoringScheduleData, MonitoringDashboardData } from '../types/workflow'

import { buttonClass, EmptyPanel, ErrorMessage } from '../components/ui'

import { PageTitle } from '../components/PageTitle'
import { AlertEvaluation } from '../components/AlertEvaluation'
import { SourceTypeBadge } from '../components/SourceTypeControls'

export function MonitoringPage({
  userRole,
  onAction,
}: {
  userRole: string
  onAction: Action
}) {
  const [schedule, setSchedule] = useState<MonitoringScheduleData | null>(null)
  const [dashboard, setDashboard] = useState<MonitoringDashboardData | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const canRun = userRole === 'administrator' || userRole === 'data_analyst'
  const isAdmin = userRole === 'administrator'

  const refresh = useCallback(async () => {
    try {
      const [scheduleData, dashboardData] = await Promise.all([
        request<MonitoringScheduleData>('/monitoring'),
        request<MonitoringDashboardData>('/monitoring/dashboard'),
      ])
      setSchedule(scheduleData)
      setDashboard(dashboardData)
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load monitoring data')
    }
  }, [])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refresh())
    const timer = window.setInterval(() => void refresh(), 15000)
    return () => {
      window.cancelAnimationFrame(frame)
      window.clearInterval(timer)
    }
  }, [refresh])

  async function saveSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!schedule) return
    setSaving(true)
    try {
      const updated = await request<MonitoringScheduleData>('/monitoring', {
        method: 'PATCH',
        body: JSON.stringify({
          enabled: schedule.enabled,
          interval_minutes: schedule.interval_minutes,
          simulation_enabled: schedule.simulation_enabled,
          simulation_batch_size: schedule.simulation_batch_size,
        }),
      })
      setSchedule(updated)
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to save monitoring settings')
    } finally {
      setSaving(false)
    }
  }

  async function runNow() {
    await onAction(
      () => request('/monitoring/run', { method: 'POST' }),
      'Automatic analysis queued',
    )
    await refresh()
  }

  const panel = 'rounded-xl border border-slate-200 bg-white p-5 shadow-sm'
  const visibleDevices = dashboard?.devices ?? []
  const visibleAnomalies = dashboard?.recent_anomalies ?? []
  const visibleRuns = dashboard?.analysis_history ?? []
  const healthColor = (health: string) => {
    if (health === 'healthy') return 'bg-emerald-100 text-emerald-800'
    if (health === 'attention' || health === 'analysis_failed' || health === 'stopped_reporting') return 'bg-rose-100 text-rose-800'
    if (health === 'analyzing') return 'bg-amber-100 text-amber-800'
    return 'bg-slate-100 text-slate-600'
  }

  return (
    <>
      <PageTitle
        title="Automated monitoring"
        description="Scheduled Isolation Forest and Z-score analysis, device health, anomaly history, and notification delivery."
        action={canRun ? <button className={buttonClass} type="button" onClick={() => void runNow()}>Run analysis now</button> : undefined}
      />
      <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-900">
        NASA IMS plots describe historical experiments. Reporting status for registered equipment uses server receipt times; simulated devices are synthetic test data, not physical telemetry.
      </section>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      {schedule && <section className={panel}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">Scheduled analysis</h2>
            <p className="mt-1 text-xs text-slate-500">New NASA IMS datasets are analyzed automatically with both supported algorithms.</p>
          </div>
          <span className={`rounded-full px-3 py-1 text-[10px] font-bold uppercase ${schedule.enabled ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>
            {schedule.enabled ? 'Enabled' : 'Paused'}
          </span>
        </div>
        {isAdmin && <form className="mt-4 grid gap-4 border-t border-slate-100 pt-4 sm:grid-cols-2 xl:grid-cols-4" onSubmit={(event) => void saveSchedule(event)}>
          <label className="flex items-center gap-2 self-end text-xs font-medium text-slate-700">
            <input type="checkbox" checked={schedule.enabled} onChange={(event) => setSchedule({ ...schedule, enabled: event.target.checked })} />
            Enable scheduled analysis
          </label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Run interval (minutes; e.g. 60, 360, 1440)
            <input type="number" min={1} max={1440} value={schedule.interval_minutes} onChange={(event) => setSchedule({ ...schedule, interval_minutes: Number(event.target.value) })} required />
          </label>
          <label className="flex items-center gap-2 self-end text-xs font-medium text-slate-700">
            <input type="checkbox" checked={schedule.simulation_enabled} onChange={(event) => setSchedule({ ...schedule, simulation_enabled: event.target.checked })} />
            Simulate historical readings over time
          </label>
          <label>Simulation batch size (rows)
            <input type="number" min={2} max={10000} value={schedule.simulation_batch_size} disabled={!schedule.simulation_enabled} onChange={(event) => setSchedule({ ...schedule, simulation_batch_size: Number(event.target.value) })} required />
          </label>
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2 xl:col-span-4">
            <button className={buttonClass} type="submit" disabled={saving}>{saving ? 'Saving...' : 'Save schedule'}</button>
            <span className="text-[11px] text-slate-500">
              {schedule.simulation_enabled
                ? `Simulation replays ${schedule.simulation_batch_size} stored rows per interval; it is not a live sensor connection.`
                : 'Historical data is analyzed automatically. Later cycles process changed dataset snapshots or retry failures; unchanged data is skipped.'}
            </span>
          </div>
        </form>}
        {!isAdmin && <p className="mt-4 text-xs text-slate-500">
          Runs every {schedule.interval_minutes} minutes. Simulation is {schedule.simulation_enabled ? 'enabled for historical replay' : 'disabled'}.
        </p>}
        <div className="mt-4 grid gap-3 border-t border-slate-100 pt-4 text-xs sm:grid-cols-2 xl:grid-cols-4">
          <div><span className="block text-[10px] uppercase text-slate-400">Execution status</span><strong className={`mt-1 inline-block rounded-full px-2 py-1 capitalize ${schedule.execution_status === 'failed' ? 'bg-rose-100 text-rose-800' : schedule.execution_status === 'running' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'}`}>{schedule.execution_status}</strong></div>
          <div><span className="block text-[10px] uppercase text-slate-400">Next run</span><strong className="mt-1 block text-slate-700">{schedule.enabled && schedule.next_run_at ? new Date(schedule.next_run_at).toLocaleString() : 'Not scheduled'}</strong></div>
          <div><span className="block text-[10px] uppercase text-slate-400">Last started</span><strong className="mt-1 block text-slate-700">{schedule.last_started_at ? new Date(schedule.last_started_at).toLocaleString() : 'No runs yet'}</strong></div>
          <div><span className="block text-[10px] uppercase text-slate-400">Last completed</span><strong className="mt-1 block text-slate-700">{schedule.last_completed_at ? new Date(schedule.last_completed_at).toLocaleString() : 'No runs yet'}</strong></div>
        </div>
        {schedule.last_error && <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800" role="alert">Last execution issue: {schedule.last_error}</p>}
      </section>}
      {dashboard && <>
        <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <div className={panel}><span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Devices monitored</span><strong className="mt-2 block text-2xl text-slate-900">{visibleDevices.length}</strong></div>
          <div className={panel}><span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Open anomalies</span><strong className="mt-2 block text-2xl text-slate-900">{visibleDevices.reduce((total, device) => total + device.open_anomalies, 0) + visibleAnomalies.filter((anomaly) => anomaly.source_type === 'dataset').length}</strong></div>
          <div className={panel}><span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Overdue measurements</span><strong className="mt-2 block text-2xl text-slate-900">{visibleDevices.reduce((total, device) => total + device.overdue_measurements, 0)}</strong></div>
          <div className={panel}><span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Your anomaly alerts</span><strong className="mt-2 block text-2xl text-slate-900">{dashboard.notification_status.anomaly_notifications}</strong><span className="mt-1 block text-[10px] text-slate-500">{dashboard.notification_status.unread} unread in-app notifications</span></div>
          <div className={panel}><span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Failed analysis runs</span><strong className="mt-2 block text-2xl text-slate-900">{visibleRuns.filter((run) => run.status === 'failed').length}</strong></div>
          <div className={panel}><span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Pending maintenance tasks</span><strong className="mt-2 block text-2xl text-slate-900">{dashboard.pending_maintenance_tasks}</strong></div>
        </section>
        <section className={`${panel} grid gap-4 sm:grid-cols-2`}>
          {([
            ['Registered equipment', dashboard.severity_distribution.equipment],
            ['NASA dataset experiments', dashboard.severity_distribution.dataset_experiments],
          ] as const).map(([label, counts]) => <div key={label}>
            <h2 className="text-xs font-semibold text-slate-800">{label} · detections by severity</h2>
            <div className="mt-3 grid grid-cols-4 gap-2">{['critical', 'high', 'medium', 'low'].map((severity) => <div className="rounded-lg bg-slate-50 p-2" key={severity}><span className="block text-[9px] uppercase text-slate-400">{severity}</span><strong className="mt-1 block text-sm text-slate-800">{counts[severity] ?? 0}</strong></div>)}</div>
          </div>)}
        </section>
        <section className={panel}>
          <h2 className="mb-4 text-sm font-semibold text-slate-900">Device health</h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] border-collapse text-left text-xs">
              <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wide text-slate-400"><tr><th className="px-3 py-2">Device</th><th className="px-3 py-2">Health</th><th className="px-3 py-2">Latest measurement</th><th className="px-3 py-2">Next measurement due</th><th className="px-3 py-2">Overdue</th><th className="px-3 py-2">Open anomalies</th><th className="px-3 py-2">Latest analysis</th><th className="px-3 py-2">Data source</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {visibleDevices.map((device) => <tr key={device.id}>
                  <td className="px-3 py-3"><strong className="block text-slate-800">{device.name}</strong><span className="text-[10px] text-slate-400">{device.device_code}</span><div className="mt-1"><SourceTypeBadge sourceType={device.source_type} /></div></td>
                  <td className="px-3 py-3"><span className={`rounded-full px-2 py-1 text-[10px] font-bold capitalize ${healthColor(device.health)}`}>{device.health.replaceAll('_', ' ')}</span></td>
                  <td className="px-3 py-3">{device.latest_measurements[0] ? <>{device.latest_measurements[0].parameter_name}: {device.latest_measurements[0].value} {device.latest_measurements[0].unit} · {new Date(device.latest_measurements[0].measured_at).toLocaleString()} <SourceTypeBadge sourceType={device.latest_measurements[0].source_type} /></> : 'No recorded values'}</td>
                  <td className="px-3 py-3">{device.measurement_schedule.length ? device.measurement_schedule.map((schedule) => <div className="mb-2" key={schedule.parameter_id}><span className="font-medium">{schedule.parameter_name}</span> · last received {schedule.last_reading_received_at ? new Date(schedule.last_reading_received_at).toLocaleString() : 'never'} · due {schedule.enabled && schedule.next_due_at ? new Date(schedule.next_due_at).toLocaleString() : 'disabled'} · {schedule.stale_data_status === 'stopped_reporting' ? <strong className="text-rose-700">stopped reporting</strong> : schedule.stale_data_status === 'reporting_resumed' ? 'reporting resumed' : schedule.status.replaceAll('_', ' ')}<AlertEvaluation alert_rules={schedule.stale_data_episode?.alert_rules ?? schedule.alert_rules} alert_priority={schedule.stale_data_episode?.priority ?? schedule.alert_priority} history_state={schedule.history_state} history_size={schedule.history_size} history_required={schedule.history_required} className="mt-1" /></div>) : 'No parameters configured'}</td>
                  <td className="px-3 py-3">{device.overdue_measurements}</td>
                  <td className="px-3 py-3">{device.open_anomalies}</td>
                  <td className="px-3 py-3">{device.last_analysis ? `${device.last_analysis.status} · ${new Date(device.last_analysis.created_at).toLocaleString()}` : 'Not analyzed'}</td>
                  <td className="px-3 py-3 text-slate-500">{device.data_source}</td>
                </tr>)}
              </tbody>
            </table>
          </div>
          {visibleDevices.length === 0 && <EmptyPanel title="No matching equipment" body="No registered equipment matches this source selection. Historical dataset analysis remains separate." />}
        </section>
        {visibleDevices.some((device) => (device.measurement_trend ?? device.vibration_trend)?.points.length) && <section className={panel}>
          <h2 className="text-sm font-semibold text-slate-900">Measurement trends</h2>
          <p className="mt-1 text-[10px] text-slate-500">Registered equipment uses manually recorded values; NASA IMS charts show historical experiment observations. Neither is a live sensor feed.</p>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {visibleDevices.filter((device) => (device.measurement_trend ?? device.vibration_trend)?.points.length).map((device) => {
              const trend = device.measurement_trend ?? device.vibration_trend
              return <article className="rounded-lg border border-slate-100 p-3" key={device.id}>
              <h3 className="text-xs font-semibold text-slate-800">{device.name} · {trend?.feature}{device.measurement_trend?.unit ? ` (${device.measurement_trend.unit})` : ''}</h3>
              <p className="text-[10px] text-slate-500">{trend?.data_source}</p>
              <div className="mt-3 h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trend?.points}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="label" tick={{ fontSize: 8 }} minTickGap={24} />
                    <YAxis tick={{ fontSize: 9 }} />
                    <Tooltip labelFormatter={(label) => `Measurement: ${label}`} />
                    <Line dataKey="value" name={trend?.feature} dot={false} stroke="#466b4c" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </article>})}
          </div>
        </section>}
        <section className={`${panel} grid gap-6 xl:grid-cols-2`}>
          <div>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Recent anomalies</h2>
            <div className="divide-y divide-slate-100">
              {visibleAnomalies.map((anomaly) => <article className="py-3" key={anomaly.id}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <strong className="text-xs text-slate-800">{anomaly.device_name ?? anomaly.dataset_name ?? 'Detection'} · {anomaly.parameter_name ?? (anomaly.source_type === 'dataset' ? 'Historical dataset experiment' : 'Equipment anomaly')}</strong>
                  <span className={`rounded-full px-2 py-1 text-[10px] font-bold capitalize ${anomaly.severity === 'high' || anomaly.severity === 'critical' ? 'bg-rose-100 text-rose-800' : 'bg-amber-100 text-amber-800'}`}>{anomaly.severity} severity</span>
                </div>
                <p className="mt-1 text-[10px] text-slate-500">{anomaly.source_type === 'dataset' ? 'Historical dataset experiment' : 'Registered equipment'} | {anomaly.anomaly_type.replaceAll('_', ' ')} · {anomaly.status.replaceAll('_', ' ')} · detected {new Date(anomaly.detected_at).toLocaleString()}</p>
                <SourceTypeBadge sourceType={anomaly.measurement_source_type ?? (anomaly.source_type === 'dataset' ? 'dataset' : 'manual')} />
                <AlertEvaluation alert_rules={anomaly.alert_rules} alert_priority={anomaly.alert_priority} history_state={anomaly.history_state} history_size={anomaly.history_size} history_required={anomaly.history_required} />
                {anomaly.measurement_time && <p className="mt-1 text-[10px] text-slate-500">Source measurement: {new Date(anomaly.measurement_time).toLocaleString()}</p>}
                <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(anomaly.feature_values).slice(0, 5).map(([feature, value]) => <span className="rounded bg-slate-50 px-2 py-1 font-mono text-[9px] text-slate-600" key={feature}>{feature}: {typeof value === 'number' ? value.toPrecision(4) : String(value ?? 'n/a')}</span>)}</div>
              </article>)}
              {visibleAnomalies.length === 0 && <p className="py-4 text-xs text-slate-500">No measurements match this source filter in the recent analysis window.</p>}
            </div>
          </div>
          <div>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Analysis history</h2>
            <div className="divide-y divide-slate-100">
              {visibleRuns.map((run) => <article className="py-3" key={run.id}>
                <div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-xs text-slate-800">#{run.id} · {run.device_name ?? 'Device'} · {run.model_name ?? 'Model'}</strong><span className={`rounded-full px-2 py-1 text-[10px] font-bold capitalize ${run.status === 'completed' ? 'bg-emerald-100 text-emerald-800' : run.status === 'failed' ? 'bg-rose-100 text-rose-800' : 'bg-amber-100 text-amber-800'}`}>{run.status}</span></div>
                <p className="mt-1 text-[10px] text-slate-500">{run.execution_type === 'simulated' ? 'Simulated historical replay' : run.execution_type === 'scheduled' ? 'Automatic historical analysis' : run.execution_type === 'measurement' ? 'Equipment measurement analysis' : 'User-requested analysis'} · {run.dataset_name ?? (run.source_type === 'equipment' ? 'Registered equipment' : 'Dataset')} · {new Date(run.created_at).toLocaleString()} <SourceTypeBadge sourceType={run.measurement_source_type ?? (run.source_type === 'dataset' ? 'dataset' : null)} /></p>
                {run.source_start_row != null && <p className="mt-1 text-[10px] text-slate-500">Replay rows {run.source_start_row.toLocaleString()}–{run.source_end_row?.toLocaleString()}</p>}
                <p className="mt-1 text-[10px] text-slate-600">{run.total_records.toLocaleString()} measurements · {run.anomalies_found.toLocaleString()} anomalies</p>
                {run.error_message && <p className="mt-1 text-[10px] text-rose-700">{run.error_message}</p>}
              </article>)}
              {dashboard.analysis_history.length === 0 && <p className="py-4 text-xs text-slate-500">Automatic analysis will appear here after dataset import.</p>}
            </div>
          </div>
        </section>
      </>}
    </>
  )
}
