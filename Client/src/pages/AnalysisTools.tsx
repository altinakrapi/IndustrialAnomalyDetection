import { useCallback, useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { download, request } from '../api'
import { numericAnalysisFeatures } from '../datasetFeatures'

import type { Action, Dataset, DetectorCatalogEntry, ModelConfig, Analysis, AnalysisSummary } from '../types/workflow'

import { inputClass, buttonClass, panelClass, Field, EmptyPanel, ErrorMessage } from '../components/ui'

import { PageTitle } from '../components/PageTitle'
import { StatusBadge } from '../components/StatusBadge'

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
  type AnalysisTab = 'history' | 'run' | 'models'
  const [activeTab, setActiveTab] = useState<AnalysisTab>('history')
  const [hideZeroDetections, setHideZeroDetections] = useState(false)
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
  const displayedHistory = hideZeroDetections
    ? history.filter((analysis) => analysis.anomalies_found > 0)
    : history
  const tabs: { id: AnalysisTab; label: string }[] = [
    { id: 'history', label: 'History' },
    ...(canEdit ? [
      { id: 'run' as const, label: 'Run an analysis' },
      { id: 'models' as const, label: 'Model configurations' },
    ] : []),
  ]

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const currentIndex = tabs.findIndex((tab) => tab.id === activeTab)
    const nextIndex = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : (currentIndex + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length
    const nextTab = tabs[nextIndex]
    setActiveTab(nextTab.id)
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]
      ?.focus()
  }

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
      {summary && <section className="space-y-2" aria-label="Analysis summary">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {[
            ['Total analysis runs', summary.total_runs.toLocaleString()],
            ['Completed', summary.completed.toLocaleString()],
            ['Failed', summary.failed.toLocaleString()],
            ['In progress', summary.in_progress.toLocaleString()],
          ].map(([label, value]) => <article className="green-panel rounded-xl border border-forest-400/20 px-4 py-3" key={label}>
            <span className="block text-[10px] font-semibold uppercase tracking-wide text-forest-200">{label}</span>
            <strong className="mt-1 block text-xl font-semibold tabular-nums text-forest-50">{value}</strong>
          </article>)}
        </div>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <article className="green-panel rounded-lg border border-forest-400/20 px-3 py-2"><span className="block text-[10px] text-forest-200">Algorithm-specific anomaly detections</span><strong className="text-sm font-semibold tabular-nums text-forest-50">{summary.anomaly_detections.toLocaleString()}</strong></article>
          <article className="green-panel rounded-lg border border-forest-400/20 px-3 py-2"><span className="block text-[10px] text-forest-200">Unique affected measurement windows</span><strong className="text-sm font-semibold tabular-nums text-forest-50">{summary.unique_affected_measurements.toLocaleString()}</strong></article>
          <article className="green-panel rounded-lg border border-forest-400/20 px-3 py-2"><span className="block text-[10px] text-forest-200">Most recent execution</span><strong className="mt-0.5 block text-[11px] font-medium text-forest-50">{summary.latest_analysis ? new Date(summary.latest_analysis.started_at ?? summary.latest_analysis.created_at).toLocaleString() : 'No runs yet'}</strong></article>
          <article className="green-panel rounded-lg border border-forest-400/20 px-3 py-2"><span className="block text-[10px] text-forest-200">Next scheduled execution</span><strong className="mt-0.5 block text-[11px] font-medium text-forest-50">{summary.schedule_enabled && summary.next_scheduled_at ? new Date(summary.next_scheduled_at).toLocaleString() : 'Not scheduled'}</strong></article>
        </div>
      </section>}
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <p className="text-[11px] text-slate-500">Detection totals count run-level algorithm flags and may repeat the same measurement across runs. Model outputs support review and are not a fault diagnosis.</p>
      <div className="min-w-0 overflow-x-auto">
        <div className="flex min-w-max gap-1 border-b border-forest-400/20" role="tablist" aria-label="Analysis sections">
          {tabs.map((tab) => <button
            aria-controls={`analysis-panel-${tab.id}`}
            aria-selected={activeTab === tab.id}
            className={`min-h-10 border-b-2 px-4 text-xs font-semibold transition-colors motion-reduce:transition-none ${activeTab === tab.id ? 'border-forest-400 text-forest-50' : 'border-transparent text-forest-200 hover:text-forest-50'}`}
            id={`analysis-tab-${tab.id}`}
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            onKeyDown={handleTabKeyDown}
            role="tab"
            tabIndex={activeTab === tab.id ? 0 : -1}
            type="button"
          >{tab.label}</button>)}
        </div>
      </div>
      {canEdit && <section aria-labelledby="analysis-tab-run" className={`${panelClass} green-panel space-y-5`} hidden={activeTab !== 'run'} id="analysis-panel-run" role="tabpanel">
        <div><h2 className="text-sm font-semibold text-slate-900">Run an analysis</h2><p className="mt-1 text-xs text-slate-500">Choose features and preprocessing without modifying the preserved source data.</p></div>
        <form className="grid gap-4" onSubmit={launch}>
          <div className="grid items-start gap-4 sm:grid-cols-2 xl:grid-cols-4">
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
          </div>
          {detectedTimeColumns.length > 0 && <label className="grid max-w-sm gap-1.5 text-xs font-medium text-slate-600">Time column for trend review
            <select className={inputClass} value={timeColumn} onChange={(event) => setTimeColumn(event.target.value)}><option value="">None</option>{detectedTimeColumns.map((column) => <option key={column}>{column}</option>)}</select>
          </label>}
          <fieldset className="grid gap-2 border-t border-forest-400/20 pt-4">
            <legend className="text-xs font-semibold text-slate-700">Features to analyze</legend>
            {requiresSingleFeature && <p className="text-[11px] text-slate-500">EWMA and CUSUM are univariate control charts. Select exactly one parameter.</p>}
            {numericFeatures.length > 0
              ? <div className="flex flex-wrap gap-2">{numericFeatures.map((feature) => <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-forest-400/20 px-3 py-2 text-xs" key={feature}><input type="checkbox" checked={featureNames.includes(feature)} onChange={() => setFeatureNames((current) => current.includes(feature) ? current.filter((value) => value !== feature) : requiresSingleFeature ? [feature] : [...current, feature])} />{feature}</label>)}</div>
              : <p className="text-[11px] text-slate-500">{selectedDataset ? 'No numeric features are available for this dataset.' : 'Select a dataset to review available features.'}</p>}
            <p className="text-[11px] text-slate-500">{selectedDataset ? `${selectedDataset.row_count.toLocaleString()} source rows  -  ${featureNames.length} features` : 'Feature choices and source row count will appear after selecting a dataset.'}</p>
          </fieldset>
          <div className="flex justify-end">
            <button className={buttonClass} type="submit" disabled={!datasetId || !modelId || !featureNames.length}>Queue analysis</button>
          </div>
        </form>
      </section>}
      {canEdit && <section aria-labelledby="analysis-tab-models" className="space-y-4" hidden={activeTab !== 'models'} id="analysis-panel-models" role="tabpanel">
      <details className={`${panelClass} green-panel`}>
        <summary className="cursor-pointer text-sm font-semibold text-slate-900">Create a model configuration</summary>
        <form className="mt-4 grid gap-4" onSubmit={(event) => void saveModel(event)}>
          <fieldset className="grid gap-3 border-b border-forest-400/20 pb-4">
            <legend className="text-xs font-semibold text-forest-50">Configuration basics</legend>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              <Field label="Configuration name" name="name" required />
              <Field label="Description" name="description" />
              <label className="grid gap-1.5 text-xs font-medium text-slate-600">Algorithm<select className={inputClass} name="algorithm" value={selectedAlgorithm} onChange={(event) => setSelectedAlgorithm(event.target.value)} required>{detectors.map((detector) => <option value={detector.algorithm} key={detector.algorithm}>{detector.display_name}</option>)}</select></label>
            </div>
          </fieldset>
          <fieldset className="grid gap-3 border-b border-forest-400/20 pb-4">
            <legend className="text-xs font-semibold text-forest-50">Detector parameters</legend>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
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
            </div>
          {selectedDetector && <p className="text-[11px] text-slate-500">{selectedDetector.execution_mode === 'walk_forward' ? `Minimum history: ${selectedDetector.minimum_history} earlier readings for this detector's default settings.` : 'Existing batch-analysis behavior is preserved for this detector.'} Detector results are statistical evidence for human review.</p>}
          </fieldset>
          <fieldset className="grid gap-3">
            <legend className="text-xs font-semibold text-forest-50">Preprocessing and sampling</legend>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {selectedAlgorithm === 'isolation_forest' && <>
            <label className="grid gap-1.5 text-xs font-medium text-slate-600">Maximum samples<select className={inputClass} name="max_samples"><option value="auto">Auto</option><option value="256">256</option><option value="512">512</option></select></label>
            <Field label="Max features fraction" name="max_features" type="number" defaultValue={1} min={0.01} max={1} step={0.05} required />
            <label className="flex items-center gap-2 text-xs text-slate-600"><input name="bootstrap" type="checkbox" /> Bootstrap samples</label>
          </>}
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Missing values<select className={inputClass} name="missing_values_strategy"><option value="median">Median</option><option value="mean">Mean</option><option value="drop">Drop incomplete rows</option></select></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">Scaling<select className={inputClass} name="scaling"><option value="standard">Standardize</option><option value="none">None</option></select></label>
            </div>
            <div className="flex justify-end"><button className={buttonClass} type="submit">Save configuration</button></div>
          </fieldset>
        </form>
      </details>
      <section className={`${panelClass} green-panel space-y-4`}>
        <div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">Saved model configurations</h2><p className="mt-1 text-xs text-slate-500">Reusable detector settings available for analysis runs.</p></div><span className="text-xs tabular-nums text-slate-500">{models.length} configurations</span></div>
        {models.length > 0
          ? <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{models.map((model) => <article className="rounded-lg border border-forest-400/20 p-4" key={model.id}>
            <h3 className="text-sm font-semibold text-slate-900">{model.name}</h3>
            <p className="mt-1 text-xs capitalize text-slate-500">{model.algorithm.replaceAll('_', ' ')}</p>
            <p className="mt-2 text-[11px] text-slate-500">{String(model.parameters.n_estimators ?? '—')} trees · {String(model.parameters.missing_values_strategy ?? 'median')} imputation</p>
            <span className={`mt-3 inline-flex rounded-full px-2 py-1 text-[10px] font-semibold ${model.is_active === false ? 'bg-forest-950/40 text-forest-200' : 'bg-forest-600/20 text-forest-50'}`}>{model.is_active === false ? 'Inactive' : 'Active'}</span>
          </article>)}</div>
          : <EmptyPanel title="No configurations saved" body="Create a model configuration to make it available for analysis runs." />}
      </section>
      </section>}
      <section aria-labelledby="analysis-tab-history" className={`${panelClass} green-panel space-y-5 rounded-2xl border-forest-400/25 p-5 shadow-md sm:p-6`} hidden={activeTab !== 'history'} id="analysis-panel-history" role="tabpanel">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-forest-400/20 pb-4">
          <div className="min-w-0 flex-1"><h2 className="text-base font-bold text-slate-900">Analysis history</h2><p className="mt-1 max-w-4xl text-xs leading-5 text-forest-200">Filter and compare stored executions. Detection totals count run-level algorithm flags, so the same measurement may be counted in separate runs.</p></div>
          <span className="shrink-0 rounded-full border border-forest-400/25 bg-forest-950/40 px-3 py-1.5 text-xs font-semibold tabular-nums text-forest-100">{historyTotal.toLocaleString()} matching runs</span>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="grid min-w-0 content-start gap-3 rounded-xl border border-forest-400/20 bg-forest-950/25 p-4 md:col-span-2">
            <h3 className="text-sm font-bold text-forest-50">Search</h3>
            <label className="grid gap-1.5 text-[11px] font-medium text-forest-200">Analysis history<input className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} value={historySearch} onChange={(event) => { setHistorySearch(event.target.value); setHistoryOffset(0) }} placeholder="Dataset, device, model, status" /></label>
          </div>
          <div className="grid min-w-0 content-start gap-3 rounded-xl border border-forest-400/20 bg-forest-950/25 p-4">
            <h3 className="text-sm font-bold text-forest-50">Source</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-forest-200">Dataset<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} value={historyDataset} onChange={(event) => { setHistoryDataset(event.target.value); setHistoryOffset(0) }}><option value="">All datasets</option>{datasets.map((dataset) => <option value={dataset.id} key={dataset.id}>{dataset.name}</option>)}</select></label>
              <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-forest-200">Registered device<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} value={historyDevice} onChange={(event) => { setHistoryDevice(event.target.value); setHistoryOffset(0) }}><option value="">All devices</option>{Array.from(new Map(datasets.filter((dataset) => dataset.device_id !== null).map((dataset) => [dataset.device_id, dataset.device_name ?? `Device ${dataset.device_id}`])).entries()).map(([id, name]) => <option value={String(id)} key={id}>{name}</option>)}</select></label>
            </div>
          </div>
          <div className="grid min-w-0 content-start gap-3 rounded-xl border border-forest-400/20 bg-forest-950/25 p-4">
            <h3 className="text-sm font-bold text-forest-50">Run</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-forest-200">Algorithm<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} value={historyAlgorithm} onChange={(event) => { setHistoryAlgorithm(event.target.value); setHistoryOffset(0) }}><option value="">All algorithms</option>{detectors.map((detector) => <option value={detector.algorithm} key={detector.algorithm}>{detector.display_name}</option>)}</select></label>
              <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-forest-200">Status<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} value={historyStatus} onChange={(event) => { setHistoryStatus(event.target.value); setHistoryOffset(0) }}><option value="">All statuses</option>{['pending', 'running', 'completed', 'failed'].map((status) => <option value={status} key={status}>{status}</option>)}</select></label>
              <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-forest-200">From<input className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} type="datetime-local" value={historyStart} onChange={(event) => { setHistoryStart(event.target.value); setHistoryOffset(0) }} /></label>
              <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-forest-200">To<input className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} type="datetime-local" value={historyEnd} onChange={(event) => { setHistoryEnd(event.target.value); setHistoryOffset(0) }} /></label>
              <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-forest-200">Sort<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} value={historySort} onChange={(event) => setHistorySort(event.target.value)}>{[['created_at', 'Execution time'], ['anomalies_found', 'Anomaly count'], ['total_records', 'Measurements'], ['status', 'Status']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-forest-200">Order<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} value={historyDirection} onChange={(event) => setHistoryDirection(event.target.value)}><option value="desc">Descending</option><option value="asc">Ascending</option></select></label>
            </div>
          </div>
        </div>
        <div className="flex min-h-11 items-center rounded-lg border border-forest-400/20 bg-forest-950/25 px-3 py-2">
          <label className="flex cursor-pointer items-center gap-2.5 text-xs font-medium text-forest-100"><input className="h-4 w-4 cursor-pointer rounded border-forest-400/50 accent-forest-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-400/40" checked={hideZeroDetections} onChange={(event) => setHideZeroDetections(event.target.checked)} type="checkbox" />Hide runs with 0 detections</label>
        </div>
        {hideZeroDetections && <p className="-mt-2 text-[10px] text-slate-500">This toggle filters only the currently loaded page.</p>}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-forest-400/20 bg-forest-950/20 px-4 py-3">
          <div><p className="text-xs text-slate-500">Compare counts and available metrics. Algorithms can flag different measurements; only same-dataset runs with comparable measurements are matched by source row.</p><p className="mt-1 text-xs text-slate-800"><strong className="font-semibold">{selectedAnalysisIds.length} selected</strong><span className="ml-2 text-slate-500">Compare up to 10 runs.</span></p></div>
          <button className={`${buttonClass} min-h-10 rounded-lg transition-colors duration-150 hover:bg-forest-700 active:bg-forest-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-300/60`} disabled={selectedAnalysisIds.length < 2} onClick={() => void compare()}>Compare selected</button>
        </div>
        <div className="max-h-[70vh] overflow-auto rounded-xl border border-forest-400/20">
          <table className="w-full min-w-[1180px] table-fixed text-left text-xs">
            <thead className="sticky top-0 z-[1] bg-forest-950 text-[10px] uppercase tracking-wider text-forest-200"><tr>{['Compare', 'Run', 'Device', 'Dataset / experiment', 'Algorithm', 'Execution time', 'Measurements', 'Detections', 'Duration', 'Status', 'Actions'].map((heading) => <th className={`whitespace-nowrap border-b border-forest-400/25 px-3 py-3 font-semibold ${['Measurements', 'Detections', 'Duration'].includes(heading) ? 'text-right' : ''} ${heading === 'Compare' ? 'w-20' : ''} ${heading === 'Run' ? 'w-20' : ''} ${heading === 'Device' ? 'w-36' : ''} ${heading === 'Dataset / experiment' ? 'w-56' : ''} ${heading === 'Algorithm' ? 'w-36' : ''} ${heading === 'Execution time' ? 'w-52' : ''} ${['Measurements', 'Detections', 'Duration'].includes(heading) ? 'w-28' : ''} ${heading === 'Status' ? 'w-32' : ''} ${heading === 'Actions' ? 'w-36' : ''}`} key={heading}>{heading}</th>)}</tr></thead>
            <tbody className="divide-y divide-forest-400/20">{displayedHistory.map((analysis) => <tr className={`transition-colors duration-150 hover:bg-forest-600/10 ${selectedAnalysisIds.includes(analysis.id) ? 'bg-forest-600/10' : ''}`} key={analysis.id}>
              <td className="px-3 py-3"><input className="h-4 w-4 cursor-pointer rounded border-forest-400/50 accent-forest-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-400/40" aria-label={`Select analysis ${analysis.id} for comparison`} type="checkbox" checked={selectedAnalysisIds.includes(analysis.id)} onChange={() => toggleComparison(analysis.id)} /></td>
              <td className="px-3 py-3 font-mono tabular-nums">#{analysis.id}</td>
              <td className="truncate px-3 py-3" title={analysis.device_name ?? ''}>{analysis.device_name}</td>
              <td className="truncate whitespace-nowrap px-3 py-3" title={analysis.dataset_name ?? ''}>{analysis.dataset_name}</td>
              <td className="truncate px-3 py-3 capitalize">{(analysis.algorithm ?? analysis.model_name ?? 'analysis').replaceAll('_', ' ')}</td>
              <td className="whitespace-nowrap px-3 py-3">{new Date(analysis.started_at ?? analysis.created_at).toLocaleString()}<small className="mt-1 block text-[10px] text-forest-200">{analysis.execution_type ?? 'manual'}</small></td>
              <td className="px-3 py-3 text-right tabular-nums">{analysis.total_records.toLocaleString()}</td>
              <td className="px-3 py-3 text-right tabular-nums">{analysis.anomalies_found.toLocaleString()}</td>
              <td className="px-3 py-3 text-right tabular-nums">{analysis.duration_seconds == null ? '—' : `${analysis.duration_seconds.toFixed(2)} s`}</td>
              <td className="px-3 py-3"><span className={analysis.status === 'completed' ? '[&>span]:!bg-forest-600/25 [&>span]:!text-forest-300 [&>span>span]:!bg-forest-300' : ''}><StatusBadge value={analysis.status} /></span>{analysis.error_message && <small className="mt-1 block max-w-40 text-rose-700">{analysis.error_message}</small>}</td>
              <td className="whitespace-nowrap px-3 py-3"><button className="mr-2 inline-flex h-8 w-[4.75rem] items-center justify-center rounded-lg border border-forest-400/40 px-2 text-[10px] font-semibold text-forest-100 transition-colors duration-150 hover:bg-forest-600/20 active:bg-forest-600/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-300/60" type="button" onClick={() => void inspect(analysis)}>Details</button><button className="inline-flex h-8 w-[4.75rem] items-center justify-center rounded-lg bg-forest-600 px-2 text-[10px] font-semibold text-forest-50 transition-colors duration-150 hover:bg-forest-700 active:bg-forest-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-300/60 disabled:cursor-not-allowed disabled:opacity-50" type="button" disabled={!canEdit} onClick={() => void repeat(analysis)}>Repeat</button></td>
            </tr>)}</tbody>
          </table>
          {historyLoading && <p className="py-5 text-center text-xs text-slate-500">Loading analysis history…</p>}
          {!historyLoading && displayedHistory.length === 0 && <EmptyPanel title={hideZeroDetections && history.length ? 'No runs with detections on this page' : 'No matching analysis runs'} body="Change the filters or start a new analysis to create a history record." />}
        </div>
        <div className="flex items-center justify-between text-xs text-slate-500"><span>{historyTotal ? `${historyOffset + 1}–${Math.min(historyOffset + history.length, historyTotal)} of ${historyTotal}` : 'No records'}</span><div className="flex gap-2"><button className={`${buttonClass} bg-forest-950/40 text-forest-100 ring-1 ring-forest-400/30 hover:bg-forest-800`} disabled={historyOffset === 0 || historyLoading} onClick={() => setHistoryOffset(Math.max(0, historyOffset - historyPageSize))}>Previous</button><button className={`${buttonClass} bg-forest-950/40 text-forest-100 ring-1 ring-forest-400/30 hover:bg-forest-800`} disabled={historyOffset + historyPageSize >= historyTotal || historyLoading} onClick={() => setHistoryOffset(historyOffset + historyPageSize)}>Next</button></div>        </div>
      {comparison && <section className={`${panelClass} green-panel space-y-4`}>
        <div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">Comparison #{String(comparison.id)}</h2><p className="mt-1 text-xs text-slate-500">Stored comparison snapshot · statistical evidence for human review</p></div><button className={buttonClass} type="button" onClick={() => void onAction(() => download(`/analyses/comparisons/${String(comparison.id)}/csv`, `analysis-comparison-${String(comparison.id)}.csv`), 'Comparison exported')}>Export CSV</button></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[950px] text-left text-xs"><thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500"><tr>{['Run', 'Detector', 'Status', 'Measurements', 'Anomalies / rate', 'Score distribution (min / p25 / median / p75 / p95 / max)', 'Runtime', 'History states'].map((label) => <th className="px-3 py-2" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{(comparison.runs as Record<string, unknown>[]).map((run) => {
          const distribution = run.score_distribution as Record<string, unknown>
          return <tr key={String(run.analysis_id)}><td className="px-3 py-2">#{String(run.analysis_id)}</td><td className="px-3 py-2">{String(run.algorithm)}</td><td className="px-3 py-2 capitalize">{String(run.status)}</td><td className="px-3 py-2">{Number(run.total_records).toLocaleString()}</td><td className="px-3 py-2">{String(run.anomalies_found)} / {(Number(run.anomaly_rate) * 100).toFixed(2)}%</td><td className="px-3 py-2">{distribution.min == null ? '—' : ['min', 'p25', 'median', 'p75', 'p95', 'max'].map((key) => Number(distribution[key]).toFixed(4)).join(' / ')}</td><td className="px-3 py-2">{run.runtime_seconds == null ? '—' : `${Number(run.runtime_seconds).toFixed(2)} s`}</td><td className="px-3 py-2">{JSON.stringify(run.history_state_counts)}</td></tr>
        })}</tbody></table></div>
        <div><h3 className="text-xs font-semibold text-slate-700">Agreement matrix</h3><div className="mt-2 overflow-x-auto"><table className="w-full text-left text-xs"><thead className="bg-slate-50"><tr>{['Run', 'Compared run', 'Matched by', 'Both flagged', 'Left only', 'Right only'].map((label) => <th className="px-3 py-2" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{(comparison.agreement_matrix as Record<string, unknown>[]).map((pair) => <tr key={`${String(pair.analysis_id)}-${String(pair.other_analysis_id)}`}><td className="px-3 py-2">#{String(pair.analysis_id)}</td><td className="px-3 py-2">#{String(pair.other_analysis_id)}</td><td className="px-3 py-2">{String(pair.matched_by)}</td><td className="px-3 py-2">{String(pair.both)}</td><td className="px-3 py-2">{String(pair.left_only)}</td><td className="px-3 py-2">{String(pair.right_only)}</td></tr>)}</tbody></table></div></div>
      </section>}
      {details && <section className={`${panelClass} green-panel space-y-4`}>
        <div className="sticky top-16 z-10 flex flex-wrap items-center justify-between gap-3 border-b border-forest-400/20 bg-forest-950/95 py-3 backdrop-blur">
          <div><h2 className="text-sm font-semibold text-slate-900">Analysis #{String(details.id)} results</h2><p className="mt-1 text-xs text-slate-500">Run results and stored metadata for review.</p></div>
          <button className="rounded-md border border-forest-400/30 px-3 py-2 text-xs font-semibold text-forest-100 hover:bg-forest-600/20" onClick={() => { setDetails(null); setResults([]); setTimeline([]) }}>Close details</button>
        </div>
        <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            ['Dataset', String(details.dataset_name ?? '—')],
            ['Registered device', String(details.device_name ?? '—')],
            ['Algorithm', String(details.algorithm ?? details.model_name ?? '—').replaceAll('_', ' ')],
            ['Model configuration', (() => {
              const configuration = details.configuration as Record<string, unknown> | null | undefined
              const name = configuration?.name ?? details.model_name
              return typeof name === 'string' && name.trim() ? name : 'No model configuration'
            })()],
            ['Status', String(details.status ?? '—')],
            ['Execution time', new Date(String(details.started_at ?? details.created_at)).toLocaleString()],
            ['Measurements processed', Number(details.total_records ?? 0).toLocaleString()],
            ['Detections', Number(details.anomalies_found ?? 0).toLocaleString()],
            ['Duration', details.duration_seconds == null ? '—' : `${String(details.duration_seconds)} seconds`],
          ].map(([label, value]) => <div className="rounded-lg border border-forest-400/20 p-3" key={label}>
            <dt className="text-[10px] font-semibold uppercase tracking-wide text-forest-200">{label}</dt>
            <dd className="mt-1 break-words text-xs font-medium text-forest-50">{value}</dd>
          </div>)}
        </dl>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {(['critical', 'high', 'medium', 'low'] as const).map((severity) => {
            const distribution = (details.severity_distribution ?? {}) as Record<string, number | undefined>
            const count = distribution[severity]
            return <article className="rounded-lg border border-forest-400/20 px-3 py-2" key={severity}>
              <span className="text-[10px] font-semibold uppercase text-forest-200">{severity} potential detections</span>
              <strong className="mt-1 block text-lg tabular-nums text-forest-50">{count === undefined ? '—' : count.toLocaleString()}</strong>
            </article>
          })}
        </div>
        <details className="mt-4"><summary className="cursor-pointer text-xs font-semibold text-slate-700">Analysis configuration and stored metrics</summary><pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-forest-950/50 p-3 text-[10px] text-forest-100">{JSON.stringify({ configuration: details.configuration, metrics: details.metrics }, null, 2)}</pre></details>
        {error && <ErrorMessage>{error}</ErrorMessage>}
        {timeline.length > 0 && <div className="mt-4 h-64"><ResponsiveContainer width="100%" height="100%"><LineChart data={timeline}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="timestamp" tick={{ fontSize: 9 }} /><YAxis /><Tooltip /><Line dataKey="value" dot={false} stroke="#466b4c" /><Line dataKey={(point: Record<string, unknown>) => point.is_anomaly ? point.value : null} name="Flagged" stroke="none" dot={{ r: 4, fill: '#e11d48' }} /></LineChart></ResponsiveContainer></div>}
        <div className="mt-4 max-h-64 overflow-auto"><table className="w-full min-w-[700px] text-left text-xs"><thead className="sticky top-0 bg-forest-950 text-forest-200"><tr>{['Source row', 'Classification / history', 'Score', 'Detector votes / shift', 'Feature values'].map((label) => <th className="px-3 py-2" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-forest-400/20">{results.map((result) => {
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
          const featureValues = result.feature_values as Record<string, unknown>
          return <tr key={String(result.id)}><td className="px-3 py-2 tabular-nums">{String(result.source_row_number)}</td><td className="px-3 py-2">{classification}</td><td className="px-3 py-2 text-right tabular-nums">{result.anomaly_score == null ? '—' : Number(result.anomaly_score).toFixed(4)}</td><td className="px-3 py-2"><p>{String(resultDetails.fired_rule ?? shiftDetails)}</p>{resultDetails.confidence != null && <p>Confidence {Number(resultDetails.confidence).toFixed(2)}</p>}{detectorVotes.map((vote) => <p key={String(vote.algorithm)}>{String(vote.algorithm)}: {vote.vote == null ? String(vote.history_state) : `${vote.vote ? 'voted unusual' : 'voted normal'} · score ${Number(vote.score).toFixed(4)}`}</p>)}</td><td className="px-3 py-2"><dl className="grid max-w-xl grid-cols-2 gap-1">{Object.entries(featureValues).map(([name, value]) => {
            const fullValue = typeof value === 'string' ? value : JSON.stringify(value)
            const displayValue = typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : String(value)
            return <div className="flex min-w-0 items-center justify-between gap-2 rounded-md border border-forest-400/20 px-2 py-1" key={name} title={`${name}: ${fullValue}`}>
              <dt className="truncate text-[10px] text-forest-200">{name}</dt><dd className="truncate font-mono text-[10px] text-forest-50">{displayValue}</dd>
            </div>
          })}</dl></td></tr>
        })}</tbody></table></div>
      </section>}
      </section>
    </>
  )
}
