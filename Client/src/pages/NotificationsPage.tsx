import { useCallback, useEffect, useState } from 'react'
import { request } from '../api'

import type { Notification } from '../types/workflow'

import { buttonClass, panelClass, EmptyPanel, ErrorMessage } from '../components/ui'

import { PageTitle } from '../components/PageTitle'
import { AlertEvaluation } from '../components/AlertEvaluation'

export function NotificationsPage({ onNotificationsChange }: { onNotificationsChange?: () => Promise<void> }) {
  const [items, setItems] = useState<Notification[]>([])
  const [filter, setFilter] = useState<'all' | 'unread' | 'read'>('all')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const query = filter === 'all' ? '' : `?is_read=${filter === 'read'}`
      setItems(await request<Notification[]>(`/notifications${query}`))
      setError('')
      await onNotificationsChange?.()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load notifications')
    }
  }, [filter, onNotificationsChange])
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refresh())
    return () => window.cancelAnimationFrame(frame)
  }, [refresh])

  async function markRead(id: number) {
    try {
      await request(`/notifications/${id}/read`, { method: 'PATCH' })
      await refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to update notification')
    }
  }
  async function markAllRead() {
    try {
      await request('/notifications/read-all', { method: 'POST' })
      await refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to update notifications')
    }
  }

  return (
    <>
      <PageTitle title="Notifications" description="Equipment, dataset, analysis, anomaly, and maintenance updates for your account."
        action={<button className={buttonClass} onClick={() => void markAllRead()}>Mark all read</button>} />
      <div className="flex gap-2">
        {(['all', 'unread', 'read'] as const).map((choice) => (
          <button key={choice} className={`rounded-full px-3 py-1.5 text-xs font-semibold capitalize ${filter === choice ? 'bg-forest-600 text-white' : 'bg-white text-slate-600'}`} onClick={() => setFilter(choice)}>{choice}</button>
        ))}
      </div>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <div className="grid gap-3">
        {items.map((item) => (
          <article className={`${panelClass} flex items-start gap-3`} key={item.id}>
            <span className={`mt-1 size-2 shrink-0 rounded-full ${item.is_read ? 'bg-slate-300' : 'bg-forest-600'}`} />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-slate-900">{item.title}</h2>
              <p className="mt-1 text-xs leading-5 text-slate-600">{item.message}</p>
              {item.notification_type === 'anomaly_detected' && item.details && <div className="mt-3 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {item.details.device?.name && <span>Device: <strong>{item.details.device.name}</strong></span>}
                  {item.details.device?.source_type && <span>Source: <strong className="capitalize">{item.details.device.source_type.replaceAll('_', ' ')}</strong></span>}
                  {item.details.detection?.severity && <span>Severity: <strong className="capitalize">{item.details.detection.severity}</strong></span>}
                  {item.details.detection?.alert_priority && <span>Alert priority: <strong className="capitalize">{item.details.detection.alert_priority}</strong></span>}
                  {item.details.detection?.detected_at && <span>Detected: {new Date(item.details.detection.detected_at).toLocaleString()}</span>}
                  {item.details.detection?.measurement_time && <span>Measurement: {new Date(item.details.detection.measurement_time).toLocaleString()}</span>}
                  {item.details.detection?.source_row_number && <span>Source row: {item.details.detection.source_row_number.toLocaleString()}</span>}
                  {item.details.analysis?.algorithm && <span>Method: {item.details.analysis.algorithm.replaceAll('_', ' ')}</span>}
                  {item.details.analysis?.score != null && <span>Score: {item.details.analysis.score.toPrecision(4)}</span>}
                </div>
                {item.details.measurement && <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(item.details.measurement).map(([name, value]) => <span className="rounded bg-white px-2 py-1 font-mono text-[10px]" key={name}>{name}: {value == null ? 'n/a' : value.toPrecision(5)}</span>)}
                  {item.details.measurement_unit && <span className="rounded bg-white px-2 py-1 text-[10px]">Unit: {item.details.measurement_unit}</span>}
                </div>}
                <AlertEvaluation alert_rules={item.details.detection?.alert_rules} alert_priority={item.details.detection?.alert_priority} history_state={item.details.detection?.history_state} history_size={item.details.detection?.history_size} history_required={item.details.detection?.history_required} />
                {item.details.analysis?.description && <p className="mt-2">{item.details.analysis.description}</p>}
              </div>}
              <small className="mt-2 block text-[10px] text-slate-400">{item.notification_type.replaceAll('_', ' ')}  -  {new Date(item.created_at).toLocaleString()}</small>
            </div>
            {!item.is_read && <button className="text-xs font-semibold text-forest-700" onClick={() => void markRead(item.id)}>Mark read</button>}
          </article>
        ))}
        {items.length === 0 && !error && <EmptyPanel title="No notifications" body="Updates will appear here as analyses and maintenance work progress." />}
      </div>
    </>
  )
}
