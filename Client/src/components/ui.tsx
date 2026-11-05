export const inputClass =
  'h-10 min-w-0 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800 outline-none focus:border-forest-500 focus:ring-2 focus:ring-forest-100'

export const buttonClass =
  'inline-flex min-h-9 cursor-pointer items-center justify-center gap-2 rounded-lg bg-forest-600 px-4 py-2 text-xs font-semibold text-white hover:bg-forest-700 disabled:cursor-not-allowed disabled:opacity-50'

export const panelClass = 'panel-surface rounded-xl border border-slate-200 bg-white p-5 shadow-sm'

export function Field({
  label,
  name,
  defaultValue,
  value,
  onChange,
  type = 'text',
  required,
  min,
  max,
  step,
  minLength,
  placeholder,
}: {
  label: string
  name: string
  defaultValue?: string | number
  value?: string
  onChange?: (value: string) => void
  type?: string
  required?: boolean
  min?: number | string
  max?: number | string
  step?: number | string
  minLength?: number
  placeholder?: string
}) {
  return <label className="grid gap-2 text-xs font-semibold tracking-wide text-slate-600">{label}<input className={inputClass} name={name} defaultValue={defaultValue} value={value} onChange={onChange ? (event) => onChange(event.target.value) : undefined} type={type} required={required} min={min} max={max} step={step} minLength={minLength} placeholder={placeholder} /></label>
}

export function StatCard({ label, value }: { label: string; value: string }) {
  return <article className={`${panelClass} panel-surface`}><span className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">{label}</span><strong className="mt-2 block text-3xl font-semibold tracking-tight text-slate-900">{value}</strong></article>
}

export function HistoryList({ title, items }: { title: string; items: string[] }) {
  return <div className="rounded-lg border border-slate-100 p-3"><h3 className="text-xs font-semibold text-slate-800">{title}</h3>{items.length ? <ul className="mt-2 space-y-2 text-[10px] text-slate-600">{items.map((item) => <li className="border-t border-slate-100 pt-2" key={item}>{item}</li>)}</ul> : <p className="mt-2 text-[10px] text-slate-500">No records yet.</p>}</div>
}

export function EmptyPanel({ title, body }: { title: string; body: string }) {
  return <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm"><strong className="text-sm text-slate-800">{title}</strong><p className="mt-1 text-xs leading-5 text-slate-500">{body}</p></div>
}

export function ErrorMessage({ children }: { children: string }) {
  return <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">{children}</p>
}
