import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import type { Anomaly } from '../src/types.ts'
import type { AnomalyTimeBucket } from '../src/types.ts'
import {
  groupAnomaliesByParameter,
  groupBucketsByParameter,
  formatBucketRange,
  groupedAnomalyRequestError,
  groupedDeviceCountTotal,
  reconcileGroupedDeviceCounts,
} from '../src/pages/anomalyGrouping.ts'

function anomaly(
  id: number,
  parameterId: number,
  parameterName: string,
  measurementTime: string,
): Anomaly {
  return {
    id,
    analysis_run_id: id,
    analysis_status: null,
    analysis_created_at: null,
    dataset_id: null,
    dataset_name: null,
    device_id: 1,
    device_name: 'Press 1',
    device_code: 'PRESS-1',
    model_config_id: null,
    model_name: null,
    algorithm: 'z_score',
    analysis_execution_type: null,
    measurement_record_id: null,
    equipment_measurement_id: id,
    source_type: 'equipment',
    parameter_id: parameterId,
    parameter_name: parameterName,
    measurement_unit: 'mm/s',
    measurement_source: 'sensor',
    measurement_source_type: 'sensor',
    is_test_data: false,
    source_row_number: null,
    measurement_time: measurementTime,
    anomaly_type: 'z_score',
    anomaly_score: null,
    severity: 'medium',
    computed_severity: null,
    severity_reason: null,
    measurement_severity: null,
    measurement_severity_reason: null,
    measurement_detectors: [],
    measurement_agreeing_detectors: null,
    measurement_rule_fired: null,
    measurement_analysis_run_ids: [],
    measurement_deduplicated: false,
    measurement_severity_rule: '',
    status: 'new',
    feature_values: {},
    measurement_values: {},
    expected_range: {},
    score_threshold: null,
    algorithm_parameters: {},
    description: null,
    detected_at: measurementTime,
    reviews: [],
    maintenance_order_ids: [],
    maintenance_orders: [],
  }
}

function bucket(
  parameterId: number,
  parameterName: string,
  bucketStart: string,
  anomalyCount = 1,
): AnomalyTimeBucket {
  return {
    bucket_start: bucketStart,
    parameter_id: parameterId,
    parameter_name: parameterName,
    anomaly_count: anomalyCount,
    minimum_value: 1,
    maximum_value: 3,
    unit: 'mm/s',
    highest_severity: 'medium',
    detectors: ['z_score'],
    not_reviewed_count: 0,
    review_status: 'new',
  }
}

describe('device anomaly grouping helpers', () => {
  it('groups time buckets by parameter and keeps newest buckets first', () => {
    const groups = groupBucketsByParameter([
      bucket(10, 'Vibration', '2026-03-01T12:01:00Z', 2),
      bucket(20, 'Temperature', '2026-03-01T12:02:00Z'),
      bucket(10, 'Vibration', '2026-03-01T12:00:00Z'),
    ])

    assert.deepEqual(groups.map((group) => group.parameterName), ['Temperature', 'Vibration'])
    assert.deepEqual(
      groups[1].buckets.map((item) => item.bucket_start),
      ['2026-03-01T12:01:00Z', '2026-03-01T12:00:00Z'],
    )
    assert.equal(
      groups.flatMap((group) => group.buckets).reduce((total, item) => total + item.anomaly_count, 0),
      4,
    )
  })

  it('formats the full bucket range in the display timezone', () => {
    assert.equal(
      formatBucketRange('2026-11-07T21:20:00Z', 10, 'Europe/Paris'),
      '07 Nov 2026, 22:20 – 22:29',
    )
    assert.equal(
      formatBucketRange('2026-11-01T06:00:00Z', 30, 'America/New_York'),
      '01 Nov 2026, 01:00 – 01:29',
    )
  })

  it('groups by parameter newest detection and sorts anomalies newest first', () => {
    const groups = groupAnomaliesByParameter([
      anomaly(1, 10, 'Vibration', '2026-03-01T12:00:00Z'),
      anomaly(2, 20, 'Temperature', '2026-03-01T11:00:00Z'),
      anomaly(3, 10, 'Vibration', '2026-03-01T10:00:00Z'),
    ])

    assert.deepEqual(groups.map((group) => group.parameterName), ['Vibration', 'Temperature'])
    assert.deepEqual(groups[0].anomalies.map((item) => item.id), [1, 3])
  })

  it('orders by detection time even when measurement timestamps differ', () => {
    const newerDetection = {
      ...anomaly(1, 10, 'Vibration', '2026-03-01T12:00:00Z'),
      detected_at: '2026-03-01T11:00:00Z',
    }
    const olderDetection = {
      ...anomaly(2, 10, 'Vibration', '2026-03-01T10:00:00Z'),
      detected_at: '2026-03-01T12:00:00Z',
    }

    const groups = groupAnomaliesByParameter([olderDetection, newerDetection])

    assert.deepEqual(groups.map((group) => group.parameterName), ['Vibration'])
    assert.deepEqual(groups[0].anomalies.map((item) => item.id), [2, 1])
  })

  it('device anomaly counts add to the filtered total', () => {
    const devices = [
      { device_id: 1, anomaly_count: 7 },
      { device_id: 2, anomaly_count: 5 },
      { device_id: 3, anomaly_count: 0 },
    ]
    const filteredTotal = 12

    assert.equal(groupedDeviceCountTotal(devices), filteredTotal)
  })

  it('sums PostgreSQL bigint counts serialized as numeric strings', () => {
    const devices = [
      { device_id: 18, anomaly_count: '459' },
      { device_id: 19, anomaly_count: '145' },
      { device_id: 20, anomaly_count: '171' },
      { device_id: 14, anomaly_count: '2' },
      { device_id: 15, anomaly_count: '1' },
      { device_id: 16, anomaly_count: '1' },
    ]

    assert.equal(groupedDeviceCountTotal(devices), 779)
  })

  it('shows HTTP status codes for grouped endpoint errors', () => {
    for (const status of [404, 422, 500]) {
      assert.equal(
        groupedAnomalyRequestError({ status, message: 'request failed' }),
        `Unable to load grouped anomalies (HTTP ${status}): request failed`,
      )
    }
  })

  it('keeps returned devices available to render when flat and grouped totals differ', () => {
    const devices = [
      { device_id: 1, anomaly_count: 7 },
      { device_id: 2, anomaly_count: 5 },
    ]

    const result = reconcileGroupedDeviceCounts(devices, 12, 13)

    assert.deepEqual(result.devices, devices)
    assert.match(result.warning, /12 device anomaly rows/)
    assert.match(result.warning, /grouped total is 12/)
    assert.match(result.warning, /flat-list total is 13/)
  })
})
