import { useCallback, useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ApiError, download, request } from '../api'
import { numericAnalysisFeatures } from '../datasetFeatures'

import type { Action, Dataset, DetectorCatalogEntry, ModelConfig, Analysis, AnalysisSummary, Device, EquipmentParameter } from '../types/workflow'

import { inputClass, buttonClass, panelClass, Field, EmptyPanel, ErrorMessage } from '../components/ui'

import { PageTitle } from '../components/PageTitle'
import { StatusBadge } from '../components/StatusBadge'
import { analysisStrings as text } from './analysisStrings'
import { canAdvanceAnalysisStep, canBeginAnalysisSubmission, canCompareAnalyses, canSubmitAnalysisRun, getAnalysisTabs, getHistoryPanelState, shouldExcludeLiveReadings, toggleAnalysisSelection, type AnalysisTab } from './analysisFlow'

type RequestFailure = { message: string; status: number | null }

function getAnalysisSourceType(analysis: Analysis) {
  if (analysis.source_type === 'dataset') return 'dataset'
  const configuration = analysis.metrics.configuration
  if (typeof configuration !== 'object' || configuration === null) return null
  const sourceType = (configuration as Record<string, unknown>).data_source_type
  return typeof sourceType === 'string' ? sourceType : null
}

function getSourceTypeLabel(sourceType: string | null | undefined) {
  if (!sourceType) return text.history.table.sourceTypes.notRecorded
  return text.history.table.sourceTypes[sourceType as keyof typeof text.history.table.sourceTypes]
    ?? text.history.table.sourceTypes.notRecorded
}

function getDeviceMeasurementSourceType(sourceType: string | undefined): 'manual' | 'sensor' | 'simulated' {
  return sourceType === 'sensor' || sourceType === 'simulated' ? sourceType : 'manual'
}

function analysisStatusTone(status: string) {
  if (status === 'completed') return '[&>span]:!bg-forest-600/25 [&>span]:!text-forest-300 [&>span>span]:!bg-forest-300'
  if (status === 'failed') return '[&>span]:!bg-rose-950/70 [&>span]:!text-rose-200 [&>span>span]:!bg-rose-300'
  if (status === 'pending' || status === 'running') return '[&>span]:!bg-amber-950/70 [&>span]:!text-amber-200 [&>span>span]:!bg-amber-300'
  return ''
}

function requestFailure(error: unknown, fallback: string): RequestFailure {
  return {
    message: error instanceof Error ? error.message : fallback,
    status: error instanceof ApiError ? error.status : null,
  }
}

