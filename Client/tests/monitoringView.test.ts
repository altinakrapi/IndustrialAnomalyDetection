import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import type { AnomalyTimeBucket, GroupedAnomalyDevice } from '../src/types.ts'
import type { Analysis, MonitoringDashboardData } from '../src/types/workflow.ts'
import { MonitoringPageState } from '../src/pages/MonitoringPageState.ts'
import { MonitoringScheduleSettings } from '../src/pages/MonitoringScheduleSettings.ts'
import {
  buildRecentAnomalyGroups,
  getDataDevices,
  getDeviceCounts,
  getDevicesNeedingAttention,
  getDevicesWithoutData,
  getLastHourAnalysisSummary,
  getMonitoringViewState,
  getOverdueMeasurementDetails,
  getRelevantAnalysisRuns,
  getUniqueMeasurementWindowCount,
  formatMonitoringRequestError,
  makeOperationalAnomalyParams,
  type MonitoringDevice,
} from '../src/pages/monitoringView.ts'

function device(overrides: Partial<MonitoringDevice> = {}): MonitoringDevice {
  return {
    id: 1,
    name: 'Press 1',
    device_code: 'PRESS-1',
    device_type: 'press',
    source_type: 'sensor',
    status: 'active',
    health: 'healthy',
    dataset_count: 0,
    open_anomalies: 0,
    overdue_measurements: 0,
    measurement_schedule: [],
    latest_measurements: [{
      id: 1,
      parameter_name: 'Vibration',
      value: 1.2,
      unit: 'mm/s',
      measured_at: '2026-11-08T10:00:00Z',
      source_type: 'sensor',
    }],
    data_source: 'sensor',
    vibration_trend: null,
    measurement_trend: null,
    last_analysis: null,
    ...overrides,
  }
}

function dashboard(devices: MonitoringDevice[]): MonitoringDashboardData {
  return { devices } as MonitoringDashboardData
}

function analysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    id: 1,
    dataset_id: null,
    model_config_id: null,
    dataset_name: null,
    source_type: 'equipment',
    equipment_measurement_id: null,
    device_name: 'Press 1',
    model_name: 'Detector',
    status: 'completed',
    execution_type: 'measurement',
    total_records: 1,
    anomalies_found: 0,
    metrics: {},
    created_at: '2026-11-08T10:00:00Z',
    ...overrides,
  }
}

function groupedDevice(id: number): GroupedAnomalyDevice {
  return {
    device_id: id,
    device_name: `Press ${id}`,
    device_code: `PRESS-${id}`,
    source_types: ['sensor'],
    anomaly_count: 8,
    measurement_count: 5,
    highest_severity: 'medium',
    latest_at: '2026-11-08T10:00:00Z',
    not_reviewed_count: 3,
  }
}

function bucket(minute: number): AnomalyTimeBucket {
  return {
    bucket_start: `2026-11-08T10:${String(minute).padStart(2, '0')}:00Z`,
    parameter_id: 1,
    parameter_name: 'Vibration',
    anomaly_count: 2,
    minimum_value: 1.1,
    maximum_value: 2.4,
    unit: 'mm/s',
    highest_severity: 'medium',
    detectors: ['z_score', 'flatline'],
    not_reviewed_count: 1,
    review_status: 'new',
  }
}

