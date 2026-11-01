import { useCallback, useEffect, useState } from 'react'
import { request } from '../api'

import type { AuditEntry } from '../types/workflow'

import { buttonClass, panelClass, Field, EmptyPanel, ErrorMessage } from '../components/ui'

import { PageTitle } from '../components/PageTitle'
import { auditDetailLabelForDisplay, auditEntityLabels, auditSummary, auditValueForDisplay } from './auditHelpers'

export function AuditPage({ isAdmin }: { isAdmin: boolean }) {
  const [logs, setLogs] = useState<AuditEntry[]>([])
  const [filters, setFilters] = useState({ user_id: '', action: '', entity_type: '' })
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const pageSize = 50
  const refresh = useCallback(async (nextOffset = 0) => {
    try {
      const params = new URLSearchParams({ limit: String(pageSize), offset: String(nextOffset) })
      for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
      setLogs(await request<AuditEntry[]>(`/audit?${params}`))
      setOffset(nextOffset)
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load audit history')
    }
  }, [filters])
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refresh(0))
    return () => window.cancelAnimationFrame(frame)
  }, [refresh])
  return (
    <>
      <PageTitle
        title="Audit history"
        description={isAdmin
          ? 'Review important changes and exports across all users.'
          : 'Review important changes and exports from your account.'}
      />
      <form className={`${panelClass} grid gap-3 ${isAdmin ? 'sm:grid-cols-4' : 'sm:grid-cols-3'}`} onSubmit={(event) => { event.preventDefault(); void refresh(0) }}>
        {isAdmin && <Field label="User ID" name="user_id" value={filters.user_id} onChange={(value) => setFilters({ ...filters, user_id: value })} />}
        <Field label="Action" name="action" value={filters.action} onChange={(value) => setFilters({ ...filters, action: value })} />
        <Field label="Object type" name="entity_type" value={filters.entity_type} onChange={(value) => setFilters({ ...filters, entity_type: value })} />
        <button className={buttonClass} type="submit">Apply filters</button>
      </form>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-50 text-[10px] uppercase text-slate-500"><tr>{['Date', 'User', 'Action', 'Object', 'Details'].map((title) => <th className="px-4 py-3" key={title}>{title}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-100">{logs.map((log) => <tr key={log.id}>
            <td className="whitespace-nowrap px-4 py-3">{new Date(log.created_at).toLocaleString()}</td>
            <td className="px-4 py-3">{log.user_id ?? 'system'}</td>
            <td className="px-4 py-3 capitalize">{log.action.replaceAll('_', ' ')}</td>
            <td className="px-4 py-3 capitalize">{auditEntityLabels[log.entity_type] ?? log.entity_type.replaceAll('_', ' ')}</td>
            <td className="max-w-md px-4 py-3">
              <p className="font-medium leading-5 text-slate-700">{auditSummary(log)}</p>
              {Object.entries(log.details ?? {}).length > 0 && <ul className="mt-1 space-y-0.5 text-[10px] leading-4 text-slate-500">
                {Object.entries(log.details).map(([key, value]) => <li key={key}>
                  <span className="font-medium text-slate-600">{auditDetailLabelForDisplay(key)}:</span> {auditValueForDisplay(value)}
                </li>)}
              </ul>}
            </td>
          </tr>)}</tbody>
        </table>
        {logs.length === 0 && <EmptyPanel title="No audit events" body="Try a different filter or check again after workspace activity." />}
      </div>
      <div className="flex justify-end gap-2">
        <button className={buttonClass} disabled={offset === 0} onClick={() => void refresh(Math.max(0, offset - pageSize))}>Previous</button>
        <button className={buttonClass} disabled={logs.length < pageSize} onClick={() => void refresh(offset + pageSize)}>Next</button>
      </div>
    </>
  )
}
