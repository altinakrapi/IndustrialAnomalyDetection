import type { ReactNode } from 'react'

export function PageHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end [&_h1]:mt-2 [&_h1]:text-3xl [&_h1]:font-semibold [&_h1]:tracking-tight [&_h1]:text-slate-900 dark:[&_h1]:text-slate-100 sm:[&_h1]:text-4xl [&_p]:mt-2 [&_p]:max-w-2xl [&_p]:text-sm [&_p]:leading-6 [&_p]:text-slate-500 dark:[&_p]:text-slate-300"><div><span className="text-[10px] font-bold tracking-[0.17em] text-forest-600">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{action && <div className="flex shrink-0 gap-2">{action}</div>}</div>
}
