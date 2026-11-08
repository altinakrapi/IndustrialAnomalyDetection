import { createElement, type ChangeEvent, type FormEvent } from 'react'

import type { MonitoringScheduleData } from '../types/workflow'

const inputClass = 'h-10 min-w-0 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-800 outline-none focus:border-forest-500 focus:ring-2 focus:ring-forest-100'
const buttonClass = 'inline-flex min-h-9 cursor-pointer items-center justify-center gap-2 rounded-lg bg-forest-600 px-4 py-2 text-xs font-semibold text-white hover:bg-forest-700 disabled:cursor-not-allowed disabled:opacity-50'

export function MonitoringScheduleSettings({
  isAdmin,
  schedule,
  loading,
  saving,
  error,
  message,
  onRetry,
  onChange,
  onSubmit,
}: {
  isAdmin: boolean
  schedule: MonitoringScheduleData | null
  loading: boolean
  saving: boolean
  error: string
  message: string
  onRetry: () => void
  onChange: (schedule: MonitoringScheduleData) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}) {
  if (!isAdmin) return null

  const update = (changes: Partial<MonitoringScheduleData>) => {
    if (schedule) onChange({ ...schedule, ...changes })
  }
  const checkbox = (label: string, checked: boolean, onChange: (event: ChangeEvent<HTMLInputElement>) => void, disabled = false) => createElement('label', {
    className: 'flex items-center gap-2 self-end text-xs font-medium text-slate-700',
  },
  createElement('input', { type: 'checkbox', checked, onChange, disabled }),
  label,
  )
  const numberField = (
    label: string,
    name: string,
    value: number,
    min: number,
    max: number,
    onChange: (event: ChangeEvent<HTMLInputElement>) => void,
    disabled = false,
  ) => createElement('label', {
    className: 'grid gap-1.5 text-xs font-medium text-slate-600',
  },
  label,
  createElement('input', {
    className: inputClass,
    type: 'number',
    name,
    min,
    max,
    value,
    onChange,
    disabled,
    required: true,
  }),
  )

  return createElement('section', {
    className: 'rounded-xl border border-slate-200 bg-white p-5 shadow-sm',
  },
  createElement('div', null,
    createElement('h2', { className: 'text-sm font-semibold text-slate-900' },
      'Scheduled analysis (NASA IMS datasets and monitoring checks)',
    ),
    createElement('p', { className: 'mt-1 text-xs leading-5 text-slate-600' },
      'Live equipment readings are analysed as they arrive; this schedule covers NASA IMS datasets, and stopped-reporting checks run regardless.',
    ),
  ),
  error && createElement('div', {
    className: 'mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800',
    role: 'alert',
  },
  createElement('span', null, error),
  createElement('button', { type: 'button', className: 'font-semibold underline', onClick: onRetry }, 'Retry'),
  ),
  message && createElement('p', {
    className: 'mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800',
    role: 'status',
  }, message),
  loading && !schedule && createElement('div', {
    className: 'loading-skeleton mt-4 h-10 w-full',
    'aria-label': 'Loading scheduled analysis settings',
  }),
  schedule && createElement('form', {
    className: 'mt-4 grid gap-4 border-t border-slate-100 pt-4 sm:grid-cols-2 xl:grid-cols-4',
    onSubmit,
  },
  checkbox('Enable scheduled analysis', schedule.enabled, (event) => update({ enabled: event.currentTarget.checked })),
  numberField('Interval (minutes)', 'interval_minutes', schedule.interval_minutes, 1, 1440, (event) => update({ interval_minutes: Number(event.currentTarget.value) })),
  checkbox('Historical replay', schedule.simulation_enabled, (event) => update({ simulation_enabled: event.currentTarget.checked })),
  numberField('Replay batch size (rows)', 'simulation_batch_size', schedule.simulation_batch_size, 2, 10000, (event) => update({ simulation_batch_size: Number(event.currentTarget.value) }), !schedule.simulation_enabled),
  createElement('div', { className: 'flex flex-wrap items-center gap-3 sm:col-span-2 xl:col-span-4' },
    createElement('button', { className: buttonClass, type: 'submit', disabled: saving }, saving ? 'Saving...' : 'Save schedule'),
    createElement('span', { className: 'text-[11px] text-slate-500' }, schedule.simulation_enabled
      ? `Replay processes ${schedule.simulation_batch_size} stored rows per interval; it is not live sensor data.`
      : 'Scheduled cycles analyze changed dataset snapshots or retry failures; unchanged data is skipped.'),
  ),
  ),
  schedule && createElement('div', {
    className: 'mt-4 grid gap-3 border-t border-slate-100 pt-4 text-xs sm:grid-cols-2 xl:grid-cols-4',
  },
  createElement('div', null,
    createElement('span', { className: 'block text-[10px] uppercase text-slate-500' }, 'Execution status'),
    createElement('strong', { className: 'mt-1 block capitalize text-slate-800' }, schedule.execution_status),
  ),
  createElement('div', null,
    createElement('span', { className: 'block text-[10px] uppercase text-slate-500' }, 'Next run'),
    createElement('strong', { className: 'mt-1 block text-slate-800' }, schedule.enabled && schedule.next_run_at ? new Date(schedule.next_run_at).toLocaleString() : 'Not scheduled'),
  ),
  createElement('div', null,
    createElement('span', { className: 'block text-[10px] uppercase text-slate-500' }, 'Last started'),
    createElement('strong', { className: 'mt-1 block text-slate-800' }, schedule.last_started_at ? new Date(schedule.last_started_at).toLocaleString() : 'No runs yet'),
  ),
  createElement('div', null,
    createElement('span', { className: 'block text-[10px] uppercase text-slate-500' }, 'Last completed'),
    createElement('strong', { className: 'mt-1 block text-slate-800' }, schedule.last_completed_at ? new Date(schedule.last_completed_at).toLocaleString() : 'No runs yet'),
  ),
  ),
  schedule?.last_error && createElement('p', {
    className: 'mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800',
    role: 'alert',
  }, `Last execution issue: ${schedule.last_error}`),
  )
}