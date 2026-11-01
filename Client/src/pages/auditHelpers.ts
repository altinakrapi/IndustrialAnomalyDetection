import type { AuditEntry } from '../types/workflow'

const auditActionLabels: Record<string, string> = {
  create: 'Created',
  update: 'Updated',
  delete: 'Deleted',
  archive: 'Archived',
  upload: 'Imported',
  export: 'Exported',
  start: 'Started',
  review: 'Reviewed',
  record_action: 'Recorded maintenance work for',
  register: 'Registered',
  login: 'Signed in to',
  change_password: 'Changed the password for',
  reset_password: 'Reset the password for',
}

export const auditEntityLabels: Record<string, string> = {
  anomaly: 'anomaly',
  anomaly_report: 'anomaly report',
  dataset: 'dataset',
  device: 'equipment',
  maintenance_order: 'maintenance order',
  model_config: 'analysis model',
  system_settings: 'workspace settings',
  user: 'user account',
}

const auditDetailLabels: Record<string, string> = {
  bootstrap: 'Account setup',
  comments: 'Comments',
  decision: 'Decision',
  default_contamination: 'Default anomaly threshold',
  default_n_estimators: 'Default model estimators',
  due_date: 'Due date',
  email: 'Email address',
  entity_id: 'Record',
  file_name: 'File name',
  is_active: 'Account active',
  max_upload_size_mb: 'Maximum upload size',
  priority: 'Priority',
  resolution_notes: 'Resolution notes',
  role: 'Role',
  rows: 'Rows included',
  row_count: 'Rows imported',
  status: 'Status',
}

function capitalizeSentences(value: string): string {
  return value.replace(/(^|[.!?]\s+)([a-z])/g, (_, separator: string, letter: string) =>
    `${separator}${letter.toUpperCase()}`,
  )
}

function auditDetailLabel(key: string): string {
  const label = auditDetailLabels[key] ?? key.replaceAll('_', ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

function auditValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not specified'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (Array.isArray(value)) return value.map(auditValue).join(', ')
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([key, item]) => `${auditDetailLabel(key)}: ${auditValue(item)}`)
      .join('; ')
  }
  return capitalizeSentences(String(value).replaceAll('_', ' '))
}

export function auditSummary(log: AuditEntry): string {
  const action = auditActionLabels[log.action] ?? log.action.replaceAll('_', ' ')
  const entity = auditEntityLabels[log.entity_type] ?? log.entity_type.replaceAll('_', ' ')
  return capitalizeSentences(`${action} ${entity}.`)
}

export function auditDetailLabelForDisplay(key: string): string {
  return auditDetailLabel(key)
}

export function auditValueForDisplay(value: unknown): string {
  return auditValue(value)
}
