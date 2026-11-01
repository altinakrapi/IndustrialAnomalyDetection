export type User = {
  id: number
  full_name: string
  email: string
  role: string
  is_active?: boolean
}
export type Dataset = {
  id: number
  name: string
  device_id: number | null
  device_name?: string | null
  row_count: number
  column_metadata: { name: string; type: string; unit?: string }[]
  status?: string
  file_format?: string
}
export type Device = {
  id: number
  name: string
  device_code: string
  source_type?: 'manual' | 'sensor' | 'simulated' | 'dataset' | string
  responsible_user_id?: number | null
  responsible_user_name?: string | null
}
export type ModelConfig = { id: number; name: string; algorithm: string; parameters: Record<string, unknown>; is_active?: boolean }
export type DetectorParameter = {
  name: string
  type: 'integer' | 'integer_list' | 'number' | 'number_or_auto' | 'string' | 'string_or_number' | 'boolean' | 'json'
  default: string | number | boolean | number[] | Record<string, unknown> | unknown[]
  minimum?: number
  maximum?: number
  choices?: (string | number)[]
}
export type DetectorCatalogEntry = {
  algorithm: string
  display_name: string
  version: string
  minimum_history: number
  execution_mode: 'batch' | 'walk_forward'
  parameters: DetectorParameter[]
}
export type Analysis = {
  id: number
  dataset_id: number | null
  model_config_id: number | null
  dataset_name: string | null
  source_type: string
  equipment_measurement_id: number | null
  device_name: string | null
  model_name: string | null
  algorithm?: string
  status: string
  execution_type?: string
  total_records: number
  anomalies_found: number
  metrics: Record<string, unknown>
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  error_message?: string
  created_at: string
}
export type AnalysisSummary = {
  total_runs: number
  completed: number
  failed: number
  in_progress: number
  anomaly_detections: number
  unique_affected_measurements: number
  latest_analysis: Analysis | null
  next_scheduled_at: string | null
  schedule_enabled: boolean
}
export type Action = (action: () => Promise<unknown>, success: string) => Promise<void>
export type NoticeState = { message: string; error?: boolean } | null
export type DeviceDetail = Device & {
  name: string
  status: string
  category?: string
  device_type: string
  manufacturer?: string
  model?: string
  serial_number?: string
  location?: string
  installation_date?: string
  description?: string
  responsible_user_id?: number | null
  responsible_user_name?: string | null
  dataset_count: number
  analysis_count: number
  anomaly_count: number
  unreviewed_anomaly_count: number
  maintenance_order_count: number
  datasets: { id: number; name: string; row_count: number; status: string }[]
  measurement_parameters: EquipmentParameter[]
  latest_measurements: EquipmentMeasurement[]
  analyses: {
    id: number
    dataset_id: number | null
    dataset_name: string | null
    device_id: number
    source_type: string
    equipment_measurement_id: number | null
    model_config_id: number | null
    model_name: string | null
    algorithm: string | null
    status: string
    execution_type: string
    source_start_row: number | null
    source_end_row: number | null
    total_records: number
    anomalies_found: number
    metrics: Record<string, unknown>
    error_message: string | null
    started_at: string | null
    completed_at: string | null
    created_at: string
  }[]
  analysis_offset: number
  analysis_limit: number
  analysis_total: number
  maintenance_orders: {
    id: number
    title: string
    status: string
    priority: string
    anomaly_id: number | null
    assigned_to: number | null
    due_date: string | null
    resolution_notes: string | null
    created_at: string
    actions: {
      id: number
      action_description: string
      findings: string | null
      recommendations: string | null
      issue_confirmed: boolean | null
      started_at: string | null
      completed_at: string | null
      created_at: string
    }[]
  }[]
  maintenance_offset: number
  maintenance_limit: number
}
export type AnalysisResultItem = {
  id: number
  source_row_number: number | null
  equipment_measurement_id: number | null
  timestamp: string | null
  is_anomaly: boolean
  anomaly_score: number | null
  history_state: 'sufficient' | 'insufficient_history' | 'excluded_missing_values' | null
  history_size: number | null
  history_required: number | null
  result_details: Record<string, unknown>
  feature_values: Record<string, unknown>
}
export type AnalysisResultPage = {
  total: number
  results: AnalysisResultItem[]
}
export type DeviceAnalysisDetail = {
  id: number
  dataset_id?: number | null
  dataset_name?: string | null
  source_type?: string
  algorithm?: string | null
  status: string
  execution_type: string
  total_records: number
  anomalies_found: number
  metrics: Record<string, unknown>
  configuration: Record<string, unknown>
  started_at: string | null
  completed_at: string | null
}
export type DeviceAnomaly = {
  id: number
  severity: string
  status: string
  anomaly_type: string
  anomaly_score: number | null
  measurement_time: string | null
  source_row_number?: number | null
  equipment_measurement_id?: number | null
  detected_at: string
  feature_values: Record<string, unknown>
  description: string | null
  alert_rules?: unknown
  alert_priority?: string | null
  history_state?: string | null
  history_size?: number | null
  history_required?: number | null
  reviews: {
    id: number
    decision: string
    comments: string | null
    reviewed_by: number
    reviewed_at: string
  }[]
  maintenance_order_ids: number[]
}
export type EquipmentParameter = {
  id: number
  device_id: number
  name: string
  unit: string
  lower_limit: number | null
  upper_limit: number | null
  warning_low: number | null
  warning_high: number | null
  critical_low: number | null
  critical_high: number | null
  expected_interval_minutes: number
  expected_interval_seconds?: number | null
  last_measurement_at?: string | null
  next_due_at?: string | null
  measurement_status?: 'disabled' | 'overdue' | 'awaiting_first_measurement' | 'scheduled'
  analysis_method: 'threshold' | 'z_score' | 'isolation_forest' | 'ensemble'
  z_score_threshold: number
  isolation_contamination: number
  consecutive_anomaly_threshold?: number | null
  persistence_window_size?: number | null
  persistence_anomaly_threshold?: number | null
  flatline_reading_count: number
  flatline_variance_tolerance: number
  alert_rules?: unknown
  alert_priority?: string | null
  history_state?: string | null
  history_size?: number | null
  history_required?: number | null
  enabled: boolean
  measurement_count?: number
  latest_measurement?: EquipmentMeasurement | null
  last_analysis_at?: string | null
  last_analysis_status?: string
  last_analysis_note?: string | null
  next_analysis_due_at?: string | null
  analysis_trigger?: 'on_measurement'
}
export type EquipmentMeasurement = {
  id: number
  device_id: number
  parameter_id: number
  parameter_name: string
  value: number
  unit: string
  measured_at: string
  recorded_at: string
  recorded_by: number | null
  recorder_name: string | null
  source: string
  source_type: 'manual' | 'sensor' | 'simulated' | 'dataset' | string
  received_at?: string
  sensor_timestamp?: string | null
  analysis_runs?: { id: number; status: string }[]
  anomaly_ids?: number[]
  analysis_run_id?: number | null
  anomaly_count?: number
  analysis_status?: string
}
export type EquipmentMeasurementPage = {
  items: EquipmentMeasurement[]
  total: number
  limit: number
  offset: number
}
export type Notification = {
  id: number
  title: string
  message: string
  notification_type: string
  details?: {
    device?: { id?: number; name?: string; code?: string; source_type?: string }
    detection?: {
      detected_at?: string
      measurement_time?: string | null
      severity?: string
      source_row_number?: number | null
      alert_rules?: unknown
      alert_priority?: string | null
      history_state?: string | null
      history_size?: number | null
      history_required?: number | null
    }
    measurement?: Record<string, number | null>
    measurement_unit?: string
    analysis?: {
      run_id?: number
      dataset_name?: string
      algorithm?: string
      score?: number | null
      score_threshold?: number | null
      description?: string
      model_result?: Record<string, unknown>
      limit_result?: Record<string, unknown>
    }
  }
  is_read: boolean
  created_at: string
}
export type AuditEntry = {
  id: number
  user_id?: number
  action: string
  entity_type: string
  entity_id?: number
  details: Record<string, unknown>
  created_at: string
}
export type AppSettings = {
  max_upload_size_mb: number
  default_n_estimators: number
  default_contamination: number | null
  default_consecutive_anomaly_threshold: number
  default_persistence_window_size: number
  default_persistence_anomaly_threshold: number
  alert_episode_close_normal_count: number
  alert_escalation_minutes: number
  manual_extreme_z_score_threshold: number
  stale_data_interval_multiplier: number
}
export type Exploration = {
  histogram: { range: string; count: number }[]
  series: {
    source_row_number: number
    timestamp: string | null
    value: number | null
    values: Record<string, number | null>
    anomalies: {
      id: number | null
      analysis_id: number
      algorithm: string
      severity: string
      score: number | null
      threshold: number | null
      expected_range: Record<string, unknown>
      description: string | null
    }[]
  }[]
  numeric_features: string[]
  time_columns: string[]
  feature: string
  features: string[]
  units: Record<string, string | null>
  total_measurements: number
  sampled_measurements: number
  stride: number
  analysis_runs: { id: number; algorithm: string; model_name: string }[]
}
export type DatasetStatistics = {
  rows: number
  invalid_rows: number
  duplicate_rows: number | null
  columns: Record<string, { count: number; mean: number | null; median: number | null; min: number | null; max: number | null; std: number | null; missing: number; invalid_numeric: number }>
}
export type MonitoringScheduleData = {
  enabled: boolean
  interval_minutes: number
  simulation_enabled: boolean
  simulation_batch_size: number
  execution_status: 'idle' | 'running' | 'completed' | 'failed' | 'disabled'
  next_run_at: string | null
  last_started_at: string | null
  last_completed_at: string | null
  last_error: string | null
}
export type MonitoringDashboardData = {
  devices: {
    id: number
    name: string
    device_code: string
    device_type: string
    source_type?: string
    status: string
    health: string
    dataset_count: number
    open_anomalies: number
    overdue_measurements: number
    measurement_schedule: {
      parameter_id: number
      parameter_name: string
      enabled: boolean
      expected_interval_minutes: number
      expected_interval_seconds: number | null
      last_measurement_at: string | null
      last_reading_received_at: string | null
      next_due_at: string | null
      status: string
      stale_data_status: 'monitoring' | 'stopped_reporting' | 'reporting_resumed'
      stale_data_episode: {
        id: number
        status: string
        priority: string
        severity: string
        first_detected_at: string
        closed_at: string | null
        alert_rules: string[]
      } | null
      alert_rules?: unknown
      alert_priority?: string | null
      history_state?: string | null
      history_size?: number | null
      history_required?: number | null
    }[]
    latest_measurements: {
      id: number
      parameter_name: string
      value: number
      unit: string
      measured_at: string
      source_type?: string
    }[]
    data_source: string
    vibration_trend: {
      feature: string
      data_source: string
      points: { label: string; source_row_number: number; value: number }[]
    } | null
    measurement_trend: {
      feature: string
      unit: string
      data_source: string
      points: { label: string; source_row_number: number; value: number }[]
    } | null
    last_analysis: {
      id: number
      status: string
      created_at: string
      anomalies_found: number
      execution_type: string
    } | null
  }[]
  analysis_history: {
    id: number
    dataset_name: string | null
    source_type: string
    measurement_source_type?: string | null
    equipment_measurement_id: number | null
    device_name: string | null
    model_name: string | null
    status: string
    execution_type: string
    source_start_row: number | null
    source_end_row: number | null
    total_records: number
    anomalies_found: number
    error_message: string | null
    created_at: string
    completed_at: string | null
  }[]
  recent_anomalies: {
    id: number
    analysis_run_id: number
    device_name: string | null
    dataset_name: string | null
    source_type: string | null
    measurement_source_type?: string | null
    parameter_name: string | null
    severity: string
    status: string
    anomaly_type: string
    anomaly_score: number | null
    measurement_time: string | null
    detected_at: string
    feature_values: Record<string, unknown>
    description: string | null
    alert_rules?: unknown
    alert_priority?: string | null
    history_state?: string | null
    history_size?: number | null
    history_required?: number | null
  }[]
  notification_status: {
    unread: number
    anomaly_notifications: number
    overdue_measurement_notifications: number
  }
  severity_distribution: {
    equipment: Record<string, number>
    dataset_experiments: Record<string, number>
  }
  failed_analysis_runs: number
  pending_maintenance_tasks: number
}
