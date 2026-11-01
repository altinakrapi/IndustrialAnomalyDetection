export type StoredMetricSet = Record<string, unknown> & {
  confusion_matrix?: Record<string, number>
  roc_curve?: Array<{ false_positive_rate: number; true_positive_rate: number }>
  precision_recall_curve?: Array<{ recall: number; precision: number }>
}

export type StoredDetectorRun = {
  run_id: string
  mode: string
  metrics?: StoredMetricSet
  detection_delays?: Array<{
    detected?: boolean
    delay_readings?: number | null
    delay_seconds?: number | null
  }>
}

export type StoredAggregateMetric = {
  mean: number | null
  spread_stddev: number | null
  run_count: number
}

export type StoredDetector = {
  mode_by_run?: Record<string, string>
  runs?: StoredDetectorRun[]
  aggregate?: Record<string, StoredAggregateMetric>
  per_fault_type_aggregate?: Record<string, Record<string, unknown>>
  detection_delay_aggregate?: Record<string, unknown>
}

export type EvaluationResultFixture = {
  result: {
    detectors: Record<string, StoredDetector>
  }
}

export const metricValue = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

export function perRunMetric(run: StoredDetectorRun, metric: string): number | null {
  if (!run.metrics) return null
  return metricValue(run.metrics[metric])
}

export function perRunDetectionDelay(run: StoredDetectorRun): number | null {
  const values = (run.detection_delays ?? [])
    .filter((item) => item.detected !== false && metricValue(item.delay_readings) !== null)
    .map((item) => metricValue(item.delay_readings) as number)
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
}

export function formatAggregateMetric(
  aggregate: StoredAggregateMetric | null | undefined,
): string {
  if (!aggregate || metricValue(aggregate.mean) === null) return 'not stored'
  const spread = metricValue(aggregate.spread_stddev)
  return `${aggregate.mean!.toFixed(3)} ${spread === null ? '\u00b1 not stored' : `\u00b1 ${spread.toFixed(3)}`} (n=${aggregate.run_count ?? 0})`
}
