import { statusBadgeClass } from './statusBadgeClass'

export function StatusBadge({ value }: { value: string }) {
  const label = value.replaceAll('_', ' ')
  return <span className={statusBadgeClass(value)}><span />{label}</span>
}