export function AnalysisTools({
  datasets,
  devices,
  models,
  analyses,
  canEdit,
  onAction,
}: {
  datasets: (Dataset & { column_metadata: { name: string; type: string }[]; status: string })[]
  devices: Device[]
  models: ModelConfig[]
  analyses: Analysis[]
  canEdit: boolean
  onAction: Action
}) {
  const [activeTab, setActiveTab] = useState<AnalysisTab>('history')
  const [hideZeroDetections, setHideZeroDetections] = useState(false)
  const [runSource, setRunSource] = useState<'dataset' | 'equipment'>('dataset')
  const [runStep, setRunStep] = useState(1)
  const [datasetId, setDatasetId] = useState('')
  const [modelId, setModelId] = useState('')
  const [deviceId, setDeviceId] = useState('')
  const [parameterId, setParameterId] = useState('')
  const [equipmentParameters, setEquipmentParameters] = useState<EquipmentParameter[]>([])
  const [equipmentParametersLoading, setEquipmentParametersLoading] = useState(false)
  const [equipmentAlgorithm, setEquipmentAlgorithm] = useState('isolation_forest')
  const [equipmentSourceType, setEquipmentSourceType] = useState<'manual' | 'sensor' | 'simulated'>('manual')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submittedAnalysis, setSubmittedAnalysis] = useState<Analysis | null>(null)
  const [detectors, setDetectors] = useState<DetectorCatalogEntry[]>([])
  const [selectedAlgorithm, setSelectedAlgorithm] = useState('isolation_forest')
  const [featureNames, setFeatureNames] = useState<string[]>([])
  const [timeColumn, setTimeColumn] = useState('')
  const [selectedAnalysisIds, setSelectedAnalysisIds] = useState<number[]>([])
  const [comparison, setComparison] = useState<Record<string, unknown> | null>(null)
  const [details, setDetails] = useState<Record<string, unknown> | null>(null)
  const [detailAnalysis, setDetailAnalysis] = useState<Analysis | null>(null)
  const [detailsError, setDetailsError] = useState<RequestFailure | null>(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [results, setResults] = useState<Record<string, unknown>[]>([])
  const [timeline, setTimeline] = useState<Record<string, unknown>[]>([])
  const [error, setError] = useState('')
  const [summary, setSummary] = useState<AnalysisSummary | null>(null)
  const [history, setHistory] = useState<Analysis[]>(analyses)
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<RequestFailure | null>(null)
  const [summaryError, setSummaryError] = useState<RequestFailure | null>(null)
  const [historySearch, setHistorySearch] = useState('')
  const [historySource, setHistorySource] = useState('')
  const [historyDataset, setHistoryDataset] = useState('')
  const [historyDevice, setHistoryDevice] = useState('')
  const [historyAlgorithm, setHistoryAlgorithm] = useState('')
  const [historyStatus, setHistoryStatus] = useState('')
  const [historyRunKind, setHistoryRunKind] = useState('')
  const [expandLiveReadings, setExpandLiveReadings] = useState(false)
  const [historyStart, setHistoryStart] = useState('')
  const [historyEnd, setHistoryEnd] = useState('')
  const [historySort, setHistorySort] = useState('created_at')
  const [historyDirection, setHistoryDirection] = useState('desc')
  const historyPageSize = 50
  const selectedDataset = datasets.find((dataset) => String(dataset.id) === datasetId)
  const selectedDevice = devices.find((device) => String(device.id) === deviceId)
  const enabledEquipmentParameters = deviceId
    ? equipmentParameters.filter((parameter) => parameter.enabled)
    : []
  const selectedEquipmentParameter = enabledEquipmentParameters.find((parameter) => String(parameter.id) === parameterId)
  const numericFeatures = useMemo(
    () => numericAnalysisFeatures(selectedDataset),
    [selectedDataset],
  )
  const detectedTimeColumns = useMemo(() => selectedDataset?.column_metadata
    .filter((column) => /time|date/i.test(column.name))
    .map((column) => column.name) ?? [], [selectedDataset])
  const selectedDetector = detectors.find((detector) => detector.algorithm === selectedAlgorithm)
  const equipmentDetector = detectors.find((detector) => detector.algorithm === equipmentAlgorithm)
  const selectedModel = models.find((model) => String(model.id) === modelId)
  const requiresSingleFeature = selectedModel?.algorithm === 'ewma' || selectedModel?.algorithm === 'cusum'
  const displayedHistory = hideZeroDetections
    ? history.filter((analysis) => analysis.anomalies_found > 0)
    : history
  const tabs = getAnalysisTabs(canEdit, text.tabs)

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
        if (active) setError(caught instanceof Error ? caught.message : text.notices.failedCatalog)
      })
    return () => { active = false }
  }, [])

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    setHistoryError(null)
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
    if (historySource) params.set('source_type', historySource)
    if (historyRunKind) params.set('run_kind', historyRunKind)
    else if (shouldExcludeLiveReadings(historyRunKind, expandLiveReadings)) params.set('exclude_live_readings', 'true')
    if (historyStart) params.set('start_date', new Date(historyStart).toISOString())
    else if (expandLiveReadings && historyRunKind === 'live_reading') {
      params.set('start_date', new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString())
    }
    if (historyEnd) params.set('end_date', new Date(historyEnd).toISOString())
    try {
      const response = await request<{ items: Analysis[]; total: number }>(
        `/analyses/history?${params}`,
      )
      setHistory(response.items)
      setHistoryTotal(response.total)
      setHistoryError(null)
    } catch (caught) {
      setHistoryError(requestFailure(caught, text.notices.failedLoad))
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
    historySource,
    historySort,
    historyStart,
    historyStatus,
    historyRunKind,
    expandLiveReadings,
  ])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      void Promise.all([
        loadHistory(),
        request<AnalysisSummary>('/analyses/summary').then((result) => {
          setSummary(result)
          setSummaryError(null)
        }).catch((caught: unknown) => {
          setSummaryError(requestFailure(caught, text.notices.failedSummary))
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

  useEffect(() => {
    if (!deviceId) return
    let active = true
    request<EquipmentParameter[]>(`/devices/${deviceId}/parameters`)
      .then((parameters) => {
        if (!active) return
        const enabled = parameters.filter((parameter) => parameter.enabled)
        setEquipmentParameters(enabled)
        setParameterId((current) => enabled.some((parameter) => String(parameter.id) === current)
          ? current
          : '')
      })
      .catch((caught: unknown) => {
        if (active) {
          setEquipmentParameters([])
          setParameterId('')
          setError(caught instanceof Error ? caught.message : text.notices.failedParameters)
        }
      })
      .finally(() => {
        if (active) setEquipmentParametersLoading(false)
      })
    return () => { active = false }
  }, [deviceId])

  function handleDeviceChange(nextDeviceId: string) {
    setDeviceId(nextDeviceId)
    setEquipmentParameters([])
    setParameterId('')
    setEquipmentParametersLoading(Boolean(nextDeviceId))
    setEquipmentSourceType(getDeviceMeasurementSourceType(
      devices.find((device) => String(device.id) === nextDeviceId)?.source_type,
    ))
  }

  const flowRequirements = {
    source: runSource,
    hasData: runSource === 'dataset'
      ? Boolean(selectedDataset)
      : Boolean(selectedDevice && selectedEquipmentParameter),
    hasFeatures: runSource === 'equipment' || featureNames.length > 0,
    hasMethod: runSource === 'equipment' ? Boolean(equipmentDetector) : Boolean(selectedModel),
  }
  const canAdvanceRunStep = canAdvanceAnalysisStep(runStep, flowRequirements)

  function readDetectorParameters(form: FormData, detector: DetectorCatalogEntry): Record<string, unknown> {
    return Object.fromEntries(detector.parameters.map((parameter) => {
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
      if (parameter.type === 'boolean') return [parameter.name, value === 'true']
      if ((parameter.type === 'string_or_number' || parameter.type === 'number_or_auto') && Number.isFinite(Number(value))) {
        return [parameter.name, Number(value)]
      }
      return [parameter.name, value]
    }))
  }

  async function launch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canBeginAnalysisSubmission(isSubmitting)) return
    const config = models.find((item) => String(item.id) === modelId)
    if (runSource === 'dataset' && (config?.algorithm === 'ewma' || config?.algorithm === 'cusum') && featureNames.length !== 1) {
      setError(text.run.datasetFeatureLimit(config.algorithm))
      return
    }
    const form = new FormData(event.currentTarget)
    let queuedAnalysis: Analysis | null = null
    setIsSubmitting(true)
    try {
      if (runSource === 'dataset' && selectedDataset && config) {
        if (selectedDataset.row_count >= 50000) {
          const durationParams = new URLSearchParams({
            source_type: 'dataset',
            algorithm: config.algorithm,
            status_filter: 'completed',
            sort_by: 'completed_at',
            sort_direction: 'desc',
            limit: '500',
            offset: '0',
          })
          const durationHistory = await request<{ items: Analysis[] }>(`/analyses/history?${durationParams}`)
          const priorDurations = durationHistory.items
            .filter((run) => run.duration_seconds != null && run.total_records > 0)
            .map((run) => Number(run.duration_seconds) / run.total_records * selectedDataset.row_count)
            .filter((duration) => Number.isFinite(duration) && duration > 0)
          if (priorDurations.length) {
            const estimate = `${Math.min(...priorDurations).toFixed(0)}–${Math.max(...priorDurations).toFixed(0)} seconds`
            if (!window.confirm(text.run.confirmLarge(config.algorithm.replaceAll('_', ' '), estimate, selectedDataset.row_count))) return
          } else if (!window.confirm(text.run.noDurationHistory)) {
            return
          }
        }
        await onAction(async () => {
          queuedAnalysis = await request<Analysis>('/analyses', {
            method: 'POST',
            body: JSON.stringify({
              dataset_id: Number(datasetId),
              model_config_id: Number(modelId),
              features: featureNames,
              time_column: timeColumn || null,
              missing_values_strategy: form.get('missing_values_strategy'),
              scaling: form.get('scaling'),
            }),
          })
          return queuedAnalysis
        }, text.run.queuedWithModel(config.name))
      } else if (selectedDevice && selectedEquipmentParameter && equipmentDetector) {
        let detectorParameters: Record<string, unknown>
        try {
          detectorParameters = readDetectorParameters(form, equipmentDetector)
        } catch (caught) {
          setError(caught instanceof Error ? `${text.notices.configurationInvalid}: ${caught.message}` : text.notices.configurationInvalid)
          return
        }
        await onAction(async () => {
          queuedAnalysis = await request<Analysis>('/analyses/equipment-history', {
            method: 'POST',
            body: JSON.stringify({
              device_id: selectedDevice.id,
              parameter_id: selectedEquipmentParameter.id,
              algorithm: equipmentAlgorithm,
              detector_parameters: detectorParameters,
              source_type: equipmentSourceType,
              training_window: Number(form.get('training_window')),
              refit_every: Number(form.get('refit_every')),
              max_readings: Number(form.get('max_readings')),
            }),
          })
          return queuedAnalysis
        }, text.run.queuedWithDetector(equipmentDetector.display_name))
      }
      if (queuedAnalysis) setSubmittedAnalysis(queuedAnalysis)
      await loadHistory()
      setSummary(await request<AnalysisSummary>('/analyses/summary'))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : text.notices.failedQueue)
    } finally {
      setIsSubmitting(false)
    }
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
      setError(caught instanceof Error ? `${text.notices.configurationInvalid}: ${caught.message}` : text.models.invalidConfigurationJson)
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
    }), text.notices.modelSaved)
    event.currentTarget.reset()
  }

  async function compare() {
    if (!canCompareAnalyses(canEdit, selectedAnalysisIds)) return
    await onAction(async () => {
      setComparison(await request<Record<string, unknown>>('/analyses/comparisons', {
        method: 'POST',
        body: JSON.stringify({ analysis_ids: selectedAnalysisIds }),
      }))
    }, text.notices.comparisonReady)
  }

  async function inspect(analysis: Analysis) {
    setDetailsLoading(true)
    setDetails(null)
    setResults([])
    setTimeline([])
    setDetailsError(null)
    setDetailAnalysis(analysis)
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
      setDetailsError(requestFailure(caught, text.notices.failedDetails))
    } finally {
      setDetailsLoading(false)
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
    }), text.run.queuedMessage(analysis.id))
  }

  function toggleComparison(id: number) {
    setSelectedAnalysisIds((previous) => toggleAnalysisSelection(previous, id))
  }

  function modelCard(model: ModelConfig) {
    return <article className="grid gap-3 rounded-lg border border-forest-400/20 p-4" key={model.id}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div><h3 className="text-sm font-semibold text-slate-900">{model.name}</h3><p className="mt-1 text-xs capitalize text-slate-500">{model.algorithm.replaceAll('_', ' ')}</p></div>
        <span className="rounded-full border border-forest-400/25 px-2 py-1 text-[10px] font-semibold text-forest-100">{model.source === 'tuned_from_development_evidence' ? text.models.tuned : text.models.saved}</span>
      </div>
      <dl className="grid gap-1 text-[11px] text-forest-100 sm:grid-cols-2">
        {Object.entries(model.parameters).map(([name, value]) => <div className="min-w-0" key={name}><dt className="inline font-medium">{name.replaceAll('_', ' ')} = </dt><dd className="inline break-all text-forest-200">{typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)}</dd></div>)}
      </dl>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-forest-400/15 pt-2 text-[10px] text-forest-200">
        <span>{text.models.created}: {model.created_at ? new Date(model.created_at).toLocaleString() : text.common.notAvailable}</span>
        <span>{text.models.usedRuns(model.used_in_runs ?? 0)}</span>
        <span>{model.is_active === false ? text.models.inactive : text.models.active}</span>
      </div>
      {model.is_superseded && model.superseded_reason && <p className="text-[11px] text-forest-200">{text.models.supersededReason}: {model.superseded_reason}</p>}
    </article>
  }

  function analysisResultPanel() {
    if (!details) {
      if (!detailsLoading && !detailsError) return null
      return <section className={`${panelClass} green-panel space-y-4`}>
        {detailsLoading && <div aria-label={text.accessibility.analysisResultLoading} className="grid animate-pulse gap-3 sm:grid-cols-2 xl:grid-cols-4 motion-reduce:animate-none" role="status">{Array.from({ length: 4 }, (_, index) => <div className="h-20 rounded-lg bg-forest-800/30" key={index} />)}</div>}
        {detailsError && <div><ErrorMessage>{`${detailsError.message}${detailsError.status ? text.common.httpStatus(detailsError.status) : ''}`}</ErrorMessage>{detailAnalysis && <button className="mt-2 text-xs underline" type="button" onClick={() => void inspect(detailAnalysis)}>{text.history.retry}</button>}</div>}
      </section>
    }
    const configuration = (details.configuration ?? {}) as Record<string, unknown>
    const metrics = (details.metrics ?? {}) as {
      history_evaluation?: {
        state?: string
        required?: number
        scored_rows?: number
        insufficient_history_rows?: number
      } | null
    }
    const historyEvaluation = metrics.history_evaluation
    const parameters = (configuration.detector_parameters ?? configuration.parameters ?? {}) as Record<string, unknown>
    const sourceType = getSourceTypeLabel(typeof configuration.data_source_type === 'string'
      ? configuration.data_source_type
      : details.source_type === 'dataset' ? 'dataset' : null)
    const sourceName = details.dataset_name ?? details.device_name ?? text.common.notAvailable
    const historyMissing = historyEvaluation?.insufficient_history_rows ?? 0
    const scoredRows = historyEvaluation?.scored_rows ?? Number(details.total_records ?? 0)
    const flaggedRows = Number(details.algorithm_flagged_results ?? details.anomaly_count ?? details.anomalies_found ?? 0)

    return <section className={`${panelClass} green-panel space-y-4`} id={`analysis-result-${String(details.id)}`}>
      <div className="sticky top-16 z-10 flex flex-wrap items-center justify-between gap-3 border-b border-forest-400/20 bg-forest-950/95 py-3 backdrop-blur">
        <div><h2 className="text-sm font-semibold text-slate-900">{text.result.resultsTitle(Number(details.id))}</h2><p className="mt-1 text-xs text-slate-500">{text.result.description}</p></div>
        <div className="flex items-center gap-3"><span className={analysisStatusTone(String(details.status ?? ''))}><StatusBadge value={String(details.status ?? 'unknown')} label={text.history.statuses[String(details.status)] ?? String(details.status ?? text.common.notAvailable)} /></span><button className="rounded-md border border-forest-400/30 px-3 py-2 text-xs font-semibold text-forest-100 hover:bg-forest-600/20" type="button" onClick={() => { setDetails(null); setDetailAnalysis(null); setDetailsError(null); setResults([]); setTimeline([]) }}>{text.result.close}</button></div>
      </div>
      {detailsLoading && <div aria-label={text.accessibility.analysisResultLoading} className="grid animate-pulse gap-3 sm:grid-cols-2 xl:grid-cols-4 motion-reduce:animate-none" role="status">{Array.from({ length: 4 }, (_, index) => <div className="h-20 rounded-lg bg-forest-800/30" key={index} />)}</div>}
      {detailsError && <div><ErrorMessage>{`${detailsError.message}${detailsError.status ? text.common.httpStatus(detailsError.status) : ''}`}</ErrorMessage>{detailAnalysis && <button className="mt-2 text-xs underline" type="button" onClick={() => void inspect(detailAnalysis)}>{text.history.retry}</button>}</div>}
      <h3 className="text-xs font-semibold text-forest-50">{text.result.summary}</h3>
      <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          [text.result.source, `${sourceName} · ${sourceType}`],
          [text.result.method, String(details.algorithm ?? details.model_name ?? text.common.notAvailable).replaceAll('_', ' ')],
          [text.result.parameters, Object.keys(parameters).length ? Object.entries(parameters).map(([name, value]) => `${name} = ${typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)}`).join(' · ') : text.common.notAvailable],
          [text.result.rowsScored, scoredRows.toLocaleString()],
          [text.result.flagged, flaggedRows.toLocaleString()],
          [text.result.insufficient, historyEvaluation ? `${historyMissing.toLocaleString()}${historyMissing && historyEvaluation.required != null ? ` · ${text.result.insufficientReason(historyMissing, historyEvaluation.required)}` : ''}` : text.result.notApplicable],
          [text.result.executionTime, new Date(String(details.started_at ?? details.created_at)).toLocaleString()],
          [text.result.duration, details.duration_seconds == null ? text.common.notAvailable : `${String(details.duration_seconds)} ${text.result.seconds}`],
        ].map(([label, value]) => <div className="min-w-0 rounded-lg border border-forest-400/20 p-3" key={label}>
          <dt className="text-[10px] font-semibold uppercase tracking-wide text-forest-200">{label}</dt>
          <dd className="mt-1 break-words text-xs font-medium text-forest-50">{value}</dd>
        </div>)}
      </dl>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {(['critical', 'high', 'medium', 'low'] as const).map((severity) => {
          const distribution = (details.severity_distribution ?? {}) as Record<string, number | undefined>
          const count = distribution[severity]
          return <article className="rounded-lg border border-forest-400/20 px-3 py-2" key={severity}>
            <span className="text-[10px] font-semibold uppercase text-forest-200">{text.result.severity(severity)}</span>
            <strong className="mt-1 block text-lg tabular-nums text-forest-50">{count === undefined ? text.result.noSeverity : count.toLocaleString()}</strong>
          </article>
        })}
      </div>
      <h3 className="text-xs font-semibold text-forest-50">{text.result.tableTitle}</h3>
      {!detailsLoading && !detailsError && results.length === 0 && <EmptyPanel title={text.result.noResult} body={text.result.noResult} />}
      {results.length > 0 && <div className="max-h-96 overflow-auto rounded-lg border border-forest-400/20">
        <table className="w-full min-w-[700px] text-left text-xs">
          <thead className="sticky top-0 bg-forest-950 text-forest-200"><tr>{[text.result.sourceRow, text.result.classification, text.result.score, text.result.votes, text.result.featureValues].map((label, index) => <th className={`px-3 py-2 ${index === 0 ? 'sticky left-0 z-[2] bg-forest-950' : ''}`} key={label}>{label}</th>)}</tr></thead>
          <tbody className="divide-y divide-forest-400/20">{results.map((result) => {
            const historyState = String(result.history_state ?? '')
            const resultDetails = (result.result_details ?? {}) as Record<string, unknown>
            const classification = historyState === 'insufficient_history'
              ? text.result.insufficientLabel(result.history_size, result.history_required)
              : historyState === 'excluded_missing_values'
                ? text.result.excludedMissing
                : result.is_anomaly ? text.result.flaggedRow : text.result.notFlagged
            const shiftDetails = resultDetails.shift_direction
              ? text.result.shift(String(resultDetails.shift_direction), String(resultDetails.shift_started_at ?? `source row ${resultDetails.shift_started_source_row_number ?? text.result.unknownRow}`))
              : text.common.notAvailable
            const detectorVotes = Array.isArray(resultDetails.detectors) ? resultDetails.detectors as Record<string, unknown>[] : []
            const featureValues = result.feature_values as Record<string, unknown>
            return <tr key={String(result.id)}>
              <td className="sticky left-0 z-[1] bg-forest-950 px-3 py-2 tabular-nums">{String(result.source_row_number)}</td>
              <td className="px-3 py-2">{classification}</td>
              <td className="px-3 py-2 text-right tabular-nums">{result.anomaly_score == null ? text.common.notAvailable : Number(result.anomaly_score).toFixed(4)}</td>
              <td className="px-3 py-2"><p>{String(resultDetails.fired_rule ?? shiftDetails)}</p>{resultDetails.confidence != null && <p>{text.result.confidence(Number(resultDetails.confidence).toFixed(2))}</p>}{detectorVotes.map((vote) => <p key={String(vote.algorithm)}>{String(vote.algorithm)}: {vote.vote == null ? String(vote.history_state) : `${vote.vote ? text.result.votedUnusual : text.result.votedNormal} · ${text.result.scoreLabel(Number(vote.score).toFixed(4))}`}</p>)}</td>
              <td className="px-3 py-2"><dl className="grid max-w-xl grid-cols-2 gap-1">{Object.entries(featureValues).map(([name, value]) => {
                const fullValue = typeof value === 'string' ? value : JSON.stringify(value)
                const displayValue = typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : String(value)
                return <div className="flex min-w-0 items-center justify-between gap-2 rounded-md border border-forest-400/20 px-2 py-1" key={name} title={`${name}: ${fullValue}`}>
                  <dt className="truncate text-[10px] text-forest-200">{name}</dt><dd className="truncate font-mono text-[10px] text-forest-50">{displayValue}</dd>
                </div>
              })}</dl></td>
            </tr>
          })}</tbody>
        </table>
      </div>}
      {timeline.length > 0 && <div className="mt-4 h-64"><ResponsiveContainer width="100%" height="100%"><LineChart data={timeline}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="timestamp" tick={{ fontSize: 9 }} /><YAxis /><Tooltip /><Line dataKey="value" dot={false} stroke="#466b4c" /><Line dataKey={(point: Record<string, unknown>) => point.is_anomaly ? point.value : null} name={text.result.flaggedSeries} stroke="none" dot={{ r: 4, fill: '#e11d48' }} /></LineChart></ResponsiveContainer></div>}
      <details className="rounded-lg border border-forest-400/20 p-3"><summary className="cursor-pointer text-xs font-semibold text-slate-700">{text.result.details}</summary><pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-forest-950/50 p-3 text-[10px] text-forest-100">{JSON.stringify({ configuration: details.configuration, metrics: details.metrics }, null, 2)}</pre></details>
    </section>
  }

  const canSubmitRun = canSubmitAnalysisRun(flowRequirements)
  const historyPanelState = getHistoryPanelState(historyLoading, Boolean(historyError), displayedHistory.length > 0)
  const activeHistoryFilters = [
    historySearch.trim(),
    historySource === 'dataset' ? text.history.sourceDataset : historySource === 'equipment' ? text.history.sourceEquipment : '',
    historyDataset && datasets.find((dataset) => String(dataset.id) === historyDataset)?.name,
    historyDevice && devices.find((device) => String(device.id) === historyDevice)?.name,
    historyAlgorithm,
    historyStatus && text.history.statuses[historyStatus],
    historyRunKind && text.history.kinds[historyRunKind],
    historyStart && `${text.history.from} ${historyStart}`,
    historyEnd && `${text.history.to} ${historyEnd}`,
  ].filter(Boolean).join(', ')
  const currentSubmittedAnalysis = submittedAnalysis
    ? history.find((analysis) => analysis.id === submittedAnalysis.id) ?? submittedAnalysis
    : null

  return (
    <>
      <PageTitle
        title={text.pageTitle}
        description={text.pageDescription}
      />
      {summary && <section aria-label={text.accessibility.analysisSummary} className="grid gap-3 xl:grid-cols-2">
        {[
          [text.summary.equipment, summary.registered_equipment],
          [text.summary.datasets, summary.datasets],
        ].map(([heading, population]) => {
          const value = population as AnalysisSummary['registered_equipment']
          const latest = value.latest_execution
          const next = value.next_scheduled_execution
          return <article className="green-panel space-y-3 rounded-xl border border-forest-400/20 p-4" key={heading as string}>
            <h2 className="text-sm font-semibold text-forest-50">{heading as string}</h2>
            <div className="grid gap-3 sm:grid-cols-3">
              <div><span className="block text-[10px] font-medium text-forest-200">{text.summary.analyses}</span><strong className="text-lg font-semibold tabular-nums text-forest-50">{value.analyses.toLocaleString()}</strong></div>
              <div><span className="block text-[10px] font-medium text-forest-200">{text.summary.uniqueMeasurements}</span><strong className="text-lg font-semibold tabular-nums text-forest-50">{value.measurements_flagged_unique.toLocaleString()}</strong></div>
              <div title={text.summary.countExplanation}><span className="block text-[10px] font-medium text-forest-200">{text.summary.detections}</span><strong className="text-lg font-semibold tabular-nums text-forest-50">{value.detections.toLocaleString()}</strong></div>
            </div>
            <div className="grid gap-2 border-t border-forest-400/15 pt-2 text-[11px] sm:grid-cols-2">
              <p><span className="text-forest-200">{text.summary.latest}: </span><span className="text-forest-50">{latest ? new Date(latest.started_at ?? latest.created_at).toLocaleString() : text.summary.none}</span></p>
              <p><span className="text-forest-200">{text.summary.next}: </span><span className="text-forest-50">{next ? new Date(next).toLocaleString() : text.summary.notScheduled}</span></p>
            </div>
          </article>
        })}
      </section>}
      {!summary && !summaryError && <div aria-label={text.accessibility.analysisSummaryLoading} className="grid animate-pulse gap-3 sm:grid-cols-2" role="status"><div className="h-36 rounded-xl bg-forest-800/30" /><div className="h-36 rounded-xl bg-forest-800/30" /></div>}
      {summaryError && <div><ErrorMessage>{`${summaryError.message}${summaryError.status ? text.common.httpStatus(summaryError.status) : ''}`}</ErrorMessage><button className="mt-2 text-xs underline" type="button" onClick={() => { setSummaryError(null); void request<AnalysisSummary>('/analyses/summary').then(setSummary).catch((caught: unknown) => setSummaryError(requestFailure(caught, text.notices.failedSummary))) }}>{text.history.retry}</button></div>}
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <p className="text-[11px] text-forest-200">{text.notices.detectionTotals}</p>
      <div className="min-w-0 overflow-x-auto">
        <div className="flex min-w-max gap-1 border-b border-forest-400/20" role="tablist" aria-label={text.accessibility.analysisSections}>
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
        <div><h2 className="text-sm font-semibold text-slate-900">{text.run.title}</h2><p className="mt-1 text-xs text-slate-500">{text.run.description}</p></div>
        <ol aria-label={text.accessibility.setupSteps} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {text.run.steps.map((label, index) => {
            const step = index + 1
            return <li key={label}><button aria-current={runStep === step ? 'step' : undefined} className={`min-h-10 w-full rounded-lg border px-3 py-2 text-left text-xs font-semibold ${runStep === step ? 'border-forest-400 bg-forest-600/20 text-forest-50' : 'border-forest-400/20 text-forest-200'}`} type="button" onClick={() => { if (step <= runStep) setRunStep(step) }}><span className="mr-2 tabular-nums">{step}</span>{label}</button></li>
          })}
        </ol>
        <form className="grid gap-4" onSubmit={launch}>
          {runStep === 1 && <section aria-labelledby="analysis-step-data" className="grid gap-4">
            <h3 className="text-xs font-semibold text-forest-50" id="analysis-step-data">{text.run.stepStatus(1)} · {text.run.steps[0]}</h3>
            <fieldset className="flex flex-wrap gap-3">
              <legend className="mb-2 text-xs font-medium text-slate-600">{text.run.dataSource}</legend>
              {(['dataset', 'equipment'] as const).map((source) => <label className="flex min-h-9 cursor-pointer items-center gap-2 rounded-lg border border-forest-400/25 px-3 py-2 text-xs text-forest-100" key={source}>
                <input checked={runSource === source} name="analysis-source" onChange={() => { setRunSource(source); setRunStep(1) }} type="radio" value={source} />
                {source === 'dataset' ? text.run.dataset : text.run.registeredDevice}
              </label>)}
            </fieldset>
            {runSource === 'dataset'
              ? <label className="grid max-w-2xl gap-1.5 text-xs font-medium text-slate-600">{text.run.dataset}
                  <select className={inputClass} value={datasetId} onChange={(event) => setDatasetId(event.target.value)} required>
                    <option value="">{text.run.selectDataset}</option>{datasets.filter((dataset) => dataset.status !== 'archived').map((dataset) => <option value={dataset.id} key={dataset.id}>{dataset.name} · {dataset.device_name}</option>)}
                  </select>
                </label>
              : <div className="grid gap-3 sm:grid-cols-2">
                  <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.run.registeredDevice}
                    <select className={inputClass} value={deviceId} onChange={(event) => handleDeviceChange(event.target.value)} required>
                      <option value="">{text.run.selectDevice}</option>{devices.filter((device) => device.status !== 'archived').map((device) => <option value={device.id} key={device.id}>{device.name} · {device.device_code} · {getSourceTypeLabel(device.source_type)}</option>)}
                    </select>
                    {devices.length === 0 && <span className="text-[11px] text-slate-500">{text.run.noDevices}</span>}
                  </label>
                  <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.run.parameter}
                    <select className={inputClass} value={parameterId} disabled={!deviceId || equipmentParametersLoading} onChange={(event) => setParameterId(event.target.value)} required>
                      <option value="">{equipmentParametersLoading ? text.run.loadParameters : text.run.selectParameter}</option>{enabledEquipmentParameters.map((parameter) => <option value={parameter.id} key={parameter.id}>{parameter.name} ({parameter.unit})</option>)}
                    </select>
                    {deviceId && !equipmentParametersLoading && enabledEquipmentParameters.length === 0 && <span className="text-[11px] text-slate-500">{text.run.noParameters}</span>}
                  </label>
                  <label className="grid max-w-sm gap-1.5 text-xs font-medium text-slate-600">{text.run.sourceType}
                    <select className={inputClass} value={equipmentSourceType} onChange={(event) => setEquipmentSourceType(event.target.value as 'manual' | 'sensor' | 'simulated')}>
                      {Object.entries(text.run.sourceTypes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                  </label>
                </div>}
          </section>}
          {runStep === 2 && <section aria-labelledby="analysis-step-features" className="grid gap-3">
            <h3 className="text-xs font-semibold text-forest-50" id="analysis-step-features">{text.run.stepStatus(2)} · {text.run.steps[1]}</h3>
            {runSource === 'equipment'
              ? <p className="text-xs text-slate-500">{selectedEquipmentParameter ? `${selectedEquipmentParameter.name} (${selectedEquipmentParameter.unit})` : text.run.noParameters}</p>
              : <fieldset className="grid gap-2">
                  <legend className="text-xs font-semibold text-slate-700">{text.run.features}</legend>
                  {requiresSingleFeature && <p className="text-[11px] text-slate-500">{text.run.singleFeature}</p>}
                  {numericFeatures.length > 0
                    ? <div className="flex flex-wrap gap-2">{numericFeatures.map((feature) => <label className="flex min-h-9 cursor-pointer items-center gap-2 rounded-lg border border-forest-400/20 px-3 py-2 text-xs" key={feature}><input type="checkbox" checked={featureNames.includes(feature)} onChange={() => setFeatureNames((current) => current.includes(feature) ? current.filter((value) => value !== feature) : requiresSingleFeature ? [feature] : [...current, feature])} />{feature}</label>)}</div>
                    : <p className="text-[11px] text-slate-500">{selectedDataset ? text.run.noNumericFeatures : text.run.selectDatasetFeatures}</p>}
                  <p className="text-[11px] text-slate-500">{selectedDataset ? text.run.sourceRows(selectedDataset.row_count, featureNames.length) : text.run.featureChoices}</p>
                  {detectedTimeColumns.length > 0 && <label className="grid max-w-sm gap-1.5 text-xs font-medium text-slate-600">{text.run.timeColumn}
                    <select className={inputClass} value={timeColumn} onChange={(event) => setTimeColumn(event.target.value)}><option value="">{text.run.noTimeColumn}</option>{detectedTimeColumns.map((column) => <option key={column}>{column}</option>)}</select>
                  </label>}
                </fieldset>}
          </section>}
          {runStep === 3 && <section aria-labelledby="analysis-step-method" className="grid gap-4">
            <h3 className="text-xs font-semibold text-forest-50" id="analysis-step-method">{text.run.stepStatus(3)} · {text.run.steps[2]}</h3>
            {runSource === 'equipment'
              ? <label className="grid max-w-xl gap-1.5 text-xs font-medium text-slate-600">{text.run.method}
                  <select className={inputClass} value={equipmentAlgorithm} onChange={(event) => setEquipmentAlgorithm(event.target.value)} required>
                    {detectors.map((detector) => <option value={detector.algorithm} key={detector.algorithm}>{detector.display_name}</option>)}
                  </select>
                </label>
              : <label className="grid max-w-xl gap-1.5 text-xs font-medium text-slate-600">{text.run.model}
                  <select className={inputClass} value={modelId} onChange={(event) => setModelId(event.target.value)} required>
                    <option value="">{text.run.selectModel}</option>{models.filter((model) => model.is_active !== false).map((model) => <option value={model.id} key={model.id}>{model.name} · {model.algorithm.replaceAll('_', ' ')}</option>)}
                  </select>
                </label>}
            <div className="grid gap-2 md:grid-cols-2">
              {detectors.map((detector) => <article className={`rounded-lg border p-3 ${detector.algorithm === (runSource === 'equipment' ? equipmentAlgorithm : selectedModel?.algorithm) ? 'border-forest-400/50 bg-forest-700/15' : 'border-forest-400/15'}`} key={detector.algorithm}>
                <h4 className="text-xs font-semibold text-forest-50">{detector.display_name}</h4>
                <p className="mt-1 text-[11px] text-forest-200">{text.run.methodDescriptions[detector.algorithm] ?? detector.algorithm.replaceAll('_', ' ')}</p>
                <p className="mt-1 text-[10px] text-forest-200">{runSource === 'equipment' || detector.execution_mode === 'walk_forward' ? text.run.walkForward : text.run.batch}{detector.minimum_history > 0 ? ` · ${text.run.minimumHistory(detector.minimum_history)}` : ''}</p>
              </article>)}
            </div>
            <p className="text-[11px] text-slate-500">{text.run.statisticalEvidence}</p>
          </section>}
          {runStep === 4 && <section aria-labelledby="analysis-step-parameters" className="grid gap-4">
            <h3 className="text-xs font-semibold text-forest-50" id="analysis-step-parameters">{text.run.stepStatus(4)} · {text.run.steps[3]}</h3>
            {runSource === 'dataset'
              ? <div className="grid gap-3 sm:grid-cols-2">
                  <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.run.missingValues}
                    <select className={inputClass} name="missing_values_strategy"><option value="median">{text.run.median}</option><option value="mean">{text.run.mean}</option><option value="drop">{text.run.dropIncomplete}</option></select>
                  </label>
                  <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.run.scaling}
                    <select className={inputClass} name="scaling"><option value="standard">{text.run.standardize}</option><option value="none">{text.run.none}</option></select>
                  </label>
                </div>
              : <div className="grid gap-4">
                  <p className="text-[11px] text-slate-500">{text.run.defaults}</p>
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.run.trainingWindow}<input className={inputClass} name="training_window" type="number" defaultValue={Math.max(100, equipmentDetector?.minimum_history ?? 100)} min={equipmentDetector?.minimum_history ?? 8} max={5000} required /></label>
                    <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.run.refitEvery}<input className={inputClass} name="refit_every" type="number" defaultValue={25} min={1} max={5000} required /></label>
                    <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.run.maxReadings}<input className={inputClass} name="max_readings" type="number" defaultValue={500} min={equipmentDetector?.minimum_history ?? 1} max={5000} required /></label>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {equipmentDetector?.parameters.map((parameter) => <label className="grid gap-1.5 text-xs font-medium capitalize text-slate-600" key={`${equipmentAlgorithm}-${parameter.name}`}>
                      {parameter.name.replaceAll('_', ' ')} <span className="text-[10px] font-normal text-slate-500">{text.run.defaults} {String(parameter.default)}</span>
                      {parameter.type === 'json'
                        ? <textarea className={`${inputClass} min-h-24 font-mono text-[11px]`} name={`detector_${parameter.name}`} defaultValue={JSON.stringify(parameter.default, null, 2)} required spellCheck={false} />
                        : parameter.type === 'boolean'
                          ? <select className={inputClass} name={`detector_${parameter.name}`} defaultValue={String(parameter.default)}><option value="true">{text.common.true}</option><option value="false">{text.common.false}</option></select>
                          : parameter.choices && parameter.type !== 'string_or_number'
                            ? <select className={inputClass} name={`detector_${parameter.name}`} defaultValue={String(parameter.default)}>{parameter.choices.map((choice) => <option key={String(choice)} value={choice}>{String(choice)}</option>)}</select>
                            : <input className={inputClass} name={`detector_${parameter.name}`} type={parameter.type === 'integer' || parameter.type === 'number' ? 'number' : 'text'} defaultValue={parameter.type === 'integer_list' && Array.isArray(parameter.default) ? parameter.default.join(',') : String(parameter.default)} min={parameter.minimum} max={parameter.maximum} step={parameter.type === 'integer' ? 1 : 'any'} placeholder={parameter.type === 'integer_list' ? text.models.integerList : undefined} required />}
                    </label>)}
                  </div>
                </div>}
            <div className="rounded-lg border border-forest-400/20 bg-forest-950/20 p-3 text-xs">
              <h4 className="font-semibold text-forest-50">{text.run.review}</h4>
              <p className="mt-1 text-forest-200">{runSource === 'dataset'
                ? `${selectedDataset?.name ?? text.run.selectDataset} · ${selectedModel?.name ?? text.run.selectModel} · ${featureNames.join(', ') || text.run.features}`
                : `${selectedDevice?.name ?? text.run.selectDevice} · ${selectedEquipmentParameter?.name ?? text.run.selectParameter} · ${equipmentDetector?.display_name ?? text.run.method}`}</p>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <button className={buttonClass} type="button" onClick={() => setRunStep(3)}>{text.run.back}</button>
              <button className={buttonClass} type="submit" disabled={isSubmitting || !canSubmitRun}>{isSubmitting ? text.run.submitting : text.run.queue}</button>
            </div>
          </section>}
          {runStep < 4 && <div className="flex justify-end">
            <button className={buttonClass} type="button" disabled={!canAdvanceRunStep || (runSource === 'equipment' && equipmentParametersLoading)} onClick={() => setRunStep((step) => Math.min(4, step + 1))}>{text.run.next}</button>
          </div>}
        </form>
        {currentSubmittedAnalysis && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-forest-400/20 bg-forest-950/20 p-3 text-xs" role="status">
          <div><p className="font-semibold text-forest-50">{text.run.queued} · #{currentSubmittedAnalysis.id}</p><p className="mt-1 text-forest-200">{currentSubmittedAnalysis.status}</p></div>
          <button className={buttonClass} type="button" onClick={() => void inspect(currentSubmittedAnalysis)}>{text.run.viewResult}</button>
        </div>}
      </section>}
      {canEdit && <section aria-labelledby="analysis-tab-models" className="space-y-4" hidden={activeTab !== 'models'} id="analysis-panel-models" role="tabpanel">
      <details className={`${panelClass} green-panel`}>
        <summary className="cursor-pointer text-sm font-semibold text-slate-900">{text.models.create}</summary>
        <form className="mt-4 grid gap-4" onSubmit={(event) => void saveModel(event)}>
          <fieldset className="grid gap-3 border-b border-forest-400/20 pb-4">
            <legend className="text-xs font-semibold text-forest-50">{text.models.basics}</legend>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              <Field label={text.models.name} name="name" required />
              <Field label={text.models.description} name="description" />
              <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.models.algorithm}<select className={inputClass} name="algorithm" value={selectedAlgorithm} onChange={(event) => setSelectedAlgorithm(event.target.value)} required>{detectors.map((detector) => <option value={detector.algorithm} key={detector.algorithm}>{detector.display_name}</option>)}</select></label>
            </div>
          </fieldset>
          <fieldset className="grid gap-3 border-b border-forest-400/20 pb-4">
            <legend className="text-xs font-semibold text-forest-50">{text.models.detectorParameters}</legend>
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
          {selectedDetector && <p className="text-[11px] text-slate-500">{selectedDetector.execution_mode === 'walk_forward' ? text.models.minimumHistory(selectedDetector.minimum_history) : text.models.batchBehavior} {text.models.statistics}</p>}
          </fieldset>
          <fieldset className="grid gap-3">
            <legend className="text-xs font-semibold text-forest-50">{text.models.preprocessing}</legend>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {selectedAlgorithm === 'isolation_forest' && <>
            <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.models.maximumSamples}<select className={inputClass} name="max_samples"><option value="auto">{text.models.auto}</option><option value="256">256</option><option value="512">512</option></select></label>
            <Field label={text.models.maxFeatures} name="max_features" type="number" defaultValue={1} min={0.01} max={1} step={0.05} required />
            <label className="flex items-center gap-2 text-xs text-slate-600"><input name="bootstrap" type="checkbox" /> {text.models.bootstrap}</label>
          </>}
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.models.missingValues}<select className={inputClass} name="missing_values_strategy"><option value="median">{text.models.median}</option><option value="mean">{text.models.mean}</option><option value="drop">{text.models.drop}</option></select></label>
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">{text.models.scaling}<select className={inputClass} name="scaling"><option value="standard">{text.models.standardize}</option><option value="none">{text.run.none}</option></select></label>
            </div>
            <div className="flex justify-end"><button className={buttonClass} type="submit">{text.models.save}</button></div>
          </fieldset>
        </form>
      </details>
      <section className={`${panelClass} green-panel space-y-4`}>
        <div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">{text.models.title}</h2><p className="mt-1 text-xs text-slate-500">{text.models.intro}</p></div><span className="text-xs tabular-nums text-slate-500">{text.models.configurations(models.length)}</span></div>
        {models.some((model) => model.source === 'tuned_from_development_evidence') && <p className="rounded-lg border border-forest-400/20 bg-forest-950/20 p-3 text-[11px] text-forest-100">{Array.from(new Set(models.filter((model) => model.source === 'tuned_from_development_evidence').map((model) => model.tuning_limitations).filter((limitation): limitation is string => Boolean(limitation)))).join(' ') || text.models.limitation}</p>}
        {models.filter((model) => !model.is_superseded).length > 0
          ? <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{models.filter((model) => !model.is_superseded).map(modelCard)}</div>
          : <EmptyPanel title={text.models.emptyTitle} body={text.models.emptyBody} />}
        {models.some((model) => model.is_superseded) && <details className="rounded-lg border border-forest-400/20 p-3">
          <summary className="cursor-pointer text-xs font-semibold text-forest-100">{text.models.superseded} ({models.filter((model) => model.is_superseded).length})</summary>
          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{models.filter((model) => model.is_superseded).map(modelCard)}</div>
        </details>}
      </section>
      </section>}
      <section aria-labelledby="analysis-tab-history" className={`${panelClass} green-panel space-y-5 rounded-2xl border-forest-400/25 p-5 shadow-md sm:p-6`} hidden={activeTab !== 'history'} id="analysis-panel-history" role="tabpanel">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-forest-400/20 pb-4">
          <div className="min-w-0 flex-1"><h2 className="text-base font-bold text-slate-900">{text.history.title}</h2><p className="mt-1 max-w-4xl text-xs leading-5 text-forest-200">{text.history.description}</p></div>
          <span className="shrink-0 rounded-full border border-forest-400/25 bg-forest-950/40 px-3 py-1.5 text-xs font-semibold tabular-nums text-forest-100">{text.history.matchingRuns(historyTotal)}</span>
        </div>
        <div className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-8">
          <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200 xl:col-span-2">{text.history.searchLabel}<input className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50 transition-colors duration-150 hover:border-forest-400/60 focus:border-forest-400 focus:ring-2 focus:ring-forest-400/30`} value={historySearch} onChange={(event) => { setHistorySearch(event.target.value); setHistoryOffset(0) }} placeholder={text.history.searchPlaceholder} /></label>
          <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200">{text.history.source}<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50`} value={historySource} onChange={(event) => { setHistorySource(event.target.value); setHistoryOffset(0) }}><option value="">{text.history.sourceAll}</option><option value="dataset">{text.history.sourceDataset}</option><option value="equipment">{text.history.sourceEquipment}</option></select></label>
          <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200">{text.history.algorithm}<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50`} value={historyAlgorithm} onChange={(event) => { setHistoryAlgorithm(event.target.value); setHistoryOffset(0) }}><option value="">{text.history.allAlgorithms}</option>{detectors.map((detector) => <option value={detector.algorithm} key={detector.algorithm}>{detector.display_name}</option>)}</select></label>
          <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200">{text.history.status}<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50`} value={historyStatus} onChange={(event) => { setHistoryStatus(event.target.value); setHistoryOffset(0) }}><option value="">{text.history.allStatuses}</option>{['pending', 'running', 'completed', 'failed'].map((status) => <option value={status} key={status}>{text.history.statuses[status]}</option>)}</select></label>
          <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200">{text.history.runKind}<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50`} value={historyRunKind} onChange={(event) => { setHistoryRunKind(event.target.value); setExpandLiveReadings(false); setHistoryOffset(0) }}><option value="">{text.history.allRunKinds}</option>{Object.entries(text.history.kinds).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <fieldset className="grid min-w-0 gap-1 sm:col-span-2 xl:col-span-2">
            <legend className="text-[11px] font-medium text-forest-200">{text.history.dateRange}</legend>
            <div className="grid grid-cols-2 gap-2">
              <label className="grid min-w-0 gap-1 text-[10px] text-forest-200">{text.history.from}<input aria-label={text.history.from} className={`${inputClass} h-10 min-w-0 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-2 py-2 text-[10px] text-forest-50`} type="datetime-local" value={historyStart} onChange={(event) => { setHistoryStart(event.target.value); setHistoryOffset(0) }} /></label>
              <label className="grid min-w-0 gap-1 text-[10px] text-forest-200">{text.history.to}<input aria-label={text.history.to} className={`${inputClass} h-10 min-w-0 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-2 py-2 text-[10px] text-forest-50`} type="datetime-local" value={historyEnd} onChange={(event) => { setHistoryEnd(event.target.value); setHistoryOffset(0) }} /></label>
            </div>
          </fieldset>
        </div>
        <details className="rounded-lg border border-forest-400/20 px-3 py-2">
          <summary className="cursor-pointer text-xs font-semibold text-forest-100">{text.history.moreFilters}</summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200">{text.history.dataset}<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50`} value={historyDataset} onChange={(event) => { setHistoryDataset(event.target.value); setHistoryOffset(0) }}><option value="">{text.history.allDatasets}</option>{datasets.map((dataset) => <option value={dataset.id} key={dataset.id}>{dataset.name}</option>)}</select></label>
            <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200">{text.history.device}<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50`} value={historyDevice} onChange={(event) => { setHistoryDevice(event.target.value); setHistoryOffset(0) }}><option value="">{text.history.allDevices}</option>{devices.map((device) => <option value={device.id} key={device.id}>{device.name}</option>)}</select></label>
            <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200">{text.history.sort}<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50`} value={historySort} onChange={(event) => setHistorySort(event.target.value)}>{[['created_at', text.history.executionTime], ['anomalies_found', text.history.anomalyCount], ['total_records', text.history.measurements], ['status', text.history.status]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label className="grid min-w-0 gap-1 text-[11px] font-medium text-forest-200">{text.history.order}<select className={`${inputClass} h-10 w-full rounded-lg border-forest-400/30 bg-forest-950/40 px-3 py-2 text-forest-50`} value={historyDirection} onChange={(event) => setHistoryDirection(event.target.value)}><option value="desc">{text.history.descending}</option><option value="asc">{text.history.ascending}</option></select></label>
          </div>
        </details>
        <div className="flex min-h-10 flex-wrap items-center justify-between gap-3 rounded-lg border border-forest-400/20 bg-forest-950/25 px-3 py-2">
          <label className="flex min-h-8 cursor-pointer items-center gap-2.5 text-xs font-medium text-forest-100"><input className="h-4 w-4 cursor-pointer rounded border-forest-400/50 accent-forest-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-400/40" checked={hideZeroDetections} onChange={(event) => setHideZeroDetections(event.target.checked)} type="checkbox" />{text.history.hideZero}</label>
          {hideZeroDetections && <p className="text-[10px] text-forest-200">{text.history.hideZeroPage}</p>}
        </div>
        {historyError && <div><ErrorMessage>{`${historyError.message}${historyError.status ? text.common.httpStatus(historyError.status) : ''}`}</ErrorMessage><button className="mt-2 text-xs underline" type="button" onClick={() => void loadHistory()}>{text.history.retry}</button></div>}
        {canEdit && <div className="sticky bottom-0 z-[2] flex flex-wrap items-center justify-between gap-3 rounded-lg border border-forest-400/20 bg-forest-950/95 px-4 py-3 shadow-md">
          <div><p className="text-xs text-forest-200">{text.history.compareDescription}</p><p className="mt-1 text-xs text-forest-100"><strong className="font-semibold">{text.history.selected(selectedAnalysisIds.length)}</strong><span className="ml-2 text-forest-200">{text.history.compareLimit}</span></p></div>
          <button className={`${buttonClass} min-h-10 rounded-lg transition-colors duration-150 hover:bg-forest-700 active:bg-forest-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-300/60`} disabled={!canCompareAnalyses(canEdit, selectedAnalysisIds)} onClick={() => void compare()}>{text.history.compare}</button>
        </div>}
        {shouldExcludeLiveReadings(historyRunKind, expandLiveReadings) && <p className="text-[11px] text-forest-200">{text.history.liveSummary}</p>}
        {expandLiveReadings && <div className="flex items-center justify-between gap-3 text-xs text-forest-200"><span>{text.summary.liveSummary(summary?.live_reading_24h.total ?? 0, summary?.live_reading_24h.failed ?? 0)}</span><button className="underline" type="button" onClick={() => { setHistoryRunKind(''); setExpandLiveReadings(false); setHistoryOffset(0) }}>{text.history.collapseLive}</button></div>}
        <div aria-busy={historyLoading} className="max-h-[65vh] overflow-auto rounded-xl border border-forest-400/20">
          <table className="w-full min-w-[1050px] table-fixed text-left text-xs">
            <thead className="sticky top-0 z-[1] bg-forest-950 text-[10px] uppercase tracking-wider text-forest-200"><tr>
              {[text.history.table.run, text.history.table.source, text.history.table.algorithm, text.history.table.when, text.history.table.rows, text.history.table.detections, text.history.table.status, text.history.table.duration, text.history.table.actions].map((heading, index) => <th className={`whitespace-nowrap border-b border-forest-400/25 px-3 py-3 font-semibold ${index === 0 ? 'sticky left-0 z-[2] w-32 bg-forest-950' : ''} ${[4, 5, 7].includes(index) ? 'w-28 text-right' : ''} ${index === 1 ? 'w-48' : ''} ${index === 2 ? 'w-36' : ''} ${index === 3 ? 'w-48' : ''} ${index === 6 ? 'w-32' : ''} ${index === 8 ? 'w-36' : ''}`} key={heading}>{heading}</th>)}
            </tr></thead>
            <tbody className="divide-y divide-forest-400/20">
              {shouldExcludeLiveReadings(historyRunKind, expandLiveReadings) && (!historySource || historySource === 'equipment') && !historyDataset && !historyDevice && <tr className="bg-forest-950/35">
                <td className="sticky left-0 z-[1] bg-forest-950/95 px-3 py-3 font-semibold text-forest-100">{text.history.automatic}</td>
                <td className="px-3 py-3 text-forest-200" colSpan={7}>{summary ? text.summary.liveSummary(summary.live_reading_24h.total, summary.live_reading_24h.failed) : text.common.notAvailable}</td>
                <td className="px-3 py-3 text-right"><button className="inline-flex min-h-8 items-center rounded-lg border border-forest-400/40 px-3 text-[10px] font-semibold text-forest-100 hover:bg-forest-600/20" type="button" onClick={() => { setHistoryRunKind('live_reading'); setExpandLiveReadings(true); setHistoryOffset(0) }}>{text.history.showLive}</button></td>
              </tr>}
              {historyPanelState === 'loading' && Array.from({ length: 5 }, (_, index) => <tr aria-hidden="true" key={`skeleton-${index}`}><td className="sticky left-0 bg-forest-950/90 px-3 py-4"><span className="block h-3 w-16 animate-pulse rounded bg-forest-700/40 motion-reduce:animate-none" /></td>{Array.from({ length: 8 }, (_, cell) => <td className="px-3 py-4" key={cell}><span className="block h-3 w-3/4 animate-pulse rounded bg-forest-700/30 motion-reduce:animate-none" /></td>)}</tr>)}
              {displayedHistory.map((analysis) => {
                const datasetSource = analysis.source_type === 'dataset'
                const sourceName = datasetSource ? analysis.dataset_name : analysis.device_name
                const runKind = analysis.run_kind ?? 'on_demand_analysis'
                return <tr className={`transition-colors duration-150 hover:bg-forest-600/10 ${selectedAnalysisIds.includes(analysis.id) ? 'bg-forest-600/10' : ''} ${analysis.status === 'failed' || analysis.error_message ? 'border-l-2 border-rose-400/60 bg-rose-950/15' : ''}`} key={analysis.id}>
                  <td className={`sticky left-0 z-[1] px-3 py-3 font-mono tabular-nums ${selectedAnalysisIds.includes(analysis.id) ? 'bg-forest-900' : 'bg-forest-950'}`}><label className="flex items-center gap-2">{canEdit && <input className="h-4 w-4 cursor-pointer rounded border-forest-400/50 accent-forest-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-400/40" aria-label={text.history.selectForComparison(analysis.id)} type="checkbox" checked={selectedAnalysisIds.includes(analysis.id)} onChange={() => toggleComparison(analysis.id)} />}<span>#{analysis.id}</span></label><span className="mt-1 inline-flex rounded-full border border-forest-400/20 px-1.5 py-0.5 font-sans text-[9px] text-forest-200">{text.history.kinds[runKind]}</span></td>
                  <td className="px-3 py-3"><span className="block truncate" title={sourceName ?? text.history.sourceNameFallback}>{sourceName ?? text.history.sourceNameFallback}</span><span className="mt-1 inline-flex rounded-full border border-forest-400/20 px-2 py-0.5 text-[9px] text-forest-200">{getSourceTypeLabel(getAnalysisSourceType(analysis))}</span></td>
                  <td className="truncate px-3 py-3 capitalize" title={analysis.algorithm ?? analysis.model_name ?? ''}>{(analysis.algorithm ?? analysis.model_name ?? text.common.defaultAlgorithm).replaceAll('_', ' ')}</td>
                  <td className="whitespace-nowrap px-3 py-3">{new Date(analysis.started_at ?? analysis.created_at).toLocaleString()}<small className="mt-1 block text-[10px] text-forest-200">{text.history.table.executionTypes[analysis.execution_type ?? 'manual'] ?? analysis.execution_type ?? text.history.table.executionTypes.manual}</small></td>
                  <td className="px-3 py-3 text-right tabular-nums">{analysis.total_records.toLocaleString()}</td>
                  <td className="px-3 py-3 text-right tabular-nums" title={text.history.table.uniqueTooltip}>{analysis.anomalies_found.toLocaleString()}<small className="mt-1 block text-[9px] text-forest-200">{text.history.table.flags}</small></td>
                  <td className="px-3 py-3"><span className={analysisStatusTone(analysis.status)}><StatusBadge value={analysis.status} label={text.history.statuses[analysis.status] ?? analysis.status} /></span>{analysis.error_message && <small className="mt-1 block max-w-40 whitespace-normal text-rose-200">{analysis.error_message}</small>}</td>
                  <td className="px-3 py-3 text-right tabular-nums">{analysis.duration_seconds == null ? text.common.notAvailable : `${analysis.duration_seconds.toFixed(2)} s`}</td>
                  <td className="whitespace-nowrap px-3 py-3"><button className="mr-2 inline-flex h-8 w-[4.75rem] items-center justify-center rounded-lg border border-forest-400/40 px-2 text-[10px] font-semibold text-forest-100 transition-colors duration-150 hover:bg-forest-600/20 active:bg-forest-600/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-300/60" type="button" onClick={() => void inspect(analysis)}>{text.history.table.details}</button><button className="inline-flex h-8 w-[4.75rem] items-center justify-center rounded-lg bg-forest-600 px-2 text-[10px] font-semibold text-forest-50 transition-colors duration-150 hover:bg-forest-700 active:bg-forest-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-300/60 disabled:cursor-not-allowed disabled:opacity-50" type="button" disabled={!canEdit} onClick={() => void repeat(analysis)}>{text.history.table.repeat}</button></td>
                </tr>
              })}
            </tbody>
          </table>
          {historyPanelState === 'empty' && <div className="p-5"><EmptyPanel title={hideZeroDetections && history.length ? text.history.noDetectionsOnPage : text.history.noMatching} body={text.history.emptyFilters(activeHistoryFilters)} /></div>}
        </div>
        <div className="flex items-center justify-between gap-3 text-xs text-forest-200"><span>{historyTotal ? `${historyOffset + 1}–${Math.min(historyOffset + history.length, historyTotal)} of ${historyTotal}` : text.history.noRecords}</span><div className="flex gap-2"><button className={`${buttonClass} bg-forest-950/40 text-forest-100 ring-1 ring-forest-400/30 hover:bg-forest-800`} disabled={historyOffset === 0 || historyLoading} onClick={() => setHistoryOffset(Math.max(0, historyOffset - historyPageSize))}>{text.history.previous}</button><button className={`${buttonClass} bg-forest-950/40 text-forest-100 ring-1 ring-forest-400/30 hover:bg-forest-800`} disabled={historyOffset + historyPageSize >= historyTotal || historyLoading} onClick={() => setHistoryOffset(historyOffset + historyPageSize)}>{text.history.next}</button></div></div>
      {comparison && <section className={`${panelClass} green-panel space-y-4`}>
        <div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-900">{text.comparison.title(String(comparison.id))}</h2><p className="mt-1 text-xs text-slate-500">{text.comparison.description}</p></div><button className={buttonClass} type="button" onClick={() => void onAction(() => download(`/analyses/comparisons/${String(comparison.id)}/csv`, `analysis-comparison-${String(comparison.id)}.csv`), text.comparison.exported)}>{text.comparison.export}</button></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[950px] text-left text-xs"><thead className="bg-forest-950 text-[10px] uppercase tracking-wide text-forest-200"><tr>{[text.comparison.run, text.comparison.detector, text.comparison.status, text.comparison.measurements, text.comparison.anomalyRate, text.comparison.scoreDistribution, text.comparison.runtime, text.comparison.historyStates].map((label, index) => <th className={`px-3 py-2 ${index === 0 ? 'sticky left-0 z-[2] bg-forest-950' : ''}`} key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-forest-400/20">{(comparison.runs as Record<string, unknown>[]).map((run) => {
          const distribution = run.score_distribution as Record<string, unknown>
          return <tr key={String(run.analysis_id)}><td className="sticky left-0 z-[1] bg-forest-950 px-3 py-2">#{String(run.analysis_id)}</td><td className="px-3 py-2">{String(run.algorithm)}</td><td className="px-3 py-2">{text.history.statuses[String(run.status)] ?? String(run.status)}</td><td className="px-3 py-2 tabular-nums">{Number(run.total_records).toLocaleString()}</td><td className="px-3 py-2 tabular-nums">{String(run.anomalies_found)} / {(Number(run.anomaly_rate) * 100).toFixed(2)}%</td><td className="px-3 py-2 tabular-nums">{distribution.min == null ? text.common.notAvailable : ['min', 'p25', 'median', 'p75', 'p95', 'max'].map((key) => Number(distribution[key]).toFixed(4)).join(' / ')}</td><td className="px-3 py-2 tabular-nums">{run.runtime_seconds == null ? text.common.notAvailable : `${Number(run.runtime_seconds).toFixed(2)} s`}</td><td className="px-3 py-2">{JSON.stringify(run.history_state_counts)}</td></tr>
        })}</tbody></table></div>
        <div><h3 className="text-xs font-semibold text-forest-100">{text.comparison.agreement}</h3><div className="mt-2 overflow-x-auto"><table className="w-full min-w-[650px] text-left text-xs"><thead className="bg-forest-950 text-forest-200"><tr>{[text.comparison.run, text.comparison.comparedRun, text.comparison.matchedBy, text.comparison.bothFlagged, text.comparison.leftOnly, text.comparison.rightOnly].map((label, index) => <th className={`px-3 py-2 ${index === 0 ? 'sticky left-0 z-[2] bg-forest-950' : ''}`} key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-forest-400/20">{(comparison.agreement_matrix as Record<string, unknown>[]).map((pair) => <tr key={`${String(pair.analysis_id)}-${String(pair.other_analysis_id)}`}><td className="sticky left-0 z-[1] bg-forest-950 px-3 py-2">#{String(pair.analysis_id)}</td><td className="px-3 py-2">#{String(pair.other_analysis_id)}</td><td className="px-3 py-2">{String(pair.matched_by)}</td><td className="px-3 py-2">{String(pair.both)}</td><td className="px-3 py-2">{String(pair.left_only)}</td><td className="px-3 py-2">{String(pair.right_only)}</td></tr>)}</tbody></table></div></div>
      </section>}
      {analysisResultPanel()}
      </section>
    </>
  )
}
