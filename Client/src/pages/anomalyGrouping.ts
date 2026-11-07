import type { Anomaly, AnomalyTimeBucket } from '../types'

export type ParameterAnomalyGroup = {
  parameterId: number | null
  parameterName: string
  anomalies: Anomaly[]
}

export function groupAnomaliesByParameter(items: Anomaly[]): ParameterAnomalyGroup[] {
  const grouped = new Map<string, ParameterAnomalyGroup>()
  for (const anomaly of items) {
    const key = anomaly.parameter_id === null
      ? 'unassigned'
      : String(anomaly.parameter_id)
    const group = grouped.get(key) ?? {
      parameterId: anomaly.parameter_id,
      parameterName: anomaly.parameter_name ?? 'Equipment parameter',
      anomalies: [],
    }
    group.anomalies.push(anomaly)
    grouped.set(key, group)
  }
  return [...grouped.values()]
    .map((group) => ({
      ...group,
      anomalies: [...group.anomalies].sort((a, b) => (
        new Date(b.detected_at).getTime()
        - new Date(a.detected_at).getTime()
      )),
    }))
    .sort((a, b) => (
      new Date(b.anomalies[0]?.detected_at ?? 0).getTime()
      - new Date(a.anomalies[0]?.detected_at ?? 0).getTime()
    ))
}

export function groupedDeviceCountTotal<
  T extends { anomaly_count: number | string },
>(
  devices: T[],
): number {
  return devices.reduce(
    (total, device) => total + Number(device.anomaly_count),
    0,
  )
}

export type ParameterBucketGroup = {
  parameterId: number
  parameterName: string
  buckets: AnomalyTimeBucket[]
}

export function formatBucketRange(
  bucketStart: string,
  bucketMinutes: number,
  timeZone: string,
): string {
  const startParts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(bucketStart))
  const parts = Object.fromEntries(
    startParts
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value]),
  )
  const wallClockStart = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
  )
  const wallClockEnd = new Date(wallClockStart + (bucketMinutes - 1) * 60_000)
  const formatDate = (date: Date) => new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date)
  const formatTime = (date: Date) => new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date)
  const startDate = new Date(wallClockStart)
  const dateLabel = formatDate(startDate)
  const endDateLabel = formatDate(wallClockEnd)
  const dateRange = dateLabel === endDateLabel
    ? dateLabel
    : `${dateLabel} – ${endDateLabel}`
  return `${dateRange}, ${formatTime(startDate)} – ${formatTime(wallClockEnd)}`
}

export function groupBucketsByParameter(
  buckets: AnomalyTimeBucket[],
): ParameterBucketGroup[] {
  const grouped = new Map<number, ParameterBucketGroup>()
  for (const bucket of buckets) {
    const group = grouped.get(bucket.parameter_id) ?? {
      parameterId: bucket.parameter_id,
      parameterName: bucket.parameter_name,
      buckets: [],
    }
    group.buckets.push(bucket)
    grouped.set(bucket.parameter_id, group)
  }
  return [...grouped.values()]
    .map((group) => ({
      ...group,
      buckets: [...group.buckets].sort(
        (a, b) => Date.parse(b.bucket_start) - Date.parse(a.bucket_start),
      ),
    }))
    .sort((a, b) => (
      Date.parse(b.buckets[0]?.bucket_start ?? '')
      - Date.parse(a.buckets[0]?.bucket_start ?? '')
    ))
}

export function reconcileGroupedDeviceCounts<
  T extends { anomaly_count: number | string },
>(
  devices: T[],
  groupedTotal: number,
  flatTotal?: number,
): { devices: T[]; warning: string } {
  const deviceTotal = groupedDeviceCountTotal(devices)
  const countsMatch = deviceTotal === groupedTotal
    && (flatTotal === undefined || deviceTotal === flatTotal)

  return {
    devices,
    warning: countsMatch
      ? ''
      : `Showing ${deviceTotal.toLocaleString()} device anomaly rows; grouped total is ${groupedTotal.toLocaleString()}${flatTotal === undefined ? '' : ` and flat-list total is ${flatTotal.toLocaleString()}`}. Counts may differ briefly while new detections are recorded.`,
  }
}

export function groupedAnomalyRequestError(error: unknown): string {
  const record = error !== null && typeof error === 'object'
    ? error as { message?: unknown; status?: unknown }
    : null
  const message = error instanceof Error
    ? error.message
    : typeof record?.message === 'string'
      ? record.message
      : 'Unable to load grouped anomalies'
  return typeof record?.status === 'number'
    ? `Unable to load grouped anomalies (HTTP ${record.status}): ${message}`
    : message
}
