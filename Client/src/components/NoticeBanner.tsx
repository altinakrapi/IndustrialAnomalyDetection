import { AlertTriangle, Check } from 'lucide-react'

import type { Notice } from '../types'

export function NoticeBanner({ notice, onDismiss }: { notice: Notice; onDismiss?: () => void }) {
  return <div className={`flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${notice.kind === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-rose-200 bg-rose-50 text-rose-800'}`} role="status"><span>{notice.kind === 'success' ? <Check size={16} /> : <AlertTriangle size={16} />}</span>{notice.text}{onDismiss && <button aria-label="Dismiss message" onClick={onDismiss}>x</button>}</div>
}
