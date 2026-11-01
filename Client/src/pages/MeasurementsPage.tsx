import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Activity, Clock3, Cpu, Plus, RefreshCw } from 'lucide-react'
import { request, isManualConfirmationRequired } from '../api'
import { PageHeading } from '../components/PageHeading'
import { EmptyState } from '../components/EmptyState'
import { StatusBadge } from '../components/StatusBadge'
import { AlertEvaluation } from '../components/AlertEvaluation'
import { SourceTypeBadge, SourceTypeSelect } from '../components/SourceTypeControls'
import { matchesSourceType, type SourceTypeFilter } from '../sourceType'
import type { Device } from '../types'
import type { EquipmentMeasurement, EquipmentMeasurementPage, EquipmentParameter } from '../types/workflow'

type DeviceMeasurements = {
  parameters: EquipmentParameter[]
  measurements: EquipmentMeasurement[]
  total: number
  offset: number
}

const pageSize = 25
const parameterPresets = [
  { name: 'Vibration RMS', unit: 'mm/s' },
  { name: 'Vibration Mean', unit: 'mm/s' },
  { name: 'Vibration Standard Deviation', unit: 'mm/s' },
  { name: 'Vibration Peak', unit: 'mm/s' },
  { name: 'Temperature', unit: '°C' },
  { name: 'Rotational Speed', unit: 'RPM' },
]

function parameterLabel(parameter: EquipmentParameter) {
  const normalized = parameter.name.trim().toLowerCase().replace(/[_-]+/g, ' ')
  const labels: Record<string, string> = {
    'vibration rms': 'Vibration RMS',
    'vibration mean': 'Vibration Mean',
    'vibration standard deviation': 'Vibration Standard Deviation',
    'vibration std deviation': 'Vibration Standard Deviation',
    'vibration std dev': 'Vibration Standard Deviation',
    'vibration peak': 'Vibration Peak',
    temperature: 'Temperature',
    'rotational speed': 'Rotational Speed',
  }
  const name = labels[normalized] ?? parameter.name
  const unit = parameter.unit.trim().toLowerCase()
  const displayUnit = name === 'Temperature' && ['c', '°c', 'celsius'].includes(unit)
    ? '°C'
    : name === 'Rotational Speed' && ['rpm', 'rev/min', 'revolutions per minute'].includes(unit)
      ? 'RPM'
      : parameter.unit
  return `${name} (${displayUnit})`
}

