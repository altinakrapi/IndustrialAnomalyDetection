import { evaluationStrings as text } from './evaluationStrings'

type EvaluationRecord = {
  id: number
  purpose: string
  created_at: string
  status?: string
}

type ValidationRecord = {
  id: number
  status: string
  created_at: string
}

type MetricAggregate = {
  mean?: unknown
  spread_stddev?: unknown
  run_count?: unknown
}

type DetectorEvidence = {
  aggregate?: Record<string, MetricAggregate>
  per_fault_type_aggregate?: Record<string, Record<string, MetricAggregate>>
  detection_delay_aggregate?: Record<string, { delay_readings?: MetricAggregate }>
}

const numeric = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

function latest<T extends { created_at: string }>(records: T[]): T | null {
  return [...records].sort((left, right) =>
    Date.parse(right.created_at) - Date.parse(left.created_at),
  )[0] ?? null
}

export function isEvaluationEditor(role: string): boolean {
  return role === 'administrator' || role === 'data_analyst'
}

export function defaultEvaluationId(records: EvaluationRecord[]): number | null {
  const completed = records.filter((record) => !record.status || record.status === 'completed')
  const finalEvaluation = latest(completed.filter((record) => record.purpose === 'evaluation'))
  const development = latest(completed.filter((record) => record.purpose === 'development'))
  return (finalEvaluation ?? development)?.id ?? null
}

export function defaultValidationId(records: ValidationRecord[]): number | null {
  return latest(records.filter((record) => record.status === 'completed'))?.id ?? null
}

export function splitValidationAttempts<T extends ValidationRecord>(records: T[]) {
  return {
    completed: records.filter((record) => record.status === 'completed'),
    attempts: records.filter((record) => record.status === 'failed' || record.status === 'cancelled'),
  }
}

export function simulatorSummary(detectors: Record<string, DetectorEvidence>): string[] {
  const entries = Object.entries(detectors)
  const bestF1 = entries
    .map(([detector, evidence]) => ({
      detector,
      f1: numeric(evidence.aggregate?.f1?.mean),
      falseAlarm: numeric(evidence.aggregate?.false_alarm_rate?.mean),
    }))
    .filter((entry): entry is typeof entry & { f1: number } => entry.f1 !== null)
    .sort((left, right) => right.f1 - left.f1)[0]

  const lowestFalseAlarm = entries
    .map(([detector, evidence]) => ({
      detector,
      value: numeric(evidence.aggregate?.false_alarm_rate?.mean),
    }))
    .filter((entry): entry is typeof entry & { value: number } => entry.value !== null)
    .sort((left, right) => left.value - right.value)[0]

  const delays = entries.flatMap(([detector, evidence]) =>
    Object.entries(evidence.detection_delay_aggregate ?? {}).map(([fault, value]) => ({
      detector,
      fault,
      mean: numeric(value.delay_readings?.mean),
    })),
  ).filter((entry): entry is typeof entry & { mean: number } => entry.mean !== null)
  const fastest = delays.sort((left, right) => left.mean - right.mean)[0]

  const faultRecalls = entries.flatMap(([detector, evidence]) =>
    Object.entries(evidence.per_fault_type_aggregate ?? {}).map(([fault, metrics]) => ({
      detector,
      fault,
      recall: numeric(metrics.recall?.mean),
    })),
  ).filter((entry): entry is typeof entry & { recall: number } => entry.recall !== null)
  const hardest = faultRecalls.sort((left, right) => left.recall - right.recall)[0]

  return [
    bestF1
      ? text.summaryBestF1(bestF1.detector, bestF1.f1, bestF1.falseAlarm)
      : text.summaryNoF1,
    lowestFalseAlarm
      ? text.summaryLowestFalseAlarm(lowestFalseAlarm.detector, lowestFalseAlarm.value)
      : text.summaryNoFalseAlarm,
    fastest
      ? text.summaryFastest(fastest.detector, fastest.fault, fastest.mean)
      : text.summaryNoDelay,
    hardest
      ? text.summaryHardest(hardest.detector, hardest.fault, hardest.recall)
      : text.summaryNoHardest,
  ]
}

export function heatmapValue(
  metrics: Record<string, MetricAggregate> | undefined,
  metric: 'recall' | 'f1',
): number | null {
  return numeric(metrics?.[metric]?.mean)
}

export function validationSummary(
  detectors: Record<string, { bearings?: Record<string, { snapshot_aggregation?: { lead_time_to_test_end_hours?: unknown; sustained_alert?: { detected?: boolean } }; snapshot_burn_in?: { unfiltered_view?: { lead_time_to_test_end_hours?: unknown; sustained_alert?: { detected?: boolean } } } }> }>,
  failedBearing: number | null,
  filtered = true,
): string[] {
  if (failedBearing === null) return [text.noFailedBearing]
  const leadTime = (row: { snapshot_aggregation?: { lead_time_to_test_end_hours?: unknown; sustained_alert?: { detected?: boolean } }; snapshot_burn_in?: { unfiltered_view?: { lead_time_to_test_end_hours?: unknown; sustained_alert?: { detected?: boolean } } } }) => {
    const view = filtered ? row.snapshot_aggregation : row.snapshot_burn_in?.unfiltered_view
    return view?.sustained_alert?.detected ? numeric(view.lead_time_to_test_end_hours) : null
  }
  return Object.entries(detectors).map(([detector, evidence]) => {
    const failedLead = evidence.bearings?.[String(failedBearing)]
      ? leadTime(evidence.bearings[String(failedBearing)])
      : null
    const healthy = Object.entries(evidence.bearings ?? {})
      .filter(([bearing]) => Number(bearing) !== failedBearing)
      .map(([, row]) => leadTime(row))
      .filter((lead): lead is number => lead !== null)
    return text.validationSummaryLine(
      detector,
      failedBearing,
      failedLead,
      healthy.length ? Math.min(...healthy) : null,
      healthy.length ? Math.max(...healthy) : null,
    )
  })
}

export function evaluationState(loading: boolean, hasError: boolean, hasSelection: boolean) {
  if (loading) return 'loading'
  if (hasError) return 'error'
  return hasSelection ? 'ready' : 'empty'
}
