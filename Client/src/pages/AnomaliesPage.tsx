import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { AlertTriangle, Check, FileChartColumn, Search, Wrench } from 'lucide-react'
import { CartesianGrid, ResponsiveContainer, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { download, request } from '../api'

import { PageHeading } from '../components/PageHeading'
import { EmptyState } from '../components/EmptyState'
import { StatusBadge } from '../components/StatusBadge'
import { AlertEvaluation } from '../components/AlertEvaluation'
import { SourceTypeBadge, SourceTypeSelect } from '../components/SourceTypeControls'
import type { SourceTypeFilter } from '../sourceType'

import type { Device, Anomaly, AnomalySummary, Maintenance, Page, User } from '../types'

type JsonRecord = Record<string, unknown>

function asRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : null
}

export function AnomaliesPage({
  devices,
  userRole,
  onAction,
  onNavigate,
}: {
  devices: Device[]
  userRole: string
  onAction: (action: () => Promise<unknown>, success: string) => Promise<void>
  onNavigate: (target: Page, query?: string, deviceId?: number) => void
}) {
  const [summary, setSummary] = useState<AnomalySummary | null>(null)
  const [items, setItems] = useState<Anomaly[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [sourceFilter, setSourceFilter] = useState<SourceTypeFilter>('all')
  const [statusFilter, setStatusFilter] = useState('')
  const [severityFilter, setSeverityFilter] = useState('')
  const [algorithmFilter, setAlgorithmFilter] = useState('')
  const [deviceFilter, setDeviceFilter] = useState('')
  const [parameterFilter, setParameterFilter] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [selected, setSelected] = useState<Anomaly | null>(null)
  const [reviewStatus, setReviewStatus] = useState('')
  const [reviewNotes, setReviewNotes] = useState('')
  const [maintenanceTitle, setMaintenanceTitle] = useState('')
  const [maintenancePriority, setMaintenancePriority] = useState('medium')
  const [maintenanceDueDate, setMaintenanceDueDate] = useState('')
  const [maintenanceAssignee, setMaintenanceAssignee] = useState('')
  const [technicians, setTechnicians] = useState<User[]>([])
  const [feedbackLabel, setFeedbackLabel] = useState('')
  const [feedbackComment, setFeedbackComment] = useState('')
  const [feedbackOrderId, setFeedbackOrderId] = useState('')
  const [feedbackStats, setFeedbackStats] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const canReview = userRole === 'administrator' || userRole === 'maintenance_technician'
  const canCreateOrder = canReview || userRole === 'data_analyst'
  const pageSize = 50
  const ensembleDetails = selected?.algorithm === 'ensemble'
    ? asRecord(selected.expected_range.detector_details)
    : null
  const ensembleMembers = Array.isArray(ensembleDetails?.detectors)
    ? ensembleDetails.detectors.map(asRecord).filter((member): member is JsonRecord => member !== null)
    : []

  useEffect(() => {
    request<Record<string, unknown>>('/anomalies/feedback/stats')
      .then(setFeedbackStats)
      .catch(() => setFeedbackStats(null))
  }, [])

  useEffect(() => {
    if (userRole !== 'administrator') return
    void request<User[]>('/maintenance/technicians')
      .then(setTechnicians)
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : 'Unable to load inspection assignees')
      })
  }, [userRole])

  async function exportFiltered() {
    const params = new URLSearchParams({
      source_type: sourceFilter === 'all' || sourceFilter === 'dataset' ? sourceFilter : 'equipment',
      include_test_data: 'true',
      include_dataset: String(sourceFilter === 'dataset'),
    })
    if (sourceFilter !== 'all') params.set('measurement_source_type', sourceFilter)
    if (debouncedQuery.trim()) params.set('search', debouncedQuery.trim())
    if (statusFilter) params.set('status_filter', statusFilter)
    if (severityFilter) params.set('severity', severityFilter)
    if (algorithmFilter) params.set('algorithm', algorithmFilter)
    if (deviceFilter) params.set('device_id', deviceFilter)
    if (parameterFilter.trim()) params.set('parameter', parameterFilter.trim())
    if (startDate) params.set('start_date', new Date(`${startDate}T00:00:00`).toISOString())
    if (endDate) params.set('end_date', new Date(`${endDate}T23:59:59.999`).toISOString())
    await download(`/anomalies/export.csv?${params}`, 'anomalies.csv')
  }

  const refreshSummary = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        source_type: sourceFilter === 'all' || sourceFilter === 'dataset' ? sourceFilter : 'equipment',
        include_test_data: 'true',
        include_dataset: String(sourceFilter === 'dataset'),
      })
      if (sourceFilter !== 'all') params.set('measurement_source_type', sourceFilter)
      setSummary(await request<AnomalySummary>(`/anomalies/summary?${params}`))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load anomaly summary')
    }
  }, [sourceFilter])

  const refreshList = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String(offset),
        source_type: sourceFilter === 'all' || sourceFilter === 'dataset' ? sourceFilter : 'equipment',
        include_test_data: 'true',
        include_dataset: String(sourceFilter === 'dataset'),
      })
      if (sourceFilter !== 'all') params.set('measurement_source_type', sourceFilter)
      if (debouncedQuery.trim()) params.set('search', debouncedQuery.trim())
      if (statusFilter) params.set('status_filter', statusFilter)
      if (severityFilter) params.set('severity', severityFilter)
      if (algorithmFilter) params.set('algorithm', algorithmFilter)
      if (deviceFilter) params.set('device_id', deviceFilter)
      if (parameterFilter.trim()) params.set('parameter', parameterFilter.trim())
      if (startDate) params.set('start_date', new Date(`${startDate}T00:00:00`).toISOString())
      if (endDate) params.set('end_date', new Date(`${endDate}T23:59:59.999`).toISOString())
      const response = await request<{ total: number; items: Anomaly[] }>(`/anomalies/page?${params}`)
      setItems(response.items)
      setTotal(response.total)
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load anomalies')
    } finally {
      setLoading(false)
    }
  }, [algorithmFilter, debouncedQuery, deviceFilter, endDate, offset, parameterFilter, severityFilter, sourceFilter, startDate, statusFilter])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setOffset(0)
      setDebouncedQuery(query)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refreshList())
    return () => window.cancelAnimationFrame(frame)
  }, [refreshList])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refreshSummary())
    return () => window.cancelAnimationFrame(frame)
  }, [refreshSummary])

  async function openAnomaly(anomaly: Anomaly) {
    try {
      const detail = await request<Anomaly>(`/anomalies/${anomaly.id}`)
      setSelected(detail)
      setFeedbackLabel(detail.feedback?.label ?? '')
      setFeedbackComment(detail.feedback?.comment ?? '')
      setFeedbackOrderId(detail.feedback?.maintenance_order_id ? String(detail.feedback.maintenance_order_id) : '')
      setReviewStatus(detail.status)
      setReviewNotes('')
      setMaintenanceTitle(`Inspect ${detail.device_name ?? detail.dataset_name ?? 'detection'} anomaly #${detail.id}`)
      setMaintenancePriority(detail.severity === 'critical' ? 'critical' : detail.severity)
      setMaintenanceAssignee('')
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load anomaly details')
    }
  }

  async function saveReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected) return
    await onAction(async () => {
      const updated = await request<Anomaly>(`/anomalies/${selected.id}/review`, {
        method: 'POST',
        body: JSON.stringify({ status: reviewStatus, comments: reviewNotes || null }),
      })
      setSelected(updated)
      await Promise.all([refreshList(), refreshSummary()])
    }, 'Review decision and notes recorded')
  }

  async function saveFeedback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected || !feedbackLabel) return
    await onAction(async () => {
      const feedback = await request<NonNullable<Anomaly['feedback']>>(`/anomalies/${selected.id}/feedback`, {
        method: 'POST',
        body: JSON.stringify({
          label: feedbackLabel,
          comment: feedbackComment || null,
          maintenance_order_id: feedbackOrderId ? Number(feedbackOrderId) : null,
        }),
      })
      setSelected({ ...selected, feedback })
      setFeedbackStats(await request<Record<string, unknown>>('/anomalies/feedback/stats'))
    }, 'Human feedback recorded without changing the model result')
  }

  async function createMaintenanceOrder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected) return
    await onAction(async () => {
      const order = await request<Maintenance>(`/anomalies/${selected.id}/maintenance-order`, {
        method: 'POST',
        body: JSON.stringify({
          title: maintenanceTitle,
          priority: maintenancePriority,
          description: selected.description,
          assigned_to: maintenanceAssignee ? Number(maintenanceAssignee) : null,
          due_date: maintenanceDueDate ? new Date(maintenanceDueDate).toISOString() : null,
        }),
      })
      const updated = await request<Anomaly>(`/anomalies/${selected.id}`)
      setSelected(updated)
      setMaintenanceTitle(`Inspect ${updated.device_name ?? updated.dataset_name ?? 'detection'} anomaly #${updated.id}`)
      await refreshList()
      return order
    }, 'Maintenance order created')
  }

  function clearFilters() {
    setQuery('')
    setDebouncedQuery('')
    setStatusFilter('')
    setSeverityFilter('')
    setAlgorithmFilter('')
    setDeviceFilter('')
    setParameterFilter('')
    setStartDate('')
    setEndDate('')
    setOffset(0)
  }

  const cardClass = 'rounded-xl border border-slate-200 bg-white p-4 shadow-sm'
  const reviewableStatuses = ['new', 'under_review', 'confirmed', 'rejected', 'reviewed', 'closed']

  return <>
    <PageHeading
      eyebrow="CASE REVIEW"
      title="Equipment anomaly review"
      description="Review detections from actual measurements recorded for registered equipment. NASA IMS experiment detections remain in dataset analysis."
      action={<button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-forest-300 hover:text-forest-700" onClick={() => void exportFiltered().catch((caught: unknown) => onAction(() => Promise.reject(caught), ''))}><FileChartColumn size={16} /> Export filtered CSV</button>}
    />
    <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-900">
      <AlertTriangle size={17} className="mt-0.5 shrink-0" />
      <span><strong>Detected does not mean confirmed.</strong> These are statistical flags from historical or imported measurements, not an automatic diagnosis of equipment failure. Only an authorized human review marks a case confirmed or rejected.</span>
    </div>
    {error && <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">{error}</p>}

    {summary && <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
      {[
        { title: 'High severity', count: summary.severity.high, tone: 'text-rose-700' },
        { title: 'Critical severity', count: summary.severity.critical, tone: 'text-rose-800' },
        { title: 'Medium severity', count: summary.severity.medium, tone: 'text-amber-700' },
        { title: 'Low severity', count: summary.severity.low, tone: 'text-forest-700' },
        { title: 'Pending reviews', count: summary.pending_reviews, tone: 'text-sky-700' },
        { title: 'Confirmed', count: summary.confirmed, tone: 'text-emerald-700' },
        { title: 'Rejected false positives', count: summary.rejected_false_positives, tone: 'text-slate-700' },
        { title: 'Unique affected measurement windows', count: summary.unique_affected_measurements, tone: 'text-indigo-700' },
      ].map((card) => <article className={cardClass} key={card.title}>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{card.title}</span>
        <strong className={`mt-2 block text-2xl ${card.tone}`}>{card.count.toLocaleString()}</strong>
      </article>)}
    </section>}

    {feedbackStats && <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Human feedback statistics</h3>
          <p className="mt-1 text-xs text-slate-500">{String(feedbackStats.reviewed_anomalies ?? 0).toLocaleString()} reviewed anomalies · NASA IMS dataset anomalies excluded</p>
        </div>
        <button className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold" type="button" onClick={() => download('/anomalies/feedback/export.csv', 'anomaly-feedback.csv')}>Export labelled CSV</button>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        {Object.entries(asRecord(feedbackStats.counts_by_label) ?? {}).map(([label, count]) => <article className={cardClass} key={label}><span className="text-[10px] uppercase text-slate-500">{label.replaceAll('_', ' ')}</span><strong className="mt-1 block text-xl">{String(count)}</strong></article>)}
        <article className={cardClass}><span className="text-[10px] uppercase text-slate-500">False alarms among reviewed</span><strong className="mt-1 block text-xl">{feedbackStats.sufficient_cases ? `${String(feedbackStats.false_alarm_percentage ?? 0)}%` : 'not enough reviewed cases'} </strong><span className="text-[10px] text-slate-500">{String(feedbackStats.reviewed_anomalies ?? 0)} reviewed anomalies</span></article>
      </div>
      <p className="mt-3 text-xs text-slate-600">Registered equipment only; simulator ground truth is separate from human feedback. {String(feedbackStats.supervised_model_note ?? '')}</p>
    </section>}

    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <label className="sm:col-span-2 xl:col-span-2">
          Search detections
          <span className="relative block">
            <Search size={15} className="pointer-events-none absolute left-3 top-3 text-slate-400" />
            <input className="w-full pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Equipment, parameter, algorithm, explanation…" />
          </span>
        </label>
        <label>Device
          <select value={deviceFilter} onChange={(event) => { setDeviceFilter(event.target.value); setOffset(0) }}>
            <option value="">All devices</option>
            {devices.map((device) => <option key={device.id} value={device.id}>{device.name} · {device.device_code}</option>)}
          </select>
        </label>
        <label>Parameter
          <input value={parameterFilter} onChange={(event) => { setParameterFilter(event.target.value); setOffset(0) }} placeholder="e.g. vibration RMS" />
        </label>
        <label>Severity
          <select value={severityFilter} onChange={(event) => { setSeverityFilter(event.target.value); setOffset(0) }}>
            <option value="">All severities</option><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
          </select>
        </label>
        <label>Algorithm
          <select value={algorithmFilter} onChange={(event) => { setAlgorithmFilter(event.target.value); setOffset(0) }}>
            <option value="">All algorithms</option><option value="isolation_forest">Isolation Forest</option><option value="z_score">Z-score</option><option value="threshold">Configured limits</option>
          </select>
        </label>
        <label>Review status
          <select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setOffset(0) }}>
            <option value="">All statuses</option><option value="new">Pending</option><option value="under_review">Under review</option><option value="confirmed">Confirmed</option><option value="rejected">Rejected false positive</option><option value="reviewed">Reviewed</option><option value="closed">Closed</option>
          </select>
        </label>
        <label>Detected from<input type="date" value={startDate} onChange={(event) => { setStartDate(event.target.value); setOffset(0) }} /></label>
        <label>Detected through<input type="date" value={endDate} onChange={(event) => { setEndDate(event.target.value); setOffset(0) }} /></label>
        <button className="self-end text-left text-xs font-semibold text-forest-700 hover:text-forest-900" type="button" onClick={clearFilters}>Clear filters</button>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
        <SourceTypeSelect value={sourceFilter} onChange={(value) => { setSourceFilter(value); setOffset(0) }} />
        <span className="text-[10px] text-slate-500">Simulated readings are included in source filtering and are labeled as synthetic test data.</span>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3 text-xs text-slate-500">
        <span>{loading ? 'Loading detections…' : `${total.toLocaleString()} unique measurements`}</span>
        <span>{summary ? `${summary.algorithm_specific_detections.toLocaleString()} detections · ${summary.unique_affected_measurements.toLocaleString()} unique measurement windows` : ''}</span>
      </div>
      <div className="rounded-lg border border-sky-100 bg-sky-50 px-4 py-3 text-xs text-sky-900">
        Equipment results are shown once per measurement. Physical limits take priority. Statistical High requires at least 3 eligible detectors with the persistence rule; Medium requires exactly 2; Low (watch) requires 1. Statistical results are never Critical. The 5-reading control-chart lag follows the displayed 5-reading ground-truth tolerance.
      </div>
      <div className="overflow-x-auto rounded-lg border border-slate-100">
        {(sourceFilter === 'all' || sourceFilter === 'simulated') && <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">
          Simulated readings are included. They are labeled TEST DATA and must not be interpreted as physical readings or confirmed equipment faults.
        </div>}
        <table className="w-full min-w-[1350px] border-collapse text-left text-xs">
          <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wide text-slate-500">
            <tr>{['Registered equipment', 'Parameter · analysis', 'Measurement timestamp', 'Algorithm / model', 'Recorded values', 'Detection reason', 'Severity', 'Alert priority', 'Review status', 'Inspection assigned to', ''].map((column) => <th className="whitespace-nowrap px-3 py-3" key={column}>{column}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((anomaly) => <tr className="hover:bg-slate-50/70" key={anomaly.id}>
              <td className="px-3 py-3"><strong className="block text-slate-800">{anomaly.device_name ?? 'No registered device'}</strong><span className="text-[10px] text-slate-500">{anomaly.device_code ?? '—'}</span><div className="mt-1"><SourceTypeBadge sourceType={anomaly.measurement_source_type ?? (anomaly.source_type === 'dataset' ? 'dataset' : null)} /></div>{anomaly.is_test_data && <span className="mt-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 text-[9px] font-bold tracking-wide text-amber-900">TEST DATA · SYNTHETIC</span>}</td>
              <td className="px-3 py-3"><span className="block max-w-48 truncate text-slate-700">{anomaly.parameter_name ?? 'Equipment parameter'}</span><span className="text-[10px] text-slate-500">run #{anomaly.analysis_run_id}</span></td>
              <td className="whitespace-nowrap px-3 py-3">{anomaly.measurement_time ? new Date(anomaly.measurement_time).toLocaleString() : 'Time unavailable'}<span className="mt-1 block text-[10px] text-slate-400">Detected {new Date(anomaly.detected_at).toLocaleString()}</span></td>
              <td className="px-3 py-3 font-medium capitalize text-slate-700">{anomaly.model_name ?? anomaly.algorithm.replaceAll('_', ' ')}<span className="mt-1 block text-[10px] font-normal text-slate-500">{anomaly.algorithm === 'z_score' ? 'Z-score' : anomaly.algorithm.replaceAll('_', ' ')}</span></td>
              <td className="max-w-64 px-3 py-3"><div className="flex flex-wrap gap-1">{Object.entries(anomaly.measurement_values).slice(0, 3).map(([name, value]) => <code className="rounded bg-slate-100 px-1.5 py-1 text-[9px] text-slate-600" key={name}>{name}: {typeof value === 'number' ? value.toPrecision(4) : String(value ?? '—')}</code>)}</div></td>
              <td className="max-w-72 px-3 py-3 text-[10px] leading-4 text-slate-600"><span className="line-clamp-3">{anomaly.description ?? 'No detection explanation was stored.'}</span></td>
              <td className="px-3 py-3"><span className={`rounded-full px-2 py-1 text-[10px] font-semibold capitalize ${anomaly.measurement_severity === 'critical' || anomaly.measurement_severity === 'high' ? 'bg-rose-100 text-rose-800' : anomaly.measurement_severity === 'medium' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'}`}>{anomaly.measurement_severity ?? anomaly.computed_severity ?? anomaly.severity}</span><span className="mt-1 block text-[10px] text-slate-500">Original: {anomaly.severity}</span><span className="mt-1 block text-[10px] text-slate-500">{anomaly.measurement_detectors?.join(', ') || 'Detector evidence'} · {anomaly.measurement_agreeing_detectors ?? 0} agree</span>{anomaly.measurement_analysis_run_ids?.length > 0 && <span className="mt-1 block text-[10px] text-slate-400">Runs: {anomaly.measurement_analysis_run_ids.join(', ')}</span>}</td>
              <td className="px-3 py-3">{anomaly.alert_priority ? <span className="rounded-full bg-indigo-50 px-2 py-1 text-[10px] font-semibold capitalize text-indigo-800">{anomaly.alert_priority}</span> : 'Not set'}</td>
              <td className="px-3 py-3"><StatusBadge value={anomaly.status} /></td>
              <td className="px-3 py-3 text-[10px] text-slate-600">{anomaly.maintenance_orders?.length
                ? anomaly.maintenance_orders.map((order) => order.assigned_name ?? 'Unassigned').join(', ')
                : 'Not assigned'}</td>
              <td className="px-3 py-3"><button className="whitespace-nowrap font-semibold text-forest-700 hover:text-forest-900" type="button" onClick={() => void openAnomaly(anomaly)}>Open details</button></td>
            </tr>)}
          </tbody>
        </table>
        {!loading && items.length === 0 && <EmptyState icon={<Check size={20} />} title={summary?.total === 0 ? 'No equipment anomalies detected' : 'No matching anomalies'} body={summary?.total === 0 ? 'Anomalies will appear here when analysis flags an actual measurement recorded for registered equipment. NASA IMS experiment detections are shown separately in dataset analysis.' : 'Try adjusting the search or filters.'} />}
      </div>
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>{total ? `Showing ${offset + 1}–${Math.min(total, offset + items.length)} of ${total.toLocaleString()}` : 'No results'}</span>
        <div className="flex gap-2">
          <button className="rounded-lg border border-slate-200 px-3 py-2 font-semibold disabled:opacity-40" type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Previous</button>
          <button className="rounded-lg border border-slate-200 px-3 py-2 font-semibold disabled:opacity-40" type="button" disabled={offset + items.length >= total || loading} onClick={() => setOffset(offset + pageSize)}>Next</button>
        </div>
      </div>
    </section>

    {selected && <div className="fixed inset-0 z-40 overflow-y-auto bg-slate-950/40 p-3 sm:p-6" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null) }}>
      <section className="mx-auto my-4 max-w-5xl space-y-5 rounded-2xl bg-white p-5 shadow-2xl sm:p-7" role="dialog" aria-modal="true" aria-labelledby="anomaly-detail-title">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><span className="text-[10px] font-bold uppercase tracking-widest text-forest-700">Detection #{selected.id} · {selected.source_type === 'equipment' ? 'registered equipment' : 'historical dataset experiment'}{selected.is_test_data ? ' · TEST DATA' : ''}</span><h2 id="anomaly-detail-title" className="mt-1 text-xl font-semibold text-slate-900">{selected.device_name ?? selected.dataset_name ?? 'Detection'} · {selected.computed_severity ?? selected.severity} severity</h2><p className="mt-1 text-xs text-slate-500">Original severity: {selected.severity} · {selected.severity_reason ?? 'Statistical severity evidence'}</p><p className="mt-1 text-xs text-slate-500">{selected.parameter_name ? `${selected.parameter_name} · ` : ''}{selected.dataset_name ?? 'Equipment measurement'} · {selected.measurement_source_type?.replaceAll('_', ' ') ?? selected.source_type} · {selected.algorithm === 'z_score' ? 'Z-score' : selected.algorithm.replaceAll('_', ' ')} · {selected.measurement_time ? new Date(selected.measurement_time).toLocaleString() : 'Measurement time unavailable'}{selected.measurement_unit ? ` · ${selected.measurement_unit}` : ''}</p>{selected.is_test_data && <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">Synthetic test fixture only. This is not a physical measurement or a confirmed equipment fault.</p>}</div>
          <div className="flex items-center gap-2"><StatusBadge value={selected.status} /><button className="rounded-lg border border-slate-200 px-3 py-2 text-xs" type="button" onClick={() => setSelected(null)}>Close</button></div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <article className={cardClass}><span className="text-[10px] uppercase text-slate-500">Algorithm score</span><strong className="mt-1 block text-lg text-slate-900">{selected.anomaly_score?.toFixed(5) ?? 'Not recorded'}</strong></article>
          <article className={cardClass}><span className="text-[10px] uppercase text-slate-500">Threshold</span><strong className="mt-1 block text-lg text-slate-900">{selected.score_threshold !== null && selected.score_threshold !== undefined ? selected.score_threshold : 'Not exposed'}</strong><span className="text-[10px] text-slate-500">{selected.algorithm === 'isolation_forest' ? `Contamination: ${String(selected.algorithm_parameters.contamination ?? 'automatic')}` : 'Stored algorithm threshold'}</span></article>
          <article className={cardClass}><span className="text-[10px] uppercase text-slate-500">Source analysis</span><strong className="mt-1 block text-lg text-slate-900">#{selected.analysis_run_id}</strong><span className="text-[10px] text-slate-500">{selected.analysis_execution_type ?? 'analysis'} · {selected.analysis_status ?? 'status unavailable'}</span></article>
          <article className={cardClass}><span className="text-[10px] uppercase text-slate-500">Source row</span><strong className="mt-1 block text-lg text-slate-900">{selected.source_row_number ?? 'Unavailable'}</strong><span className="text-[10px] text-slate-500">Detected {new Date(selected.detected_at).toLocaleString()}</span></article>
        </div>
        <section className="rounded-xl border border-slate-200 p-4">
          <h3 className="text-sm font-semibold text-slate-900">Why it was flagged</h3>
          <p className="mt-2 text-xs leading-5 text-slate-600">{selected.description ?? 'No explanation was stored for this detection.'}</p>
          <div className="mt-3 flex flex-wrap gap-2">{Object.entries(selected.expected_range).map(([key, value]) => <code className="rounded bg-slate-100 px-2 py-1 text-[10px] text-slate-600" key={key}>{key.replaceAll('_', ' ')}: {typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—')}</code>)}</div>
          <AlertEvaluation alert_rules={selected.alert_rules} alert_priority={selected.alert_priority} history_state={selected.history_state} history_size={selected.history_size} history_required={selected.history_required} />
        </section>
        {selected.explanation && <section className="rounded-xl border border-sky-200 bg-sky-50/30 p-4">
          <h3 className="text-sm font-semibold text-slate-900">Stored-data explanation · {selected.explanation.evidence_note}</h3>
          <p className="mt-2 text-xs text-slate-700">Rules fired: {selected.explanation.rules_fired.join(', ') || 'model'} · alert priority: {selected.explanation.alert_priority ?? 'not assigned'} · episode: {selected.explanation.episode_id ?? 'none'}</p>
          <div className="mt-3 overflow-x-auto rounded-lg border border-sky-100 bg-white">
            <table className="w-full text-left text-xs">
              <thead className="bg-sky-50 text-[10px] uppercase text-slate-500"><tr><th className="px-3 py-2">Parameter / feature</th><th className="px-3 py-2">Value</th><th className="px-3 py-2">Baseline mean</th><th className="px-3 py-2">Baseline standard deviations</th><th className="px-3 py-2">History</th></tr></thead>
              <tbody>{selected.explanation.contributing_parameters.map((item, index) => <tr className="border-t border-slate-100" key={`${item.feature}-${index}`}><td className="px-3 py-2 font-medium">{item.feature}</td><td className="px-3 py-2">{item.value ?? '—'}</td><td className="px-3 py-2">{item.baseline_mean ?? '—'}</td><td className="px-3 py-2">{item.baseline_standard_deviations?.toFixed(3) ?? 'not stored'}</td><td className="px-3 py-2">{item.history_size ?? '—'}</td></tr>)}</tbody>
            </table>
          </div>
          <div className="mt-3 grid gap-2 text-xs text-slate-700 sm:grid-cols-2">
            <p><strong>Model configuration:</strong> {selected.explanation.model.configuration_name ?? selected.explanation.model.name ?? 'not recorded'} · version {String(selected.explanation.model.version ?? 'not recorded')} · {selected.explanation.model.configuration_type}</p>
            <p><strong>History:</strong> {selected.explanation.history.state ?? 'unknown'} · {selected.explanation.history.available_readings ?? 'unknown'} available / {selected.explanation.history.required_readings ?? 'unknown'} required</p>
            <p><strong>Source:</strong> {selected.explanation.source_type ?? 'not recorded'}</p>
          </div>
          {selected.explanation.ensemble.members.length > 0 && <div className="mt-3 rounded-lg bg-white p-3 text-xs">
            <strong>Ensemble</strong> · confidence {selected.explanation.ensemble.confidence ?? 'not recorded'} · voting rule {selected.explanation.ensemble.voting_rule ?? 'not recorded'}
            <ul className="mt-2 list-disc pl-5">{selected.explanation.ensemble.members.map((member, index) => <li key={index}>{String(member.algorithm ?? member.name ?? 'detector')}: vote {String(member.vote ?? 'none')}, score {String(member.score ?? 'not recorded')}, history {String(member.history_state ?? 'not recorded')}, execution {String(member.execution_mode ?? 'not recorded')}</li>)}</ul>
          </div>}
        </section>}
        {(userRole === 'administrator' || userRole === 'data_analyst' || userRole === 'maintenance_technician') && <section className="rounded-xl border border-slate-200 p-4">
          <h3 className="text-sm font-semibold text-slate-900">Human feedback</h3>
          <form className="mt-3 grid gap-3 sm:grid-cols-2" onSubmit={saveFeedback}>
            <label>Review label<select value={feedbackLabel} onChange={(event) => setFeedbackLabel(event.target.value)}><option value="">Select label</option><option value="confirmed_fault">Confirmed fault</option><option value="false_alarm">False alarm</option><option value="needs_more_data">Needs more data</option></select></label>
            <label>Maintenance order<select value={feedbackOrderId} onChange={(event) => setFeedbackOrderId(event.target.value)}><option value="">None</option>{selected.maintenance_orders.map((order) => <option key={order.id} value={order.id}>#{order.id} · {order.title}</option>)}</select></label>
            <label className="sm:col-span-2">Comment<textarea maxLength={5000} value={feedbackComment} onChange={(event) => setFeedbackComment(event.target.value)} /></label>
            <div className="sm:col-span-2"><button className="rounded-lg bg-forest-700 px-4 py-2 text-xs font-semibold text-white" type="submit" disabled={!feedbackLabel}>Save feedback</button></div>
          </form>
          <p className="mt-2 text-[10px] text-slate-500">The original detector result is never changed. Simulator ground truth remains separate from this human feedback.</p>
        </section>}
        {ensembleDetails && <section className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">Ensemble evidence · statistical evidence for human review</h3>
              <p className="mt-1 text-xs text-slate-600">
                {typeof ensembleDetails.voted_count === 'number' ? ensembleDetails.voted_count : 0} voted of {typeof ensembleDetails.eligible_count === 'number' ? ensembleDetails.eligible_count : 0} history-eligible detectors
                {typeof ensembleDetails.configured_count === 'number' ? ` · ${ensembleDetails.configured_count} configured` : ''}
              </p>
            </div>
            <strong className="rounded-lg bg-white px-3 py-2 text-sm text-indigo-900">
              Confidence {typeof ensembleDetails.confidence === 'number' ? ensembleDetails.confidence.toFixed(3) : 'unavailable'}
            </strong>
          </div>
          <p className="mt-2 text-xs text-slate-700">
            History state: {String(ensembleDetails.ensemble_state ?? 'unavailable')} · Rule fired: {String(ensembleDetails.fired_rule ?? 'unavailable')}
          </p>
          <div className="mt-3 overflow-x-auto rounded-lg border border-indigo-100 bg-white">
            <table className="w-full min-w-[700px] text-left text-xs">
              <thead className="bg-indigo-50 text-[10px] uppercase tracking-wide text-slate-500">
                <tr>{['Detector', 'Vote', 'Score', 'History state', 'Available / required', 'Execution'].map((column) => <th className="px-3 py-2" key={column}>{column}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {ensembleMembers.map((member, index) => <tr key={`${String(member.algorithm ?? 'member')}-${index}`}>
                  <td className="px-3 py-2 font-medium text-slate-800">{String(member.algorithm ?? 'Unknown detector').replaceAll('_', ' ')}</td>
                  <td className="px-3 py-2">{member.vote === true ? 'Anomaly' : member.vote === false ? 'Normal' : 'No vote'}</td>
                  <td className="px-3 py-2 font-mono">{typeof member.score === 'number' ? member.score.toFixed(5) : '—'}</td>
                  <td className="px-3 py-2">{String(member.history_state ?? 'unavailable')}</td>
                  <td className="px-3 py-2">{typeof member.history_size === 'number' ? member.history_size : '—'} / {typeof member.history_required === 'number' ? member.history_required : '—'}</td>
                  <td className="px-3 py-2">{String(member.execution_mode ?? 'unavailable')}</td>
                </tr>)}
              </tbody>
            </table>
          </div>
        </section>}
        <section className="rounded-xl border border-slate-200 p-4">
          <h3 className="text-sm font-semibold text-slate-900">Measurements for this detection</h3>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(selected.measurement_values).map(([name, value]) => <div className="flex justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs" key={name}><span className="text-slate-600">{name.replaceAll('_', ' ')}</span><strong className="font-mono text-slate-900">{typeof value === 'number' ? value.toPrecision(6) : String(value ?? '—')}</strong></div>)}</div>
        </section>
        {selected.vibration_trend && selected.vibration_trend.length > 0 && <section className="rounded-xl border border-slate-200 p-4">
          <h3 className="text-sm font-semibold text-slate-900">Nearby measurement trend · {selected.trend_feature}</h3>
          <p className="mt-1 text-[10px] text-slate-500">{selected.source_type === 'equipment' ? 'Nearby manual or sensor-recorded equipment values; not necessarily a live feed.' : `Stored historical measurements surrounding source row ${selected.source_row_number ?? 'unavailable'}.`}</p>
          <div className="mt-3 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={selected.vibration_trend}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="chart_label" tick={{ fontSize: 9 }} tickFormatter={(value: string) => Number.isNaN(Date.parse(value)) ? value : new Date(value).toLocaleDateString()} />
                <YAxis tick={{ fontSize: 9 }} />
                <Tooltip labelFormatter={(label) => `Measurement: ${String(label)}`} />
                <Line dataKey="value" name={selected.trend_feature ?? 'Measurement'} dot={(props) => <circle cx={props.cx} cy={props.cy} r={props.payload.is_selected_measurement ? 6 : 2} fill={props.payload.is_selected_measurement ? '#e11d48' : '#466b4c'} />} stroke="#466b4c" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>}
        <section className="rounded-xl border border-slate-200 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-slate-900">Related records</h3><span className="text-[10px] text-slate-500">{selected.maintenance_orders.length} linked maintenance order(s)</span></div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-forest-700" type="button" onClick={() => onNavigate('Analysis', selected.dataset_name ?? '')}>Analysis #{selected.analysis_run_id}</button>
            {selected.dataset_id !== null && <button className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-forest-700" type="button" onClick={() => onNavigate('Datasets', selected.dataset_name ?? '')}>Dataset #{selected.dataset_id}</button>}
            {selected.device_id !== null && selected.device_id !== undefined && <button className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-forest-700" type="button" onClick={() => { const deviceId = selected.device_id; if (deviceId !== null && deviceId !== undefined) onNavigate('Devices', selected.device_name ?? '', deviceId) }}>Device history · {selected.device_name}</button>}
          </div>
          {selected.maintenance_orders.length > 0 && <div className="mt-3 space-y-2">{selected.maintenance_orders.map((order) => <article className="rounded-lg bg-slate-50 p-3 text-xs" key={order.id}><strong>Order #{order.id} · {order.title}</strong><p className="mt-1 text-slate-600">Inspector: {order.assigned_name ?? 'Not assigned'} · {order.priority} priority · {order.status}{order.due_date ? ` · due ${new Date(order.due_date).toLocaleString()}` : ''}</p>{order.resolution_notes && <p className="mt-1 text-slate-500">{order.resolution_notes}</p>}</article>)}</div>}
        </section>
        {selected.reviews.length > 0 && <section className="rounded-xl border border-slate-200 p-4">
          <h3 className="text-sm font-semibold text-slate-900">Review history</h3>
          <ol className="mt-3 space-y-3">{selected.reviews.map((review) => <li className="border-l-2 border-forest-200 pl-3 text-xs" key={review.id}><div className="flex flex-wrap justify-between gap-2"><StatusBadge value={review.decision} /><time className="text-[10px] text-slate-500">{new Date(review.reviewed_at).toLocaleString()}</time></div><p className="mt-1 text-slate-500">Reviewer #{review.reviewed_by ?? 'unavailable'}</p>{review.comments && <p className="mt-1 whitespace-pre-wrap leading-5 text-slate-700">{review.comments}</p>}</li>)}</ol>
        </section>}
        {canReview && <form className="space-y-3 rounded-xl border border-forest-100 bg-forest-50/40 p-4" onSubmit={saveReview}>
          <h3 className="text-sm font-semibold text-slate-900">Record a review</h3>
          <div className="grid gap-3 sm:grid-cols-[1fr_2fr]">
            <label>Decision
              <select value={reviewStatus} onChange={(event) => setReviewStatus(event.target.value)}>{reviewableStatuses.map((status) => <option key={status} value={status}>{status === 'rejected' ? 'Rejected false positive' : status.replaceAll('_', ' ')}</option>)}</select>
            </label>
            <label>Review notes
              <textarea className="min-h-20 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs" value={reviewNotes} onChange={(event) => setReviewNotes(event.target.value)} maxLength={5000} placeholder="Record supporting observations, reasons for rejection, or follow-up findings." />
            </label>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <button className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-xs font-semibold text-rose-700" type="button" onClick={() => setReviewStatus('rejected')}>Reject false positive</button>
            <button className="rounded-lg border border-emerald-200 bg-white px-3 py-2 text-xs font-semibold text-emerald-700" type="button" onClick={() => setReviewStatus('confirmed')}>Confirm anomaly</button>
            <button className="rounded-lg bg-forest-600 px-4 py-2 text-xs font-semibold text-white" type="submit">Save review</button>
          </div>
        </form>}
        {canCreateOrder && selected.device_id !== null && selected.device_id !== undefined && <form className="grid gap-3 rounded-xl border border-slate-200 p-4 sm:grid-cols-2" onSubmit={createMaintenanceOrder}>
          <div className="sm:col-span-2"><h3 className="text-sm font-semibold text-slate-900">Create maintenance order</h3><p className="mt-1 text-[10px] text-slate-500">Creating an order records follow-up; it does not change the detection review status.</p></div>
          <label>Order title<input value={maintenanceTitle} onChange={(event) => setMaintenanceTitle(event.target.value)} maxLength={200} required /></label>
          <label>Priority<select value={maintenancePriority} onChange={(event) => setMaintenancePriority(event.target.value)}>{['low', 'medium', 'high', 'critical'].map((priority) => <option key={priority} value={priority}>{priority}</option>)}</select></label>
          <label>Due date (optional)<input type="datetime-local" value={maintenanceDueDate} onChange={(event) => setMaintenanceDueDate(event.target.value)} /></label>
          {userRole === 'administrator' && <label>Assign inspection to<select value={maintenanceAssignee} onChange={(event) => setMaintenanceAssignee(event.target.value)} required><option value="" disabled>Select an active technician</option>{technicians.filter((technician) => technician.role === 'maintenance_technician').map((technician) => <option key={technician.id} value={technician.id}>{technician.full_name}</option>)}</select></label>}
          <button className="self-end rounded-lg bg-forest-600 px-4 py-2 text-xs font-semibold text-white" type="submit"><Wrench size={14} className="mr-1 inline" />Create order</button>
        </form>}
        {canCreateOrder && (selected.device_id === null || selected.device_id === undefined) && <p className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">This detection belongs to a historical dataset without a registered physical device. You can record a review, but a physical maintenance order cannot be created from experiment data.</p>}
      </section>
    </div>}
  </>
}
