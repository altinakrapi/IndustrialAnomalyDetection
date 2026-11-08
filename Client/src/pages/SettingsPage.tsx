import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { request } from '../api'

import type { AppSettings, MonitoringScheduleData, NoticeState } from '../types/workflow'

import { buttonClass, panelClass, Field } from '../components/ui'

import { PageTitle } from '../components/PageTitle'
import { MonitoringScheduleSettings } from './MonitoringScheduleSettings'

function scheduleRequestError(error: unknown): string {
  const record = error !== null && typeof error === 'object'
    ? error as { message?: unknown; status?: unknown }
    : null
  const message = error instanceof Error
    ? error.message
    : typeof record?.message === 'string'
      ? record.message
      : 'Unable to load scheduled analysis settings'
  return typeof record?.status === 'number'
    ? `Scheduled analysis settings could not be loaded (HTTP ${record.status}): ${message}`
    : `Scheduled analysis settings could not be loaded: ${message}`
}

export function SettingsPage({
  isAdmin,
  theme,
  onThemeChange,
}: {
  isAdmin: boolean
  theme: 'light' | 'dark'
  onThemeChange: (theme: 'light' | 'dark') => void
}) {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [schedule, setSchedule] = useState<MonitoringScheduleData | null>(null)
  const [scheduleLoading, setScheduleLoading] = useState(true)
  const [scheduleSaving, setScheduleSaving] = useState(false)
  const [scheduleError, setScheduleError] = useState('')
  const [scheduleMessage, setScheduleMessage] = useState('')
  const [notice, setNotice] = useState<NoticeState>(null)
  useEffect(() => {
    if (!isAdmin) return
    request<AppSettings>('/settings').then(setSettings).catch((error: unknown) => {
      setNotice({ message: error instanceof Error ? error.message : 'Unable to load settings', error: true })
    })
  }, [isAdmin])
  const loadSchedule = useCallback(async () => {
    setScheduleLoading(true)
    try {
      setSchedule(await request<MonitoringScheduleData>('/monitoring'))
      setScheduleError('')
    } catch (error) {
      setScheduleError(scheduleRequestError(error))
    } finally {
      setScheduleLoading(false)
    }
  }, [])
  useEffect(() => {
    if (isAdmin) void loadSchedule()
  }, [isAdmin, loadSchedule])
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    try {
      const updated = await request<AppSettings>('/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          max_upload_size_mb: Number(form.get('max_upload_size_mb')),
          default_n_estimators: Number(form.get('default_n_estimators')),
          default_contamination: form.get('default_contamination') ? Number(form.get('default_contamination')) : null,
          default_consecutive_anomaly_threshold: Number(form.get('default_consecutive_anomaly_threshold')),
          default_persistence_window_size: Number(form.get('default_persistence_window_size')),
          default_persistence_anomaly_threshold: Number(form.get('default_persistence_anomaly_threshold')),
          alert_episode_close_normal_count: Number(form.get('alert_episode_close_normal_count')),
          alert_escalation_minutes: Number(form.get('alert_escalation_minutes')),
          manual_extreme_z_score_threshold: Number(form.get('manual_extreme_z_score_threshold')),
          stale_data_interval_multiplier: Number(form.get('stale_data_interval_multiplier')),
        }),
      })
      setSettings(updated)
      setNotice({ message: 'Settings saved' })
    } catch (error) {
      setNotice({ message: error instanceof Error ? error.message : 'Unable to save settings', error: true })
    }
  }
  async function saveSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!schedule) return
    setScheduleSaving(true)
    setScheduleMessage('')
    try {
      const updated = await request<MonitoringScheduleData>('/monitoring', {
        method: 'PATCH',
        body: JSON.stringify({
          enabled: schedule.enabled,
          interval_minutes: schedule.interval_minutes,
          simulation_enabled: schedule.simulation_enabled,
          simulation_batch_size: schedule.simulation_batch_size,
        }),
      })
      setSchedule(updated)
      setScheduleError('')
      setScheduleMessage('Scheduled analysis settings saved')
    } catch (error) {
      setScheduleError(scheduleRequestError(error))
    } finally {
      setScheduleSaving(false)
    }
  }
  return (
    <>
      <PageTitle title="Settings" description="Choose how the workspace looks and manage your preferences." />
      <section className={`${panelClass} grid gap-4`}>
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Appearance</h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">Choose the theme for your account. This preference is saved separately for each user on this device.</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2" role="group" aria-label="Color theme">
          {(['light', 'dark'] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={theme === option}
              onClick={() => onThemeChange(option)}
              className={`flex items-center justify-between rounded-xl border p-4 text-left transition ${
                theme === option
                  ? 'border-forest-600 bg-forest-50 text-forest-800 ring-2 ring-forest-100'
                  : 'border-slate-200 bg-white text-slate-700 hover:border-forest-300'
              }`}
            >
              <span>
                <strong className="block text-sm capitalize">{option} mode</strong>
                <span className="mt-1 block text-xs text-slate-500">{option === 'light' ? 'A bright, warm workspace' : 'A calm, low-light workspace'}</span>
              </span>
              <span className={`size-5 rounded-full border-4 ${theme === option ? 'border-forest-600 bg-white' : 'border-slate-300 bg-white'}`} />
            </button>
          ))}
        </div>
      </section>
      {isAdmin && <>
        <PageTitle title="System settings" description="Update validated workspace defaults. Changes are saved and audited." />
        {notice && <p className={`rounded-lg border p-3 text-xs ${notice.error ? 'border-rose-200 bg-rose-50 text-rose-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{notice.message}</p>}
        {settings && <form className={`${panelClass} grid gap-4 sm:grid-cols-3`} onSubmit={save}>
          <Field label="Maximum dataset archive size (MB)" name="max_upload_size_mb" defaultValue={settings.max_upload_size_mb} type="number" min={1} max={2048} required />
          <Field label="Default Isolation Forest trees" name="default_n_estimators" defaultValue={settings.default_n_estimators} type="number" min={10} max={1000} required />
          <Field label="Default contamination (blank = auto)" name="default_contamination" defaultValue={settings.default_contamination ?? ''} type="number" min={0.001} max={0.5} step={0.01} />
          <Field label="Default consecutive anomaly count" name="default_consecutive_anomaly_threshold" defaultValue={settings.default_consecutive_anomaly_threshold} type="number" min={1} max={100} required />
          <Field label="Default persistence window size" name="default_persistence_window_size" defaultValue={settings.default_persistence_window_size} type="number" min={1} max={100} required />
          <Field label="Default anomalies required in window" name="default_persistence_anomaly_threshold" defaultValue={settings.default_persistence_anomaly_threshold} type="number" min={1} max={100} required />
          <Field label="Normal readings to close alert episode" name="alert_episode_close_normal_count" defaultValue={settings.alert_episode_close_normal_count} type="number" min={1} max={1000} required />
          <Field label="Alert escalation delay (minutes)" name="alert_escalation_minutes" defaultValue={settings.alert_escalation_minutes} type="number" min={1} max={525600} required />
          <Field label="Manual extreme Z-score threshold" name="manual_extreme_z_score_threshold" defaultValue={settings.manual_extreme_z_score_threshold} type="number" min={0.01} max={1000} step="any" required />
          <Field label="Expected intervals before stale alert" name="stale_data_interval_multiplier" defaultValue={settings.stale_data_interval_multiplier} type="number" min={1} max={100} required />
          <button className={buttonClass} type="submit">Save settings</button>
        </form>}
        <MonitoringScheduleSettings
          isAdmin={isAdmin}
          schedule={schedule}
          loading={scheduleLoading}
          saving={scheduleSaving}
          error={scheduleError}
          message={scheduleMessage}
          onRetry={() => void loadSchedule()}
          onChange={setSchedule}
          onSubmit={(event) => void saveSchedule(event)}
        />
        <p className="text-xs leading-5 text-slate-500">Device categories and locations are maintained in the equipment register. Database URL, signing secrets, and CORS origins remain deployment-managed and are not editable here.</p>
      </>}
    </>
  )
}
