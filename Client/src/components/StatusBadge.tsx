import { statusBadgeClass } from './statusBadgeClass'

export function StatusBadge({ value, label }: { value: string; label?: string }) {
  const displayLabel = label ?? value.replaceAll('_', ' ')
  return <span className={statusBadgeClass(value)}><span />{displayLabel}</span>
}
