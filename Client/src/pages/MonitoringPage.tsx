import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, Info } from 'lucide-react'
import { request } from '../api'

import type { Analysis, MonitoringDashboardData } from '../types/workflow'
import type { AnomalyTimeBucket, GroupedAnomalyDevice, Page } from '../types'

import { PageTitle } from '../components/PageTitle'
import { SourceTypeBadge } from '../components/SourceTypeControls'
import { startVisiblePolling } from '../polling'
import { formatBucketRange } from './anomalyGrouping'
import { MonitoringPageState } from './MonitoringPageState'
import {
  buildRecentAnomalyGroups,
  getDataDevices,
  getDeviceCounts,
  getDeviceStatus,
  getDevicesNeedingAttention,
  getDevicesWithoutData,
  formatMonitoringRequestError,
  getLastHourAnalysisSummary,
  getMonitoringViewState,
  getOverdueMeasurementDetails,
  getRelevantAnalysisRuns,
  getUniqueMeasurementWindowCount,
  makeOperationalAnomalyParams,
} from './monitoringView'

const attentionRowClass = 'grid min-w-0 grid-cols-1 items-center gap-2 py-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(8rem,auto)_auto]'
const recentAnomalyRowClass = 'grid min-w-0 grid-cols-1 items-start gap-3 py-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,2.2fr)_minmax(0,1.2fr)_minmax(0,1.4fr)_auto]'

type AnalysisHistoryResponse = {
  items: Analysis[]
  total: number
  limit: number
  offset: number
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return 'Unavailable'
  return seconds < 60 ? `${seconds.toFixed(1)} s` : `${(seconds / 60).toFixed(1)} min`
}

