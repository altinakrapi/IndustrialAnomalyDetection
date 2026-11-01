export function statusBadgeClass(value: string) {
  const palette: Record<string, string> = {
    active: 'bg-emerald-50 text-emerald-700 before:bg-emerald-500',
    completed: 'bg-emerald-50 text-emerald-700 before:bg-emerald-500',
    reviewed: 'bg-emerald-50 text-emerald-700 before:bg-emerald-500',
    new: 'bg-forest-50 text-forest-700 before:bg-forest-500',
    uploaded: 'bg-forest-50 text-forest-700 before:bg-forest-500',
    running: 'bg-amber-50 text-amber-700 before:animate-pulse before:bg-amber-500',
    pending: 'bg-amber-50 text-amber-700 before:bg-amber-500',
    'under-review': 'bg-amber-50 text-amber-700 before:bg-amber-500',
    assigned: 'bg-indigo-50 text-indigo-700 before:bg-indigo-500',
    'in-progress': 'bg-indigo-50 text-indigo-700 before:bg-indigo-500',
    'on-hold': 'bg-orange-50 text-orange-700 before:bg-orange-500',
    failed: 'bg-rose-50 text-rose-700 before:bg-rose-500',
    archived: 'bg-slate-100 text-slate-500 before:bg-slate-400',
    inactive: 'bg-slate-100 text-slate-500 before:bg-slate-400',
    closed: 'bg-slate-100 text-slate-600 before:bg-slate-400',
  }
  return `inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-[10px] font-semibold capitalize before:size-1.5 before:rounded-full before:bg-slate-400 before:content-[''] ${palette[value.replaceAll('_', '-')] ?? 'bg-slate-100 text-slate-600'}`
}
