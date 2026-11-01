import { sourceTypes, type SourceTypeFilter } from '../sourceType'

export function SourceTypeSelect({
  value,
  onChange,
}: {
  value: SourceTypeFilter
  onChange: (value: SourceTypeFilter) => void
}) {
  return <label className="inline-flex items-center gap-2 text-xs font-medium text-slate-600">
    Source
    <select
      className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-800"
      aria-label="Filter by source type"
      value={value}
      onChange={(event) => onChange(event.target.value as SourceTypeFilter)}
    >
      {sourceTypes.map((sourceType) => <option value={sourceType} key={sourceType}>
        {sourceType === 'all' ? 'All' : sourceType[0].toUpperCase() + sourceType.slice(1)}
      </option>)}
    </select>
  </label>
}

export function SourceTypeBadge({ sourceType }: { sourceType?: string | null }) {
  const label = (sourceType || 'manual').replaceAll('_', ' ')
  const simulated = sourceType === 'simulated'
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-[9px] font-semibold capitalize ${simulated ? 'bg-amber-100 text-amber-900' : 'bg-slate-100 text-slate-600'}`}>
    {label}{simulated ? ' · synthetic' : ''}
  </span>
}
