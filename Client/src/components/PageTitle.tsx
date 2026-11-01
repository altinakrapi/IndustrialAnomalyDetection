import type { ReactNode } from 'react'

export function PageTitle({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="flex flex-wrap items-end justify-between gap-4"><div><span className="text-[10px] font-bold uppercase tracking-[0.17em] text-forest-600">Workspace</span><h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">{title}</h1><p className="mt-2 text-sm leading-6 text-slate-500">{description}</p></div>{action}</div>
}
