import { AlertTriangle, Check, X } from 'lucide-react'

import type { Notice } from '../types'

export function NoticeBanner({ notice, onDismiss }: { notice: Notice; onDismiss?: () => void }) {
  return <div className={`flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${notice.kind === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-rose-200 bg-rose-50 text-rose-800'}`} role="status"><span>{notice.kind === 'success' ? <Check size={16} /> : <AlertTriangle size={16} />}</span>{notice.text}{onDismiss && <button className="ml-auto grid size-8 shrink-0 place-items-center rounded-md hover:bg-black/5" type="button" aria-label="Dismiss message" onClick={onDismiss}><X size={16} aria-hidden="true" /></button>}</div>
}