export function MonitoringPage({
  onNavigate,
}: {
  onNavigate: (target: Page, query?: string, deviceId?: number) => void
}) {
  const [dashboard, setDashboard] = useState<MonitoringDashboardData | null>(null)
  const [openMeasurementWindows, setOpenMeasurementWindows] = useState<number | null>(null)
  const [recentAnomalyGroups, setRecentAnomalyGroups] = useState<ReturnType<typeof buildRecentAnomalyGroups>>([])
  const [lastHourAnalyses, setLastHourAnalyses] = useState<Analysis[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      const now = new Date()
      const dayAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000)
      const hourAgo = new Date(now.getTime() - 60 * 60 * 1000)
      const operationalParams = makeOperationalAnomalyParams()
      const recentParams = makeOperationalAnomalyParams(dayAgo)
      const historyParams = new URLSearchParams({
        start_date: hourAgo.toISOString(),
        end_date: now.toISOString(),
        sort_by: 'created_at',
        sort_direction: 'desc',
        limit: '500',
        offset: '0',
      })
      const [dashboardData, anomalySummary, recentDeviceSummary, historyPage] = await Promise.all([
        request<MonitoringDashboardData>('/monitoring/dashboard', { signal }),
        request<{ measurement_total: number }>(`/anomalies/devices/grouped?${operationalParams}`, { signal }),
        request<{ devices: GroupedAnomalyDevice[] }>(`/anomalies/devices/grouped?${recentParams}`, { signal }),
        request<AnalysisHistoryResponse>(`/analyses/history?${historyParams}`, { signal }),
      ])

      const analyses = [...historyPage.items]
      while (analyses.length < historyPage.total) {
        historyParams.set('offset', String(analyses.length))
        const nextPage = await request<AnalysisHistoryResponse>(`/analyses/history?${historyParams}`, { signal })
        if (!nextPage.items.length) throw new Error('Analysis history changed while loading; retry to refresh it')
        analyses.push(...nextPage.items)
      }

      const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
      const bucketResults: { deviceId: number; items: AnomalyTimeBucket[] }[] = []
      for (let index = 0; index < recentDeviceSummary.devices.length; index += 10) {
        const deviceBatch = recentDeviceSummary.devices.slice(index, index + 10)
        const batchResults = await Promise.all(deviceBatch.map(async (device) => {
          const params = makeOperationalAnomalyParams(dayAgo)
          params.set('bucket_minutes', '10')
          params.set('timezone', timeZone)
          params.set('limit', '50')
          const response = await request<{ items: AnomalyTimeBucket[] }>(
            `/anomalies/devices/${device.device_id}/buckets?${params}`,
            { signal },
          )
          return { deviceId: device.device_id, items: response.items }
        }))
        bucketResults.push(...batchResults)
      }
      const bucketsByDevice = Object.fromEntries(bucketResults.map((item) => [item.deviceId, item.items]))

      setDashboard(dashboardData)
      setOpenMeasurementWindows(getUniqueMeasurementWindowCount(anomalySummary))
      setRecentAnomalyGroups(buildRecentAnomalyGroups(recentDeviceSummary.devices, bucketsByDevice))
      setLastHourAnalyses(analyses)
      setError('')
    } catch (caught) {
      if (!signal?.aborted) setError(formatMonitoringRequestError(caught))
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => startVisiblePolling(refresh, 60000), [refresh])

  const activeDevices = dashboard?.devices.filter((device) => device.status !== 'archived') ?? []
  const dataDevices = getDataDevices(activeDevices)
  const devicesWithoutData = getDevicesWithoutData(activeDevices)
  const attentionDevices = getDevicesNeedingAttention(activeDevices)
  const deviceCounts = getDeviceCounts(activeDevices)
  const overdueDetails = getOverdueMeasurementDetails(activeDevices)
  const overdueTooltip = overdueDetails.length
    ? overdueDetails.map((item) => `${item.deviceName} · ${item.parameterName} · ${item.sourceType === 'manual' ? 'manual-entry reminder' : 'stale data; expected reading not received'}`).join('\n')
    : 'No overdue measurements for active devices with data.'
  const analysisSummary = getLastHourAnalysisSummary(lastHourAnalyses)
  const relevantRuns = getRelevantAnalysisRuns(lastHourAnalyses)
  const latestAnalysisAt = dashboard?.analysis_history[0]?.created_at
  const viewState = getMonitoringViewState(loading, error, dashboard)
  const panel = 'rounded-lg border border-slate-200 bg-white p-4 shadow-sm'
  const statusLabel = (device: MonitoringDashboardData['devices'][number]) => {
    const status = getDeviceStatus(device)
    return status === 'not_reporting' ? 'Not reporting' : status === 'overdue' ? 'Overdue' : status === 'no_data' ? 'No data' : 'OK'
  }
  const statusTone = (device: MonitoringDashboardData['devices'][number]) => {
    const status = getDeviceStatus(device)
    return status === 'not_reporting' || status === 'overdue'
      ? 'bg-rose-100 text-rose-800'
      : status === 'no_data'
        ? 'bg-slate-100 text-slate-600'
        : 'bg-emerald-100 text-emerald-800'
  }

  return <>
    <PageTitle
      title="Automated monitoring"
      description="Monitor device health and review results from all configured detectors across registered equipment and NASA IMS datasets."
    />

    <MonitoringPageState state={viewState} error={error} onRetry={() => void refresh()}>
    {dashboard && <>
      <section className={`${panel} grid gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7`} aria-label="Monitoring status">
        <div><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-500">Devices OK</span><strong className="mt-1 block text-xl text-slate-900">{deviceCounts.ok}</strong></div>
        <div><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-500">Needing attention</span><strong className="mt-1 block text-xl text-slate-900">{deviceCounts.attention}</strong></div>
        <div><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-500">Not reporting</span><strong className="mt-1 block text-xl text-slate-900">{deviceCounts.notReporting}</strong></div>
        <div><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-500">No data</span><strong className="mt-1 block text-xl text-slate-900">{deviceCounts.noData}</strong></div>
        <div><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-500">Open anomalies (unique measurements)</span><button className="mt-1 block text-left text-xl font-bold text-forest-700 hover:underline" type="button" onClick={() => onNavigate('Anomalies')}>{openMeasurementWindows?.toLocaleString() ?? '—'}</button></div>
        <div><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-500">Unread notifications</span><strong className="mt-1 block text-xl text-slate-900">{dashboard.notification_status.unread.toLocaleString()}</strong></div>
        <div><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-500">Last analysis</span><strong className="mt-1 block text-xs text-slate-800">{latestAnalysisAt ? new Date(latestAnalysisAt).toLocaleString() : 'No analysis yet'}</strong></div>
        <div className="sm:col-span-3 lg:col-span-4 xl:col-span-7">
          <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide text-slate-500" title={overdueTooltip}>Overdue measurements · {overdueDetails.length}<Info size={12} aria-hidden="true" /></span>
        </div>
      </section>

      <section className={`${panel} space-y-3`}>
        <div className="flex items-baseline justify-between gap-3"><h2 className="text-sm font-semibold text-slate-900">Needs attention</h2><span className="text-[10px] text-slate-500">{attentionDevices.length} devices</span></div>
        {attentionDevices.length ? <ul className="divide-y divide-slate-100">
          {attentionDevices.slice(0, 5).map((device) => <li className={attentionRowClass} key={device.id}>
            <div className="min-w-0"><strong className="block truncate text-xs text-slate-800">{device.name}</strong><span className="block truncate text-[10px] text-slate-500">{device.device_code}</span><div className="mt-1"><SourceTypeBadge sourceType={device.source_type} /></div></div>
            <span className="min-w-0 text-left text-[10px] tabular-nums text-slate-600">{device.measurement_schedule.some((item) => item.stale_data_status === 'stopped_reporting') ? 'Stopped reporting' : device.overdue_measurements ? `${device.overdue_measurements} overdue` : `${device.open_anomalies} open alerts`}</span>
            <button className="justify-self-start whitespace-nowrap text-left text-xs font-semibold text-forest-700 hover:underline sm:justify-self-end sm:text-right" type="button" onClick={() => onNavigate('Devices', undefined, device.id)}>Open device</button>
          </li>)}
          {attentionDevices.length > 5 && <li className="pt-2 text-[10px] text-slate-500">Showing 5 of {attentionDevices.length} devices needing attention.</li>}
        </ul> : devicesWithoutData.length
          ? <p className="text-xs text-slate-500">No devices are overdue, stopped reporting, or have open alerts.</p>
          : <p className="text-xs text-slate-500">All devices are reporting normally.</p>}
      </section>

      <section className={`${panel} space-y-3`}>
        <div className="flex items-baseline justify-between gap-3"><h2 className="text-sm font-semibold text-slate-900">Device health</h2><span className="text-[10px] text-slate-500">{dataDevices.length} devices with readings</span></div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[780px] border-collapse text-left text-xs">
            <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wide text-slate-500"><tr><th className="px-3 py-2">Device</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Last reading</th><th className="px-3 py-2">Next expected</th><th className="px-3 py-2">Open alerts</th><th className="px-3 py-2">Source</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {dataDevices.map((device) => {
                const latest = device.latest_measurements[0]
                const nextDue = device.measurement_schedule
                  .filter((item) => item.enabled && item.next_due_at)
                  .sort((left, right) => Date.parse(left.next_due_at ?? '') - Date.parse(right.next_due_at ?? ''))[0]?.next_due_at
                return <tr key={device.id}>
                  <td className="px-3 py-3"><button className="text-left font-semibold text-slate-800 hover:underline" type="button" onClick={() => onNavigate('Devices', undefined, device.id)}>{device.name}</button><span className="mt-0.5 block text-[10px] text-slate-500">{device.device_code}</span></td>
                  <td className="px-3 py-3"><span className={`rounded-full px-2 py-1 text-[10px] font-bold ${statusTone(device)}`}>{statusLabel(device)}</span></td>
                  <td className="px-3 py-3">{latest ? <><time>{new Date(latest.measured_at).toLocaleString()}</time><span className="ml-2 text-slate-500">{latest.parameter_name}: {latest.value} {latest.unit}</span></> : 'No recorded values'}</td>
                  <td className="px-3 py-3">{nextDue ? new Date(nextDue).toLocaleString() : 'Not scheduled'}</td>
                  <td className="px-3 py-3"><button className="font-semibold text-forest-700 hover:underline" type="button" onClick={() => onNavigate('Anomalies', undefined, device.id)}>{device.open_anomalies.toLocaleString()}</button></td>
                  <td className="px-3 py-3"><SourceTypeBadge sourceType={device.source_type} /></td>
                </tr>
              })}
              {devicesWithoutData.length > 0 && <tr><td className="px-3 py-3" colSpan={6}><details>
                <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-semibold text-slate-600"><ChevronDown size={14} aria-hidden="true" />{devicesWithoutData.length} devices without data (show)</summary>
                <ul className="mt-3 divide-y divide-slate-100">{devicesWithoutData.map((device) => <li className="flex items-center justify-between gap-3 py-2" key={device.id}><span><strong className="text-xs text-slate-700">{device.name}</strong><small className="ml-2 text-[10px] text-slate-500">{device.device_code}</small></span><SourceTypeBadge sourceType={device.source_type} /></li>)}</ul>
              </details></td></tr>}
              {activeDevices.length === 0 && <tr><td className="px-3 py-4 text-center text-xs text-slate-500" colSpan={6}>No registered devices.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className={`${panel} space-y-3`}>
        <div className="flex items-baseline justify-between gap-3"><h2 className="text-sm font-semibold text-slate-900">Recent anomalies</h2><span className="text-[10px] text-slate-500">Last 24 hours · 10-minute buckets</span></div>
        {recentAnomalyGroups.length ? <div className="divide-y divide-slate-100">
          {recentAnomalyGroups.map((group) => <article className={recentAnomalyRowClass} key={`${group.deviceId}-${group.bucket.parameter_id}-${group.bucket.bucket_start}`}>
            <div className="min-w-0">
              <strong className="block truncate text-xs text-slate-800">{group.deviceName} · {group.bucket.parameter_name}</strong><span className="block truncate text-[10px] text-slate-500">{group.deviceCode}</span>
              <div className="mt-1 flex flex-wrap items-center gap-1">{group.sourceTypes.map((source) => <SourceTypeBadge sourceType={source} key={source} />)}</div>
              <time className="mt-1 block text-[10px] text-slate-500">{formatBucketRange(group.bucket.bucket_start, 10, Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')}</time>
            </div>
            <div className="min-w-0 text-left text-xs tabular-nums text-slate-700"><button className="font-semibold text-forest-700 hover:underline" type="button" onClick={() => onNavigate('Anomalies', undefined, group.deviceId)}>{group.bucket.anomaly_count.toLocaleString()} detections</button><span className="mt-1 block text-[10px]">{group.bucket.minimum_value ?? '—'}–{group.bucket.maximum_value ?? '—'} {group.bucket.unit ?? ''}</span></div>
            <div className="min-w-0 text-left text-xs text-slate-700"><span className="block text-[9px] font-bold uppercase text-slate-500">Highest severity</span><strong className="capitalize">{group.bucket.highest_severity}</strong><span className="mt-1 block break-words text-[10px] text-slate-500">{group.bucket.detectors.join(', ') || 'No detector detail'}</span></div>
            <button className="justify-self-start whitespace-nowrap text-left text-xs font-semibold text-forest-700 hover:underline sm:justify-self-end sm:text-right" type="button" onClick={() => onNavigate('Anomalies', undefined, group.deviceId)}>View all in Anomalies</button>
          </article>)}
        </div> : <p className="text-xs text-slate-500">No operational anomalies were detected in the last 24 hours.</p>}
      </section>

      <section className={`${panel} space-y-3`}>
        <div className="flex flex-wrap items-baseline justify-between gap-3"><h2 className="text-sm font-semibold text-slate-900">Analysis history</h2><button className="text-xs font-semibold text-forest-700 hover:underline" type="button" onClick={() => onNavigate('Analysis')}>Open Analysis</button></div>
        <p className="text-xs text-slate-600">Last hour: {analysisSummary.total} analyses · {analysisSummary.failed} failed · average recorded duration {formatDuration(analysisSummary.averageDurationSeconds)} across {analysisSummary.durationCount} timed runs.</p>
        {relevantRuns.length ? <div className="divide-y divide-slate-100">
          {relevantRuns.map((run) => <article className="flex flex-wrap items-start justify-between gap-3 py-2.5" key={run.id}>
            <div><strong className="text-xs text-slate-800">{run.dataset_name ?? run.device_name ?? 'Analysis run'} · {(run.algorithm ?? run.model_name ?? 'detector').replaceAll('_', ' ')}</strong><span className="ml-2 rounded-full bg-slate-100 px-2 py-1 text-[10px] font-semibold capitalize text-slate-700">{run.status}</span><p className="mt-1 text-[10px] text-slate-500">Source: {run.source_type.replaceAll('_', ' ')} · {run.execution_type === 'scheduled' ? 'Scheduled cycle' : 'Failed analysis'} · {new Date(run.created_at).toLocaleString()} · {formatDuration(run.duration_seconds ?? null)}</p>{run.error_message && <p className="mt-1 text-[10px] text-rose-700">{run.error_message}</p>}</div>
          </article>)}
        </div> : <p className="text-xs text-slate-500">No failed analyses or scheduled cycles in the last hour.</p>}
      </section>

      <p className="monitoring-notice flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-900"><Info className="mt-0.5 shrink-0" size={14} aria-hidden="true" />NASA IMS data are historical experiments, not live equipment readings. Simulated readings are synthetic test data, not physical telemetry.</p>
    </>}
    </MonitoringPageState>
  </>
}