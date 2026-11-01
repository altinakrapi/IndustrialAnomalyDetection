import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { download, request } from '../api'
import { numericAnalysisFeatures } from '../datasetFeatures'

import type { Action, Dataset, DetectorCatalogEntry, ModelConfig, Analysis, AnalysisSummary } from '../types/workflow'

import { inputClass, buttonClass, panelClass, Field, StatCard, EmptyPanel, ErrorMessage } from '../components/ui'

import { PageTitle } from '../components/PageTitle'

export function AnalysisTools({
  datasets,
  models,
  analyses,
  canEdit,
  onAction,
}: {
  datasets: (Dataset & { column_metadata: { name: string; type: string }[]; status: string })[]
  models: ModelConfig[]
  analyses: Analysis[]
  canEdit: boolean
  onAction: Action
}) {
  const [datasetId, setDatasetId] = useState('')
  const [modelId, setModelId] = useState('')
  const [detectors, setDetectors] = useState<DetectorCatalogEntry[]>([])
  const [selectedAlgorithm, setSelectedAlgorithm] = useState('isolation_forest')
  const [featureNames, setFeatureNames] = useState<string[]>([])
  const [timeColumn, setTimeColumn] = useState('')
  const [selectedAnalysisIds, setSelectedAnalysisIds] = useState<number[]>([])
  const [comparison, setComparison] = useState<Record<string, unknown> | null>(null)
  const [details, setDetails] = useState<Record<string, unknown> | null>(null)
  const [results, setResults] = useState<Record<string, unknown>[]>([])
  const [timeline, setTimeline] = useState<Record<string, unknown>[]>([])
  const [error, setError] = useState('')
  const [summary, setSummary] = useState<AnalysisSummary | null>(null)
  const [history, setHistory] = useState<Analysis[]>(analyses)
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historySearch, setHistorySearch] = useState('')
  const [historyDataset, setHistoryDataset] = useState('')
  const [historyDevice, setHistoryDevice] = useState('')
  const [historyAlgorithm, setHistoryAlgorithm] = useState('')
  const [historyStatus, setHistoryStatus] = useState('')
  const [historyStart, setHistoryStart] = useState('')
  const [historyEnd, setHistoryEnd] = useState('')
  const [historySort, setHistorySort] = useState('created_at')
  const [historyDirection, setHistoryDirection] = useState('desc')
  const historyPageSize = 50
  const selectedDataset = datasets.find((dataset) => String(dataset.id) === datasetId)
  const numericFeatures = useMemo(
    () => numericAnalysisFeatures(selectedDataset),
    [selectedDataset],
  )
  const detectedTimeColumns = useMemo(() => selectedDataset?.column_metadata
    .filter((column) => /time|date/i.test(column.name))
    .map((column) => column.name) ?? [], [selectedDataset])
  const selectedDetector = detectors.find((detector) => detector.algorithm === selectedAlgorithm)
  const selectedModel = models.find((model) => String(model.id) === modelId)
  const requiresSingleFeature = selectedModel?.algorithm === 'ewma' || selectedModel?.algorithm === 'cusum'

  useEffect(() => {
    let active = true
    request<DetectorCatalogEntry[]>('/analyses/detectors')
      .then((catalog) => {
        if (!active) return
        setDetectors(catalog)
        setSelectedAlgorithm((current) => catalog.some((detector) => detector.algorithm === current)
          ? current
          : catalog[0]?.algorithm ?? '')
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : 'Unable to load detector catalog')
      })
    return () => { active = false }
  }, [])

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    const params = new URLSearchParams({
      limit: String(historyPageSize),
      offset: String(historyOffset),
      sort_by: historySort,
      sort_direction: historyDirection,
    })
    if (historySearch.trim()) params.set('search', historySearch.trim())
    if (historyDataset) params.set('dataset_id', historyDataset)
    if (historyDevice) params.set('device_id', historyDevice)
    if (historyAlgorithm) params.set('algorithm', historyAlgorithm)
    if (historyStatus) params.set('status_filter', historyStatus)
    if (historyStart) params.set('start_date', new Date(historyStart).toISOString())
    if (historyEnd) params.set('end_date', new Date(historyEnd).toISOString())
    try {
      const response = await request<{ items: Analysis[]; total: number }>(
        `/analyses/history?${params}`,
      )
      setHistory(response.items)
      setHistoryTotal(response.total)
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load analysis history')
    } finally {
      setHistoryLoading(false)
    }
  }, [
    historyAlgorithm,
    historyDataset,
    historyDirection,
    historyDevice,
    historyEnd,
    historyOffset,
    historySearch,
    historySort,
    historyStart,
    historyStatus,
  ])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      void Promise.all([
        loadHistory(),
        request<AnalysisSummary>('/analyses/summary').then(setSummary).catch((caught: unknown) => {
          setError(caught instanceof Error ? caught.message : 'Unable to load analysis summary')
        }),
      ])
    })
    return () => window.cancelAnimationFrame(frame)
  }, [loadHistory, analyses])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setFeatureNames(numericFeatures)
      setTimeColumn(detectedTimeColumns[0] ?? '')
    })
    return () => window.cancelAnimationFrame(frame)
  }, [datasetId, numericFeatures, detectedTimeColumns])

  async function launch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const config = models.find((item) => String(item.id) === modelId)
    if ((config?.algorithm === 'ewma' || config?.algorithm === 'cusum') && featureNames.length !== 1) {
      setError(`${config.algorithm.toUpperCase()} requires exactly one selected parameter.`)
      return
    }
    const form = new FormData(event.currentTarget)
    const model = config
    await onAction(() => request('/analyses', {
      method: 'POST',
      body: JSON.stringify({
        dataset_id: Number(datasetId),
        model_config_id: Number(modelId),
        features: featureNames,
        time_column: timeColumn || null,
        missing_values_strategy: form.get('missing_values_strategy'),
        scaling: form.get('scaling'),
      }),
    }), `Analysis queued${model ? ` with ${model.name}` : ''}`)
    await loadHistory()
    setSummary(await request<AnalysisSummary>('/analyses/summary'))
  }

  async function saveModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    let detectorParameters: Record<string, unknown>
    try {
      detectorParameters = Object.fromEntries((selectedDetector?.parameters ?? []).map((parameter) => {
      const rawValue = form.get(`detector_${parameter.name}`)
      const value = typeof rawValue === 'string' ? rawValue.trim() : ''
      if (!value) return [parameter.name, parameter.default]
      if (parameter.type === 'json') return [parameter.name, JSON.parse(value) as unknown]
      if (parameter.type === 'integer_list') {
        return [parameter.name, value.split(',').map((item) => Number(item.trim()))]
      }
      if (parameter.type === 'integer' || parameter.type === 'number') {
        return [parameter.name, Number(value)]
      }
      if ((parameter.type === 'string_or_number' || parameter.type === 'number_or_auto') && Number.isFinite(Number(value))) {
        return [parameter.name, Number(value)]
      }
      return [parameter.name, value]
      }))
    } catch (caught) {
      setError(caught instanceof Error ? `Invalid detector configuration: ${caught.message}` : 'Invalid detector configuration JSON')
      return
    }
    const contamination = detectorParameters.contamination
    const body = {
      name: form.get('name'),
      description: form.get('description'),
      algorithm: selectedAlgorithm,
      n_estimators: Number(detectorParameters.n_estimators ?? 100),
      contamination: contamination === 'auto' || contamination === undefined ? null : Number(contamination),
      threshold: Number(detectorParameters.threshold ?? 3),
      missing_values_strategy: form.get('missing_values_strategy'),
      scaling: form.get('scaling'),
      max_samples: form.get('max_samples') || 'auto',
      max_features: Number(form.get('max_features') || 1),
      bootstrap: form.get('bootstrap') === 'on',
      detector_parameters: detectorParameters,
    }
    await onAction(() => request('/analyses/models', {
      method: 'POST',
      body: JSON.stringify(body),
    }), 'Model configuration saved')
    event.currentTarget.reset()
  }

  async function compare() {
    if (selectedAnalysisIds.length < 2) return
    await onAction(async () => {
      setComparison(await request<Record<string, unknown>>('/analyses/comparisons', {
        method: 'POST',
        body: JSON.stringify({ analysis_ids: selectedAnalysisIds }),
      }))
    }, 'Analysis comparison ready')
  }

  async function inspect(analysis: Analysis) {
    try {
      const [detail, resultPage] = await Promise.all([
        request<Record<string, unknown>>(`/analyses/${analysis.id}`),
        request<{ results: Record<string, unknown>[] }>(`/analyses/${analysis.id}/results?limit=100`),
      ])
      setDetails(detail)
      setResults(resultPage.results)
      setTimeline([])
      const config = detail.configuration as { features?: string[]; time_column?: string } | undefined
      if (config?.features?.[0] && config.time_column) {
        const series = await request<{ points: Record<string, unknown>[] }>(
          `/analyses/${analysis.id}/timeline?feature=${encodeURIComponent(config.features[0])}&time_column=${encodeURIComponent(config.time_column)}`,
        )
        setTimeline(series.points)
      }
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load analysis details')
    }
  }

  async function repeat(analysis: Analysis) {
    const details = await request<Record<string, unknown>>(`/analyses/${analysis.id}`)
    const config = details.configuration as {
      features?: string[]
      time_column?: string
      missing_values_strategy?: string
      scaling?: string
    } | undefined
    await onAction(() => request('/analyses', {
      method: 'POST',
      body: JSON.stringify({
        dataset_id: analysis.dataset_id,
        model_config_id: analysis.model_config_id,
        features: config?.features,
        time_column: config?.time_column,
        missing_values_strategy: config?.missing_values_strategy,
        scaling: config?.scaling,
      }),
    }), `Analysis #${analysis.id} repeated`)
  }

  function toggleComparison(id: number) {
    setSelectedAnalysisIds((previous) =>
      previous.includes(id)
        ? previous.filter((value) => value !== id)
        : previous.length < 10 ? [...previous, id] : previous,
    )
  }

  return (
    <>
      <PageTitle
        title="Analysis executions"
        description="Review persisted analysis runs and their database-backed results. NASA IMS records are historical experiment measurements, not live equipment telemetry."
      />
      {summary && <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Total analysis runs" value={summary.total_runs.toLocaleString()} />
        <StatCard label="Completed" value={summary.completed.toLocaleString()} />
        <StatCard label="Failed" value={summary.failed.toLocaleString()} />
        <StatCard label="In progress" value={summary.in_progress.toLocaleString()} />
        <StatCard label="Algorithm-specific anomaly detections" value={summary.anomaly_detections.toLocaleString()} />
        <StatCard label="Unique affected measurement windows" value={summary.unique_affected_measurements.toLocaleString()} />
        <StatCard label="Most recent execution" value={summary.latest_analysis ? new Date(summary.latest_analysis.started_at ?? summary.latest_analysis.created_at).toLocaleString() : 'No runs yet'} />
        <StatCard label="Next scheduled execution" value={summary.schedule_enabled && summary.next_scheduled_at ? new Date(summary.next_scheduled_at).toLocaleString() : 'Not scheduled'} />
      </section>}
      {error && <ErrorMessage>{error}</ErrorMessage>}
      {canEdit && <section className={`${panelClass} space-y-5`}>
        <div><h2 className="text-sm font-semibold text-slate-900">Run an analysis</h2><p className="mt-1 text-xs text-slate-500">Choose features and preprocessing without modifying the preserved source data.</p></div>
        <form className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" onSubmit={launch}>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Dataset
            <select className={inputClass} value={datasetId} onChange={(event) => setDatasetId(event.target.value)} required>
              <option value="">Select a dataset</option>{datasets.filter((dataset) => dataset.status !== 'archived').map((dataset) => <option value={dataset.id} key={dataset.id}>{dataset.name}  -  {dataset.device_name}</option>)}
            </select>
          </label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Model configuration
            <select className={inputClass} value={modelId} onChange={(event) => setModelId(event.target.value)} required>
              <option value="">Select a model</option>{models.filter((model) => model.is_active !== false).map((model) => <option value={model.id} key={model.id}>{model.name}  -  {model.algorithm.replaceAll('_', ' ')}</option>)}
            </select>
          </label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Missing values
            <select className={inputClass} name="missing_values_strategy"><option value="median">Fill with median</option><option value="mean">Fill with mean</option><option value="drop">Exclude incomplete rows</option></select>
          </label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Feature scaling
            <select className={inputClass} name="scaling"><option value="standard">Standardize</option><option value="none">None</option></select>
          </label>
          {numericFeatures.length > 0 && <fieldset className="grid gap-2 sm:col-span-2 xl:col-span-4">
            <legend className="text-xs font-semibold text-slate-700">Features to analyze</legend>
            {requiresSingleFeature && <p className="text-[11px] text-slate-500">EWMA and CUSUM are univariate control charts. Select exactly one parameter.</p>}
            <div className="flex flex-wrap gap-2">{numericFeatures.map((feature) => <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs" key={feature}><input type="checkbox" checked={featureNames.includes(feature)} onChange={() => setFeatureNames((current) => current.includes(feature) ? current.filter((value) => value !== feature) : requiresSingleFeature ? [feature] : [...current, feature])} />{feature}</label>)}</div>
          </fieldset>}
          {detectedTimeColumns.length > 0 && <label className="grid gap-1.5 text-xs font-medium text-slate-600">Time column for trend review
            <select className={inputClass} value={timeColumn} onChange={(event) => setTimeColumn(event.target.value)}><option value="">None</option>{detectedTimeColumns.map((column) => <option key={column}>{column}</option>)}</select>
          </label>}
          <p className="self-center text-[11px] text-slate-500">{selectedDataset ? `${selectedDataset.row_count.toLocaleString()} source rows  -  ${featureNames.length} features` : 'Select a dataset to review available features.'}</p>
          <button className={`${buttonClass} self-end`} type="submit" disabled={!datasetId || !modelId || !featureNames.length}>Queue analysis</button>
        </form>
      </section>}
      {canEdit && <details className={panelClass}>
        <summary className="cursor-pointer text-sm font-semibold text-slate-900">Create a model configuration</summary>
        <form className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4" onSubmit={(event) => void saveModel(event)}>
          <Field label="Configuration name" name="name" required />
          <Field label="Description" name="description" />
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Algorithm<select className={inputClass} name="algorithm" value={selectedAlgorithm} onChange={(event) => setSelectedAlgorithm(event.target.value)} required>{detectors.map((detector) => <option value={detector.algorithm} key={detector.algorithm}>{detector.display_name}</option>)}</select></label>
          {selectedDetector?.parameters.map((parameter) => (
            <label className="grid gap-1.5 text-xs font-medium text-slate-600" key={parameter.name}>
              {parameter.name.replaceAll('_', ' ')}
              {parameter.type === 'json'
                ? <textarea
                    className={`${inputClass} min-h-32 font-mono text-[11px]`}
                    name={`detector_${parameter.name}`}
                    defaultValue={JSON.stringify(parameter.default, null, 2)}
                    required
                    spellCheck={false}
                  />
                : parameter.choices && parameter.type !== 'string_or_number'
                ? <select className={inputClass} name={`detector_${parameter.name}`} defaultValue={String(parameter.default)}>
                    {parameter.choices.map((choice) => <option key={String(choice)} value={choice}>{String(choice)}</option>)}
                  </select>
                : <input
                    className={inputClass}
                    name={`detector_${parameter.name}`}
                    type={parameter.type === 'integer' || parameter.type === 'number' ? 'number' : 'text'}
                    defaultValue={parameter.type === 'integer_list' && Array.isArray(parameter.default) ? parameter.default.join(',') : String(parameter.default)}
                    min={parameter.minimum}
                    max={parameter.maximum}
                    step={parameter.type === 'integer' ? 1 : 'any'}
                    placeholder={parameter.type === 'integer_list' ? 'e.g. 32,16' : undefined}
                    required
                  />}
            </label>
          ))}
          {selectedDetector && <p className="text-[11px] text-slate-500">{selectedDetector.execution_mode === 'walk_forward' ? `Minimum history: ${selectedDetector.minimum_history} earlier readings for this detector's default settings.` : 'Existing batch-analysis behavior is preserved for this detector.'} Detector results are statistical evidence for human review.</p>}
          {selectedAlgorithm === 'isolation_forest' && <>
            <label className="grid gap-1.5 text-xs font-medium text-slate-600">Maximum samples<select className={inputClass} name="max_samples"><option value="auto">Auto</option><option value="256">256</option><option value="512">512</option></select></label>
            <Field label="Max features fraction" name="max_features" type="number" defaultValue={1} min={0.01} max={1} step={0.05} required />
            <label className="flex items-center gap-2 text-xs text-slate-600"><input name="bootstrap" type="checkbox" /> Bootstrap samples</label>
          </>}
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Missing values<select className={inputClass} name="missing_values_strategy"><option value="median">Median</option><option value="mean">Mean</option><option value="drop">Drop incomplete rows</option></select></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Scaling<select className={inputClass} name="scaling"><option value="standard">Standardize</option><option value="none">None</option></select></label>
          <button className={`${buttonClass} self-end`} type="submit">Save configuration</button>
        </form>
      </details>}
      <section className={`${panelClass} space-y-4`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-sm font-semibold text-slate-900">Analysis history</h2><p className="mt-1 text-xs text-slate-500">Filter and compare stored executions. Detection totals count run-level algorithm flags, so the same measurement may be counted in separate runs.</p></div>
          <span className="text-xs text-slate-500">{historyTotal.toLocaleString()} matching runs</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Search<input className={inputClass} value={historySearch} onChange={(event) => { setHistorySearch(event.target.value); setHistoryOffset(0) }} placeholder="Dataset, device, model, status" /></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Dataset<select className={inputClass} value={historyDataset} onChange={(event) => { setHistoryDataset(event.target.value); setHistoryOffset(0) }}><option value="">All datasets</option>{datasets.map((dataset) => <option value={dataset.id} key={dataset.id}>{dataset.name}</option>)}</select></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Registered device<select className={inputClass} value={historyDevice} onChange={(event) => { setHistoryDevice(event.target.value); setHistoryOffset(0) }}><option value="">All devices</option>{Array.from(new Map(datasets.filter((dataset) => dataset.device_id !== null).map((dataset) => [dataset.device_id, dataset.device_name ?? `Device ${dataset.device_id}`])).entries()).map(([id, name]) => <option value={String(id)} key={id}>{name}</option>)}</select></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Algorithm<select className={inputClass} value={historyAlgorithm} onChange={(event) => { setHistoryAlgorithm(event.target.value); setHistoryOffset(0) }}><option value="">All algorithms</option>{detectors.map((detector) => <option value={detector.algorithm} key={detector.algorithm}>{detector.display_name}</option>)}</select></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Status<select className={inputClass} value={historyStatus} onChange={(event) => { setHistoryStatus(event.target.value); setHistoryOffset(0) }}><option value="">All statuses</option>{['pending', 'running', 'completed', 'failed'].map((status) => <option value={status} key={status}>{status}</option>)}</select></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">From<input className={inputClass} type="datetime-local" value={historyStart} onChange={(event) => { setHistoryStart(event.target.value); setHistoryOffset(0) }} /></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">To<input className={inputClass} type="datetime-local" value={historyEnd} onChange={(event) => { setHistoryEnd(event.target.value); setHistoryOffset(0) }} /></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Sort<select className={inputClass} value={historySort} onChange={(event) => setHistorySort(event.target.value)}>{[['created_at', 'Execution time'], ['anomalies_found', 'Anomaly count'], ['total_records', 'Measurements'], ['status', 'Status']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Order<select className={inputClass} value={historyDirection} onChange={(event) => setHistoryDirection(event.target.value)}><option value="desc">Descending</option><option value="asc">Ascending</option></select></label>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1050px] text-left text-xs">
            <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500"><tr>{['Compare', 'Run', 'Device', 'Dataset / experiment', 'Algorithm', 'Execution time', 'Measurements', 'Detections', 'Duration', 'Status', 'Actions'].map((heading) => <th className="whitespace-nowrap px-3 py-3" key={heading}>{heading}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-100">{history.map((analysis) => <tr key={analysis.id}>
              <td className="px-3 py-3"><input aria-label={`Select analysis ${analysis.id} for comparison`} type="checkbox" checked={selectedAnalysisIds.includes(analysis.id)} onChange={() => toggleComparison(analysis.id)} /></td>
              <td className="px-3 py-3 font-mono">#{analysis.id}</td>
              <td className="px-3 py-3">{analysis.device_name}</td>
              <td className="px-3 py-3">{analysis.dataset_name}</td>
              <td className="px-3 py-3 capitalize">{(analysis.algorithm ?? analysis.model_name ?? 'analysis').replaceAll('_', ' ')}</td>
              <td className="px-3 py-3 whitespace-nowrap">{new Date(analysis.started_at ?? analysis.created_at).toLocaleString()}<small className="mt-1 block text-[10px] text-slate-400">{analysis.execution_type ?? 'manual'}</small></td>
              <td className="px-3 py-3">{analysis.total_records.toLocaleString()}</td>
              <td className="px-3 py-3">{analysis.anomalies_found.toLocaleString()}</td>
              <td className="px-3 py-3">{analysis.duration_seconds == null ? '—' : `${analysis.duration_seconds.toFixed(2)} s`}</td>
              <td className="px-3 py-3"><span className="capitalize">{analysis.status}</span>{analysis.error_message && <small className="mt-1 block max-w-40 text-rose-700">{analysis.error_message}</small>}</td>
              <td className="px-3 py-3 whitespace-nowrap"><button className="mr-2 font-semibold text-forest-700" type="button" onClick={() => void inspect(analysis)}>Details</button><button className="font-semibold text-forest-700 disabled:text-slate-400" type="button" disabled={!canEdit} onClick={() => void repeat(analysis)}>Repeat</button></td>
            </tr>)}</tbody>
          </table>
          {historyLoading && <p className="py-5 text-center text-xs text-slate-500">Loading analysis history…</p>}
          {!historyLoading && history.length === 0 && <EmptyPanel title="No matching analysis runs" body="Change the filters or start a new analysis to create a history record." />}
        </div>
        <div className="flex items-center justify-between text-xs text-slate-500"><span>{historyTotal ? `${historyOffset + 1}–${Math.min(historyOffset + history.length, historyTotal)} of ${historyTotal}` : 'No records'}</span><div className="flex gap-2"><button className={`${buttonClass} bg-white text-slate-700 ring-1 ring-slate-200`} disabled={historyOffset === 0 || historyLoading} onClick={() => setHistoryOffset(Math.max(0, historyOffset - historyPageSize))}>Previous</button><button className={`${buttonClass} bg-white text-slate-700 ring-1 ring-slate-200`} disabled={historyOffset + historyPageSize >= historyTotal || historyLoading} onClick={() => setHistoryOffset(historyOffset + historyPageSize)}>Next</button></div></div>
      </section>
      <section className={panelClass}>
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">Compare selected runs</h2><p className="mt-1 text-xs text-slate-500">Compare counts and available metrics. Algorithms can flag different measurements; only same-dataset runs with comparable measurements are matched by source row.</p></div><button className={buttonClass} disabled={selectedAnalysisIds.length < 2} onClick={() => void compare()}>Compare {selectedAnalysisIds.length} runs</button></div>
      </section>
      {comparison && <section className={`${panelClass} space-y-4`}>
        <div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">Comparison #{String(comparison.id)}</h2><p className="mt-1 text-xs text-slate-500">Stored comparison snapshot · statistical evidence for human review</p></div><button className={buttonClass} type="button" onClick={() => void onAction(() => download(`/analyses/comparisons/${String(comparison.id)}/csv`, `analysis-comparison-${String(comparison.id)}.csv`), 'Comparison exported')}>Export CSV</button></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[950px] text-left text-xs"><thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500"><tr>{['Run', 'Detector', 'Status', 'Measurements', 'Anomalies / rate', 'Score distribution (min / p25 / median / p75 / p95 / max)', 'Runtime', 'History states'].map((label) => <th className="px-3 py-2" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{(comparison.runs as Record<string, unknown>[]).map((run) => {
          const distribution = run.score_distribution as Record<string, unknown>
          return <tr key={String(run.analysis_id)}><td className="px-3 py-2">#{String(run.analysis_id)}</td><td className="px-3 py-2">{String(run.algorithm)}</td><td className="px-3 py-2 capitalize">{String(run.status)}</td><td className="px-3 py-2">{Number(run.total_records).toLocaleString()}</td><td className="px-3 py-2">{String(run.anomalies_found)} / {(Number(run.anomaly_rate) * 100).toFixed(2)}%</td><td className="px-3 py-2">{distribution.min == null ? '—' : ['min', 'p25', 'median', 'p75', 'p95', 'max'].map((key) => Number(distribution[key]).toFixed(4)).join(' / ')}</td><td className="px-3 py-2">{run.runtime_seconds == null ? '—' : `${Number(run.runtime_seconds).toFixed(2)} s`}</td><td className="px-3 py-2">{JSON.stringify(run.history_state_counts)}</td></tr>
        })}</tbody></table></div>
        <div><h3 className="text-xs font-semibold text-slate-700">Agreement matrix</h3><div className="mt-2 overflow-x-auto"><table className="w-full text-left text-xs"><thead className="bg-slate-50"><tr>{['Run', 'Compared run', 'Matched by', 'Both flagged', 'Left only', 'Right only'].map((label) => <th className="px-3 py-2" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{(comparison.agreement_matrix as Record<string, unknown>[]).map((pair) => <tr key={`${String(pair.analysis_id)}-${String(pair.other_analysis_id)}`}><td className="px-3 py-2">#{String(pair.analysis_id)}</td><td className="px-3 py-2">#{String(pair.other_analysis_id)}</td><td className="px-3 py-2">{String(pair.matched_by)}</td><td className="px-3 py-2">{String(pair.both)}</td><td className="px-3 py-2">{String(pair.left_only)}</td><td className="px-3 py-2">{String(pair.right_only)}</td></tr>)}</tbody></table></div></div>
      </section>}
      {details && <section className={panelClass}>
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">Analysis #{String(details.id)} results</h2><p className="mt-1 text-xs text-slate-500">{String(details.dataset_name)} · {String(details.device_name)} · {String(details.algorithm ?? details.model_name)} · {String(details.status)} · executed {new Date(String(details.started_at ?? details.created_at)).toLocaleString()}</p><p className="mt-1 text-xs text-slate-500">{String(details.total_records)} processed · {String(details.anomalies_found)} flagged · duration {String(details.duration_seconds ?? '—')} seconds</p></div><button className="text-xs text-slate-500" onClick={() => { setDetails(null); setResults([]); setTimeline([]) }}>Close details</button></div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{Object.entries((details.severity_distribution ?? {}) as Record<string, number>).map(([severity, count]) => <StatCard key={severity} label={`${severity} potential detections`} value={count.toLocaleString()} />)}</div>
        <details className="mt-4"><summary className="cursor-pointer text-xs font-semibold text-slate-700">Analysis configuration and stored metrics</summary><pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-slate-50 p-3 text-[10px]">{JSON.stringify({ configuration: details.configuration, metrics: details.metrics }, null, 2)}</pre></details>
        {error && <ErrorMessage>{error}</ErrorMessage>}
        {timeline.length > 0 && <div className="mt-4 h-64"><ResponsiveContainer width="100%" height="100%"><LineChart data={timeline}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="timestamp" tick={{ fontSize: 9 }} /><YAxis /><Tooltip /><Line dataKey="value" dot={false} stroke="#466b4c" /><Line dataKey={(point: Record<string, unknown>) => point.is_anomaly ? point.value : null} name="Flagged" stroke="none" dot={{ r: 4, fill: '#e11d48' }} /></LineChart></ResponsiveContainer></div>}
        <div className="mt-4 max-h-64 overflow-auto"><table className="w-full text-left text-xs"><thead className="sticky top-0 bg-slate-50"><tr>{['Source row', 'Classification / history', 'Score', 'Detector votes / shift', 'Feature values'].map((label) => <th className="px-3 py-2" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{results.map((result) => {
          const historyState = String(result.history_state ?? '')
          const resultDetails = (result.result_details ?? {}) as Record<string, unknown>
          const classification = historyState === 'insufficient_history'
            ? `Insufficient history (${String(result.history_size)}/${String(result.history_required)} earlier readings)`
            : historyState === 'excluded_missing_values'
              ? 'Excluded: missing values'
              : result.is_anomaly ? 'Unusual' : 'Normal'
          const shiftDetails = resultDetails.shift_direction
            ? `${String(resultDetails.shift_direction)} shift · ${String(resultDetails.shift_started_at ?? `source row ${resultDetails.shift_started_source_row_number ?? 'unknown'}`)}`
            : '—'
          const detectorVotes = Array.isArray(resultDetails.detectors) ? resultDetails.detectors as Record<string, unknown>[] : []
          return <tr key={String(result.id)}><td className="px-3 py-2">{String(result.source_row_number)}</td><td className="px-3 py-2">{classification}</td><td className="px-3 py-2">{result.anomaly_score == null ? '—' : Number(result.anomaly_score).toFixed(4)}</td><td className="px-3 py-2"><p>{String(resultDetails.fired_rule ?? shiftDetails)}</p>{resultDetails.confidence != null && <p>Confidence {Number(resultDetails.confidence).toFixed(2)}</p>}{detectorVotes.map((vote) => <p key={String(vote.algorithm)}>{String(vote.algorithm)}: {vote.vote == null ? String(vote.history_state) : `${vote.vote ? 'voted unusual' : 'voted normal'} · score ${Number(vote.score).toFixed(4)}`}</p>)}</td><td className="px-3 py-2 font-mono text-[10px]">{JSON.stringify(result.feature_values)}</td></tr>
        })}</tbody></table></div>
      </section>}
    </>
  )
}
