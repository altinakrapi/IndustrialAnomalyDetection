import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { download, request, isManualConfirmationRequired } from '../api'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import type { Action, Device, DeviceDetail, AnalysisResultItem, AnalysisResultPage, DeviceAnalysisDetail, DeviceAnomaly, EquipmentMeasurement, EquipmentMeasurementPage, EquipmentParameter, User } from '../types/workflow'

import { inputClass, buttonClass, panelClass, Field, StatCard, HistoryList, ErrorMessage } from '../components/ui'
import { AlertEvaluation } from '../components/AlertEvaluation'
import { DEVICE_ANALYSIS_PAGE_SIZE, DEVICE_MAINTENANCE_PAGE_SIZE, DEVICE_RESULT_PAGE_SIZE, DEVICE_ANOMALY_PAGE_SIZE } from '../components/pageConstants'

export function DeviceDetailPanel({ devices, canEdit, canRecord, isAdministrator, currentUserId, initialDeviceId, onAction }: { devices: Device[]; canEdit: boolean; canRecord: boolean; isAdministrator: boolean; currentUserId: number; initialDeviceId?: number; onAction: Action }) {
  const [deviceId, setDeviceId] = useState(() => initialDeviceId ? String(initialDeviceId) : '')
  const [device, setDevice] = useState<DeviceDetail | null>(null)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState('')
  const [analysisOffset, setAnalysisOffset] = useState(0)
  const [maintenanceOffset, setMaintenanceOffset] = useState(0)
  const [selectedAnalysisIds, setSelectedAnalysisIds] = useState<number[]>([])
  const [selectedAnalysis, setSelectedAnalysis] = useState<DeviceAnalysisDetail | null>(null)
  const [analysisResults, setAnalysisResults] = useState<AnalysisResultItem[]>([])
  const [analysisResultTotal, setAnalysisResultTotal] = useState(0)
  const [analysisResultOffset, setAnalysisResultOffset] = useState(0)
  const [selectedAnomalies, setSelectedAnomalies] = useState<DeviceAnomaly[]>([])
  const [anomalyOffset, setAnomalyOffset] = useState(0)
  const [comparison, setComparison] = useState<Record<string, unknown>[]>([])
  const [loadingAnalysis, setLoadingAnalysis] = useState(false)
  const [measurements, setMeasurements] = useState<EquipmentMeasurement[]>([])
  const [measurementTotal, setMeasurementTotal] = useState(0)
  const [measurementOffset, setMeasurementOffset] = useState(0)
  const [trendParameterId, setTrendParameterId] = useState('')
  const [responsibleUsers, setResponsibleUsers] = useState<User[]>([])
  const measurementPageSize = 25

  const refreshMeasurements = useCallback(async (id: number, offset = 0) => {
    const page = await request<EquipmentMeasurementPage>(`/devices/${id}/measurements?limit=${measurementPageSize}&offset=${offset}`)
    setMeasurements(page.items)
    setMeasurementTotal(page.total)
    setMeasurementOffset(offset)
  }, [])

  const refresh = useCallback(async (
    id: string | number = deviceId,
    analysisStart = 0,
    maintenanceStart = 0,
  ) => {
    if (!id) return
    try {
      const query = new URLSearchParams({
        analysis_limit: String(DEVICE_ANALYSIS_PAGE_SIZE),
        analysis_offset: String(analysisStart),
        maintenance_limit: String(DEVICE_MAINTENANCE_PAGE_SIZE),
        maintenance_offset: String(maintenanceStart),
      })
      const detail = await request<DeviceDetail>(`/devices/${id}?${query}`)
      setDevice(detail)
      await refreshMeasurements(Number(id))
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load equipment details')
    }
  }, [deviceId, refreshMeasurements])

  const inspectAnalysis = useCallback(async (
    id: number,
    resultStart = 0,
    anomalyStart = 0,
  ) => {
    setLoadingAnalysis(true)
    try {
      const [detail, resultPage, anomalyPage] = await Promise.all([
        request<DeviceAnalysisDetail & { id: number }>(`/analyses/${id}`),
        request<AnalysisResultPage>(`/analyses/${id}/results?limit=${DEVICE_RESULT_PAGE_SIZE}&offset=${resultStart}`),
        request<DeviceAnomaly[]>(`/anomalies?analysis_id=${id}&limit=${DEVICE_ANOMALY_PAGE_SIZE}&offset=${anomalyStart}`),
      ])
      setSelectedAnalysis(detail)
      setAnalysisResults(resultPage.results)
      setAnalysisResultTotal(resultPage.total)
      setAnalysisResultOffset(resultStart)
      setSelectedAnomalies(anomalyPage)
      setAnomalyOffset(anomalyStart)
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load analysis details')
    } finally {
      setLoadingAnalysis(false)
    }
  }, [])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refresh(deviceId))
    return () => window.cancelAnimationFrame(frame)
  }, [deviceId, refresh])

  useEffect(() => {
    if (!isAdministrator) return
    const frame = window.requestAnimationFrame(() => {
      void request<User[]>('/devices/responsible-users')
        .then(setResponsibleUsers)
        .catch((caught: unknown) => {
          setError(caught instanceof Error ? caught.message : 'Unable to load responsible users')
        })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [isAdministrator])

  async function update(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!device) return
    const values = Object.fromEntries(new FormData(event.currentTarget))
    await onAction(async () => {
      await request<DeviceDetail>(`/devices/${device.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ...values,
          installation_date: values.installation_date || null,
          responsible_user_id: values.responsible_user_id ? Number(values.responsible_user_id) : null,
        }),
      })
      await refresh(device.id)
      setEditing(false)
    }, 'Equipment details updated')
  }
  async function archive() {
    if (!device || !window.confirm(`Archive ${device.name}? Linked datasets and maintenance history will be kept.`)) return
    await onAction(async () => {
      await request(`/devices/${device.id}/archive`, { method: 'PATCH' })
      await refresh(device.id, analysisOffset, maintenanceOffset)
    }, 'Equipment archived')
  }
  async function remove() {
    if (!device || !window.confirm(`Permanently delete ${device.name}? This is only allowed when no data is linked.`)) return
    await onAction(async () => {
      await request(`/devices/${device.id}`, { method: 'DELETE' })
      setDevice(null)
      setDeviceId('')
    }, 'Equipment deleted')
  }

  async function saveParameter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!device) return
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const numberOrNull = (key: string) => {
      const value = String(form.get(key) ?? '').trim()
      return value ? Number(value) : null
    }
    await onAction(async () => {
      await request<EquipmentParameter>(`/devices/${device.id}/parameters`, {
        method: 'POST',
        body: JSON.stringify({
          name: String(form.get('name') ?? ''),
          unit: String(form.get('unit') ?? ''),
          analysis_method: String(form.get('analysis_method') ?? 'isolation_forest'),
          expected_interval_minutes: Number(form.get('expected_interval_minutes')),
          lower_limit: numberOrNull('lower_limit'),
          upper_limit: numberOrNull('upper_limit'),
          warning_low: numberOrNull('warning_low'),
          warning_high: numberOrNull('warning_high'),
          critical_low: numberOrNull('critical_low'),
          critical_high: numberOrNull('critical_high'),
          flatline_reading_count: Number(form.get('flatline_reading_count') ?? 10),
          flatline_variance_tolerance: Number(form.get('flatline_variance_tolerance') ?? 0.05),
          z_score_threshold: numberOrNull('z_score_threshold') ?? 3,
          isolation_contamination: numberOrNull('isolation_contamination') ?? 0.05,
          consecutive_anomaly_threshold: numberOrNull('consecutive_anomaly_threshold'),
          persistence_window_size: numberOrNull('persistence_window_size'),
          persistence_anomaly_threshold: numberOrNull('persistence_anomaly_threshold'),
          enabled: true,
        }),
      })
      await refresh(device.id, analysisOffset, maintenanceOffset)
      formElement.reset()
    }, 'Measurement parameter added')
  }

  async function saveParameterOverrides(parameter: EquipmentParameter, event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!device) return
    const values = new FormData(event.currentTarget)
    const numberOrNull = (name: string) => {
      const value = String(values.get(name) ?? '').trim()
      return value ? Number(value) : null
    }
    await onAction(async () => {
      await request<EquipmentParameter>(`/devices/${device.id}/parameters/${parameter.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: parameter.name, unit: parameter.unit,
          lower_limit: parameter.lower_limit, upper_limit: parameter.upper_limit,
          warning_low: parameter.warning_low, warning_high: parameter.warning_high,
          critical_low: parameter.critical_low, critical_high: parameter.critical_high,
          expected_interval_minutes: parameter.expected_interval_minutes,
          expected_interval_seconds: parameter.expected_interval_seconds ?? null,
          analysis_method: parameter.analysis_method,
          z_score_threshold: parameter.z_score_threshold,
          isolation_contamination: parameter.isolation_contamination,
          flatline_reading_count: Number(values.get('flatline_reading_count')),
          flatline_variance_tolerance: Number(values.get('flatline_variance_tolerance')),
          consecutive_anomaly_threshold: numberOrNull('consecutive_anomaly_threshold'),
          persistence_window_size: numberOrNull('persistence_window_size'),
          persistence_anomaly_threshold: numberOrNull('persistence_anomaly_threshold'),
          enabled: parameter.enabled,
        }),
      })
      await refresh(device.id, analysisOffset, maintenanceOffset)
    }, 'Alert overrides saved')
  }

  async function toggleParameter(parameter: EquipmentParameter) {
    if (!device) return
    await onAction(async () => {
      await request<EquipmentParameter>(`/devices/${device.id}/parameters/${parameter.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: parameter.name,
          unit: parameter.unit,
          lower_limit: parameter.lower_limit,
          upper_limit: parameter.upper_limit,
          warning_low: parameter.warning_low,
          warning_high: parameter.warning_high,
          critical_low: parameter.critical_low,
          critical_high: parameter.critical_high,
          expected_interval_minutes: parameter.expected_interval_minutes,
          expected_interval_seconds: parameter.expected_interval_seconds ?? null,
          analysis_method: parameter.analysis_method,
          z_score_threshold: parameter.z_score_threshold,
          isolation_contamination: parameter.isolation_contamination,
          flatline_reading_count: parameter.flatline_reading_count,
          flatline_variance_tolerance: parameter.flatline_variance_tolerance,
          consecutive_anomaly_threshold: parameter.consecutive_anomaly_threshold ?? null,
          persistence_window_size: parameter.persistence_window_size ?? null,
          persistence_anomaly_threshold: parameter.persistence_anomaly_threshold ?? null,
          enabled: !parameter.enabled,
        }),
      })
      await refresh(device.id, analysisOffset, maintenanceOffset)
    }, `Parameter ${parameter.enabled ? 'disabled' : 'enabled'}`)
  }

  async function recordMeasurement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!device) return
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const payload = {
      parameter_id: Number(form.get('parameter_id')),
      value: Number(form.get('value')),
      idempotency_key: crypto.randomUUID(),
    }
    await onAction(async () => {
      let result: EquipmentMeasurement
      try {
        result = await request<EquipmentMeasurement>(`/devices/${device.id}/measurements`, { method: 'POST', body: JSON.stringify(payload) })
      } catch (caught) {
        if (!isManualConfirmationRequired(caught)) throw caught
        const detail = caught.detail
        const accepted = window.confirm(`${detail.message}\nScore: ${detail.score}; threshold: ${detail.threshold}\n\nConfirm and submit this exact measurement?`)
        if (!accepted) throw new Error('Measurement was not submitted because confirmation was declined.', { cause: caught })
        result = await request<EquipmentMeasurement>(`/devices/${device.id}/measurements`, { method: 'POST', body: JSON.stringify({ ...payload, confirm_extreme: true }) })
      }
      await Promise.all([
        refresh(device.id, analysisOffset, maintenanceOffset),
        refreshMeasurements(device.id),
      ])
      setSelectedAnalysisIds([])
      formElement.reset()
      if (result.analysis_status !== 'completed') {
        throw new Error(`Measurement was stored, but its analysis status is ${result.analysis_status ?? 'unknown'}`)
      }
    }, 'Measurement recorded and analysis executed')
  }

  function toggleAnalysisComparison(id: number) {
    setSelectedAnalysisIds((current) => current.includes(id)
      ? current.filter((selectedId) => selectedId !== id)
      : current.length < 10 ? [...current, id] : current)
    setComparison([])
  }

  async function compareSelectedAnalyses() {
    try {
      setComparison(await request<Record<string, unknown>[]>('/analyses/compare', {
        method: 'POST',
        body: JSON.stringify(selectedAnalysisIds),
      }))
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to compare analyses')
    }
  }

  const trendParameter = device?.measurement_parameters.find(
    (parameter) => String(parameter.id) === trendParameterId,
  ) ?? device?.measurement_parameters.find(
    (parameter) => parameter.id === measurements[0]?.parameter_id,
  )
  const trendPoints = trendParameter
    ? measurements
      .filter((measurement) => measurement.parameter_id === trendParameter.id)
      .slice()
      .reverse()
      .map((measurement) => ({
        label: measurement.measured_at,
        value: measurement.value,
      }))
    : []
  const canRecordForDevice = Boolean(
    canRecord
    && device
    && (
      isAdministrator
    || device.responsible_user_id === currentUserId
    ),
  )

  return (
    <section className={`${panelClass} space-y-4`}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="grid min-w-64 gap-1.5 text-xs font-medium text-slate-600">Equipment details
          <select className={inputClass} value={deviceId} onChange={(event) => {
            setAnalysisOffset(0)
            setMaintenanceOffset(0)
            setSelectedAnalysisIds([])
            setSelectedAnalysis(null)
            setComparison([])
            setTrendParameterId('')
            setMeasurements([])
            setMeasurementTotal(0)
            setDeviceId(event.target.value)
          }}>
            <option value="">Choose a device</option>{devices.map((item) => <option key={item.id} value={item.id}>{item.name}  -  {item.device_code}</option>)}
          </select>
        </label>
        {device && <div className="flex flex-wrap gap-2">
          <button className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold" onClick={() => void download(`/reports/device/${device.id}.pdf`, `device-${device.id}.pdf`).catch((caught: unknown) => onAction(() => Promise.reject(caught), ''))}>Device report</button>
          {canEdit && <button className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold" onClick={() => setEditing(!editing)}>{editing ? 'Cancel edit' : 'Edit details'}</button>}
          {canEdit && device.status !== 'archived' && <button className="rounded-lg border border-amber-200 px-3 py-2 text-xs font-semibold text-amber-800" onClick={() => void archive()}>Archive</button>}
          {canEdit && !device.dataset_count && !device.maintenance_order_count && !device.analysis_count && !device.measurement_parameters.length && <button className="rounded-lg border border-rose-200 px-3 py-2 text-xs font-semibold text-rose-800" onClick={() => void remove()}>Delete</button>}
        </div>}
      </div>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      {device && <>
        {editing ? <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" onSubmit={(event) => void update(event)}>
          <Field label="Name" name="name" defaultValue={device.name} required />
          <Field label="Unique code" name="device_code" defaultValue={device.device_code} required />
          <Field label="Type" name="device_type" defaultValue={device.device_type} required />
          <Field label="Category" name="category" defaultValue={device.category ?? ''} />
          <Field label="Manufacturer" name="manufacturer" defaultValue={device.manufacturer ?? ''} />
          <Field label="Model" name="model" defaultValue={device.model ?? ''} />
          <Field label="Serial number" name="serial_number" defaultValue={device.serial_number ?? ''} />
          <Field label="Location" name="location" defaultValue={device.location ?? ''} />
          <Field label="Installation date" name="installation_date" type="date" defaultValue={device.installation_date ?? ''} />
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Status<select className={inputClass} name="status" defaultValue={device.status}><option value="active">Active</option><option value="inactive">Inactive</option><option value="under_maintenance">Under maintenance</option><option value="archived">Archived</option></select></label>
          <Field label="Description" name="description" defaultValue={device.description ?? ''} />
          {isAdministrator && <label className="grid gap-1.5 text-xs font-medium text-slate-600">Responsible measurement recorder<select className={inputClass} name="responsible_user_id" defaultValue={device.responsible_user_id ?? ''} required><option value="" disabled>Select an active user</option>{responsibleUsers.map((responsibleUser) => <option key={responsibleUser.id} value={responsibleUser.id}>{responsibleUser.full_name} · {responsibleUser.role.replaceAll('_', ' ')}</option>)}</select></label>}
          <button className={`${buttonClass} self-end`} type="submit">Save changes</button>
        </form> : <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <StatCard label="Datasets" value={String(device.dataset_count)} />
            <StatCard label="Analyses" value={String(device.analysis_count)} />
            <StatCard label="Algorithm detections" value={String(device.anomaly_count)} />
            <StatCard label="Pending reviews" value={String(device.unreviewed_anomaly_count)} />
            <StatCard label="Maintenance orders" value={String(device.maintenance_order_count)} />
          </div>
          <p className="text-xs text-slate-600">{device.manufacturer ?? 'Manufacturer not set'}  -  {device.model ?? 'Model not set'}  -  {device.serial_number ?? 'Serial number not set'}  -  {device.location ?? 'Location not set'} · Source: {(device.source_type ?? 'manual').replaceAll('_', ' ')} · Responsible recorder: {device.responsible_user_name ?? 'Not assigned'}</p>
          <HistoryList title="Linked datasets" items={device.datasets.map((item) => `#${item.id} ${item.name}  -  ${item.row_count} rows  -  ${item.status}`)} />
          <section className="space-y-3 rounded-lg border border-slate-100 p-3">
            <div>
              <h3 className="text-xs font-semibold text-slate-800">Registered equipment measurements</h3>
              <p className="mt-1 text-[10px] text-slate-500">Manual and sensor readings are analyzed on receipt using configured limits and compatible equipment history. Simulated readings are explicitly labeled and excluded from normal equipment anomaly views. The NASA IMS-trained model is used only when all 36 matching vibration features are recorded for this device at the same timestamp; dataset experiment records are never copied into this equipment history.</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <h4 className="text-[11px] font-semibold text-slate-700">Configured parameters</h4>
                {device.measurement_parameters.map((parameter) => <article className="rounded-lg bg-slate-50 p-3 text-xs" key={parameter.id}>
                  <div className="flex flex-wrap items-center justify-between gap-2"><strong>{parameter.name} ({parameter.unit})</strong><span className="flex items-center gap-2">{parameter.enabled ? 'Enabled' : 'Disabled'}{canEdit && <button className="font-semibold text-forest-700" type="button" onClick={() => void toggleParameter(parameter)}>{parameter.enabled ? 'Disable' : 'Enable'}</button>}</span></div>
                  <p className="mt-1 text-[10px] text-slate-500">Method: {parameter.analysis_method.replaceAll('_', ' ')} · Measurement interval: {parameter.expected_interval_seconds != null ? `${parameter.expected_interval_seconds} sec` : `${parameter.expected_interval_minutes} min`} · Status: {parameter.measurement_status?.replaceAll('_', ' ') ?? 'unavailable'}</p>
                  <p className="text-[10px] text-slate-500">Last recorded: {parameter.last_measurement_at ? new Date(parameter.last_measurement_at).toLocaleString() : 'Never'} · Next due: {parameter.enabled && parameter.next_due_at ? new Date(parameter.next_due_at).toLocaleString() : 'Disabled'}</p>
                  <p className="text-[10px] text-slate-500">Last analysis: {parameter.last_analysis_at ? `${parameter.last_analysis_status} · ${new Date(parameter.last_analysis_at).toLocaleString()}` : 'Not analyzed'} · Next automatic analysis: {parameter.enabled && parameter.next_analysis_due_at ? `when a measurement is recorded (due ${new Date(parameter.next_analysis_due_at).toLocaleString()})` : 'Not scheduled'}</p>
                  <AlertEvaluation alert_rules={parameter.alert_rules} alert_priority={parameter.alert_priority} history_state={parameter.history_state} history_size={parameter.history_size} history_required={parameter.history_required} />
                  {parameter.last_analysis_note && <p className="mt-1 text-[10px] font-medium text-amber-800">{parameter.last_analysis_note} Configured threshold checks remain active.</p>}
                  <p className="text-[10px] text-slate-500">Warning: {parameter.warning_low ?? '—'} to {parameter.warning_high ?? '—'} · Critical: {parameter.critical_low ?? '—'} to {parameter.critical_high ?? '—'}</p>
                  <p className="text-[10px] text-slate-500">Alert overrides: consecutive {parameter.consecutive_anomaly_threshold ?? 'global default'} | persistence window {parameter.persistence_window_size ?? 'global default'} | anomalies required {parameter.persistence_anomaly_threshold ?? 'global default'} | flatline {parameter.flatline_reading_count} readings at ≤ {(parameter.flatline_variance_tolerance * 100).toFixed(1)}% of baseline variation</p>
                  {canEdit && <details className="mt-2"><summary className="cursor-pointer text-[10px] font-semibold text-forest-700">Edit alert overrides</summary><form className="mt-2 grid gap-2 sm:grid-cols-3" onSubmit={(event) => void saveParameterOverrides(parameter, event)}>
                    <Field label="Consecutive anomaly threshold (blank = default)" name="consecutive_anomaly_threshold" type="number" min="1" max="100" defaultValue={parameter.consecutive_anomaly_threshold ?? ''} />
                    <Field label="Persistence window size (blank = default)" name="persistence_window_size" type="number" min="1" max="100" defaultValue={parameter.persistence_window_size ?? ''} />
                    <Field label="Persistence anomaly threshold (blank = default)" name="persistence_anomaly_threshold" type="number" min="1" max="100" defaultValue={parameter.persistence_anomaly_threshold ?? ''} />
                    <Field label="Flatline consecutive readings" name="flatline_reading_count" type="number" min="2" max="100" defaultValue={parameter.flatline_reading_count} />
                    <Field label="Flatline variance tolerance (0–1)" name="flatline_variance_tolerance" type="number" min="0.001" max="1" step="any" defaultValue={parameter.flatline_variance_tolerance} />
                    <button className={`${buttonClass} sm:col-span-3`} type="submit">Save alert overrides</button>
                  </form></details>}
                </article>)}
                {!device.measurement_parameters.length && <p className="text-[10px] text-slate-500">No measurement parameters configured.</p>}
                {canEdit && <details className="rounded-lg border border-slate-100 p-3">
                  <summary className="cursor-pointer text-xs font-semibold text-forest-700">Configure a parameter</summary>
                  <form className="mt-3 grid gap-2 sm:grid-cols-2" onSubmit={(event) => void saveParameter(event)}>
                    <Field label="Parameter" name="name" placeholder="Vibration RMS, temperature…" required />
                    <Field label="Unit" name="unit" placeholder="mm/s, °C, RPM…" required />
                    <label className="grid gap-1 text-[10px]">Analysis method<select className={inputClass} name="analysis_method" defaultValue="isolation_forest"><option value="isolation_forest">Isolation Forest + thresholds</option><option value="z_score">Z-score + thresholds</option><option value="threshold">Configured thresholds only</option></select></label>
                    <Field label="Measurement interval (minutes)" name="expected_interval_minutes" type="number" min="1" defaultValue="120" required />
                    <Field label="Acceptable minimum" name="lower_limit" type="number" step="any" />
                    <Field label="Acceptable maximum" name="upper_limit" type="number" step="any" />
                    <Field label="Warning minimum" name="warning_low" type="number" step="any" />
                    <Field label="Warning maximum" name="warning_high" type="number" step="any" />
                    <Field label="Critical minimum" name="critical_low" type="number" step="any" />
                    <Field label="Critical maximum" name="critical_high" type="number" step="any" />
                    <Field label="Z-score threshold" name="z_score_threshold" type="number" min="0.01" step="any" defaultValue="3" />
                    <Field label="Isolation contamination" name="isolation_contamination" type="number" min="0.001" max="0.5" step="any" defaultValue="0.05" />
                    <Field label="Flatline consecutive readings" name="flatline_reading_count" type="number" min="2" max="100" defaultValue="10" required />
                    <Field label="Flatline variance tolerance (0–1)" name="flatline_variance_tolerance" type="number" min="0.001" max="1" step="any" defaultValue="0.05" required />
                    <button className={`${buttonClass} sm:col-span-2`} type="submit">Save parameter</button>
                  </form>
                </details>}
              </div>
              {canRecordForDevice && device.status === 'active' && device.measurement_parameters.some((parameter) => parameter.enabled) && <form className="grid content-start gap-2 rounded-lg border border-slate-100 p-3" onSubmit={(event) => void recordMeasurement(event)}>
                <h4 className="text-[11px] font-semibold text-slate-700">Record equipment measurement</h4>
                <p className="text-[10px] text-slate-500">Enter the actual observed value. The server assigns the submission date and time automatically. The configured interval sets reminders only; it does not fabricate readings. Each saved measurement is analyzed immediately.</p>
                <label className="grid gap-1 text-[10px]">Parameter<select className={inputClass} name="parameter_id" required defaultValue=""><option value="" disabled>Select parameter</option>{device.measurement_parameters.filter((parameter) => parameter.enabled).map((parameter) => <option value={parameter.id} key={parameter.id}>{parameter.name} ({parameter.unit})</option>)}</select></label>
                <Field label="Observed value" name="value" type="number" step="any" required />
                <button className={buttonClass} type="submit">Save and analyze measurement</button>
              </form>}
              {canRecord && !canRecordForDevice && <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[10px] text-amber-900">{device.responsible_user_id == null ? 'An administrator must assign a responsible recorder before measurements can be submitted.' : 'Only the assigned responsible recorder or an administrator can submit actual measurements for this equipment.'}</p>}
            </div>
            <div className="flex items-center justify-between">
              <h4 className="text-[11px] font-semibold text-slate-700">Measurement history · {measurementTotal.toLocaleString()} records</h4>
              <span className="text-[10px] text-slate-500">{measurementTotal ? `${measurementOffset + 1}–${measurementOffset + measurements.length}` : 'No manually recorded values'}</span>
            </div>
            {trendParameter && trendPoints.length > 1 && <div className="rounded-lg border border-slate-100 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-[11px] font-semibold text-slate-700">Recent trend · {trendParameter.name} ({trendParameter.unit})</h4>
                <label className="flex items-center gap-2 text-[10px] text-slate-500">Parameter<select className={inputClass} value={trendParameter.id} onChange={(event) => setTrendParameterId(event.target.value)}>{device.measurement_parameters.map((parameter) => <option value={parameter.id} key={parameter.id}>{parameter.name} ({parameter.unit})</option>)}</select></label>
              </div>
              <p className="mt-1 text-[10px] text-slate-500">Latest page of manually recorded measurements, ordered by measurement time; this is not live telemetry.</p>
              <div className="mt-3 h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trendPoints}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="label" tick={{ fontSize: 8 }} minTickGap={28} tickFormatter={(value: string) => new Date(value).toLocaleDateString()} />
                    <YAxis tick={{ fontSize: 9 }} />
                    <Tooltip labelFormatter={(label) => `Measured: ${new Date(String(label)).toLocaleString()}`} />
                    <Line dataKey="value" name={trendParameter.name} stroke="#466b4c" dot={{ r: 2 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[650px] text-left text-[10px]">
                <thead className="bg-slate-50"><tr>{['Parameter', 'Value', 'Measured at', 'Recorded at', 'Recorded by', 'Source', 'Analysis trace'].map((label) => <th className="px-3 py-2" key={label}>{label}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">{measurements.map((measurement) => <tr key={measurement.id}>
                  <td className="px-3 py-2">{measurement.parameter_name}</td><td className="px-3 py-2 font-mono">{measurement.value} {measurement.unit}</td><td className="px-3 py-2">{new Date(measurement.measured_at).toLocaleString()}</td><td className="px-3 py-2">{new Date(measurement.recorded_at).toLocaleString()}</td><td className="px-3 py-2">{measurement.recorder_name ?? `User #${measurement.recorded_by}`}</td><td className="px-3 py-2 capitalize">{measurement.source_type.replaceAll('_', ' ')}</td><td className="px-3 py-2">
                    {measurement.analysis_runs?.map((run) => <button className="mr-2 whitespace-nowrap font-semibold text-forest-700" key={run.id} type="button" onClick={() => void inspectAnalysis(run.id)}>Run #{run.id} · {run.status}</button>)}
                    {measurement.anomaly_ids?.map((id) => <span className="mr-2 whitespace-nowrap text-rose-700" key={id}>Anomaly #{id}</span>)}
                    {!measurement.analysis_runs?.length && <span className="text-slate-400">No run</span>}
                  </td>
                </tr>)}</tbody>
              </table>
              {!measurements.length && <p className="p-3 text-[10px] text-slate-500">No recorded equipment measurements yet.</p>}
            </div>
            <div className="flex justify-end gap-2">
              <button className="rounded border px-2 py-1 text-[10px] disabled:opacity-40" type="button" disabled={!measurementOffset} onClick={() => void refreshMeasurements(device.id, Math.max(0, measurementOffset - measurementPageSize))}>Previous</button>
              <button className="rounded border px-2 py-1 text-[10px] disabled:opacity-40" type="button" disabled={measurementOffset + measurements.length >= measurementTotal} onClick={() => void refreshMeasurements(device.id, measurementOffset + measurementPageSize)}>Next</button>
            </div>
          </section>
          <section className="rounded-lg border border-slate-100 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><h3 className="text-xs font-semibold text-slate-800">Complete analysis history</h3><p className="mt-1 text-[10px] text-slate-500">{device.analysis_total} runs · select two or more to compare retained results</p></div>
              <button className={buttonClass} type="button" disabled={selectedAnalysisIds.length < 2} onClick={() => void compareSelectedAnalyses()}>Compare selected ({selectedAnalysisIds.length})</button>
            </div>
            {device.analyses.length ? <div className="mt-3 space-y-2">
              {device.analyses.map((run) => <article className="grid gap-2 rounded-lg border border-slate-100 p-3 sm:grid-cols-[auto_1fr_auto]" key={run.id}>
                <input aria-label={`Include analysis ${run.id} in comparison`} className="mt-1" type="checkbox" checked={selectedAnalysisIds.includes(run.id)} onChange={() => toggleAnalysisComparison(run.id)} />
                <div className="min-w-0 text-xs">
                  <strong className="text-slate-800">Run #{run.id} · {run.model_name ?? run.algorithm ?? 'Analysis'} · {run.status}</strong>
                  <p className="mt-1 text-slate-600">{run.dataset_name ?? (run.source_type === 'equipment' ? 'Registered equipment measurements' : 'No dataset')} · {run.execution_type} · {run.total_records.toLocaleString()} measurements · {run.anomalies_found.toLocaleString()} anomalies</p>
                  <p className="mt-1 text-[10px] text-slate-500">Started {run.started_at ? new Date(run.started_at).toLocaleString() : '—'} · completed {run.completed_at ? new Date(run.completed_at).toLocaleString() : '—'}</p>
                  {run.error_message && <p className="mt-1 text-rose-700">{run.error_message}</p>}
                </div>
                <button className="self-start text-xs font-semibold text-forest-700" type="button" onClick={() => void inspectAnalysis(run.id)}>Inspect results</button>
              </article>)}
            </div> : <p className="mt-3 text-xs text-slate-500">No saved analyses for this device yet.</p>}
            <div className="mt-3 flex items-center justify-between text-[10px] text-slate-500">
              <span>{device.analysis_total ? `Showing ${analysisOffset + 1}–${analysisOffset + device.analyses.length} of ${device.analysis_total}` : 'No analysis records'}</span>
              <div className="flex gap-2">
                  <button className="rounded border px-2 py-1 disabled:opacity-40" type="button" disabled={analysisOffset === 0} onClick={() => { const nextOffset = Math.max(0, analysisOffset - DEVICE_ANALYSIS_PAGE_SIZE); setAnalysisOffset(nextOffset); void refresh(device.id, nextOffset, maintenanceOffset) }}>Previous</button>
                  <button className="rounded border px-2 py-1 disabled:opacity-40" type="button" disabled={analysisOffset + device.analyses.length >= device.analysis_total} onClick={() => { const nextOffset = analysisOffset + DEVICE_ANALYSIS_PAGE_SIZE; setAnalysisOffset(nextOffset); void refresh(device.id, nextOffset, maintenanceOffset) }}>Next</button>
              </div>
            </div>
          </section>
          {selectedAnalysis && <section className="space-y-3 rounded-lg border border-forest-100 bg-forest-50/30 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div><h3 className="text-sm font-semibold text-slate-900">Analysis #{selectedAnalysis.id} · {selectedAnalysis.status}</h3><p className="mt-1 text-xs text-slate-600">{selectedAnalysis.execution_type} · {selectedAnalysis.total_records.toLocaleString()} measurements · {selectedAnalysis.anomalies_found.toLocaleString()} anomalies · {selectedAnalysis.completed_at ? new Date(selectedAnalysis.completed_at).toLocaleString() : 'not completed'}</p></div>
              <button className="text-xs text-slate-500" type="button" onClick={() => setSelectedAnalysis(null)}>Close results</button>
            </div>
            {loadingAnalysis && <p className="text-xs text-slate-500">Loading saved analysis results…</p>}
            <details><summary className="cursor-pointer text-xs font-semibold text-slate-700">Configuration and run metrics</summary><pre className="mt-2 max-h-48 overflow-auto rounded bg-white p-3 text-[10px] text-slate-600">{JSON.stringify({ configuration: selectedAnalysis.configuration, metrics: selectedAnalysis.metrics }, null, 2)}</pre></details>
            <div className="overflow-x-auto rounded border border-slate-100 bg-white">
              <table className="w-full text-left text-[10px]">
                <thead className="bg-slate-50"><tr>{['Source row / measurement', 'Measurement time', 'Result', 'Algorithm score', 'Measurements / features'].map((label) => <th className="px-3 py-2" key={label}>{label}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">{analysisResults.map((item) => <tr key={item.id}>
                  <td className="px-3 py-2">{item.source_row_number ?? item.equipment_measurement_id ?? '—'}</td>
                  <td className="whitespace-nowrap px-3 py-2">{item.timestamp ? new Date(item.timestamp).toLocaleString() : '—'}</td>
                  <td className={`px-3 py-2 font-semibold ${item.is_anomaly ? 'text-rose-700' : 'text-emerald-700'}`}>{item.is_anomaly ? 'Anomaly' : 'Normal'}</td>
                  <td className="px-3 py-2">{item.anomaly_score?.toFixed(4) ?? '—'}</td>
                  <td className="max-w-xl break-all px-3 py-2 font-mono">{JSON.stringify(item.feature_values)}</td>
                </tr>)}</tbody>
              </table>
              {!analysisResults.length && !loadingAnalysis && <p className="p-3 text-xs text-slate-500">No result rows are available for this run.</p>}
            </div>
            <div className="flex justify-between text-[10px] text-slate-500">
              <span>Showing {analysisResults.length ? analysisResultOffset + 1 : 0}–{analysisResultOffset + analysisResults.length} of {analysisResultTotal} results</span>
              <div className="flex gap-2">
                <button className="rounded border px-2 py-1 disabled:opacity-40" type="button" disabled={analysisResultOffset === 0} onClick={() => void inspectAnalysis(selectedAnalysis.id, Math.max(0, analysisResultOffset - DEVICE_RESULT_PAGE_SIZE), anomalyOffset)}>Previous results</button>
                <button className="rounded border px-2 py-1 disabled:opacity-40" type="button" disabled={analysisResultOffset + analysisResults.length >= analysisResultTotal} onClick={() => void inspectAnalysis(selectedAnalysis.id, analysisResultOffset + DEVICE_RESULT_PAGE_SIZE, anomalyOffset)}>Next results</button>
              </div>
            </div>
            <div>
              <h4 className="text-xs font-semibold text-slate-800">Anomalies and review history</h4>
              {selectedAnomalies.length ? <div className="mt-2 space-y-2">{selectedAnomalies.map((anomaly) => <article className="rounded border border-slate-100 bg-white p-3 text-[10px]" key={anomaly.id}>
                <div className="flex flex-wrap justify-between gap-2"><strong>#{anomaly.id} · {anomaly.severity} · {anomaly.status}</strong><span>Detected {new Date(anomaly.detected_at).toLocaleString()}{anomaly.measurement_time ? ` · measurement ${new Date(anomaly.measurement_time).toLocaleString()}` : ''}</span></div>
                <p className="mt-1 text-slate-600">{anomaly.description ?? anomaly.anomaly_type} · score {anomaly.anomaly_score?.toFixed(4) ?? '—'}</p>
                <p className="mt-1 break-all font-mono text-slate-500">{JSON.stringify(anomaly.feature_values)}</p>
                {anomaly.reviews.length > 0 && <ul className="mt-2 space-y-1 border-t pt-2 text-slate-600">{anomaly.reviews.map((review) => <li key={review.id}>{review.decision} · {new Date(review.reviewed_at).toLocaleString()}{review.comments ? ` · ${review.comments}` : ''}</li>)}</ul>}
                {anomaly.maintenance_order_ids.length > 0 && <p className="mt-1 text-forest-700">Maintenance orders: {anomaly.maintenance_order_ids.map((id) => `#${id}`).join(', ')}</p>}
              </article>)}</div> : <p className="mt-2 text-[10px] text-slate-500">No anomaly records for this analysis.</p>}
              <div className="mt-2 flex justify-between text-[10px] text-slate-500">
                <span>{selectedAnalysis.anomalies_found.toLocaleString()} anomalies in this analysis</span>
                <div className="flex gap-2">
                  <button className="rounded border px-2 py-1 disabled:opacity-40" type="button" disabled={anomalyOffset === 0} onClick={() => void inspectAnalysis(selectedAnalysis.id, analysisResultOffset, Math.max(0, anomalyOffset - DEVICE_ANOMALY_PAGE_SIZE))}>Previous anomalies</button>
                  <button className="rounded border px-2 py-1 disabled:opacity-40" type="button" disabled={anomalyOffset + selectedAnomalies.length >= selectedAnalysis.anomalies_found} onClick={() => void inspectAnalysis(selectedAnalysis.id, analysisResultOffset, anomalyOffset + DEVICE_ANOMALY_PAGE_SIZE)}>Next anomalies</button>
                </div>
              </div>
            </div>
          </section>}
          {comparison.length > 0 && <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {comparison.map((item) => <article className="rounded-lg border border-slate-100 p-3 text-xs" key={String(item.id)}>
              <h3 className="font-semibold text-slate-800">Analysis #{String(item.id)} · {String(item.algorithm)}</h3>
              <p className="mt-1 text-slate-600">{String(item.dataset_name)} · {String(item.total_records)} measurements · {String(item.anomalies_found)} anomalies</p>
              <p className="mt-1 text-slate-500">Status: {String(item.status)} · Duration: {String(item.duration_seconds ?? '—')} seconds</p>
              <details className="mt-2"><summary className="cursor-pointer text-slate-600">Metrics</summary><pre className="mt-1 overflow-auto text-[10px]">{JSON.stringify(item.metrics, null, 2)}</pre></details>
            </article>)}
          </section>}
          <section className="rounded-lg border border-slate-100 p-3">
            <h3 className="text-xs font-semibold text-slate-800">Maintenance history</h3>
            {device.maintenance_orders.length ? <div className="mt-2 space-y-2">{device.maintenance_orders.map((order) => <article className="rounded border border-slate-100 p-3 text-xs" key={order.id}>
              <strong>Order #{order.id} · {order.title}</strong><p className="mt-1 text-slate-600">{order.priority} priority · {order.status} · {order.anomaly_id ? `Anomaly #${order.anomaly_id}` : 'General maintenance'}{order.due_date ? ` · due ${new Date(order.due_date).toLocaleString()}` : ''}</p>
              {order.resolution_notes && <p className="mt-1 text-slate-600">Resolution: {order.resolution_notes}</p>}
              {order.actions.map((action) => <p className="mt-2 border-t pt-2 text-[10px] text-slate-500" key={action.id}>{action.action_description}{action.findings ? ` · Findings: ${action.findings}` : ''}{action.recommendations ? ` · Recommendation: ${action.recommendations}` : ''}</p>)}
            </article>)}</div> : <p className="mt-2 text-xs text-slate-500">No maintenance orders recorded.</p>}
            <div className="mt-3 flex justify-between text-[10px] text-slate-500">
              <span>{device.maintenance_order_count ? `Showing ${maintenanceOffset + 1}–${maintenanceOffset + device.maintenance_orders.length} of ${device.maintenance_order_count}` : 'No maintenance records'}</span>
              <div className="flex gap-2">
                <button className="rounded border px-2 py-1 disabled:opacity-40" type="button" disabled={maintenanceOffset === 0} onClick={() => { const nextOffset = Math.max(0, maintenanceOffset - DEVICE_MAINTENANCE_PAGE_SIZE); setMaintenanceOffset(nextOffset); void refresh(device.id, analysisOffset, nextOffset) }}>Previous</button>
                <button className="rounded border px-2 py-1 disabled:opacity-40" type="button" disabled={maintenanceOffset + device.maintenance_orders.length >= device.maintenance_order_count} onClick={() => { const nextOffset = maintenanceOffset + DEVICE_MAINTENANCE_PAGE_SIZE; setMaintenanceOffset(nextOffset); void refresh(device.id, analysisOffset, nextOffset) }}>Next</button>
              </div>
            </div>
          </section>
          {comparison.length === 0 && selectedAnalysisIds.length > 1 && <p className="text-[10px] text-slate-500">Choose Compare selected to review stored metrics and anomaly counts across runs.</p>}
        </>}
      </>}
      {!device && !error && <p className="text-xs text-slate-500">Select equipment to review its linked data, analysis, anomalies, and maintenance history.</p>}
    </section>
  )
}
