import { Fragment, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Check, Database, FileChartColumn, HardDriveUpload, Plus } from 'lucide-react'
import { CartesianGrid, ResponsiveContainer, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { request } from '../api'
import { numericAnalysisFeatures } from '../datasetFeatures'

import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/EmptyState'
import { NoticeBanner } from '../components/NoticeBanner'

import type { Dataset, ImsComparison, NasaImsImportStatus } from '../types'

export function DatasetsPage({ datasets, canEdit, onAction }: { datasets: Dataset[]; canEdit: boolean; onAction: (action: () => Promise<unknown>, success: string) => Promise<void> }) {
  const [activeTab, setActiveTab] = useState<'experiments' | 'compare' | 'import'>('experiments')
  const [readyNoticeVisible, setReadyNoticeVisible] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [selectedDatasetId, setSelectedDatasetId] = useState('')
  const [feature, setFeature] = useState('bearing_1_rms')
  const [pageOffset, setPageOffset] = useState(0)
  const [comparison, setComparison] = useState<ImsComparison | null>(null)
  const [error, setError] = useState('')
  const [importStatusError, setImportStatusError] = useState('')
  const [busy, setBusy] = useState(false)
  const [importStatus, setImportStatus] = useState<NasaImsImportStatus>({
    status: 'not_started',
    downloaded_bytes: 0,
    total_bytes: null,
    dataset_ids: [],
    error_message: null,
    updated_at: null,
  })
  const completedImportHandled = useRef(false)
  const imsDatasets = datasets.filter((dataset) => dataset.file_format === 'nasa_ims')
  const selectedDataset = imsDatasets.find((dataset) => String(dataset.id) === selectedDatasetId)
  const featureOptions = numericAnalysisFeatures(selectedDataset)
    .map((name) => ({ value: name, label: name.replaceAll('_', ' ') }))
  const selectedFeature = featureOptions.some((option) => option.value === feature)
    ? feature
    : featureOptions[0]?.value ?? ''
  const importing = ['queued', 'downloading', 'extracting', 'processing'].includes(importStatus.status)

  useEffect(() => {
    let active = true
    async function refreshImportStatus() {
      try {
        const result = await request<NasaImsImportStatus>('/datasets/nasa-ims/import-status')
        if (!active) return
        setImportStatus(result)
        setImportStatusError('')
        if (result.status === 'completed' && result.dataset_ids.length && !completedImportHandled.current) {
          completedImportHandled.current = true
          setSelectedDatasetId(String(result.dataset_ids[0]))
          setReadyNoticeVisible(true)
          await onAction(() => Promise.resolve(), 'NASA IMS datasets are ready to analyze')
        }
      } catch (caught) {
        if (active) setImportStatusError(caught instanceof Error ? caught.message : 'Unable to refresh NASA IMS import status')
      }
    }
    void refreshImportStatus()
    const timer = window.setInterval(() => void refreshImportStatus(), 3000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [onAction])

  useEffect(() => {
    if (!selectedDatasetId) return
    let active = true
    async function refreshComparison() {
      try {
        const result = await request<ImsComparison>(
          `/datasets/${selectedDatasetId}/comparison?feature=${encodeURIComponent(selectedFeature)}&limit=100&offset=${pageOffset}`,
        )
        if (active) {
          setComparison(result)
          setError('')
        }
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : 'Unable to load comparison results')
      }
    }
    void refreshComparison()
    const timer = comparison?.status === 'running'
      ? window.setInterval(() => void refreshComparison(), 3000)
      : undefined
    return () => {
      active = false
      if (timer !== undefined) window.clearInterval(timer)
    }
  }, [selectedDatasetId, selectedFeature, pageOffset, comparison?.status])

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file) return
    setBusy(true)
    try {
      const body = new FormData()
      body.set('file', file)
      await onAction(async () => {
        const imported = await request<Dataset>('/datasets', { method: 'POST', body })
        setSelectedDatasetId(String(imported.id))
        setFile(null)
        setComparison(null)
      }, 'NASA IMS dataset imported and preprocessed')
    } finally {
      setBusy(false)
    }
  }

  async function initializeNasaIms() {
    setBusy(true)
    try {
      await onAction(async () => {
        const result = await request<NasaImsImportStatus>('/datasets/nasa-ims/initialize', { method: 'POST' })
        setImportStatus(result)
        setImportStatusError('')
        completedImportHandled.current = false
      }, 'NASA IMS initialization started')
    } finally {
      setBusy(false)
    }
  }

  async function compare() {
    if (!selectedDatasetId) return
    await onAction(async () => {
      await request(`/datasets/${selectedDatasetId}/compare`, { method: 'POST' })
      setPageOffset(0)
      setComparison((current) => current
        ? { ...current, status: 'running' }
        : {
            dataset_id: Number(selectedDatasetId),
            status: 'running',
            feature: selectedFeature,
            statistics: [],
            series: [],
            anomalies: [],
            total_anomalies: 0,
            unique_affected_measurements: 0,
          })
    }, 'Isolation Forest and Z-score comparisons queued')
  }

  const chartData = new Map<number, Record<string, number>>()
  comparison?.series.forEach((series) => {
    for (const point of series.points) {
      if (point.value === null) continue
      const row = chartData.get(point.sample) ?? { sample: point.sample }
      row[series.algorithm] = point.value
      chartData.set(point.sample, row)
      if (point.is_anomaly) row[`${series.algorithm} flagged`] = point.value
    }
  })
  const chartAlgorithms = comparison?.series.map((series) => series.algorithm) ?? []

  const tabs = [
    { id: 'experiments', label: 'Experiments' },
    { id: 'compare', label: 'Compare algorithms' },
    { id: 'import', label: 'Import' },
  ] as const
  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const currentIndex = tabs.findIndex((tab) => tab.id === activeTab)
    const nextIndex = event.key === 'ArrowRight'
      ? (currentIndex + 1) % tabs.length
      : event.key === 'ArrowLeft'
        ? (currentIndex - 1 + tabs.length) % tabs.length
        : event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? tabs.length - 1
            : currentIndex
    if (nextIndex !== currentIndex) {
      event.preventDefault()
      setActiveTab(tabs[nextIndex].id)
      document.getElementById(`dataset-tab-${tabs[nextIndex].id}`)?.focus()
    }
  }
  function selectComparisonDataset(datasetId: number) {
    setSelectedDatasetId(String(datasetId))
    setPageOffset(0)
    setComparison(null)
    setActiveTab('compare')
  }

  return <div className="space-y-5">
    <PageHeading
      eyebrow="NASA IMS BEARINGS"
      title="Bearing datasets"
      description="Initialize or upload the NASA IMS Bearings archive, analyze its vibration windows, and keep each historical test set separate from physical equipment."
      action={<div className="flex flex-wrap gap-2">
        <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600"><HardDriveUpload size={15} /> {imsDatasets.length.toLocaleString()} imported experiments</span>
        <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600"><Database size={15} /> {importStatus.status === 'completed' ? importStatus.dataset_ids.length.toLocaleString() : '0'} test sets available</span>
      </div>}
    />
    <nav aria-label="Dataset sections" className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-1">
      <div className="flex min-w-max gap-1" role="tablist">
        {tabs.map((tab) => <button
          aria-controls={`dataset-panel-${tab.id}`}
          aria-selected={activeTab === tab.id}
          className={`min-h-10 whitespace-nowrap rounded-lg px-4 py-2 text-xs font-semibold ${activeTab === tab.id ? 'bg-forest-600 text-white' : 'text-slate-600 hover:bg-slate-50'}`}
          id={`dataset-tab-${tab.id}`}
          key={tab.id}
          onClick={() => setActiveTab(tab.id)}
          onKeyDown={handleTabKeyDown}
          role="tab"
          tabIndex={activeTab === tab.id ? 0 : -1}
          type="button"
        >{tab.label}</button>)}
      </div>
    </nav>
    {readyNoticeVisible && <NoticeBanner notice={{ kind: 'success', text: 'NASA IMS datasets are ready to analyze' }} onDismiss={() => setReadyNoticeVisible(false)} />}
    {activeTab === 'import' && <section aria-labelledby="dataset-tab-import" className="space-y-4" id="dataset-panel-import" role="tabpanel" tabIndex={0}>
    <div className="grid gap-4 lg:grid-cols-2">
    {canEdit && <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-5 py-4">
        <h2 className="text-sm font-semibold text-slate-900">Initialize the NASA IMS Bearings dataset</h2>
        <p className="mt-1 text-xs text-slate-500">Download the historical experiment archive and store validated vibration windows in PostgreSQL. Test sets remain datasets and are never registered as physical equipment.</p>
      </div>
      <div className="flex justify-end p-5">
        <button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg bg-forest-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-forest-700 disabled:cursor-not-allowed disabled:opacity-50" type="button" disabled={busy || importing} onClick={() => void initializeNasaIms()}>
          {busy || importing ? 'Initializing...' : importStatus.status === 'failed' ? 'Retry initialization' : importStatus.status === 'completed' ? 'Use imported dataset' : 'Download and initialize'}
          <span>{'->'}</span>
        </button>
      </div>
      {importStatusError && <p className="border-t border-slate-100 px-5 py-3 text-sm text-rose-700" role="alert">{importStatusError}</p>}
      {importStatus.status !== 'not_started' && <div className="border-t border-slate-100 px-5 py-4 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <strong className="capitalize text-slate-800">{importStatus.status.replaceAll('_', ' ')}</strong>
          {importStatus.status === 'downloading' && importStatus.total_bytes
            ? <span className="text-xs text-slate-500">{(importStatus.downloaded_bytes / 1024 / 1024).toFixed(0)} / {(importStatus.total_bytes / 1024 / 1024).toFixed(0)} MB</span>
            : importStatus.status === 'completed' ? <span className="text-xs text-forest-700">Test sets available</span> : null}
        </div>
        {importStatus.updated_at && <p className="mt-1 text-[10px] text-slate-500">Updated {new Date(importStatus.updated_at).toLocaleString()}</p>}
        {importStatus.status === 'downloading' && importStatus.total_bytes && <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-forest-600 transition-[width]" style={{ width: `${Math.min(100, (importStatus.downloaded_bytes / importStatus.total_bytes) * 100)}%` }} /></div>}
        {importStatus.status === 'extracting' && <p className="mt-1 text-xs text-slate-500">Validating and extracting the nested ZIP, 7z, and RAR test archives.</p>}
        {importStatus.status === 'processing' && <p className="mt-1 text-xs text-slate-500">Calculating vibration features and saving windows to PostgreSQL. This may take several minutes.</p>}
        {importStatus.error_message && <p className="mt-2 text-sm text-rose-700" role="alert">{importStatus.error_message}</p>}
      </div>}
    </section>}
    {canEdit && <form className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm" onSubmit={(event) => void upload(event)}>
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Manual ZIP upload fallback</h2>
          <p className="mt-1 text-xs text-slate-500">Upload the original NASA archive ZIP or a ZIP containing timestamped raw vibration files. Imported experiment datasets remain independent of registered equipment.</p>
        </div>
        <span className="grid size-9 place-items-center rounded-lg bg-forest-50 text-forest-600"><HardDriveUpload size={18} /></span>
      </div>
      <div className="grid gap-4 p-5 lg:grid-cols-[1.4fr_auto] lg:items-end">
        <label className="flex min-h-14 cursor-pointer items-center gap-3 rounded-lg border border-dashed border-slate-300 px-4 py-3 hover:border-forest-400 hover:bg-forest-50/30 [&>input]:sr-only [&>span:last-child]:grid [&>span:last-child]:min-w-0 [&>span:last-child>strong]:truncate [&>span:last-child>strong]:text-xs [&>span:last-child>strong]:text-slate-700 [&>span:last-child>small]:text-[10px] [&>span:last-child>small]:text-slate-600">
          <input type="file" accept=".zip,application/zip" required onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          <span className="grid size-9 place-items-center rounded-lg bg-slate-100 text-slate-600"><Plus size={18} /></span>
          <span><strong>{file?.name ?? 'Choose the NASA IMS ZIP archive'}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB selected` : 'Original NASA archive or ZIP of raw timestamped files'}</small></span>
        </label>
        <button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg bg-forest-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-forest-700 disabled:cursor-not-allowed disabled:opacity-50" type="submit" disabled={busy || importing || !file}>
          {busy ? 'Importing...' : 'Import ZIP'}<span>{'->'}</span>
        </button>
      </div>
    </form>}
    {!canEdit && <EmptyState icon={<HardDriveUpload size={20} />} title="Import controls unavailable" body="Your role does not have permission to initialize or upload datasets." />}
    </div>
    </section>}
    {activeTab === 'compare' && <section aria-labelledby="dataset-tab-compare" className="space-y-4" id="dataset-panel-compare" role="tabpanel" tabIndex={0}>
    {error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800" role="alert">{error}</div>}
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end">
        <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">NASA IMS dataset<select className="min-h-10 rounded-lg border border-slate-200 px-3 py-2.5 text-sm" value={selectedDatasetId} onChange={(event) => { setSelectedDatasetId(event.target.value); setPageOffset(0); setComparison(null) }}><option value="">Select an imported archive</option>{imsDatasets.map((dataset) => <option value={dataset.id} key={dataset.id}>{dataset.name} - {dataset.device_name}</option>)}</select></label>
        <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-600">Chart feature<select className="min-h-10 rounded-lg border border-slate-200 px-3 py-2.5 text-sm" value={selectedFeature} onChange={(event) => setFeature(event.target.value)} disabled={!featureOptions.length}>{featureOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>
        {canEdit && <button className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-forest-600 px-4 py-2 text-sm font-semibold text-white hover:bg-forest-700 disabled:opacity-50" disabled={!selectedDatasetId || !selectedFeature || comparison?.status === 'running'} onClick={() => void compare()}>{comparison?.status === 'running' ? 'Comparing...' : 'Compare algorithms'}</button>}
      </div>
    </section>
    <section aria-label="Algorithm comparison results" className="green-panel space-y-4 rounded-xl border border-forest-200 bg-forest-50/40 p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2 border-b border-forest-200 pb-3">
        <span className="text-xs font-semibold text-forest-800">Previous run status</span>
        {comparison && <StatusBadge value={comparison.status} />}
      </div>
      {comparison?.status === 'not_started' && <EmptyState icon={<FileChartColumn size={20} />} title="No comparison run yet" body="Run a comparison to see results from both methods." />}
      {comparison === null && selectedDatasetId && !error && <p className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500" role="status">Loading previous comparison status...</p>}
      {comparison === null && !selectedDatasetId && <EmptyState icon={<FileChartColumn size={20} />} title="Choose a NASA IMS dataset" body="Select an imported archive above to view its previous status or run an algorithm comparison." />}
      {comparison?.status === 'running' && <div className="green-panel rounded-lg border border-forest-200 bg-forest-50/70 p-4 text-sm text-forest-800" role="status"><p className="font-semibold">Comparison in progress</p><p className="mt-1">Both algorithms are running on the preprocessed bearing measurements.</p></div>}
      {comparison?.status === 'failed' && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800" role="alert"><p className="font-semibold">Comparison failed</p><p className="mt-1">{comparison.errors?.map((item) => item.message).filter(Boolean).join(' ') || 'The comparison failed. Review the analysis history for details.'}</p></div>}
    {comparison?.status === 'completed' && <>
      <div className="grid gap-4 sm:grid-cols-2">
        {comparison.statistics.map((item) => <article key={item.analysis_id} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-sm font-semibold text-slate-900">{item.algorithm}</h2><div className="mt-4 grid grid-cols-3 gap-3 text-xs"><div><span className="text-slate-500">Samples</span><strong className="mt-1 block text-lg text-slate-900">{item.records.toLocaleString()}</strong></div><div><span className="text-slate-500">Anomalies</span><strong className="mt-1 block text-lg text-slate-900">{item.anomalies.toLocaleString()}</strong></div><div><span className="text-slate-500">Flagged</span><strong className="mt-1 block text-lg text-slate-900">{item.anomaly_rate}%</strong></div></div></article>)}
      </div>
      <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-sm font-semibold text-slate-900">Bearing vibration feature comparison</h2><p className="mt-1 text-xs text-slate-500">Sampled trend of {selectedFeature.replaceAll('_', ' ')} across the measurement windows; markers indicate each method's detections.</p><div className="mt-4 h-72"><ResponsiveContainer width="100%" height="100%"><LineChart data={[...chartData.values()].sort((left, right) => Number(left.sample) - Number(right.sample))}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="sample" tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 10 }} /><Tooltip />{chartAlgorithms.map((algorithm, index) => { const color = index === 0 ? '#466b4c' : '#8b9a61'; return <Fragment key={algorithm}><Line type="monotone" dataKey={algorithm} name={algorithm} dot={false} stroke={color} connectNulls /><Line dataKey={`${algorithm} flagged`} name={`${algorithm} anomaly`} stroke="none" dot={{ r: 3, fill: color, stroke: color }} connectNulls={false} /></Fragment> })}</LineChart></ResponsiveContainer></div></article>
      <article className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"><div className="flex items-center justify-between border-b border-slate-100 px-5 py-4"><div><h2 className="text-sm font-semibold text-slate-900">Detected bearing anomalies</h2><p className="mt-1 text-xs text-slate-500">Showing {comparison.anomalies.length} of {comparison.total_anomalies.toLocaleString()} algorithm-specific detections across both methods, affecting {comparison.unique_affected_measurements.toLocaleString()} unique measurement windows.</p></div></div><div className="overflow-x-auto"><table className="w-full border-collapse text-left text-xs"><thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-400"><tr><th className="px-4 py-3">Method</th><th className="px-4 py-3">Sample</th><th className="px-4 py-3">Source file</th><th className="px-4 py-3">Timestamp</th><th className="px-4 py-3">Score</th><th className="px-4 py-3">Severity</th><th className="px-4 py-3">Feature readings</th></tr></thead><tbody>{comparison.anomalies.map((item) => <tr key={item.id} className="border-t border-slate-100"><td className="px-4 py-3 font-medium text-slate-800">{item.algorithm}</td><td className="px-4 py-3">{item.sample ?? '-'}</td><td className="px-4 py-3">{item.source_file ?? '-'}</td><td className="px-4 py-3">{item.timestamp ? new Date(item.timestamp).toLocaleString() : 'Not available'}</td><td className="px-4 py-3">{item.score?.toFixed(4) ?? '-'}</td><td className="px-4 py-3 capitalize">{item.severity}</td><td className="max-w-sm px-4 py-3"><details><summary className="cursor-pointer text-forest-700">View readings</summary><div className="mt-2 max-h-36 overflow-auto font-mono text-[10px] leading-5">{Object.entries(item.features).map(([name, value]) => <div key={name}>{name.replaceAll('_', ' ')}: {value.toPrecision(5)}</div>)}</div></details></td></tr>)}</tbody></table>{comparison.total_anomalies === 0 && <EmptyState icon={<Check size={20} />} title="No anomalies detected" body="Both methods completed and did not flag any vibration windows." />}</div>{comparison.total_anomalies > 100 && <div className="flex justify-between border-t border-slate-100 px-5 py-3"><button className="text-xs font-semibold text-forest-700 disabled:text-slate-400" disabled={pageOffset === 0} onClick={() => setPageOffset(Math.max(0, pageOffset - 100))}>Previous</button><button className="text-xs font-semibold text-forest-700 disabled:text-slate-400" disabled={pageOffset + 100 >= comparison.total_anomalies} onClick={() => setPageOffset(pageOffset + 100)}>Next</button></div>}</article>
    </>}
    </section>
    </section>}
    {activeTab === 'experiments' && <section aria-labelledby="dataset-tab-experiments" className="space-y-4" id="dataset-panel-experiments" role="tabpanel" tabIndex={0}>
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-4 text-xs">
          <strong className="text-slate-800">Imported NASA IMS experiments</strong>
          <span className="text-slate-500">Original ZIP files are preserved</span>
        </div>
        <div className="max-h-[70vh] overflow-auto">
          <table className="w-full min-w-[760px] border-collapse text-left text-xs">
            <thead className="sticky top-0 z-[1] bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-400">
              <tr><th className="px-5 py-3">Dataset / experiment</th><th className="px-5 py-3">Registered device</th><th className="px-5 py-3 text-right">Vibration windows</th><th className="px-5 py-3 text-right">Extracted features</th><th className="px-5 py-3">Import date</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Action</th></tr>
            </thead>
            <tbody>
              {imsDatasets.map((dataset) => <tr key={dataset.id} className="border-t border-slate-100">
                <td className="px-5 py-3"><div className="flex items-center gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-forest-50 text-forest-600"><FileChartColumn size={17} /></span><div><strong className="text-slate-800">{dataset.name}</strong><small className="mt-1 block text-[10px] text-slate-500">{dataset.file_name}</small></div></div></td>
                <td className="px-5 py-3 text-slate-600">{dataset.device_name ?? 'No registered device'}</td>
                <td className="px-5 py-3 text-right tabular-nums text-slate-700">{dataset.row_count.toLocaleString()}</td>
                <td className="px-5 py-3 text-right text-slate-700"><span className="tabular-nums">{numericAnalysisFeatures(dataset).length.toLocaleString()}</span><small className="ml-1 text-slate-500">numeric vibration features</small></td>
                <td className="px-5 py-3 text-slate-600">{new Date(dataset.uploaded_at).toLocaleDateString()}</td>
                <td className="px-5 py-3"><StatusBadge value={dataset.status} /></td>
                <td className="px-5 py-3"><button className="whitespace-nowrap text-xs font-semibold text-forest-700 hover:text-forest-900" type="button" onClick={() => selectComparisonDataset(dataset.id)}>Compare this dataset</button></td>
              </tr>)}
            </tbody>
          </table>
          {imsDatasets.length === 0 && <EmptyState icon={<Database size={20} />} title="No NASA IMS datasets imported" body="Upload an original IMS Bearings ZIP archive to begin." />}
        </div>
      </div>
    </section>}
  </div>
}
