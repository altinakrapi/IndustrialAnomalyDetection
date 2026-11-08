import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  defaultEvaluationId,
  defaultValidationId,
  evaluationState,
  heatmapValue,
  isEvaluationEditor,
  simulatorSummary,
  splitValidationAttempts,
  validationSummary,
} from './evaluationFlow.ts'

test('default simulator selection prefers the latest completed evaluation-purpose record', () => {
  assert.equal(defaultEvaluationId([
    { id: 1, purpose: 'evaluation', created_at: '2025-01-01' },
    { id: 2, purpose: 'development', created_at: '2025-03-01' },
    { id: 3, purpose: 'evaluation', created_at: '2025-02-01' },
    { id: 4, purpose: 'evaluation', status: 'failed', created_at: '2025-04-01' },
  ]), 3)
})

test('default simulator selection falls back to the latest development record', () => {
  assert.equal(defaultEvaluationId([
    { id: 1, purpose: 'development', created_at: '2025-01-01' },
    { id: 2, purpose: 'development', created_at: '2025-02-01' },
  ]), 2)
  assert.equal(defaultEvaluationId([]), null)
})

test('default NASA selection ignores failed and cancelled validations', () => {
  assert.equal(defaultValidationId([
    { id: 1, status: 'completed', created_at: '2025-01-01' },
    { id: 2, status: 'failed', created_at: '2025-03-01' },
    { id: 3, status: 'completed', created_at: '2025-02-01' },
  ]), 3)
  assert.equal(defaultValidationId([{ id: 4, status: 'cancelled', created_at: '2025-04-01' }]), null)
})

test('summary uses only stored values and describes missing evidence without inferring metrics', () => {
  const lines = simulatorSummary({
    stored_detector: {
      aggregate: {
        f1: { mean: 0.8 },
        false_alarm_rate: { mean: 0.1 },
      },
      detection_delay_aggregate: { outer_race: { delay_readings: { mean: 3 } } },
      per_fault_type_aggregate: { outer_race: { recall: { mean: 0.4 } } },
    },
  })
  assert.equal(lines.length, 4)
  assert.match(lines[0], /0\.800/)
  assert.match(lines[0], /0\.100/)
  assert.match(lines[2], /3\.000 readings/)
  assert.match(lines[3], /0\.400/)

  const missing = simulatorSummary({ detector: {} })
  assert.equal(missing.length, 4)
  assert.ok(missing.every((line) => /No .* stored/.test(line)))
  assert.match(simulatorSummary({})[0], /No aggregate F1/)
})

test('single-run summary reports available values without fabricating a spread', () => {
  const lines = simulatorSummary({
    only_run: {
      aggregate: { f1: { mean: 0.6 } },
      per_fault_type_aggregate: { fault: { recall: { mean: 0.5 } } },
    },
  })
  assert.match(lines[0], /0\.600/)
  assert.match(lines[3], /0\.500/)
  assert.doesNotMatch(lines.join(' '), /spread|standard deviation/i)
})

test('heatmap lookup returns the selected stored metric only', () => {
  const metrics = { recall: { mean: 0.25 }, f1: { mean: 0.75 } }
  assert.equal(heatmapValue(metrics, 'recall'), 0.25)
  assert.equal(heatmapValue(metrics, 'f1'), 0.75)
  assert.equal(heatmapValue(undefined, 'recall'), null)
})

test('NASA summary uses stored alert and lead-time values for the selected burn-in view', () => {
  const detectors = {
    detector: {
      bearings: {
        1: {
          snapshot_aggregation: { sustained_alert: { detected: true }, lead_time_to_test_end_hours: 12 },
          snapshot_burn_in: { unfiltered_view: { sustained_alert: { detected: true }, lead_time_to_test_end_hours: 15 } },
        },
        2: {
          snapshot_aggregation: { sustained_alert: { detected: true }, lead_time_to_test_end_hours: 2 },
          snapshot_burn_in: { unfiltered_view: { sustained_alert: { detected: true }, lead_time_to_test_end_hours: 4 } },
        },
        3: {
          snapshot_aggregation: { sustained_alert: { detected: false }, lead_time_to_test_end_hours: null },
          snapshot_burn_in: { unfiltered_view: { sustained_alert: { detected: false }, lead_time_to_test_end_hours: null } },
        },
      },
    },
  }
  assert.match(validationSummary(detectors, 1, true)[0], /12\.000 hours/)
  assert.match(validationSummary(detectors, 1, true)[0], /2\.000 to 2\.000 hours/)
  assert.match(validationSummary(detectors, 1, false)[0], /15\.000 hours/)
  assert.match(validationSummary(detectors, 1, false)[0], /4\.000 to 4\.000 hours/)
  assert.match(validationSummary(detectors, null)[0], /No documented failed-bearing fact/)
})

test('failed and cancelled validations are separated from completed records', () => {
  const groups = splitValidationAttempts([
    { id: 1, status: 'completed', created_at: '2025-01-01' },
    { id: 2, status: 'failed', created_at: '2025-01-02' },
    { id: 3, status: 'cancelled', created_at: '2025-01-03' },
  ])
  assert.deepEqual(groups.completed.map(({ id }) => id), [1])
  assert.deepEqual(groups.attempts.map(({ id }) => id), [2, 3])
  const source = readFileSync(new URL('./EvaluationPage.tsx', import.meta.url), 'utf8')
  assert.match(source, /<details className="rounded-xl border border-slate-200 p-4">\s*<summary[^>]*>[\s\S]*text\.otherAttempts/)
  assert.match(source, /validationGroups\.attempts\.map/)
})

test('role variants preserve read-only maintenance access and editor controls', () => {
  assert.equal(isEvaluationEditor('administrator'), true)
  assert.equal(isEvaluationEditor('data_analyst'), true)
  assert.equal(isEvaluationEditor('maintenance_technician'), false)
  const source = readFileSync(new URL('./EvaluationPage.tsx', import.meta.url), 'utf8')
  assert.match(source, /if \(!editor\) return/)
  assert.match(source, /\{editor && <details/)
})

test('loading, error, empty, and ready state variants are distinct', () => {
  assert.equal(evaluationState(true, false, false), 'loading')
  assert.equal(evaluationState(false, true, false), 'error')
  assert.equal(evaluationState(false, false, false), 'empty')
  assert.equal(evaluationState(false, false, true), 'ready')
})

test('developer-facing metric implementation wording is not shown on the page', () => {
  const source = readFileSync(new URL('./EvaluationPage.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /Per-run metrics \(stored in each run|metrics object|developer-facing/i)
})