export function MeasurementsPage({
  devices,
  currentUserId,
  userRole,
  search,
  onAction,
}: {
  devices: Device[]
  currentUserId: number
  userRole: string
  search: string
  onAction: (action: () => Promise<unknown>, success: string) => Promise<void>
}) {
  const [sourceFilter, setSourceFilter] = useState<SourceTypeFilter>('all')
  const assignedDevices = useMemo(
    () => devices.filter((device) => (
      (userRole === 'administrator' || device.responsible_user_id === currentUserId)
      && `${device.name} ${device.device_code} ${device.location ?? ''}`.toLowerCase().includes(search.toLowerCase())
    )),
    [currentUserId, devices, search, userRole],
  )
  const [deviceData, setDeviceData] = useState<Record<number, DeviceMeasurements>>({})
  const visibleDevices = useMemo(
    () => assignedDevices.filter((device) =>
      matchesSourceType(sourceFilter, device.source_type)
      || (deviceData[device.id]?.measurements.some((measurement) =>
        matchesSourceType(sourceFilter, measurement.source_type),
      ) ?? false),
    ),
    [assignedDevices, deviceData, sourceFilter],
  )
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState<number | null>(null)
  const [saving, setSaving] = useState<number | null>(null)
  const [configuring, setConfiguring] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [currentTime, setCurrentTime] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setCurrentTime(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const refreshDevice = useCallback(async (deviceId: number) => {
    const [parameters, history] = await Promise.all([
      request<EquipmentParameter[]>(`/devices/${deviceId}/parameters`),
      request<EquipmentMeasurementPage>(`/devices/${deviceId}/measurements?limit=${pageSize}&offset=0`),
    ])
    setDeviceData((current) => ({
      ...current,
      [deviceId]: {
        parameters,
        measurements: history.items,
        total: history.total,
        offset: 0,
      },
    }))
  }, [])

  useEffect(() => {
    let active = true
    void Promise.all(assignedDevices.map(async (device) => {
      try {
        const [parameters, history] = await Promise.all([
          request<EquipmentParameter[]>(`/devices/${device.id}/parameters`),
          request<EquipmentMeasurementPage>(`/devices/${device.id}/measurements?limit=${pageSize}&offset=0`),
        ])
        return [device.id, {
          parameters,
          measurements: history.items,
          total: history.total,
          offset: 0,
        } satisfies DeviceMeasurements] as const
      } catch (caught) {
        throw new Error(`Unable to load measurement history for ${device.name}: ${caught instanceof Error ? caught.message : 'request failed'}`, { cause: caught })
      }
    }))
      .then((entries) => {
        if (active) {
          setDeviceData(Object.fromEntries(entries))
          setError('')
        }
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : 'Unable to load assigned equipment measurements')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [assignedDevices])

  async function loadMore(deviceId: number) {
    const current = deviceData[deviceId]
    if (!current) return
    const nextOffset = current.offset + pageSize
    setLoadingMore(deviceId)
    try {
      const history = await request<EquipmentMeasurementPage>(`/devices/${deviceId}/measurements?limit=${pageSize}&offset=${nextOffset}`)
      setDeviceData((entries) => ({
        ...entries,
        [deviceId]: {
          ...entries[deviceId],
          measurements: [...entries[deviceId].measurements, ...history.items],
          total: history.total,
          offset: nextOffset,
        },
      }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load more measurement history')
    } finally {
      setLoadingMore(null)
    }
  }

  async function recordMeasurement(deviceId: number, event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    const payload = {
      parameter_id: Number(values.get('parameter_id')),
      value: Number(values.get('value')),
      idempotency_key: crypto.randomUUID(),
    }
    setSaving(deviceId)
    await onAction(async () => {
      try {
        await request(`/devices/${deviceId}/measurements`, { method: 'POST', body: JSON.stringify(payload) })
      } catch (caught) {
        if (!isManualConfirmationRequired(caught)) throw caught
        const detail = caught.detail
        const accepted = window.confirm(`${detail.message}\nScore: ${detail.score}; threshold: ${detail.threshold}\n\nConfirm and submit this exact measurement?`)
        if (!accepted) throw new Error('Measurement was not submitted because confirmation was declined.', { cause: caught })
        await request(`/devices/${deviceId}/measurements`, { method: 'POST', body: JSON.stringify({ ...payload, confirm_extreme: true }) })
      }
      await refreshDevice(deviceId)
      form.reset()
    }, 'Measurement saved and analysis triggered')
    setSaving(null)
  }

  async function addParameter(deviceId: number, event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    const preset = parameterPresets.find((item) => item.name === values.get('name'))
    const numberOrNull = (name: string) => {
      const value = String(values.get(name) ?? '').trim()
      return value ? Number(value) : null
    }
    if (!preset) {
      setError('Select a supported measurement parameter.')
      return
    }
    setConfiguring(deviceId)
    await onAction(async () => {
      await request(`/devices/${deviceId}/parameters`, {
        method: 'POST',
        body: JSON.stringify({
          name: preset.name,
          unit: preset.unit,
          expected_interval_minutes: Number(values.get('expected_interval_minutes')),
          consecutive_anomaly_threshold: numberOrNull('consecutive_anomaly_threshold'),
          persistence_window_size: numberOrNull('persistence_window_size'),
          persistence_anomaly_threshold: numberOrNull('persistence_anomaly_threshold'),
          analysis_method: String(values.get('analysis_method') || 'isolation_forest'),
          enabled: true,
        }),
      })
      await refreshDevice(deviceId)
      form.reset()
    }, 'Measurement parameter configured')
    setConfiguring(null)
  }

  return <>
    <div className="flex justify-end"><SourceTypeSelect value={sourceFilter} onChange={setSourceFilter} /></div>
    <PageHeading
      eyebrow="MANUAL EQUIPMENT READINGS"
      title="Measurements"
      description="Record actual readings for equipment assigned to you, check when each parameter is due, and follow its measurement history. No values are generated automatically."
      action={<button className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50" onClick={() => {
        setLoading(true)
        void Promise.all(visibleDevices.map((device) => refreshDevice(device.id)))
          .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : 'Unable to refresh equipment measurements'))
          .finally(() => setLoading(false))
      }}><RefreshCw size={14} /> Refresh</button>}
    />
    <div className="flex items-start gap-3 rounded-xl border border-sky-200 bg-sky-50 p-4 text-xs leading-5 text-sky-900">
      <Activity size={17} className="mt-0.5 shrink-0" />
      <p><strong>Manual collection:</strong> enter the actual observed values when they are due. The server records the submission date and time automatically; it cannot be entered or changed here. Configured intervals provide due-time reminders only and do not represent connected sensors or automatic measurement collection. Saving a reading starts equipment-specific analysis automatically.</p>
    </div>
    {error && <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">{error}</p>}
    {loading && <p className="text-xs text-slate-500">Loading assigned equipment and measurement history...</p>}
    {!loading && visibleDevices.length === 0 && <EmptyState
      icon={<Cpu size={20} />}
      title="No assigned equipment"
      body={userRole === 'administrator'
        ? 'No registered equipment is available yet.'
        : 'An administrator must assign equipment to your account before it appears here.'}
    />}
    {!loading && visibleDevices.map((device) => {
      const data = deviceData[device.id]
      const filteredMeasurements = data?.measurements.filter((measurement) =>
        matchesSourceType(sourceFilter, measurement.source_type),
      ) ?? []
      const enabledParameters = data?.parameters.filter((parameter) => parameter.enabled) ?? []
      const canConfigureParameters = userRole === 'administrator'
        || device.responsible_user_id === currentUserId
      return <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm" key={device.id}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-lg bg-forest-50 text-forest-700"><Cpu size={19} /></span>
            <div><h2 className="text-sm font-semibold text-slate-900">{device.name}</h2><p className="mt-1 text-[10px] text-slate-500">{device.device_code} · {device.device_type} · {device.location || 'Location not set'} · <SourceTypeBadge sourceType={device.source_type} /></p></div>
          </div>
          <span className="text-[10px] text-slate-500">Responsible recorder: {device.responsible_user_name ?? 'Not assigned'}</span>
        </div>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1.3fr)_minmax(280px,0.7fr)]">
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-slate-800">Required measurements and schedule</h3>
            {data?.parameters.length
              ? data.parameters.map((parameter) => {
                const due = parameter.next_due_at
                  ? new Date(parameter.next_due_at).getTime() <= currentTime
                  : false
                return <article className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-slate-50 p-3" key={parameter.id}>
                  <div>
                    <strong className="text-xs text-slate-800">{parameterLabel(parameter)}</strong>
                    <p className="mt-1 text-[10px] text-slate-500">Every {parameter.expected_interval_minutes} minutes · Last: {parameter.last_measurement_at ? new Date(parameter.last_measurement_at).toLocaleString() : 'Not recorded yet'} · Next due: {parameter.next_due_at ? new Date(parameter.next_due_at).toLocaleString() : 'Not scheduled'}</p>
                    <p className="mt-1 text-[10px] text-slate-500">Last analysis: {parameter.last_analysis_at ? `${parameter.last_analysis_status ?? 'unknown'} · ${new Date(parameter.last_analysis_at).toLocaleString()}` : 'No analysis yet'}</p>
                    <AlertEvaluation alert_rules={parameter.alert_rules} alert_priority={parameter.alert_priority} history_state={parameter.history_state} history_size={parameter.history_size} history_required={parameter.history_required} />
                    <p className="mt-1 text-[10px] text-slate-500">Alert overrides: consecutive {parameter.consecutive_anomaly_threshold ?? 'global default'} | persistence window {parameter.persistence_window_size ?? 'global default'} | anomalies required {parameter.persistence_anomaly_threshold ?? 'global default'}</p>
                  </div>
                  {parameter.enabled && due
                    ? <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[10px] font-semibold text-amber-800">Due now - record this value</span>
                    : <StatusBadge value={parameter.enabled ? parameter.measurement_status ?? 'scheduled' : 'disabled'} />}
                </article>
              })
              : <p className="rounded-lg bg-slate-50 p-3 text-[10px] leading-5 text-slate-500">{data
                ? canConfigureParameters
                  ? 'No measurement parameters are configured yet. Add the applicable readings below to enable value entry.'
                  : 'No measurement parameters are configured yet. Ask an administrator to configure the readings that apply to this device; value entry will appear here afterward.'
                : 'Measurement schedule unavailable.'}</p>}
            {canConfigureParameters && data && <form className="grid gap-2 rounded-lg border border-forest-100 bg-forest-50/40 p-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={(event) => void addParameter(device.id, event)}>
              <label className="grid gap-1 text-[10px] font-medium text-slate-600">Add a required measurement
                <select className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800" name="name" required defaultValue="">
                  <option value="" disabled>Choose an applicable parameter</option>
                  {parameterPresets.filter((preset) => !data.parameters.some((parameter) => parameter.name.trim().toLowerCase() === preset.name.toLowerCase())).map((preset) => <option value={preset.name} key={preset.name}>{preset.name} ({preset.unit})</option>)}
                </select>
              </label>
              <label className="grid gap-1 text-[10px] font-medium text-slate-600">Interval (minutes)
                <input className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800" name="expected_interval_minutes" type="number" min="1" max="525600" defaultValue="120" required />
              </label>
              <label className="grid gap-1 text-[10px] font-medium text-slate-600">Analysis method
                <select className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800" name="analysis_method" defaultValue="isolation_forest">
                  <option value="threshold">Configured limits</option>
                  <option value="z_score">Robust Z-score</option>
                  <option value="isolation_forest">Isolation Forest</option>
                  <option value="ensemble">IF + Z-score ensemble</option>
                </select>
              </label>
              <label className="grid gap-1 text-[10px] font-medium text-slate-600">Consecutive anomaly threshold (blank = default)
                <input className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800" name="consecutive_anomaly_threshold" type="number" min="1" max="100" />
              </label>
              <label className="grid gap-1 text-[10px] font-medium text-slate-600">Persistence window size (blank = default)
                <input className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800" name="persistence_window_size" type="number" min="1" max="100" />
              </label>
              <label className="grid gap-1 text-[10px] font-medium text-slate-600">Persistence anomaly threshold (blank = default)
                <input className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800" name="persistence_anomaly_threshold" type="number" min="1" max="100" />
              </label>
              <button className="inline-flex min-h-10 items-center justify-center gap-1 self-end rounded-lg bg-forest-600 px-3 py-2 text-xs font-semibold text-white hover:bg-forest-700 disabled:opacity-50" type="submit" disabled={configuring === device.id}>
                <Plus size={14} /> {configuring === device.id ? 'Adding...' : 'Add parameter'}
              </button>
              <p className="text-[10px] leading-4 text-slate-500 lg:col-span-3">Configure only the readings this equipment requires. The ensemble votes with Isolation Forest and robust Z-score after both meet their earlier-history requirements. Limits are checked first; persistence still controls alert escalation. Once added, the assigned recorder can enter each actual value here.</p>
            </form>}
          </div>
          <form className="grid content-start gap-3 rounded-lg border border-slate-100 p-3" onSubmit={(event) => void recordMeasurement(device.id, event)}>
            <h3 className="text-xs font-semibold text-slate-800">Record an actual reading</h3>
            {device.status !== 'active'
              ? <p className="text-[10px] text-amber-800">This equipment is not active, so new readings cannot be recorded.</p>
              : enabledParameters.length
                ? <>
                  <label className="grid gap-1.5 text-[10px] font-medium text-slate-600">Parameter
                    <select className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800" name="parameter_id" required defaultValue="">
                      <option value="" disabled>Select a parameter</option>
                      {enabledParameters.map((parameter) => <option value={parameter.id} key={parameter.id}>{parameterLabel(parameter)}</option>)}
                    </select>
                  </label>
                  <label className="grid gap-1.5 text-[10px] font-medium text-slate-600">Observed value
                    <input className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800" name="value" type="number" step="any" required />
                  </label>
                  <p className="text-[10px] text-slate-500">Submission date and time are assigned automatically by the server.</p>
                  <button className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-forest-600 px-4 py-2 text-xs font-semibold text-white hover:bg-forest-700 disabled:opacity-50" type="submit" disabled={saving === device.id}>
                    <Clock3 size={14} /> {saving === device.id ? 'Saving...' : 'Save and analyze'}
                  </button>
                </>
                : <p className="text-[10px] text-slate-500">{canConfigureParameters ? 'Add an applicable parameter above to start entering values.' : 'No enabled parameters are available. Ask an administrator to configure and enable the required readings.'}</p>}
          </form>
        </div>
        <div>
          <h3 className="mb-2 text-xs font-semibold text-slate-800">Recorded history · {filteredMeasurements.length.toLocaleString()} matching readings {data ? `(${data.total.toLocaleString()} total)` : ''}</h3>
          {filteredMeasurements.length
            ? <div className="overflow-x-auto rounded-lg border border-slate-100">
              <table className="w-full min-w-[700px] text-left text-[10px]">
                <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2">Parameter</th><th className="px-3 py-2">Value</th><th className="px-3 py-2">Measured</th><th className="px-3 py-2">Recorded by</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">Analysis</th><th className="px-3 py-2">Anomalies</th></tr></thead>
                <tbody className="divide-y divide-slate-100">{filteredMeasurements.map((measurement) => <tr key={measurement.id}>
                  <td className="px-3 py-2 text-slate-700">{measurement.parameter_name}</td>
                  <td className="px-3 py-2 font-semibold text-slate-800">{measurement.value} {measurement.unit}</td>
                  <td className="px-3 py-2 text-slate-600">{new Date(measurement.measured_at).toLocaleString()}</td>
                  <td className="px-3 py-2 text-slate-600">{measurement.recorder_name ?? (measurement.recorded_by ? `User #${measurement.recorded_by}` : 'Device API')}</td>
                  <td className="px-3 py-2"><SourceTypeBadge sourceType={measurement.source_type} /></td>
                  <td className="px-3 py-2 text-slate-600">{measurement.analysis_status ?? measurement.analysis_runs?.[0]?.status ?? 'Pending'}</td>
                  <td className="px-3 py-2 text-slate-600">{measurement.anomaly_count ?? measurement.anomaly_ids?.length ?? 0}</td>
                </tr>)}</tbody>
              </table>
              {data.measurements.length < data.total && <button className="w-full border-t border-slate-100 px-3 py-2 text-xs font-semibold text-forest-700 hover:bg-forest-50 disabled:opacity-50" disabled={loadingMore === device.id} onClick={() => void loadMore(device.id)}>{loadingMore === device.id ? 'Loading...' : 'Load more history'}</button>}
            </div>
            : <p className="rounded-lg bg-slate-50 p-4 text-center text-[10px] text-slate-500">{data?.total ? 'No readings match this source filter on the loaded page.' : 'No measurement readings recorded for this equipment yet.'}</p>}
        </div>
      </section>
    })}
  </>
}
