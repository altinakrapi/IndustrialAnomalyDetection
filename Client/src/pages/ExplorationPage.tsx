import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, Search, X } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import { download, request } from '../api'
import { numericAnalysisFeatures } from '../datasetFeatures'

import type { Action, Dataset, Exploration, DatasetStatistics } from '../types/workflow'

import { inputClass, buttonClass, panelClass, StatCard, ErrorMessage } from '../components/ui'

import { PageTitle } from '../components/PageTitle'

export function ExplorationPage({ datasets, onAction, onNavigate }: { datasets: Dataset[]; onAction: Action; onNavigate: (target: 'Analysis' | 'Anomalies', query?: string) => void }) {
  const [datasetId, setDatasetId] = useState('')
  const [deviceId, setDeviceId] = useState('')
  const [features, setFeatures] = useState<string[]>([])
  const [timeColumn, setTimeColumn] = useState('')
  const [startTime, setStartTime] = useState('')
  const [endTime, setEndTime] = useState('')
  const [algorithm, setAlgorithm] = useState('')
  const [severity, setSeverity] = useState('')
  const [exploration, setExploration] = useState<Exploration | null>(null)
  const [statistics, setStatistics] = useState<DatasetStatistics | null>(null)
  const [selectedAnomaly, setSelectedAnomaly] = useState<Record<string, unknown> | null>(null)
  const [featureSearch, setFeatureSearch] = useState('')
  const [featureMenuOpen, setFeatureMenuOpen] = useState(false)
  const [moreActionsOpen, setMoreActionsOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const availableDatasets = datasets.filter((dataset) => !deviceId
    || (deviceId === 'historical'
      ? dataset.device_id === null
      : String(dataset.device_id) === deviceId))
  const selectedDataset = datasets.find((dataset) => String(dataset.id) === datasetId)
  const numericFeatures = useMemo(
    () => numericAnalysisFeatures(selectedDataset),
    [selectedDataset],
  )
  const load = useCallback(async () => {
    if (!datasetId) {
      setExploration(null)
      setStatistics(null)
      return
    }
    setLoading(true)
    const params = new URLSearchParams()
    const selectedFeatures = features.length ? features : numericFeatures.slice(0, 1)
    const selectedFeature = selectedFeatures[0] ?? ''
    if (selectedFeature) params.set('feature', selectedFeature)
    if (selectedFeatures.length) params.set('features', selectedFeatures.join(','))
    if (timeColumn) params.set('time_column', timeColumn)
    if (startTime) params.set('start_time', new Date(startTime).toISOString())
    if (endTime) params.set('end_time', new Date(endTime).toISOString())
    if (algorithm) params.set('algorithm', algorithm)
    if (severity) params.set('severity', severity)
    try {
      const [summary, chart] = await Promise.all([
        request<DatasetStatistics>(`/datasets/${datasetId}/statistics?${new URLSearchParams({
          ...(selectedFeature ? { feature: selectedFeature } : {}),
          ...(startTime ? { start_time: new Date(startTime).toISOString() } : {}),
          ...(endTime ? { end_time: new Date(endTime).toISOString() } : {}),
        })}`),
        request<Exploration>(`/datasets/${datasetId}/exploration?${params}`),
      ])
      setStatistics(summary)
      setExploration(chart)
      setFeatures((current) => current.length === chart.features.length
        && current.every((name, index) => name === chart.features[index])
        ? current
        : chart.features)
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to explore dataset')
    } finally {
      setLoading(false)
    }
  }, [algorithm, datasetId, endTime, features, numericFeatures, severity, startTime, timeColumn])
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void load())
    return () => window.cancelAnimationFrame(frame)
  }, [load])
  const chartPoints = exploration?.series.map((point) => ({
    ...point,
    ...point.values,
    anomaly_value: point.anomalies.length ? point.value : null,
  })) ?? []
  const anomalyPoints = exploration?.series.flatMap((point) => point.anomalies.map((anomaly) => ({
    ...anomaly,
    source_row_number: point.source_row_number,
    timestamp: point.timestamp,
    feature_values: point.values,
  }))) ?? []
  const selectedStats = features[0] ? statistics?.columns[features[0]] : undefined
  const correlation = (left: string, right: string) => {
    const pairs = chartPoints
      .map((point) => [point.values[left], point.values[right]] as const)
      .filter((pair): pair is readonly [number, number] => pair[0] !== null && pair[0] !== undefined && pair[1] !== null && pair[1] !== undefined)
    if (pairs.length < 2) return null
    const leftMean = pairs.reduce((total, pair) => total + pair[0], 0) / pairs.length
    const rightMean = pairs.reduce((total, pair) => total + pair[1], 0) / pairs.length
    const covariance = pairs.reduce((total, pair) => total + (pair[0] - leftMean) * (pair[1] - rightMean), 0)
    const leftDeviation = Math.sqrt(pairs.reduce((total, pair) => total + (pair[0] - leftMean) ** 2, 0))
    const rightDeviation = Math.sqrt(pairs.reduce((total, pair) => total + (pair[1] - rightMean) ** 2, 0))
    return leftDeviation && rightDeviation ? covariance / (leftDeviation * rightDeviation) : null
  }

  function toggleFeature(name: string) {
    setFeatures((current) => current.includes(name)
      ? current.filter((featureName) => featureName !== name)
      : current.length < 8 ? [...current, name] : current)
  }

  async function inspectAnomaly(anomalyId: number | null) {
    if (anomalyId === null) return
    try {
      setSelectedAnomaly(await request<Record<string, unknown>>(`/anomalies/${anomalyId}`))
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load anomaly details')
    }
  }

  async function archive() {
    if (!selectedDataset || !window.confirm(`Archive ${selectedDataset.name}? Existing analysis history will be preserved.`)) return
    await onAction(() => request(`/datasets/${selectedDataset.id}/archive`, { method: 'PATCH' }), 'Dataset archived')
  }
  function exportCurrentView() {
    const rows = chartPoints
    if (!selectedDataset || !rows.length) return
    const headings = ['timestamp', 'source_row_number', ...features, 'algorithm', 'severity', 'anomaly_score', 'analysis_id']
    const lines = rows.map((point) => {
      const flagged = point.anomalies[0]
      return [
        point.timestamp ?? '',
        point.source_row_number,
        ...features.map((name) => point.values[name] ?? ''),
        flagged?.algorithm ?? '',
        flagged?.severity ?? '',
        flagged?.score ?? '',
        flagged?.analysis_id ?? '',
      ].map((value) => `"${String(value).replaceAll('"', '""')}"`).join(',')
    })
    const blob = new Blob([[headings.join(','), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${selectedDataset.name}-exploration-sample.csv`
    link.click()
    URL.revokeObjectURL(url)
  }
  const filteredNumericFeatures = numericFeatures.filter((name) =>
    name.toLowerCase().includes(featureSearch.toLowerCase()))

  return (
    <>
      <PageTitle title="Data exploration" description="Explore stored vibration features and algorithm flags from historical bearing experiments. Charts are sampled from PostgreSQL-backed measurements; no live telemetry is implied." />
      <section className={`${panelClass} green-panel grid gap-5`}>
        <div className="grid grid-cols-1 items-stretch gap-4 md:grid-cols-2 xl:grid-cols-3">
          <div className="grid min-w-0 content-start gap-3">
            <h2 className="text-xs font-semibold text-forest-50">Source</h2>
            <label>Device / experiment
              <select className={`${inputClass} w-full text-[11px]`} value={deviceId} onChange={(event) => { setDeviceId(event.target.value); setDatasetId(''); setFeatures([]) }}><option value="">All datasets</option>{Array.from(new Map(datasets.filter((dataset) => dataset.device_id !== null).map((dataset) => [dataset.device_id, dataset.device_name ?? `Device ${dataset.device_id}`])).entries()).map(([id, name]) => <option value={String(id)} key={id}>{name}</option>)}<option value="historical">Historical experiments (no registered device)</option></select>
            </label>
            <label>Dataset
              <select className={`${inputClass} w-full text-[11px]`} value={datasetId} onChange={(event) => { setDatasetId(event.target.value); setFeatures([]); setExploration(null) }}><option value="">Select a dataset</option>{availableDatasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name}  -  {dataset.device_name}</option>)}</select>
            </label>
          </div>
          <div className="grid min-w-0 content-start gap-3">
            <h2 className="text-xs font-semibold text-forest-50">Detection</h2>
            <label>Detection algorithm
              <select className={`${inputClass} w-full text-[11px]`} value={algorithm} onChange={(event) => setAlgorithm(event.target.value)}><option value="">All available algorithms</option><option value="isolation_forest">Isolation Forest</option><option value="z_score">Z-score</option></select>
            </label>
            <label>Anomaly severity
              <select className={`${inputClass} w-full text-[11px]`} value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option>{['critical', 'high', 'medium', 'low'].map((item) => <option key={item}>{item}</option>)}</select>
            </label>
          </div>
          <div className="grid min-w-0 content-start gap-3">
            <h2 className="text-xs font-semibold text-forest-50">Time</h2>
            <div className="grid min-w-0 grid-cols-2 gap-3">
              <label>From measurement time
                <input className={`${inputClass} scheme-dark w-full text-[11px]`} type="datetime-local" value={startTime} onChange={(event) => setStartTime(event.target.value)} />
              </label>
              <label>To measurement time
                <input className={`${inputClass} scheme-dark w-full text-[11px]`} type="datetime-local" value={endTime} onChange={(event) => setEndTime(event.target.value)} />
              </label>
            </div>
            <label>Time column
              <select className={`${inputClass} w-full text-[11px]`} value={timeColumn} onChange={(event) => setTimeColumn(event.target.value)}><option value="">Measurement timestamp</option>{(exploration?.time_columns ?? []).map((name) => <option key={name}>{name}</option>)}</select>
            </label>
          </div>
        </div>
        <fieldset className="grid gap-3 border-t border-forest-400/20 pt-4">
          <legend className="text-xs font-semibold text-forest-50">Vibration features</legend>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-xs tabular-nums text-forest-200">{features.length} of 8 selected</span>
          </div>
          {features.length > 0 && <div className="flex flex-wrap gap-2">
            {features.map((name) => <span className="inline-flex items-center gap-1 rounded-full bg-forest-600 px-2.5 py-1 text-[11px] font-medium text-forest-50" key={name}>
              {name}<button aria-label={`Remove ${name}`} className="grid size-4 place-items-center rounded-full hover:bg-forest-700" onClick={() => toggleFeature(name)} type="button"><X size={12} /></button>
            </span>)}
          </div>}
          {!selectedDataset
            ? <p className="text-xs text-forest-200">Select a dataset to choose vibration features.</p>
            : numericFeatures.length === 0
              ? <p className="text-xs text-forest-200">No numeric features are stored for this dataset.</p>
              : <div className="relative max-w-xl">
                <button aria-expanded={featureMenuOpen} className="flex min-h-10 w-full items-center justify-between rounded-lg border border-forest-400/30 bg-forest-950/40 px-3 text-left text-xs text-forest-100" onClick={() => setFeatureMenuOpen((open) => !open)} type="button">
                  <span>{featureMenuOpen ? 'Choose vibration features' : 'Select vibration features'}</span><ChevronDown size={16} />
                </button>
                {featureMenuOpen && <div className="green-panel absolute z-10 mt-2 w-full rounded-lg border border-forest-400/30 p-3 shadow-lg">
                  <label className="relative block">
                    <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-forest-200" size={15} />
                    <input aria-label="Search vibration features" className={`${inputClass} pl-9`} onChange={(event) => setFeatureSearch(event.target.value)} placeholder="Search features" value={featureSearch} />
                  </label>
                  <div className="mt-2 max-h-48 overflow-y-auto">
                    {filteredNumericFeatures.map((name) => <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-xs text-forest-100 hover:bg-forest-600/20" key={name}>
                      <input checked={features.includes(name)} disabled={!features.includes(name) && features.length >= 8} onChange={() => toggleFeature(name)} type="checkbox" />
                      {name}
                    </label>)}
                    {filteredNumericFeatures.length === 0 && <p className="px-2 py-3 text-xs text-forest-200">No matching features.</p>}
                  </div>
                </div>}
              </div>}
        </fieldset>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-forest-400/20 pt-4">
          <div className="flex flex-wrap items-center gap-2">
            <button className={buttonClass} disabled={!datasetId || loading} onClick={() => void load()}>{loading ? 'Loading…' : 'Explore data'}</button>
            <button className={`${buttonClass} bg-forest-950/40 text-forest-100 ring-1 ring-forest-400/30 hover:bg-forest-800`} disabled={!selectedDataset || !chartPoints.length} onClick={exportCurrentView}>Export filtered sample</button>
          </div>
          <div className="relative">
            <button aria-expanded={moreActionsOpen} className={`${buttonClass} bg-forest-950/40 text-forest-100 ring-1 ring-forest-400/30 hover:bg-forest-800`} onClick={() => setMoreActionsOpen((open) => !open)} type="button">More actions<ChevronDown size={15} /></button>
            {moreActionsOpen && <div className="green-panel absolute right-0 z-10 mt-2 grid min-w-52 gap-1 rounded-lg border border-forest-400/30 p-2 shadow-lg">
              <button className="rounded-md px-3 py-2 text-left text-xs text-forest-100 hover:bg-forest-600/20 disabled:cursor-not-allowed disabled:opacity-50" disabled={!selectedDataset} onClick={() => { setMoreActionsOpen(false); void archive() }} type="button">Archive dataset</button>
              <button className="rounded-md px-3 py-2 text-left text-xs text-forest-100 hover:bg-forest-600/20 disabled:cursor-not-allowed disabled:opacity-50" disabled={!selectedDataset} onClick={() => { setMoreActionsOpen(false); if (selectedDataset) void download(`/datasets/${selectedDataset.id}/download`, `${selectedDataset.name}.zip`).catch((caught: unknown) => onAction(() => Promise.reject(caught), '')) }} type="button">Download source archive</button>
            </div>}
          </div>
        </div>
      </section>
      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-lg font-semibold text-slate-900">Exploration results</h2><p className="mt-1 text-xs text-slate-500">Charts use a sample of stored measurements and do not imply live telemetry.</p></div>
          {selectedDataset && <span className="text-xs text-forest-200">{selectedDataset.name}</span>}
        </div>
        {error && <ErrorMessage>{error}</ErrorMessage>}
        {loading && <div aria-live="polite" className={`${panelClass} green-panel py-8 text-center text-xs text-forest-200`}><span className="loading-skeleton mr-2 inline-block" aria-hidden="true" />Loading actual measurements and stored anomaly results…</div>}
        {!loading && !error && !exploration && <div className={`${panelClass} green-panel py-10 text-center`}>
          <p className="text-sm font-medium text-forest-50">Select a dataset and press Explore data to see the sampled features and algorithm flags</p>
        </div>}
      {statistics && <section className="grid gap-3 sm:grid-cols-3">
        <StatCard label="Source rows" value={statistics.rows.toLocaleString()} />
        <StatCard label="Rows with missing values" value={statistics.invalid_rows.toLocaleString()} />
        <StatCard label="Duplicate content rows" value={statistics.duplicate_rows === null ? 'Not calculated' : statistics.duplicate_rows.toLocaleString()} />
      </section>}
      {selectedStats && <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard label={`${features[0]} · measurements`} value={selectedStats.count.toLocaleString()} />
        <StatCard label="Mean" value={selectedStats.mean === null ? '—' : selectedStats.mean.toPrecision(5)} />
        <StatCard label="Minimum / maximum" value={`${selectedStats.min?.toPrecision(4) ?? '—'} / ${selectedStats.max?.toPrecision(4) ?? '—'}`} />
        <StatCard label="Standard deviation" value={selectedStats.std?.toPrecision(5) ?? '—'} />
        <StatCard label="Sampled for charts" value={exploration?.sampled_measurements.toLocaleString() ?? '—'} />
      </section>}
      {exploration && <section className="grid gap-4 xl:grid-cols-2">
        <article className={panelClass}><h2 className="mb-1 text-sm font-semibold text-slate-900">{exploration.feature} distribution</h2><p className="mb-4 text-[10px] text-slate-500">Histogram of the displayed database sample ({exploration.sampled_measurements.toLocaleString()} of {exploration.total_measurements.toLocaleString()} measurements).</p><div className="h-64"><ResponsiveContainer width="100%" height="100%"><BarChart data={exploration.histogram}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="range" tick={{ fontSize: 9 }} /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="count" fill="#466b4c" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div></article>
        <article className={panelClass}><h2 className="mb-1 text-sm font-semibold text-slate-900">Measurements over historical time</h2><p className="mb-4 text-[10px] text-slate-500">{exploration.stride > 1 ? `Downsampled every ${exploration.stride} source rows; anomaly rows are retained.` : 'Stored measurement points and available detection markers.'}</p>{chartPoints.length ? <div className="h-64"><ResponsiveContainer width="100%" height="100%"><LineChart data={chartPoints}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="timestamp" tick={{ fontSize: 9 }} /><YAxis /><Tooltip labelFormatter={(timestamp) => new Date(String(timestamp)).toLocaleString()} /><Line dataKey={features[0] ?? 'value'} name={`${features[0] ?? exploration.feature}${exploration.units[features[0]] ? ` (${exploration.units[features[0]]})` : ''}`} dot={false} stroke="#466b4c" />{features.slice(1).map((name, index) => <Line key={name} dataKey={name} name={name} dot={false} stroke={['#2563eb', '#7c3aed', '#0891b2', '#ca8a04', '#ea580c', '#db2777', '#475569'][index % 7]} />)}<Line dataKey="anomaly_value" name="Potential anomaly" stroke="none" dot={(props) => <circle cx={props.cx} cy={props.cy} r={4} fill={props.payload.anomalies[0]?.severity === 'critical' || props.payload.anomalies[0]?.severity === 'high' ? '#e11d48' : props.payload.anomalies[0]?.severity === 'medium' ? '#d97706' : '#2563eb'} />} /></LineChart></ResponsiveContainer></div> : <p className="py-16 text-center text-xs text-slate-500">No stored measurements match the selected time range.</p>}</article>
        {features.length > 1 && <article className={panelClass}><h2 className="mb-4 text-sm font-semibold text-slate-900">Feature relationship sample</h2><div className="h-64"><ResponsiveContainer width="100%" height="100%"><ScatterChart><CartesianGrid strokeDasharray="3 3" /><XAxis type="number" dataKey={features[0]} name={features[0]} /><YAxis type="number" dataKey={features[1]} name={features[1]} /><Tooltip cursor={{ strokeDasharray: '3 3' }} /><Scatter data={chartPoints} fill="#466b4c" /></ScatterChart></ResponsiveContainer></div><p className="mt-2 text-[10px] text-slate-500">Scatter view uses the same filtered sample shown above.</p></article>}
        {features.length > 1 && <article className={`${panelClass} overflow-auto`}><h2 className="mb-1 text-sm font-semibold text-slate-900">Feature correlation heatmap</h2><p className="mb-3 text-[10px] text-slate-500">Pearson correlation computed from the displayed sample only.</p><table className="min-w-full text-center text-[10px]"><thead className="sticky top-0"><tr><th className="p-2" />{features.map((name) => <th className="max-w-24 truncate p-2" key={name}>{name}</th>)}</tr></thead><tbody>{features.map((left) => <tr key={left}><th className="max-w-24 truncate p-2 text-left">{left}</th>{features.map((right) => { const value = correlation(left, right); const intensity = value === null ? 0 : 12 + Math.abs(value) * 50; return <td className="p-2 font-mono tabular-nums" key={right} style={{ backgroundColor: `color-mix(in srgb, var(--color-forest-600) ${intensity}%, var(--color-forest-950))` }}>{value === null ? '—' : value.toFixed(2)}</td> })}</tr>)}</tbody></table></article>}
      </section>}
      {exploration && <section className={`${panelClass} green-panel`}><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">Potential anomaly measurements</h2><p className="mt-1 text-xs text-slate-500">Historical statistical flags are not confirmed equipment failures.</p></div><div className="flex gap-2 text-[10px] text-slate-500">{exploration.analysis_runs.map((run) => <span className="rounded-full bg-forest-600 px-2 py-1 text-forest-50" key={run.id}>{run.algorithm.replaceAll('_', ' ')} · #{run.id}</span>)}</div></div>{anomalyPoints.length ? <div className="max-h-72 overflow-auto"><table className="w-full min-w-[700px] text-left text-xs"><thead className="sticky top-0 bg-forest-950"><tr>{['Measurement time', 'Source row', 'Algorithm', 'Severity', 'Score', 'Analysis', 'Feature values', 'Inspect'].map((heading) => <th className="px-3 py-2" key={heading}>{heading}</th>)}</tr></thead><tbody className="divide-y divide-forest-400/20">{anomalyPoints.map((point, index) => <tr key={`${point.analysis_id}-${point.source_row_number}-${index}`}><td className="px-3 py-2">{point.timestamp ? new Date(point.timestamp).toLocaleString() : 'Time unavailable'}</td><td className="px-3 py-2 text-right tabular-nums">{point.source_row_number}</td><td className="px-3 py-2">{point.algorithm.replaceAll('_', ' ')}</td><td className="px-3 py-2 capitalize">{point.severity}</td><td className="px-3 py-2 text-right font-mono tabular-nums">{point.score?.toPrecision(5) ?? '—'}</td><td className="px-3 py-2 text-right tabular-nums">#{point.analysis_id}</td><td className="max-w-48 truncate px-3 py-2 font-mono text-[10px]">{JSON.stringify(point.feature_values)}</td><td className="px-3 py-2"><button className="font-semibold text-forest-700" onClick={() => void inspectAnomaly(point.id)}>Details</button></td></tr>)}</tbody></table></div> : <p className="py-8 text-center text-xs text-slate-500">No anomaly detections match the selected algorithm, severity, and time filters.</p>}</section>}
      {selectedAnomaly && <section className={panelClass}><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">Anomaly #{String(selectedAnomaly.id)} · {String(selectedAnomaly.severity)} severity</h2><p className="mt-1 text-xs text-slate-500">{String(selectedAnomaly.description ?? 'No stored explanation available')}</p></div><button className="text-xs text-slate-500" onClick={() => setSelectedAnomaly(null)}>Close</button></div><pre className="mt-3 overflow-auto rounded-lg bg-slate-50 p-3 text-[10px]">{JSON.stringify(selectedAnomaly.feature_values, null, 2)}</pre><div className="mt-3 flex gap-2"><button className={buttonClass} onClick={() => onNavigate('Anomalies', selectedDataset?.name)}>Open anomaly review</button><button className={`${buttonClass} bg-white text-slate-700 ring-1 ring-slate-200`} onClick={() => onNavigate('Analysis')}>Open analysis history</button></div></section>}
      {statistics && <section className="green-panel max-h-80 overflow-auto rounded-xl border border-forest-400/20"><table className="w-full min-w-[700px] text-left text-xs"><thead className="sticky top-0 bg-forest-950 text-[10px] uppercase text-forest-200"><tr>{['Feature', 'Mean', 'Median', 'Minimum', 'Maximum', 'Std. dev.', 'Missing', 'Invalid numeric'].map((label) => <th className="whitespace-nowrap px-4 py-3" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-forest-400/20">{Object.entries(statistics.columns).map(([name, value]) => <tr key={name}><td className="px-4 py-3 font-semibold">{name}</td>{[value.mean, value.median, value.min, value.max, value.std].map((number, index) => <td className="px-4 py-3 text-right tabular-nums" key={index}>{number === null ? ' - ' : number.toFixed(4)}</td>)}<td className="px-4 py-3 text-right tabular-nums">{value.missing}</td><td className="px-4 py-3 text-right tabular-nums">{value.invalid_numeric}</td></tr>)}</tbody></table></section>}
        <p className="text-center text-[11px] text-forest-200">Model outputs support review and are not a fault diagnosis.</p>
      </section>
    </>
  )
}
