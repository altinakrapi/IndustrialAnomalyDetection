import { useCallback, useEffect, useState } from 'react'
import { download, request } from '../api'

import type { Action, Device } from '../types/workflow'

import { inputClass, buttonClass, panelClass, ErrorMessage } from '../components/ui'

import { PageTitle } from '../components/PageTitle'
import { ModelDisclaimer } from '../components/ModelDisclaimer'

export function ReportsPage({ devices, onAction }: { devices: Device[]; onAction: Action }) {
  const [deviceId, setDeviceId] = useState('')
  const [history, setHistory] = useState<{ id: number; title: string; report_type: string; file_format: string; created_at: string }[]>([])
  const [error, setError] = useState('')
  const refresh = useCallback(async () => {
    try {
      setHistory(await request('/reports/history'))
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load report history')
    }
  }, [])
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refresh())
    return () => window.cancelAnimationFrame(frame)
  }, [refresh])
  async function exportReport(path: string, fileName: string, success: string) {
    await onAction(async () => {
      await download(path, fileName)
      await refresh()
    }, success)
  }
  return (
    <>
      <PageTitle title="Reports and exports" description="Generate device, analysis, anomaly, and maintenance reports. Generated reports are recorded in history." />
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <section className={`${panelClass} grid gap-4 sm:grid-cols-2`}>
        <label className="grid gap-1.5 text-xs font-medium text-slate-600">Device report
          <select className={inputClass} value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
            <option value="">Select equipment</option>{devices.map((device) => <option key={device.id} value={device.id}>{device.name}  -  {device.device_code}</option>)}
          </select>
        </label>
        <div className="flex flex-wrap items-end gap-2">
          <button className={buttonClass} disabled={!deviceId} onClick={() => void exportReport(`/reports/device/${deviceId}.pdf`, `device-${deviceId}.pdf`, 'Device PDF generated')}>Download device PDF</button>
          <button className={buttonClass} onClick={() => void exportReport('/anomalies/export.csv', 'anomalies.csv', 'Anomaly CSV exported')}>Export anomalies CSV</button>
          <button className={buttonClass} onClick={() => void exportReport('/reports/maintenance.csv', 'maintenance-orders.csv', 'Maintenance CSV exported')}>Export maintenance CSV</button>
        </div>
      </section>
      <p className="px-1 text-[10px] font-semibold tracking-wide text-slate-500">
        <ModelDisclaimer />
      </p>
      <section className={panelClass}>
        <h2 className="mb-4 text-sm font-semibold text-slate-900">Generated report history</h2>
        <div className="divide-y divide-slate-100">{history.map((report) => <div className="flex flex-wrap items-center justify-between gap-3 py-3 text-xs" key={report.id}>
          <div><strong className="text-slate-800">{report.title}</strong><small className="mt-1 block text-slate-500">{report.report_type}  -  {report.file_format}  -  {new Date(report.created_at).toLocaleString()}</small></div>
        </div>)}</div>
        {!history.length && <p className="text-xs text-slate-500">No reports generated yet.</p>}
      </section>
    </>
  )
}