describe('monitoring page view model', () => {
  it('selects loading, error, empty, and data states', () => {
    assert.equal(getMonitoringViewState(true, '', null), 'loading')
    assert.equal(getMonitoringViewState(false, 'HTTP 503', null), 'error')
    assert.equal(getMonitoringViewState(false, '', dashboard([])), 'empty')
    assert.equal(getMonitoringViewState(false, '', dashboard([device()])), 'data')

    const loadingMarkup = renderToStaticMarkup(createElement(MonitoringPageState, {
      state: 'loading', error: '', onRetry: () => undefined,
    }))
    const errorMarkup = renderToStaticMarkup(createElement(MonitoringPageState, {
      state: 'error', error: 'Monitoring data could not be loaded (HTTP 503)', onRetry: () => undefined,
    }))
    const emptyMarkup = renderToStaticMarkup(createElement(MonitoringPageState, {
      state: 'empty', error: '', onRetry: () => undefined,
    }))
    const dataMarkup = renderToStaticMarkup(createElement(MonitoringPageState, {
      state: 'data', error: '', onRetry: () => undefined,
    }, createElement('p', null, 'Device health data')))

    assert.match(loadingMarkup, /Loading monitoring data/)
    assert.match(errorMarkup, /HTTP 503/)
    assert.match(errorMarkup, /Retry/)
    assert.match(emptyMarkup, /No registered devices/)
    assert.match(dataMarkup, /Device health data/)
  })

  it('partitions registered devices into mutually exclusive health counts', () => {
    const devices = [
      device(),
      device({ id: 2, overdue_measurements: 1 }),
      device({ id: 3, measurement_schedule: [{
        parameter_id: 3,
        parameter_name: 'Temperature',
        enabled: true,
        expected_interval_minutes: 10,
        expected_interval_seconds: null,
        last_measurement_at: null,
        last_reading_received_at: '2026-11-08T09:00:00Z',
        next_due_at: null,
        status: 'scheduled',
        stale_data_status: 'stopped_reporting',
        stale_data_episode: null,
      }] }),
      device({ id: 4, latest_measurements: [], data_source: 'none' }),
      device({ id: 5, status: 'archived' }),
      device({ id: 6, open_anomalies: 1 }),
    ]
    const counts = getDeviceCounts(devices)

    assert.deepEqual(counts, { ok: 1, attention: 2, notReporting: 1, noData: 1 })
    assert.equal(counts.ok + counts.attention + counts.notReporting + counts.noData, 5)
    assert.deepEqual(getDataDevices(devices).map((item) => item.id), [1, 2, 3, 6])
    assert.deepEqual(getDevicesWithoutData(devices).map((item) => item.id), [4])
  })

  it('lists only overdue, stopped-reporting, or alerting devices for attention', () => {
    const devices = [
      device(),
      device({ id: 2, overdue_measurements: 1 }),
      device({ id: 3, open_anomalies: 2 }),
      device({ id: 4, status: 'archived', overdue_measurements: 1 }),
    ]

    assert.deepEqual(getDevicesNeedingAttention(devices).map((item) => item.id), [2, 3])
  })

  it('lists overdue parameters only for devices with data and excludes archived devices', () => {
    const overdueParameter = {
      parameter_id: 7,
      parameter_name: 'Temperature',
      enabled: true,
      expected_interval_minutes: 20,
      expected_interval_seconds: null,
      last_measurement_at: '2026-11-08T09:00:00Z',
      last_reading_received_at: '2026-11-08T09:00:00Z',
      next_due_at: '2026-11-08T09:20:00Z',
      status: 'overdue',
      stale_data_status: 'monitoring' as const,
      stale_data_episode: null,
    }
    const result = getOverdueMeasurementDetails([
      device({ id: 1, measurement_schedule: [overdueParameter] }),
      device({ id: 2, latest_measurements: [], measurement_schedule: [{ ...overdueParameter, last_reading_received_at: null }] }),
      device({ id: 3, status: 'archived', measurement_schedule: [overdueParameter] }),
    ])

    assert.deepEqual(result.map((item) => [item.deviceId, item.parameterName]), [[1, 'Temperature']])
  })

  it('uses the grouped Anomalies measurement total, with its operational filters', () => {
    const params = makeOperationalAnomalyParams()
    assert.equal(params.toString(), 'source_type=equipment&include_test_data=true&include_dataset=false')
    assert.equal(getUniqueMeasurementWindowCount({ measurement_total: 779 }), 779)
  })

  it('distinguishes HTTP failures from network errors', () => {
    assert.equal(
      formatMonitoringRequestError({ status: 503, message: 'service unavailable' }),
      'Monitoring data could not be loaded (HTTP 503): service unavailable',
    )
    assert.equal(
      formatMonitoringRequestError(new Error('connection lost')),
      'Monitoring data could not be loaded (HTTP status unavailable): connection lost',
    )
  })

  it('limits recent groups to five and serializes nested data without object coercion', () => {
    const groups = buildRecentAnomalyGroups(
      [groupedDevice(1)],
      { 1: [bucket(1), bucket(2), bucket(3), bucket(4), bucket(5), bucket(6)] },
    )

    assert.equal(groups.length, 5)
    assert.doesNotMatch(JSON.stringify(groups), /\[object Object\]/)
  })

  it('summarizes the supplied last-hour history and omits per-measurement runs', () => {
    const runs = [
      analysis({ id: 1, duration_seconds: 10 }),
      analysis({ id: 2, status: 'failed', execution_type: 'manual', duration_seconds: 20 }),
      analysis({ id: 3, execution_type: 'scheduled', duration_seconds: 30 }),
      analysis({ id: 4, execution_type: 'scheduled', created_at: '2026-11-08T11:00:00Z' }),
    ]

    assert.deepEqual(getLastHourAnalysisSummary(runs), {
      total: 4,
      failed: 1,
      durationCount: 3,
      averageDurationSeconds: 20,
    })
    assert.deepEqual(getRelevantAnalysisRuns(runs).map((item) => item.id), [4, 2, 3])
  })

  it('renders scheduler controls only for administrators with existing bounds', () => {
    const schedule = {
      enabled: true,
      interval_minutes: 60,
      simulation_enabled: false,
      simulation_batch_size: 100,
      execution_status: 'idle' as const,
      next_run_at: null,
      last_started_at: null,
      last_completed_at: null,
      last_error: null,
    }
    const props = {
      schedule,
      loading: false,
      saving: false,
      error: '',
      message: '',
      onRetry: () => undefined,
      onChange: () => undefined,
      onSubmit: () => undefined,
    }
    const adminMarkup = renderToStaticMarkup(createElement(MonitoringScheduleSettings, {
      ...props,
      isAdmin: true,
    }))
    const analystMarkup = renderToStaticMarkup(createElement(MonitoringScheduleSettings, {
      ...props,
      isAdmin: false,
    }))

    assert.match(adminMarkup, /Scheduled analysis \(NASA IMS datasets and monitoring checks\)/)
    assert.match(adminMarkup, /Live equipment readings are analysed as they arrive/)
    assert.match(adminMarkup, /min="1" max="1440"/)
    assert.match(adminMarkup, /min="2" max="10000"/)
    assert.match(adminMarkup, /Execution status/)
    assert.match(adminMarkup, /Next run/)
    assert.match(adminMarkup, /Last started/)
    assert.match(adminMarkup, /Last completed/)
    assert.doesNotMatch(adminMarkup, /Run analysis now|monitoring\/run/)
    assert.equal(analystMarkup, '')
  })

  it('contains no cycle caller, button, or queued toast on the Monitoring page', async () => {
    const { readFile } = await import('node:fs/promises')
    const source = await readFile(new URL('../src/pages/MonitoringPage.tsx', import.meta.url), 'utf8')
    assert.doesNotMatch(source, /\/monitoring\/run|Automatic analysis queued|Run analysis now|runNow/)
  })
})