import { createElement, Fragment, type ReactNode } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'

import type { MonitoringViewState } from './monitoringView'

export function MonitoringPageState({
  state,
  error,
  onRetry,
  children,
}: {
  state: MonitoringViewState
  error: string
  onRetry: () => void
  children?: ReactNode
}) {
  return createElement(Fragment, null,
    state === 'loading' && createElement('section', {
      className: 'rounded-lg border border-slate-200 bg-white p-4 shadow-sm space-y-4',
      'aria-label': 'Loading monitoring data',
      'aria-live': 'polite',
    },
    createElement('div', { className: 'loading-skeleton h-5 w-56' }),
    createElement('div', { className: 'grid gap-3 sm:grid-cols-3 lg:grid-cols-6' },
      ...Array.from({ length: 6 }, (_, index) => createElement('div', {
        className: 'loading-skeleton h-16',
        key: index,
      })),
    ),
    createElement('div', { className: 'loading-skeleton h-40' }),
    ),
    error && createElement('div', {
      className: 'flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800',
      role: 'alert',
    },
    createElement('span', { className: 'flex items-center gap-2' },
      createElement(AlertTriangle, { size: 17 }),
      error,
    ),
    createElement('button', {
      className: 'inline-flex items-center gap-2 rounded-md border border-rose-300 bg-white px-3 py-2 text-xs font-semibold text-rose-800',
      type: 'button',
      onClick: onRetry,
    }, createElement(RotateCw, { size: 14 }), 'Retry'),
    ),
    state === 'empty' && createElement('div', {
      className: 'rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm',
    },
    createElement('strong', { className: 'text-sm text-slate-800' }, 'No registered devices'),
    createElement('p', { className: 'mt-1 text-xs leading-5 text-slate-500' },
      'Register equipment before monitoring its health and measurements.',
    ),
    ),
    (state === 'data' || state === 'empty') && children,
  )
}