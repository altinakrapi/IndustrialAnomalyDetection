export type User = { id: number; full_name: string; email: string; role: string; is_active?: boolean }
export type Device = {
  id: number; name: string; device_code: string; device_type: string
  category?: string; location?: string; status: string
  source_type?: 'manual' | 'sensor' | 'simulated' | 'dataset' | string
  responsible_user_id?: number | null; responsible_user_name?: string | null
}
export type Dataset = {
  id: number; name: string; file_name: string; file_format: string; device_id: number | null; device_name?: string | null
  row_count: number; invalid_row_count: number; column_metadata: { name: string; type: string }[]
  status: string; uploaded_at: string
}
export type NasaImsImportStatus = {
  status: 'not_started' | 'queued' | 'downloading' | 'extracting' | 'processing' | 'completed' | 'failed'
  downloaded_bytes: number
  total_bytes: number | null
  dataset_ids: number[]
  error_message: string | null
  updated_at: string | null
}
export type ImsComparison = {
  dataset_id: number
  status: 'not_started' | 'running' | 'completed' | 'failed'
  feature: string
  statistics: { analysis_id: number; algorithm: string; status: string; records: number; anomalies: number; anomaly_rate: number; execution_seconds?: number }[]
  series: { algorithm: string; points: { sample: number; timestamp: string | null; value: number | null; is_anomaly: boolean }[] }[]
  anomalies: { id: number; algorithm: string; sample: number | null; timestamp: string | null; source_file: string | null; window_index: number | null; score: number | null; severity: string; features: Record<string, number>; description: string | null }[]
  total_anomalies: number
  unique_affected_measurements: number
  limit?: number
  offset?: number
  errors?: { analysis_id: number; message: string | null }[]
}
export type ModelConfig = { id: number; name: string; algorithm: string; parameters: Record<string, unknown> }
export type Analysis = {
  id: number; dataset_id: number | null; dataset_name: string | null; device_name: string | null
  source_type: string
  equipment_measurement_id: number | null
  model_config_id: number | null; model_name: string | null; algorithm?: string; status: string
  execution_type?: string; started_at?: string | null; completed_at?: string | null
  duration_seconds?: number | null; total_records: number
  anomalies_found: number; metrics: Record<string, unknown>; error_message?: string
  created_at: string
}
export type Anomaly = {
  id: number
  analysis_run_id: number
  analysis_status: string | null
  analysis_created_at: string | null
  dataset_id: number | null
  dataset_name: string | null
  device_id: number | null
  device_name: string | null
  device_code: string | null
  model_config_id: number | null
  model_name: string | null
  algorithm: string
  analysis_execution_type: string | null
  measurement_record_id: number | null
  equipment_measurement_id: number | null
  source_type: 'dataset' | 'equipment' | string
  parameter_id: number | null
  parameter_name: string | null
  measurement_unit: string | null
  measurement_source: string | null
  measurement_source_type: 'manual' | 'sensor' | 'simulated' | 'dataset' | null
  is_test_data: boolean
  source_row_number: number | null
  measurement_time: string | null
  anomaly_type: string
  anomaly_score: number | null
  severity: string
  computed_severity: string | null
  severity_reason: string | null
  measurement_severity: string | null
  measurement_severity_reason: string | null
  measurement_detectors: string[]
  measurement_agreeing_detectors: number | null
  measurement_rule_fired: string | null
  measurement_analysis_run_ids: number[]
  measurement_deduplicated: boolean
  measurement_severity_rule: string
  status: string
  alert_rules?: unknown
  alert_priority?: string | null
  history_state?: string | null
  history_size?: number | null
  history_required?: number | null
  feature_values: Record<string, unknown>
  measurement_values: Record<string, unknown>
  expected_range: Record<string, unknown>
  score_threshold: number | null
  algorithm_parameters: Record<string, unknown>
  description: string | null
  detected_at: string
  reviews: {
    id: number
    decision: string
    comments: string | null
    reviewed_by: number | null
    reviewed_at: string
  }[]
  maintenance_order_ids: number[]
  maintenance_orders: {
    id: number
    title: string
    status: string
    priority: string
    assigned_to: number | null
    assigned_name: string | null
    due_date: string | null
    resolution_notes: string | null
    created_at: string
  }[]
  explanation?: {
    rules_fired: string[]
    alert_priority: string | null
    episode_id: number | null
    contributing_parameters: {
      feature: string
      value: number | null
      baseline_mean: number | null
      baseline_standard_deviations: number | null
      history_size: number | null
    }[]
    ensemble: {
      confidence: number | null
      voting_rule: string | null
      members: Record<string, unknown>[]
    }
    model: {
      name: string | null
      version: string | number | null
      configuration_type: 'default' | 'tuned'
      configuration_name: string | null
      parameters: Record<string, unknown>
    }
    history: {
      state: string | null
      available_readings: number | null
      required_readings: number | null
    }
    source_type: string | null
    evidence_note: string
  }
  feedback?: {
    id: number
    label: 'confirmed_fault' | 'false_alarm' | 'needs_more_data'
    comment: string | null
    maintenance_order_id: number | null
    history: Record<string, unknown>[]
  } | null
  vibration_trend?: {
    source_row_number: number | null
    measurement_id?: number
    chart_label?: string
    timestamp: string | null
    value: number | null
    is_selected_measurement: boolean
  }[]
  trend_feature?: string | null
}
export type AnomalySummary = {
  total: number
  algorithm_specific_detections: number
  unique_affected_measurements: number
  unlinked_detections: number
  severity: { high: number; medium: number; low: number; critical: number }
  status: Record<string, number>
  pending_reviews: number
  confirmed: number
  rejected_false_positives: number
  source_type?: 'equipment' | 'dataset'
}
export type Maintenance = {
  id: number; title: string; description?: string; device_id: number | null; device_name?: string | null
  dataset_id?: number | null; dataset_name?: string | null; anomaly_id?: number
  assigned_to?: number; assigned_name?: string; priority: string; status: string
  due_date?: string; resolution_notes?: string
  actions: { id: number; action_description: string; findings?: string; recommendations?: string }[]
}
export type DashboardData = {
  metrics: Record<string, number>
  computed_severity_distribution?: Record<string, number>
  source_breakdown: {
    active_devices: Record<'manual' | 'sensor' | 'simulated' | 'dataset', number>
    completed_analyses: Record<'manual' | 'sensor' | 'simulated' | 'dataset', number>
    cases_to_review: Record<'manual' | 'sensor' | 'simulated' | 'dataset', number>
  }
  maintenance_orders_by_status: Record<string, number>
  anomalies_by_device: {
    device_id: number | null
    dataset_id?: number
    device_name: string
    source_type: 'manual' | 'sensor' | 'simulated' | 'dataset'
    count: number
  }[]
  recent_activity: { id: number; user_id?: number; action: string; entity_type: string; entity_id?: number; created_at: string }[]
}
export type Page = 'Overview' | 'Monitoring' | 'Devices' | 'Measurements' | 'Datasets' | 'Exploration' | 'Analysis' | 'Evaluation' | 'Anomalies' | 'Maintenance' | 'Reports' | 'Notifications' | 'Profile' | 'Users' | 'Audit' | 'Settings'
export type Notice = { kind: 'success' | 'error'; text: string }
export type Theme = 'light' | 'dark'
export type NotificationItem = {
  id: number
  title: string
  message: string
  notification_type: string
  details?: Record<string, unknown>
  is_read: boolean
  created_at: string
}
