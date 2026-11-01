import { Fragment, useState, type FormEvent } from 'react'
import { BarChart3, Plus, ShieldCheck, Wrench } from 'lucide-react'
import { request } from '../api'

import { FormField } from '../components/FormField'
import { PageHeading } from '../components/PageHeading'

import type { User } from '../types'

export function UsersPage({ users, currentUserId, onAction }: {
  users: User[]
  currentUserId: number
  onAction: (action: () => Promise<unknown>, success: string) => Promise<void>
}) {
  const [adding, setAdding] = useState(false)
  const [editingUserId, setEditingUserId] = useState<number | null>(null)
  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget))
    await onAction(() => request('/users', { method: 'POST', body: JSON.stringify(values) }), 'User account created')
    setAdding(false)
  }
  async function updateUser(event: FormEvent<HTMLFormElement>, userId: number) {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget))
    await onAction(
      () => request(`/users/${userId}`, { method: 'PATCH', body: JSON.stringify(values) }),
      'User details updated',
    )
    setEditingUserId(null)
  }
  function deleteUser(person: User) {
    if (!window.confirm(`Permanently delete ${person.full_name} (${person.email})? This cannot be undone.`)) return
    void onAction(
      () => request(`/users/${person.id}`, { method: 'DELETE' }),
      'User account deleted',
    )
  }
  return <>
    <PageHeading eyebrow="ACCESS CONTROL" title="People & permissions" description="Create accounts and assign the role that matches each person's responsibilities." action={<button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 bg-forest-600 text-white shadow-sm hover:bg-forest-700" onClick={() => setAdding(!adding)}><Plus size={16} /> Add user</button>} />
    <div className="grid gap-4 md:grid-cols-3"><div className="rounded-xl border border-slate-200 bg-white p-5 [&>svg]:mb-4 [&>svg]:text-forest-600 [&>strong]:block [&>strong]:text-sm [&>p]:mt-2 [&>p]:text-xs [&>p]:leading-5 [&>p]:text-slate-500"><ShieldCheck size={19} /><strong>Administrator</strong><p>Users, equipment, configurations, and all system records.</p></div><div className="rounded-xl border border-slate-200 bg-white p-5 [&>svg]:mb-4 [&>svg]:text-forest-600 [&>strong]:block [&>strong]:text-sm [&>p]:mt-2 [&>p]:text-xs [&>p]:leading-5 [&>p]:text-slate-500"><BarChart3 size={19} /><strong>Data analyst</strong><p>Dataset imports, preprocessing, model runs, and result evaluation.</p></div><div className="rounded-xl border border-slate-200 bg-white p-5 [&>svg]:mb-4 [&>svg]:text-forest-600 [&>strong]:block [&>strong]:text-sm [&>p]:mt-2 [&>p]:text-xs [&>p]:leading-5 [&>p]:text-slate-500"><Wrench size={19} /><strong>Maintenance technician</strong><p>Anomaly reviews, inspections, and maintenance work orders.</p></div></div>
    {adding && <form className="rounded-xl border border-slate-200 bg-white shadow-sm grid gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:grid-cols-2 xl:grid-cols-3" onSubmit={createUser}><FormField label="Full name" name="full_name" required /><FormField label="Email" name="email" required type="email" /><FormField label="Temporary password" name="password" required type="password" /><label>Role<select name="role"><option value="data_analyst">Data analyst</option><option value="maintenance_technician">Maintenance technician</option><option value="administrator">Administrator</option></select></label><div className="flex items-end"><button type="submit" className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 bg-forest-600 text-white shadow-sm hover:bg-forest-700">Create account</button></div></form>}
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 text-xs"><strong>{users.length} workspace members</strong></div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-400">
            <tr><th className="px-5 py-3">Person</th><th className="px-5 py-3">Email</th><th className="px-5 py-3">Role</th><th className="px-5 py-3">Access</th><th className="px-5 py-3">Actions</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100 text-slate-600">
            {users.map((person) => <Fragment key={person.id}>
              <tr>
                <td className="px-5 py-3"><strong>{person.full_name}</strong>{person.id === currentUserId && <span className="ml-2 text-[10px] text-slate-400">(you)</span>}</td>
                <td className="px-5 py-3">{person.email}</td>
                <td className="px-5 py-3">
                  <select aria-label={`Role for ${person.full_name}`} value={person.role} onChange={(event) => void onAction(() => request(`/users/${person.id}`, { method: 'PATCH', body: JSON.stringify({ role: event.target.value }) }), 'User role updated')}>
                    <option value="administrator">Administrator</option><option value="data_analyst">Data analyst</option><option value="maintenance_technician">Maintenance technician</option>
                  </select>
                </td>
                <td className="px-5 py-3">
                  <select aria-label={`Access for ${person.full_name}`} value={person.is_active ? 'active' : 'inactive'} onChange={(event) => void onAction(() => request(`/users/${person.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: event.target.value === 'active' }) }), 'Account access updated')}>
                    <option value="active">Active</option><option value="inactive">Inactive</option>
                  </select>
                </td>
                <td className="px-5 py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <button className="font-semibold text-forest-700" onClick={() => setEditingUserId(editingUserId === person.id ? null : person.id)}>{editingUserId === person.id ? 'Cancel edit' : 'Edit details'}</button>
                    <button className="font-semibold text-rose-700" disabled={person.id === currentUserId} title={person.id === currentUserId ? 'You cannot delete your own account' : 'Delete user'} onClick={() => deleteUser(person)}>Delete</button>
                    <button className="font-semibold text-forest-700" onClick={() => { if (!window.confirm(`Issue a temporary password for ${person.full_name}?`)) return; void onAction(async () => { const result = await request<{ temporary_password: string }>(`/users/${person.id}/reset-password`, { method: 'POST' }); window.alert(`Temporary password for ${person.email}:\n\n${result.temporary_password}\n\nCopy it now; it will not be shown again.`) }, 'Temporary password issued') }}>Reset password</button>
                  </div>
                </td>
              </tr>
              {editingUserId === person.id && <tr>
                <td colSpan={5} className="bg-slate-50 px-5 py-4">
                  <form className="grid gap-3 sm:grid-cols-[1fr_1fr_auto_auto]" onSubmit={(event) => void updateUser(event, person.id)}>
                    <FormField label="Full name" name="full_name" defaultValue={person.full_name} required />
                    <FormField label="Email" name="email" type="email" defaultValue={person.email} required />
                    <button className="self-end rounded-lg bg-forest-600 px-4 py-2 text-xs font-semibold text-white hover:bg-forest-700" type="submit">Save changes</button>
                    <button className="self-end rounded-lg border border-slate-200 px-4 py-2 text-xs font-semibold" type="button" onClick={() => setEditingUserId(null)}>Cancel</button>
                  </form>
                </td>
              </tr>}
            </Fragment>)}
          </tbody>
        </table>
      </div>
    </div>
  </>
}
