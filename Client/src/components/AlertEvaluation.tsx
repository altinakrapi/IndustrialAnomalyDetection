type AlertEvaluationProps = {
  alert_rules?: unknown
  alert_priority?: string | null
  history_state?: string | null
  history_size?: number | null
  history_required?: number | null
  className?: string
}

function describeRules(rules: unknown): string {
  if (typeof rules === 'string') return rules
  if (Array.isArray(rules)) {
    return rules.map((rule) => {
      if (typeof rule === 'string') return rule
      return JSON.stringify(rule)
    }).join(', ')
  }
  if (rules && typeof rules === 'object') {
    return Object.entries(rules).map(([name, value]) => `${name.replaceAll('_', ' ')}: ${String(value)}`).join(' | ')
  }
  if (rules == null) return ''
  return String(rules)
}

export function AlertEvaluation({ alert_rules, alert_priority, history_state, history_size, history_required, className = '' }: AlertEvaluationProps) {
  const rules = describeRules(alert_rules)
  const hasHistory = history_state != null || history_size != null || history_required != null
  if (!rules && !alert_priority && !hasHistory) return null
  const historyTone = history_state === 'insufficient_history' ? 'font-medium text-amber-800' : ''
  let historyCounts = ''
  if (history_size != null || history_required != null) {
    historyCounts = ` | ${history_size ?? 'n/a'} of ${history_required ?? 'n/a'} required readings`
  }
  return <div className={`mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-500 ${className}`}>
    {rules && <span>Alert rules: <strong className="font-medium text-slate-700">{rules}</strong></span>}
    {alert_priority && <span>Alert priority: <strong className="capitalize text-slate-700">{alert_priority}</strong></span>}
    {hasHistory && <span className={historyTone}>
      History evaluation: <strong className="font-medium">{history_state?.replaceAll('_', ' ') ?? 'available'}</strong>{historyCounts}
    </span>}
  </div>
}
