import { useEffect, useState, type FormEvent } from 'react'
import { Cpu, Plus } from 'lucide-react'
import { request } from '../api'

import { FormField } from '../components/FormField'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/EmptyState'
import { SourceTypeBadge, SourceTypeSelect } from '../components/SourceTypeControls'
import { matchesSourceType, type SourceTypeFilter } from '../sourceType'

import type { Device, User } from '../types'

export function DevicesPage({ devices, canEdit, isAdministrator, onAction, onSelectDevice }: { devices: Device[]; canEdit: boolean; isAdministrator: boolean; onAction: (action: () => Promise<unknown>, success: string) => Promise<void>; onSelectDevice: (deviceId: number) => void }) {
  const [adding, setAdding] = useState(false)
  const [responsibleUsers, setResponsibleUsers] = useState<User[]>([])
  const [error, setError] = useState('')
  const [sourceFilter, setSourceFilter] = useState<SourceTypeFilter>('all')
  const filteredDevices = devices.filter((device) =>
    matchesSourceType(sourceFilter, device.source_type),
  )

  useEffect(() => {
    if (!isAdministrator) return
    void request<User[]>('/devices/responsible-users')
      .then(setResponsibleUsers)
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : 'Unable to load measurement recorders')
      })
  }, [isAdministrator])

  async function createDevice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const values = Object.fromEntries(form)
    if (isAdministrator) {
      values.responsible_user_id = String(form.get('responsible_user_id') ?? '')
    }
    await onAction(() => request('/devices', { method: 'POST', body: JSON.stringify(values) }), 'Equipment registered')
    setAdding(false)
  }
  return <>
    <PageHeading eyebrow="EQUIPMENT REGISTER" title="Your equipment" description="Manage registered assets, locations, device status, and measurement provenance." action={canEdit && <button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 bg-forest-600 text-white shadow-sm hover:bg-forest-700" onClick={() => setAdding(!adding)}><Plus size={16} /> Add equipment</button>} />
    {error && <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">{error}</p>}
    {adding && <form className="rounded-xl border border-slate-200 bg-white shadow-sm grid gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:grid-cols-2 xl:grid-cols-3" onSubmit={createDevice}><FormField label="Device name" name="name" required placeholder="Hydraulic press 04" /><FormField label="Unique code" name="device_code" required placeholder="PRESS-004" /><FormField label="Type" name="device_type" required placeholder="Hydraulic press" /><FormField label="Category" name="category" placeholder="Manufacturing" /><FormField label="Location" name="location" placeholder="Factory floor  -  line 2" /><label>Description<input name="description" placeholder="Optional equipment notes" /></label><label>Measurement source<select name="source_type" defaultValue="manual"><option value="manual">Manual equipment readings</option><option value="simulated">Synthetic simulated readings</option></select></label>{isAdministrator && <label>Responsible measurement recorder<select name="responsible_user_id" required defaultValue=""><option value="" disabled>Assign an active user</option>{responsibleUsers.map((responsibleUser) => <option key={responsibleUser.id} value={responsibleUser.id}>{responsibleUser.full_name} · {responsibleUser.role.replaceAll('_', ' ')}</option>)}</select></label>}<div className="flex items-end"><button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 bg-forest-600 text-white shadow-sm hover:bg-forest-700" type="submit">Save equipment</button></div></form>}
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"><div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4 text-xs [&>div>strong]:text-slate-800 [&>div>span]:text-slate-400"><div><strong>{filteredDevices.length} devices</strong><span>  -  Sorted by name</span></div><SourceTypeSelect value={sourceFilter} onChange={setSourceFilter} /></div><div className="overflow-x-auto"><table className="w-full border-collapse text-left text-xs"><thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-400 [&>tr>th]:whitespace-nowrap [&>tr>th]:px-5 [&>tr>th]:py-3"><tr><th>Equipment</th><th>Code</th><th>Type / category</th><th>Location</th><th>Source</th><th>Status</th></tr></thead><tbody className="[&>tr]:border-t [&>tr]:border-slate-100 [&>tr>td]:px-5 [&>tr>td]:py-3.5 [&>tr>td]:text-slate-600">{filteredDevices.map((device) => <tr key={device.id} className="cursor-pointer hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-forest-600" role="button" tabIndex={0} onClick={() => onSelectDevice(device.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelectDevice(device.id) } }}><td><div className="flex items-center gap-3 [&>strong]:font-semibold [&>strong]:text-slate-800"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-forest-50 text-forest-600"><Cpu size={17} /></span><strong>{device.name}</strong></div></td><td className="font-mono text-[11px] text-slate-600">{device.device_code}</td><td>{device.device_type}{device.category && <small className="mt-1 block text-[10px] text-slate-400">{device.category}</small>}</td><td>{device.location || <span className="text-sm leading-6 text-slate-500">Not specified</span>}</td><td><SourceTypeBadge sourceType={device.source_type} /></td><td><StatusBadge value={device.status} /></td></tr>)}</tbody></table>{filteredDevices.length === 0 && <EmptyState icon={<Cpu size={20} />} title="No matching equipment" body={devices.length ? 'No devices match this source filter.' : 'Add a device to link source data and maintenance history.'} />}</div></div>
  </>
}
