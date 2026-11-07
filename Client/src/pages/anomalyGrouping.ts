import type { Anomaly } from '../types'

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
