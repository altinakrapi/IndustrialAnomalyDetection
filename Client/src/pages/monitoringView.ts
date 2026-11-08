import type { AnomalyTimeBucket, GroupedAnomalyDevice } from '../types'
import type { Analysis, MonitoringDashboardData } from '../types/workflow'

export type MonitoringDevice = MonitoringDashboardData['devices'][number]
export type MonitoringDeviceStatus = 'ok' | 'overdue' | 'not_reporting' | 'no_data'
export type MonitoringViewState = 'loading' | 'error' | 'empty' | 'data'

export type MonitoringAnomalyGroup = {
  deviceId: number
  deviceName: string
  deviceCode: string
  sourceTypes: string[]
  bucket: AnomalyTimeBucket
}

export type OverdueMeasurementDetail = {
  deviceId: number
  deviceName: string
  parameterName: string
  sourceType: string
}

export function getMonitoringViewState(
  loading: boolean,
  error: string,
  dashboard: MonitoringDashboardData | null,
): MonitoringViewState {
  if (loading && !dashboard) return 'loading'
  if (error && !dashboard) return 'error'
  if (!dashboard) return 'loading'
  return dashboard.devices.some((device) => device.status !== 'archived') ? 'data' : 'empty'
}

export function deviceHasData(device: MonitoringDevice): boolean {
  return device.latest_measurements.length > 0
    || device.measurement_schedule.some((item) => item.last_reading_received_at !== null)
}

export function getDeviceStatus(device: MonitoringDevice): MonitoringDeviceStatus {
  if (!deviceHasData(device)) return 'no_data'
  if (device.measurement_schedule.some((item) => item.stale_data_status === 'stopped_reporting')) {
    return 'not_reporting'
  }
  if (device.overdue_measurements > 0 || device.measurement_schedule.some((item) => item.status === 'overdue')) {
    return 'overdue'
  }
  return 'ok'
}

export function getDeviceCounts(devices: MonitoringDevice[]) {
  const counts = { ok: 0, attention: 0, notReporting: 0, noData: 0 }
  for (const device of devices.filter((item) => item.status !== 'archived')) {
    const status = getDeviceStatus(device)
    if (status === 'no_data') counts.noData += 1
    else if (status === 'not_reporting') counts.notReporting += 1
    else if (status === 'overdue' || device.open_anomalies > 0) counts.attention += 1
    else counts.ok += 1
  }
  return counts
}

export function getOverdueMeasurementDetails(devices: MonitoringDevice[]): OverdueMeasurementDetail[] {
  return getDataDevices(devices).flatMap((device) => device.measurement_schedule
    .filter((item) => item.enabled && item.status === 'overdue')
    .map((item) => ({
      deviceId: device.id,
      deviceName: device.name,
      parameterName: item.parameter_name,
      sourceType: device.source_type ?? 'manual',
    })))
}

export function getDevicesNeedingAttention(devices: MonitoringDevice[]): MonitoringDevice[] {
  return devices.filter((device) => {
    if (device.status === 'archived') return false
    return device.overdue_measurements > 0
      || device.measurement_schedule.some((item) => (
        item.status === 'overdue' || item.stale_data_status === 'stopped_reporting'
      ))
      || device.open_anomalies > 0
  })
}

export function getDataDevices(devices: MonitoringDevice[]): MonitoringDevice[] {
  return devices.filter((device) => device.status !== 'archived' && deviceHasData(device))
}

export function getDevicesWithoutData(devices: MonitoringDevice[]): MonitoringDevice[] {
  return devices.filter((device) => device.status !== 'archived' && !deviceHasData(device))
}

export function buildRecentAnomalyGroups(
  devices: GroupedAnomalyDevice[],
  bucketsByDevice: Record<number, AnomalyTimeBucket[]>,
  limit = 5,
): MonitoringAnomalyGroup[] {
  return devices.flatMap((device) => (
    (bucketsByDevice[device.device_id] ?? []).map((bucket) => ({
      deviceId: device.device_id,
      deviceName: device.device_name,
      deviceCode: device.device_code,
      sourceTypes: device.source_types,
      bucket,
    }))
  )).sort((left, right) => (
    Date.parse(right.bucket.bucket_start) - Date.parse(left.bucket.bucket_start)
  )).slice(0, limit)
}

export function getRelevantAnalysisRuns(analyses: Analysis[], limit = 5): Analysis[] {
  return analyses
    .filter((analysis) => analysis.status === 'failed' || analysis.execution_type === 'scheduled')
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
    .slice(0, limit)
}

export function getLastHourAnalysisSummary(analyses: Analysis[]) {
  const durations = analyses.flatMap((analysis) => {
    const duration = analysis.duration_seconds
      ?? (analysis.started_at && analysis.completed_at
        ? Math.max(0, (Date.parse(analysis.completed_at) - Date.parse(analysis.started_at)) / 1000)
        : null)
    return duration !== null && Number.isFinite(duration) ? [duration] : []
  })
  return {
    total: analyses.length,
    failed: analyses.filter((analysis) => analysis.status === 'failed').length,
    durationCount: durations.length,
    averageDurationSeconds: durations.length
      ? durations.reduce((sum, duration) => sum + duration, 0) / durations.length
      : null,
  }
}

export function makeOperationalAnomalyParams(startDate?: Date): URLSearchParams {
  const params = new URLSearchParams({
    source_type: 'equipment',
    include_test_data: 'true',
    include_dataset: 'false',
  })
  if (startDate) params.set('start_date', startDate.toISOString())
  return params
}

export function getUniqueMeasurementWindowCount(summary: { measurement_total: number }): number {
  return summary.measurement_total
}

export function formatMonitoringRequestError(error: unknown): string {
  const record = error !== null && typeof error === 'object'
    ? error as { message?: unknown; status?: unknown }
    : null
  const message = error instanceof Error
    ? error.message
    : typeof record?.message === 'string'
      ? record.message
      : 'Unable to load monitoring data'
  return typeof record?.status === 'number'
    ? `Monitoring data could not be loaded (HTTP ${record.status}): ${message}`
    : `Monitoring data could not be loaded (HTTP status unavailable): ${message}`
}