import type { ReactNode } from 'react'

export function EmptyState({ icon, title, body }: { icon: ReactNode; title: string; body: string }) {
  return <div className="flex min-h-48 flex-col items-center justify-center px-6 py-10 text-center [&>strong]:text-sm [&>strong]:font-semibold [&>strong]:text-slate-800 [&>p]:mt-1 [&>p]:max-w-sm [&>p]:text-xs [&>p]:leading-5 [&>p]:text-slate-500"><span className="mb-3 grid size-10 place-items-center rounded-xl bg-slate-100 text-slate-500">{icon}</span><strong>{title}</strong><p>{body}</p></div>
}
