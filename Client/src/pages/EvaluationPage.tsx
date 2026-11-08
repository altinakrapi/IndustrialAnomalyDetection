/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ApiError, download, request } from '../api'
import { buttonClass, ErrorMessage, panelClass } from '../components/ui'
import { formatAggregateMetric, perRunDetectionDelay, perRunMetric } from '../evaluationPayload'
import '../evaluationPayload.fixture'
import './EvaluationPage.css'
import {
  defaultEvaluationId,
  defaultValidationId,
  heatmapValue,
  isEvaluationEditor,
  simulatorSummary,
  splitValidationAttempts,
  validationSummary,
} from './evaluationFlow'
import { evaluationStrings as text } from './evaluationStrings'

type Action = (action: () => Promise<void>, success: string) => Promise<void>
type Evaluation = {
  id: number
  source_type: string
  purpose: string
  configuration: Record<string, unknown>
  limitations: string
  result: Record<string, any>
  created_at: string
}
type Validation = {
  id: number
  status: string
  source_type: string
  configuration: Record<string, unknown>
  limitations: string
  error_message?: string | null
  result?: Record<string, any>
  created_at: string
}
type TuningRun = {
  id: number
  objective: string
  false_alarm_cap: number
  data_seeds: number[]
  ground_truth_run_ids: number[]
  runtime_seconds: number
  candidates: { items?: Array<Record<string, unknown>> }
  result: { chosen?: Record<string, unknown>; selected?: Record<string, unknown> }
  detector_comparison?: Array<{
    algorithm: string
    runs: Array<{
      run_id: number
      seed: number
      default_f1: number
      default_false_alarm_rate: number
      tuned_f1: number
      tuned_false_alarm_rate: number
    }>
    decision: string
    reason: string
  }>
  limitations: string
  presentation?: {
    superseded_by_run_7?: boolean
    metric_version?: string
    scored_positions?: number
    supersession_reason?: string
  }
}
type TunedConfig = {
  id: number
  name: string
  algorithm: string
  parameters: Record<string, unknown>
  development_seeds: number[]
  limitations: string
  evidence?: Record<string, unknown>
  is_superseded: boolean
  superseded_reason?: string | null
}

const metricNames = ['precision', 'recall', 'f1', 'roc_auc', 'pr_auc', 'false_alarm_rate']
const primaryMetrics = ['precision', 'recall', 'f1', 'false_alarm_rate', 'detection_delay']
const metricLabels: Record<string, string> = {
  precision: text.precision,
  recall: text.recall,
  f1: text.f1,
  false_alarm_rate: text.falseAlarmRate,
  detection_delay: text.detectionDelay,
  roc_auc: text.rocAuc,
  pr_auc: text.prAuc,
}
const metricTooltips: Record<string, string> = {
  precision: text.tooltips.precision,
  recall: text.tooltips.recall,
  f1: text.tooltips.f1,
  false_alarm_rate: text.tooltips.falseAlarmRate,
  detection_delay: text.tooltips.detectionDelay,
  roc_auc: text.tooltips.rocAuc,
  pr_auc: text.tooltips.prAuc,
}
const metricHigherIsBetter: Record<string, boolean> = {
  precision: true,
  recall: true,
  f1: true,
  roc_auc: true,
  pr_auc: true,
  false_alarm_rate: false,
  detection_delay: false,
}
const number = (value: unknown, digits = 3) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : text.notStored
}
const statusError = (error: unknown, fallback: string) =>
  `${error instanceof Error ? error.message : fallback}${error instanceof ApiError ? text.httpStatus(error.status) : ''}`
const inputClass = 'h-10 min-w-0 w-full rounded-lg border border-slate-300 bg-white px-3 text-xs text-slate-800 outline-none focus:border-forest-500 focus:ring-2 focus:ring-forest-500'
const labelClass = 'grid min-w-0 gap-2 text-xs font-semibold text-slate-700'
const secondaryButtonClass = 'inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-transparent px-4 py-2 text-xs font-semibold text-slate-700 transition-colors duration-150 hover:bg-slate-100 dark:border-forest-700 dark:text-forest-200 dark:hover:bg-forest-900 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500'
const tableClass = 'w-full min-w-[860px] border-collapse text-left text-xs'
const headerCellClass = 'whitespace-nowrap border-b border-slate-200 bg-slate-50 px-3 py-3 font-semibold text-slate-700'
const cellClass = 'border-b border-slate-100 px-3 py-3 align-top text-slate-700'
const stickyHeaderClass = `${headerCellClass} sticky left-0 z-20`
const stickyCellClass = `${cellClass} sticky left-0 z-10 bg-white font-semibold`
function Skeleton({ label }: { label: string }) {
  return <div aria-label={label} aria-busy="true" className="space-y-3 py-4">
    <div className="h-5 w-1/3 animate-pulse rounded bg-slate-200" />
    <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
    <div className="h-40 animate-pulse rounded-xl bg-slate-100" />
    <span className="sr-only">{label}</span>
  </div>
}

function EvaluationError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="space-y-2">
    <ErrorMessage>{message}</ErrorMessage>
    <button className={secondaryButtonClass} type="button" onClick={onRetry}>{text.retry}</button>
  </div>
}

export function EvaluationPage({ userRole, onAction }: { userRole: string; onAction: Action }) {
  const [tab, setTab] = useState<'simulator' | 'nasa'>('simulator')
  const [evaluations, setEvaluations] = useState<Evaluation[]>([])
  const [validations, setValidations] = useState<Validation[]>([])
  const [evaluationSelection, setEvaluationSelection] = useState('')
  const [validationSelection, setValidationSelection] = useState('')
  const [evaluationLoad, setEvaluationLoad] = useState<{ id: string; item?: Evaluation; error?: string } | null>(null)
  const [validationLoad, setValidationLoad] = useState<{ id: string; item?: Validation; error?: string } | null>(null)
  const [filtered, setFiltered] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [groundTruthRuns, setGroundTruthRuns] = useState<Array<{ id: number; run_id: string; seed: number }>>([])
  const [groundTruthId, setGroundTruthId] = useState('')
  const [analysisRunIds, setAnalysisRunIds] = useState('')
  const [tuningRuns, setTuningRuns] = useState<TuningRun[]>([])
  const [tunedConfigs, setTunedConfigs] = useState<TunedConfig[]>([])
  const [tuningRunIds, setTuningRunIds] = useState('')
  const [saveAlgorithm, setSaveAlgorithm] = useState('isolation_forest')
  const [saveName, setSaveName] = useState('')
  const [showSupersededConfigs, setShowSupersededConfigs] = useState(false)
  const [heatmapMetric, setHeatmapMetric] = useState<'recall' | 'f1'>('recall')
  const [retryVersion, setRetryVersion] = useState(0)
  const editor = isEvaluationEditor(userRole)
  const validationGroups = useMemo(() => splitValidationAttempts(validations), [validations])
  const evaluationId = evaluationSelection || String(defaultEvaluationId(evaluations) ?? '')
  const validationId = validationSelection || String(defaultValidationId(validationGroups.completed) ?? '')
  const evaluationState = evaluationLoad?.id === evaluationId ? evaluationLoad : null
  const validationState = validationLoad?.id === validationId ? validationLoad : null
  const evaluation = evaluationState?.item ?? null
  const validation = validationState?.item ?? null
  const evaluationError = evaluationState?.error ?? ''
  const validationError = validationState?.error ?? ''
  const simulatorLoading = Boolean(evaluationId && !evaluationState)
  const nasaLoading = Boolean(validationId && !validationState)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [evaluationList, validationList] = await Promise.all([
        request<Evaluation[]>('/analyses/evaluations'),
        request<Validation[]>('/datasets/nasa-ims/evaluations'),
      ])
      setEvaluations(evaluationList)
      setValidations(validationList)
    } catch (caught) {
      setError(statusError(caught, text.loadFailure))
    } finally {
      setLoading(false)
    }
  }, [])

  const retry = useCallback(() => {
    setError('')
    setRetryVersion((version) => version + 1)
    void refresh()
  }, [refresh])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refresh())
    return () => window.cancelAnimationFrame(frame)
  }, [refresh])

  useEffect(() => {
    if (!editor) return
    let active = true
    request<Array<{ id: number; run_id: string; seed: number }>>('/analyses/evaluations/ground-truth')
      .then((runs) => { if (active) setGroundTruthRuns(runs) })
      .catch((caught) => { if (active) setError(statusError(caught, text.simulatorRunsFailure)) })
    Promise.all([
      request<TuningRun[]>('/analyses/evaluations/tuning-runs'),
      request<TunedConfig[]>('/analyses/evaluations/tuned-configs'),
    ]).then(([runs, configs]) => {
      if (!active) return
      setTuningRuns(runs)
      setTunedConfigs(configs)
    }).catch((caught) => { if (active) setError(statusError(caught, text.tuningFailure)) })
    return () => { active = false }
  }, [editor, retryVersion])

  useEffect(() => {
    if (!evaluationId) return
    let active = true
    request<Evaluation>(`/analyses/evaluations/${evaluationId}`).then((item) => {
      if (active) setEvaluationLoad({ id: evaluationId, item })
    }).catch((caught) => {
      if (active) setEvaluationLoad({ id: evaluationId, error: statusError(caught, text.evaluationFailure) })
    })
    return () => { active = false }
  }, [evaluationId, retryVersion])

  useEffect(() => {
    if (!validationId) return
    let active = true
    request<Validation>(`/datasets/nasa-ims/evaluations/${validationId}`).then((item) => {
      if (active) setValidationLoad({ id: validationId, item })
    }).catch((caught) => {
      if (active) setValidationLoad({ id: validationId, error: statusError(caught, text.validationFailure) })
    })
    return () => { active = false }
  }, [validationId, retryVersion])

  const exportSelected = async (kind: 'csv' | 'pdf') => {
    await onAction(async () => {
      if (tab === 'simulator' && evaluationId) {
        await download(`/analyses/evaluations/${evaluationId}/${kind}`, `simulator-evaluation-${evaluationId}.${kind}`)
      } else if (tab === 'nasa' && validationId && validation?.status === 'completed') {
        await download(`/datasets/nasa-ims/evaluations/${validationId}/${kind}`, `nasa-validation-${validationId}.${kind}`)
      }
    }, kind === 'csv' ? text.csvExported : text.pdfExported)
  }

  const createEvaluation = async () => {
    const runIds = analysisRunIds.split(',').map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0)
    if (!groundTruthId || !runIds.length) {
      setError(text.invalidAnalysisIds)
      return
    }
    await onAction(async () => {
      const created = await request<Evaluation>('/analyses/evaluations', {
        method: 'POST',
        body: JSON.stringify({ runs: [{ ground_truth_run_id: Number(groundTruthId), analysis_run_ids: runIds }], tolerance_readings: 5 }),
      })
      setEvaluations((items) => [created, ...items])
      setEvaluationSelection(String(created.id))
      setError('')
    }, text.created)
  }

  const runTuning = async () => {
    const ids = tuningRunIds.split(',').map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0)
    if (!ids.length) {
      setError(text.invalidTuningIds)
      return
    }
    await onAction(async () => {
      const created = await request<TuningRun>('/analyses/evaluations/tuning-runs', {
        method: 'POST',
        body: JSON.stringify({ ground_truth_run_ids: ids, false_alarm_cap: 0.05 }),
      })
      setTuningRuns((items) => [created, ...items])
      setError('')
    }, text.tuned)
  }

  const saveTunedConfig = async () => {
    const latestRun = tuningRuns[0]
    if (!latestRun || !saveName.trim()) {
      setError(text.missingCandidate)
      return
    }
    await onAction(async () => {
      await request('/analyses/evaluations/tuned-configs', {
        method: 'POST',
        body: JSON.stringify({
          tuning_run_id: latestRun.id,
          algorithm: saveAlgorithm,
          name: saveName.trim(),
        }),
      })
      setTunedConfigs(await request<TunedConfig[]>('/analyses/evaluations/tuned-configs'))
      setSaveName('')
      setError('')
    }, text.saved)
  }

  const detectorRows = useMemo(() => {
    const result = evaluation?.result?.detectors ?? {}
    return Object.entries(result).flatMap(([detector, value]: [string, any]) =>
      (value.runs ?? []).map((row: any) => ({
        detector,
        ...row,
        metrics: row.metrics ?? {},
      })))
  }, [evaluation])

  const nasaRows = useMemo(() => {
    const detectors = validation?.result?.detectors ?? {}
    return Object.entries(detectors).flatMap(([detector, value]: [string, any]) =>
      Object.entries(value.bearings ?? {}).map(([bearing, row]: [string, any]) => ({
        detector,
        bearing,
        ...row,
        attributable: value.attributable_per_bearing !== false,
      })))
  }, [validation])

  const completedEvaluationList = evaluations
  const selectedEvaluation = evaluation ?? evaluations.find((item) => String(item.id) === evaluationId) ?? null
  const selectedValidation = validation ?? validationGroups.completed.find((item) => String(item.id) === validationId) ?? null
  const selectedDevelopment = selectedEvaluation?.purpose === 'development'
  const facts = validation?.result?.verified_bearing_facts ?? []
  const failedFact = facts.find((fact: any) => fact.is_failed) as any
  const failedBearing = typeof failedFact?.bearing_number === 'number' ? failedFact.bearing_number : null
  const sourceDocument = typeof failedFact?.source_document === 'string' ? failedFact.source_document : text.sourceDocument
  const summaryLines = simulatorSummary(evaluation?.result?.detectors ?? {})
  const validationLines = validationSummary(validation?.result?.detectors ?? {}, failedBearing, filtered)
  const activeConfigs = tunedConfigs.filter((config) => !config.is_superseded)
  const supersededConfigs = tunedConfigs.filter((config) => config.is_superseded)
  const hiddenConfigs = supersededConfigs
  const hiddenTuningRuns = tuningRuns.filter((run) => run.presentation?.superseded_by_run_7)
  const visibleTuningRuns = tuningRuns.filter((run) => !run.presentation?.superseded_by_run_7)
  const activeNasaDetector = Object.keys(validation?.result?.detectors ?? {}).find((name) =>
    validation?.result?.detectors?.[name]?.attributable_per_bearing !== false,
  )
  const ensembleEvidence = validation?.result?.detectors?.ensemble?.ensemble_member_configuration

  return (
    <main className="evaluation-page space-y-6">
      <div className="space-y-3 pt-4">
        <span className="text-[10px] font-bold uppercase tracking-[0.17em] text-forest-600 dark:text-forest-200">{text.workspace}</span>
        <h1 className="text-3xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">{text.title}</h1>
        <p className="max-w-3xl text-sm leading-6 text-slate-600 dark:text-slate-300">{text.intro}</p>
      </div>
      {error && <EvaluationError message={error} onRetry={retry} />}
      <section className={`${panelClass} space-y-6 p-4 sm:p-6 lg:p-8`}>
        <details className="rounded-xl border border-slate-200 p-4">
          <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 rounded-lg text-sm font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 [&::-webkit-details-marker]:hidden">
            <span aria-hidden="true">▾</span>{text.howToRead}
          </summary>
          <div className="mt-4 grid gap-4 text-xs leading-5 text-slate-700 sm:grid-cols-2">
            {text.metricGuide.map(([label, description]) => <p key={label}><strong>{label}:</strong> {description}</p>)}
            <p className="sm:col-span-2">{text.nasaNoAccuracy}</p>
            <p className="sm:col-span-2">{text.purposeMeaning}</p>
          </div>
        </details>

        <div role="tablist" aria-label={text.title} className="flex w-full gap-2 overflow-x-auto border-b border-slate-200">
          <button type="button" role="tab" id="simulator-tab" aria-controls="simulator-panel" aria-selected={tab === 'simulator'} className={`min-h-10 shrink-0 rounded-t-lg px-4 py-2 text-xs font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 ${tab === 'simulator' ? 'border-b-2 border-forest-700 text-forest-800' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-800'}`} onClick={() => setTab('simulator')}>{text.simulatorTab}</button>
          <button type="button" role="tab" id="nasa-tab" aria-controls="nasa-panel" aria-selected={tab === 'nasa'} className={`min-h-10 shrink-0 rounded-t-lg px-4 py-2 text-xs font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 ${tab === 'nasa' ? 'border-b-2 border-forest-700 text-forest-800' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-800'}`} onClick={() => setTab('nasa')}>{text.nasaTab}</button>
        </div>

        {tab === 'simulator' ? (
          <div id="simulator-panel" role="tabpanel" aria-labelledby="simulator-tab" className="space-y-6">
            <div className="flex flex-col gap-3 border-b border-slate-200 pb-5 sm:flex-row sm:items-end">
              <label className={`${labelClass} sm:min-w-72 sm:flex-1`}>{text.evaluationSelector}
                <select className={inputClass} value={evaluationId} onChange={(event) => setEvaluationSelection(event.target.value)}>
                  {!completedEvaluationList.length && <option value="">{text.noEvaluations}</option>}
                  {completedEvaluationList.map((item) => <option key={item.id} value={item.id}>{text.evaluationOption(item.id, item.purpose, item.source_type)}</option>)}
                </select>
              </label>
              <div className="grid gap-2 sm:flex">
                <button className={`${secondaryButtonClass} w-full sm:w-32`} type="button" disabled={!evaluationId || loading} onClick={() => void exportSelected('csv')}>{text.exportCsv}</button>
                <button className={`${buttonClass} h-10 min-h-10 w-full rounded-lg px-4 py-2 text-xs sm:w-32`} type="button" disabled={!evaluationId || loading} onClick={() => void exportSelected('pdf')}>{text.exportPdf}</button>
              </div>
            </div>

            {!completedEvaluationList.length && <p className="text-xs leading-5 text-slate-600 dark:text-forest-200">{editor ? text.noEvaluationEditor : text.noEvaluationReadOnly}</p>}
            {selectedDevelopment && <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs font-semibold text-amber-900">{text.developmentWarning}</p>}
            {loading || simulatorLoading ? <Skeleton label={text.loading} /> : evaluationError ? <EvaluationError message={evaluationError} onRetry={retry} /> : evaluation ? (
              <>
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(16rem,0.7fr)]">
                  <article className={`${panelClass} p-4 sm:p-5`}>
                    <h2 className="text-sm font-semibold text-slate-900">{text.summary}</h2>
                    <ul className="mt-3 space-y-2 text-xs leading-5 text-slate-700">{summaryLines.map((line) => <li key={line}>{line}</li>)}</ul>
                    <p className="mt-3 border-t border-slate-200 pt-3 text-xs leading-5 text-slate-600">{text.limitsSentence}</p>
                  </article>
                  <article className="rounded-xl border border-amber-300 bg-amber-50 p-4 sm:p-5">
                    <h2 className="text-sm font-semibold text-amber-950">{text.limitsTitle}</h2>
                    <ul className="mt-3 list-disc space-y-2 pl-5 text-xs leading-5 text-amber-950">
                      <li>{text.limits.seeds}</li>
                      <li>{text.limits.readings}</li>
                      <li>{text.limits.simulated}</li>
                      <li>{text.limits.tolerance(Number(evaluation.result?.tolerance_readings ?? 5))}</li>
                    </ul>
                  </article>
                </div>
                <div className="flex flex-wrap items-center gap-3 text-xs">
                  <span className="rounded-full bg-violet-100 px-3 py-1 font-semibold text-violet-900">{evaluation.source_type}</span>
                  <span className="text-slate-700">{text.purpose}: {evaluation.purpose}</span>
                  <span className="text-slate-700">{text.tolerance}: {number(evaluation.result?.tolerance_readings, 0)} {text.readings}</span>
                </div>
                <AggregateMetricTables detectors={evaluation.result?.detectors ?? {}} />
                <FaultHeatmap detectors={evaluation.result?.detectors ?? {}} metric={heatmapMetric} onMetricChange={setHeatmapMetric} />
                <SimulatorCharts detectors={evaluation.result?.detectors ?? {}} />
                <details className="rounded-xl border border-slate-200 p-4">
                  <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 rounded-lg text-sm font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 [&::-webkit-details-marker]:hidden"><span aria-hidden="true">▾</span>{text.perRunMetrics}</summary>
                  {detectorRows.length ? <div className="mt-4 overflow-x-auto rounded-lg">
                    <table className={tableClass}>
                      <caption className="sr-only">{text.perRunMetrics}</caption>
                      <thead><tr><th className={stickyHeaderClass}>{text.detector}</th><th className={headerCellClass}>{text.mode}</th><th className={headerCellClass}>{text.run}</th>{metricNames.map((metric) => <th className={`${headerCellClass} text-right`} key={metric} title={metricTooltips[metric]}>{metricLabels[metric]}</th>)}<th className={`${headerCellClass} text-right`} title={metricTooltips.detection_delay}>{text.detectionDelay}</th></tr></thead>
                      <tbody>{detectorRows.map((row, index) => <tr key={`${row.detector}-${row.run_id ?? index}`}>
                        <td className={stickyCellClass}>{row.detector}</td>
                        <td className={cellClass}>{row.mode ?? evaluation.result.detectors[row.detector]?.mode_by_run?.[String(row.run_id ?? '')] ?? text.notStored}</td>
                        <td className={cellClass}>{row.run_id ?? row.run ?? index + 1}</td>
                        {metricNames.map((metric) => <td className={`${cellClass} text-right tabular-nums`} key={metric}>{number(perRunMetric(row, metric))}</td>)}
                        <td className={`${cellClass} text-right tabular-nums`}>{number(perRunDetectionDelay(row))}</td>
                      </tr>)}</tbody>
                    </table>
                  </div> : <p className="mt-3 text-xs text-slate-600">{text.noPerRunMetrics}</p>}
                </details>
                <details className="rounded-xl border border-slate-200 p-4">
                  <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 rounded-lg text-sm font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 [&::-webkit-details-marker]:hidden"><span aria-hidden="true">▾</span>{text.ruleEvidence}</summary>
                  <SimulatorEvidence detectors={evaluation.result?.detectors ?? {}} />
                </details>
              </>
            ) : <p className="rounded-xl border border-slate-200 p-5 text-xs leading-5 text-slate-700">{text.emptyMetrics}</p>}

            {editor && <details className="rounded-xl border border-slate-200 p-4">
              <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 rounded-lg px-2 text-sm font-semibold text-slate-800 transition-colors duration-150 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 [&::-webkit-details-marker]:hidden"><span aria-hidden="true">▾</span>{text.createEvaluation}</summary>
              <div className="mt-4 grid items-end gap-4 sm:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                <label className={labelClass}>{text.groundTruthRun}<select className={inputClass} value={groundTruthId} onChange={(event) => setGroundTruthId(event.target.value)}>
                  <option value="">{text.selectRun}</option>                  {groundTruthRuns.map((run) => <option key={run.id} value={run.id}>{text.runWithSeed(run.run_id, run.seed)}</option>)}
                </select></label>
                <label className={labelClass}>{text.analysisRunIds}<input className={inputClass} value={analysisRunIds} onChange={(event) => setAnalysisRunIds(event.target.value)} placeholder={text.analysisRunPlaceholder} /></label>
                <button className={`${buttonClass} h-10 min-h-10 w-full rounded-lg px-4 py-2 text-xs xl:w-auto`} type="button" onClick={() => void createEvaluation()}>{text.create}</button>
              </div>
            </details>}

            {editor && <details className="rounded-xl border border-slate-200 p-4">
              <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 rounded-lg px-2 text-sm font-semibold text-slate-800 transition-colors duration-150 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 [&::-webkit-details-marker]:hidden"><span aria-hidden="true">▾</span>{text.developmentTuning}</summary>
              <div className="mt-4 space-y-5">
                <div className="grid items-end gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
                  <label className={labelClass}>{text.developmentRunIds}<input className={inputClass} value={tuningRunIds} onChange={(event) => setTuningRunIds(event.target.value)} placeholder={text.developmentRunPlaceholder} /></label>
                  <button className={`${buttonClass} h-10 min-h-10 w-full rounded-lg px-4 py-2 text-xs sm:w-auto`} type="button" onClick={() => void runTuning()}>{text.runTuning}</button>
                </div>
                <div className="grid items-end gap-4 sm:grid-cols-2 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.5fr)_auto]">
                  <label className={labelClass}>{text.algorithm}<select className={inputClass} value={saveAlgorithm} onChange={(event) => setSaveAlgorithm(event.target.value)}>
                    {['z_score', 'ewma', 'cusum'].map((algorithm) => <option key={algorithm} value={algorithm}>{algorithm}</option>)}
                  </select></label>
                  <label className={labelClass}>{text.configName}<input className={inputClass} value={saveName} onChange={(event) => setSaveName(event.target.value)} placeholder={text.configNamePlaceholder} /></label>
                  <button className={`${buttonClass} h-10 min-h-10 w-full rounded-lg px-4 py-2 text-xs xl:w-auto`} type="button" onClick={() => void saveTunedConfig()}>{text.saveCandidate}</button>
                </div>
                <p className="max-w-3xl text-xs leading-5 text-slate-600">{text.tuningHelper}</p>
                <TuningEvidence
                  activeConfigs={activeConfigs}
                  hiddenConfigs={hiddenConfigs}
                  showHidden={showSupersededConfigs}
                  onToggleHidden={() => setShowSupersededConfigs((shown) => !shown)}
                  visibleRuns={visibleTuningRuns}
                  hiddenRuns={hiddenTuningRuns}
                />
              </div>
            </details>}
          </div>
        ) : (
          <div id="nasa-panel" role="tabpanel" aria-labelledby="nasa-tab" className="space-y-6">
            <p className="max-w-3xl text-xs leading-5 text-slate-700">{text.nasaTabSummary}</p>
            <div className="flex flex-col gap-3 border-b border-slate-200 pb-5 sm:flex-row sm:items-end">
              <label className={`${labelClass} sm:min-w-72 sm:flex-1`}>{text.validationSelector}
                <select className={inputClass} value={validationId} onChange={(event) => setValidationSelection(event.target.value)}>
                  {!validationGroups.completed.length && <option value="">{text.noValidation}</option>}
                  {validationGroups.completed.map((item) => <option key={item.id} value={item.id}>{text.validationOption(item.id, item.result?.test_set ?? text.notStored)}</option>)}
                </select>
              </label>
              <div className="grid gap-2 sm:flex">
                <button className={`${secondaryButtonClass} w-full sm:w-32`} type="button" disabled={!validationId || selectedValidation?.status !== 'completed'} onClick={() => void exportSelected('csv')}>{text.exportCsv}</button>
                <button className={`${buttonClass} h-10 min-h-10 w-full rounded-lg px-4 py-2 text-xs sm:w-32`} type="button" disabled={!validationId || selectedValidation?.status !== 'completed'} onClick={() => void exportSelected('pdf')}>{text.exportPdf}</button>
              </div>
            </div>

            {loading || nasaLoading ? <Skeleton label={text.loading} /> : validationError ? <EvaluationError message={validationError} onRetry={retry} /> : validation?.status === 'completed' ? (
              <>
                {failedFact && <p className="rounded-xl border border-slate-300 bg-slate-50 p-4 text-sm font-semibold leading-6 text-slate-900">{text.documentedFailure(failedBearing as number, String(failedFact.failure_type ?? text.notStored), sourceDocument)}</p>}
                <article className={`${panelClass} p-4 sm:p-5`}>
                  <h2 className="text-sm font-semibold text-slate-900">{text.nasaSummary}</h2>
                  <ul className="mt-3 space-y-2 text-xs leading-5 text-slate-700">{validationLines.map((line) => <li key={line}>{line}</li>)}</ul>
                </article>
                {ensembleEvidence && <article className="rounded-xl border border-slate-300 bg-slate-50 p-4 text-xs leading-5 text-slate-800">
                  <h3 className="font-semibold">{text.ensemble}</h3>
                  <p className="mt-1"><strong>{text.memberSettings}:</strong> {JSON.stringify(ensembleEvidence)}</p>
                  <p className="mt-1">{text.sharedModelNote}</p>
                </article>}
                <article className={`${panelClass} p-4 sm:p-5`}>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h2 className="text-sm font-semibold text-slate-900">{text.bearingTable}</h2>
                    <label className="inline-flex min-h-9 cursor-pointer items-center gap-3 rounded-lg px-2 text-xs font-semibold text-slate-700">
                      <span>{text.hideBurnIn}</span>
                      <input className="peer sr-only" type="checkbox" checked={filtered} onChange={(event) => setFiltered(event.target.checked)} role="switch" aria-label={text.hideBurnIn} />
                      <span aria-hidden="true" className="relative h-6 w-11 rounded-full bg-slate-300 transition-colors after:absolute after:left-1 after:top-1 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition-transform peer-checked:bg-forest-700 peer-checked:after:translate-x-5 peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-forest-500" />
                    </label>
                  </div>
                  {nasaRows.length ? <div className="mt-4 overflow-x-auto rounded-lg">
                    <table className={`${tableClass} min-w-[980px]`}>
                      <caption className="sr-only">{text.bearingTable}</caption>
                      <thead><tr><th className={stickyHeaderClass}>{text.detector}</th><th className={headerCellClass}>{text.bearing}</th><th className={headerCellClass}>{text.sustainedAlert}</th><th className={headerCellClass}>{text.firstAlert}</th><th className={`${headerCellClass} text-right`}>{text.leadHours}</th><th className={`${headerCellClass} text-right`} title={text.separationHelp}>{text.separation}</th><th className={`${headerCellClass} text-right`}>{text.healthyFalseAlarm}</th></tr></thead>
                      <tbody>{nasaRows.map((row: any) => {
                        const view = filtered ? row.snapshot_aggregation : row.snapshot_burn_in?.unfiltered_view
                        const alert = view?.sustained_alert
                        const detector = validation.result?.detectors?.[row.detector]
                        const separation = detector?.separation
                        const lead = view?.lead_time_to_test_end_hours
                        const falseAlarm = view?.healthy_period_false_alarm_proxy?.false_alarm_rate
                        return <tr key={`${row.detector}-${row.bearing}`}>
                          <td className={stickyCellClass}>{row.detector}</td>
                          <td className={cellClass}>{row.attributable ? `${text.bearing} ${row.bearing}` : text.notAttributable}</td>
                          <td className={cellClass}>{alert?.detected ? text.detected : text.notStored}</td>
                          <td className={cellClass}>{alert?.first_detected_at ?? text.notStored}</td>
                          <td className={`${cellClass} text-right tabular-nums`}>{lead === null || lead === undefined ? text.notStored : `${number(lead)} ${text.hours}`}</td>
                          <td className={`${cellClass} text-right tabular-nums`}>{!row.attributable ? text.notAttributable : Number(row.bearing) === failedBearing ? (separation?.lead_vs_earliest_healthy_hours == null ? text.notStored : `${number(separation.lead_vs_earliest_healthy_hours)} ${text.hours}`) : text.noComparison}</td>
                          <td className={`${cellClass} text-right tabular-nums`}>{falseAlarm == null ? text.notStored : number(falseAlarm)}</td>
                        </tr>
                      })}</tbody>
                    </table>
                  </div> : <p className="mt-3 text-xs text-slate-600">{text.emptyMetrics}</p>}
                  <p className="mt-3 max-w-3xl text-xs leading-5 text-slate-600">{text.separationHelp}</p>
                </article>
                <NasaChart validation={validation} filtered={filtered} initialDetector={activeNasaDetector ?? ''} />
                <article className="rounded-xl border border-amber-300 bg-amber-50 p-4 sm:p-5">
                  <h2 className="text-sm font-semibold text-amber-950">{text.limitsTitle}</h2>
                  <ul className="mt-3 list-disc space-y-2 pl-5 text-xs leading-5 text-amber-950">{text.nasaNotice.map((notice) => <li key={notice}>{notice}</li>)}</ul>
                </article>
              </>
            ) : <p className="rounded-xl border border-slate-200 p-5 text-xs leading-5 text-slate-700">{validationGroups.completed.length ? text.emptyMetrics : text.noValidation}</p>}

            {validationGroups.attempts.length > 0 && <details className="rounded-xl border border-slate-200 p-4">
              <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 rounded-lg px-2 text-sm font-semibold text-slate-800 transition-colors duration-150 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 [&::-webkit-details-marker]:hidden"><span aria-hidden="true">▾</span>{text.otherAttempts(validationGroups.attempts.length)}</summary>
              <ul className="mt-3 divide-y divide-slate-200">
                {validationGroups.attempts.map((attempt) => <li className="py-3 text-xs leading-5 text-slate-700" key={attempt.id}>
                  <strong>#{attempt.id} · {attempt.status}</strong>
                  <p className="mt-1">{text.attemptReason}: {attempt.error_message || text.noAttemptReason}</p>
                </li>)}
              </ul>
            </details>}
          </div>
        )}
      </section>
      <footer className="flex flex-wrap gap-x-1 text-xs text-slate-600 dark:text-forest-200">
        <span>{text.role}: {userRole}.</span><span>{text.exportsAudited}</span>
      </footer>
    </main>
  )
}

function AggregateMetricTables({ detectors }: { detectors: Record<string, any> }) {
  const [sortMetric, setSortMetric] = useState('f1')
  const [descending, setDescending] = useState(true)
  const rows = Object.entries(detectors).map(([name, evidence]: [string, any]) => {
    const delay = averageDetectionDelay(evidence)
    return { name, evidence, delay }
  }).sort((left, right) => {
    const a = metricMean(left, sortMetric)
    const b = metricMean(right, sortMetric)
    if (a === null) return b === null ? 0 : 1
    if (b === null) return -1
    return (a - b) * (descending ? -1 : 1)
  })

  return <article className={`${panelClass} p-4 sm:p-5`}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-sm font-semibold text-slate-900">{text.detectorComparison}</h2>
      <label className="flex min-h-9 items-center gap-2 text-xs font-semibold text-slate-700">
        <span>{text.sortBy}</span>
        <select className={`${inputClass} h-9 w-auto`} value={sortMetric} onChange={(event) => { setSortMetric(event.target.value); setDescending(metricHigherIsBetter[event.target.value]) }}>
          {[...primaryMetrics, 'roc_auc', 'pr_auc'].map((metric) => <option value={metric} key={metric}>{metricLabels[metric]}</option>)}
        </select>
        <button type="button" aria-label={descending ? text.sortDescending : text.sortAscending} className={secondaryButtonClass} onClick={() => setDescending((value) => !value)}>{descending ? '↓' : '↑'}</button>
      </label>
    </div>
    {rows.length ? <>
      <div className="mt-4 overflow-x-auto rounded-lg">
        <table className={`${tableClass} min-w-[1080px]`}>
          <caption className="sr-only">{text.detectorComparison}</caption>
          <thead><tr><th className={stickyHeaderClass}>{text.detector}</th>{primaryMetrics.map((metric) => {
            const best = bestMetric(rows, metric)
            return <th className={`${headerCellClass} text-right`} key={metric} title={`${metricTooltips[metric]} ${best === null ? '' : text.bestValue}`}>
              {metricLabels[metric]}{best !== null && <span className="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-900" title={text.bestValue}>★</span>}
            </th>
          })}</tr></thead>
          <tbody>{rows.map(({ name, evidence, delay }) => <tr key={name}>
            <td className={stickyCellClass}>{name}</td>
            {primaryMetrics.map((metric) => {
              const aggregate = metric === 'detection_delay' ? delay : evidence.aggregate?.[metric]
              const mean = metricMean({ name, evidence, delay }, metric)
              const best = bestMetric(rows, metric)
              const formatted = metric === 'detection_delay' ? formatAggregateMetric(delay) : formatAggregateMetric(aggregate)
              return <td className={`${cellClass} text-right tabular-nums`} key={metric} title={`${metricTooltips[metric]} ${text.tooltips.runCount}`}>
                {formatted}{metric === 'detection_delay' && mean !== null ? ` ${text.readings}` : ''}
                {mean !== null && mean === best && <span className="ml-1 font-bold text-forest-700" title={text.bestValue}>★</span>}
              </td>
            })}
          </tr>)}</tbody>
        </table>
      </div>
      <details className="mt-4 rounded-lg border border-slate-200 p-3">
        <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 rounded text-xs font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 [&::-webkit-details-marker]:hidden"><span aria-hidden="true">▾</span>{text.advancedMetrics}</summary>
        <div className="mt-3 overflow-x-auto">
          <table className={`${tableClass} min-w-[700px]`}>
            <thead><tr><th className={stickyHeaderClass}>{text.detector}</th>{['roc_auc', 'pr_auc'].map((metric) => {
              const best = bestMetric(rows, metric)
              return <th className={`${headerCellClass} text-right`} key={metric} title={`${metricTooltips[metric]} ${best === null ? '' : text.bestValue}`}>{metricLabels[metric]}{best !== null && <span className="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-900" title={text.bestValue}>★</span>}</th>
            })}</tr></thead>
            <tbody>{rows.map(({ name, evidence, delay }) => <tr key={name}><td className={stickyCellClass}>{name}</td>{['roc_auc', 'pr_auc'].map((metric) => {
              const mean = metricMean({ name, evidence, delay }, metric)
              const best = bestMetric(rows, metric)
              return <td className={`${cellClass} text-right tabular-nums`} key={metric}>{formatAggregateMetric(evidence.aggregate?.[metric])}{mean !== null && mean === best && <span className="ml-1 font-bold text-forest-700" title={text.bestValue}>★</span>}</td>
            })}</tr>)}</tbody>
          </table>
        </div>
      </details>
    </> : <p className="mt-3 text-xs text-slate-600">{text.noAggregate}</p>}
  </article>
}

function metricMean(row: { name?: string; evidence: any; delay: any }, metric: string): number | null {
  const value = metric === 'detection_delay' ? row.delay?.mean : row.evidence.aggregate?.[metric]?.mean
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function bestMetric(rows: Array<{ name?: string; evidence: any; delay: any }>, metric: string): number | null {
  const values = rows.map((row) => metricMean(row, metric)).filter((value): value is number => value !== null)
  if (!values.length) return null
  return metricHigherIsBetter[metric] ? Math.max(...values) : Math.min(...values)
}

function averageDetectionDelay(evidence: any) {
  const delays = (evidence.runs ?? []).map((run: any) => perRunDetectionDelay(run)).filter((value: number | null): value is number => value !== null)
  if (!delays.length) return null
  const mean = delays.reduce((sum: number, value: number) => sum + value, 0) / delays.length
  const spread = delays.length > 1
    ? Math.sqrt(delays.reduce((sum: number, value: number) => sum + ((value - mean) ** 2), 0) / delays.length)
    : null
  return { mean, spread_stddev: spread, run_count: delays.length }
}

function FaultHeatmap({ detectors, metric, onMetricChange }: { detectors: Record<string, any>; metric: 'recall' | 'f1'; onMetricChange: (metric: 'recall' | 'f1') => void }) {
  const faults = [...new Set(Object.values(detectors).flatMap((value: any) => Object.keys(value.per_fault_type_aggregate ?? {})))]
  const entries = Object.entries(detectors)
  return <article className={`${panelClass} p-4 sm:p-5`}>
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><h2 className="text-sm font-semibold text-slate-900">{text.faultHeatmap}</h2>
        <div className="mt-2 flex items-center gap-3 text-[10px] text-slate-700"><span>{text.low}</span><span className="h-3 w-36 rounded bg-gradient-to-r from-slate-100 to-forest-700" aria-hidden="true" /><span>{text.high}</span></div>
      </div>
      <label className={`${labelClass} max-w-48`}>{text.faultMetric}<select className={inputClass} value={metric} onChange={(event) => onMetricChange(event.target.value as 'recall' | 'f1')}>
        <option value="recall">{text.heatmapRecall}</option><option value="f1">{text.heatmapF1}</option>
      </select></label>
    </div>
    {faults.length ? <div className="mt-4 overflow-x-auto rounded-lg">
      <table className={`${tableClass} min-w-[640px]`}>
        <thead><tr><th className={stickyHeaderClass}>{text.detector}</th>{faults.map((fault) => <th className={`${headerCellClass} text-right`} key={fault}>{fault}</th>)}</tr></thead>
        <tbody>{entries.map(([detector, evidence]: [string, any]) => <tr key={detector}>
          <td className={stickyCellClass}>{detector}</td>
          {faults.map((fault) => {
            const value = heatmapValue(evidence.per_fault_type_aggregate?.[fault], metric)
            const intensity = value === null ? -1 : Math.max(0, Math.min(1, value))
            const cellTone = intensity < 0 ? 'bg-slate-100 text-slate-700' : intensity < 0.34 ? 'bg-forest-100 text-forest-950' : intensity < 0.67 ? 'bg-forest-300 text-forest-950' : 'bg-forest-700 text-white'
            return <td className={`${cellClass} text-right`} key={fault}><span className={`inline-flex min-h-9 min-w-16 items-center justify-end rounded px-2 font-semibold tabular-nums ${cellTone}`} title={`${metricLabels[metric]}: ${number(value)}`}>{number(value)}</span></td>
          })}
        </tr>)}</tbody>
      </table>
    </div> : <p className="mt-4 text-xs text-slate-600">{text.noFaultMetrics}</p>}
  </article>
}

function SimulatorCharts({ detectors }: { detectors: Record<string, any> }) {
  const detectorNames = Object.keys(detectors)
  const [detector, setDetector] = useState('')
  const selectedDetector = detectors[detector] ? detector : detectorNames[0] ?? ''
  const selected = detectors[selectedDetector]
  const run = selected?.runs?.[0]
  const confusion = run?.metrics?.confusion_matrix
  const delayData = Object.entries(selected?.detection_delay_aggregate ?? {}).map(([faultType, value]: [string, any]) => ({
    faultType,
    meanDelayReadings: value.delay_readings?.mean ?? null,
    meanDelaySeconds: value.delay_seconds?.mean ?? null,
  }))
  const roc = run?.metrics?.roc_curve ?? []
  const pr = run?.metrics?.precision_recall_curve ?? []
  if (!detectorNames.length) return <p className="text-xs text-slate-600">{text.noStoredData}</p>
  return <section className="space-y-4">
    <h2 className="text-sm font-semibold text-slate-900">{text.charts}</h2>
    <label className={`${labelClass} max-w-sm`}>{text.chartDetector}<select className={inputClass} value={selectedDetector} onChange={(event) => setDetector(event.target.value)}>{detectorNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
    <div className="grid gap-4 xl:grid-cols-2">
      <article className={`${panelClass} p-4 sm:p-5`}>
        <h3 className="text-sm font-semibold text-slate-900">{text.confusionMatrix}</h3>
        {confusion ? <div className="mt-4 grid max-w-md grid-cols-3 gap-2 text-center text-xs">
          <span /><strong>{text.predictedNormal}</strong><strong>{text.predictedFault}</strong>
          <strong>{text.actualNormal}</strong><span className="rounded bg-emerald-100 p-4 text-lg font-semibold text-emerald-950">{confusion.true_negative}</span><span className="rounded bg-rose-100 p-4 text-lg font-semibold text-rose-950">{confusion.false_positive}</span>
          <strong>{text.actualFault}</strong><span className="rounded bg-rose-100 p-4 text-lg font-semibold text-rose-950">{confusion.false_negative}</span><span className="rounded bg-emerald-100 p-4 text-lg font-semibold text-emerald-950">{confusion.true_positive}</span>
        </div> : <p className="mt-3 text-xs text-slate-600">{text.noStoredData}</p>}
      </article>
      <article className={`${panelClass} p-4 sm:p-5`}>
        <h3 className="text-sm font-semibold text-slate-900">{text.rocAndPr}</h3>
        {roc.length || pr.length ? <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <CurveChart title={text.rocCurve} points={roc} xField="false_positive_rate" yField="true_positive_rate" xLabel={text.falsePositiveRate} yLabel={text.truePositiveRate} />
          <CurveChart title={text.prCurve} points={pr} xField="recall" yField="precision" xLabel={text.recall} yLabel={text.precision} />
        </div> : <p className="mt-3 text-xs text-slate-600">{text.noStoredData}</p>}
      </article>
      <article className={`${panelClass} p-4 sm:p-5 xl:col-span-2`}>
        <h3 className="text-sm font-semibold text-slate-900">{text.delayByFault}</h3>
        {delayData.length ? <div className="evaluation-chart mt-3 h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={delayData} margin={{ top: 12, right: 12, bottom: 24, left: 12 }}>
              <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="faultType" /><YAxis /><Tooltip contentStyle={{ backgroundColor: 'var(--evaluation-tooltip-background)', borderColor: 'var(--evaluation-tooltip-border)', color: 'var(--evaluation-tooltip-text)' }} formatter={(value) => [typeof value === 'number' ? `${value.toFixed(3)} ${text.readings}` : String(value ?? text.notStored), text.detectionDelay]} /><Legend />
              <Bar dataKey="meanDelayReadings" name={text.detectionDelay} fill="#15803d" />
            </BarChart>
          </ResponsiveContainer>
        </div> : <p className="mt-3 text-xs text-slate-600">{text.noStoredData}</p>}
      </article>
    </div>
  </section>
}

function CurveChart({ title, points, xField, yField, xLabel, yLabel }: { title: string; points: any[]; xField: string; yField: string; xLabel: string; yLabel: string }) {
  return <div className="min-w-0">
    <h4 className="text-xs font-semibold text-slate-800">{title}</h4>
    <div className="evaluation-chart mt-2 h-64 min-w-0">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 18, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey={xField} type="number" domain={[0, 1]} tickFormatter={(value) => Number(value).toFixed(1)} /><YAxis dataKey={yField} type="number" domain={[0, 1]} tickFormatter={(value) => Number(value).toFixed(1)} /><Tooltip contentStyle={{ backgroundColor: 'var(--evaluation-tooltip-background)', borderColor: 'var(--evaluation-tooltip-border)', color: 'var(--evaluation-tooltip-text)' }} formatter={(value) => number(value)} /><Line dataKey={yField} name={yLabel} type="monotone" stroke="#15803d" dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
    <p className="text-[10px] text-slate-600">{text.xAxis(xLabel)} · {text.yAxis(yLabel)}</p>
  </div>
}

function SimulatorEvidence({ detectors }: { detectors: Record<string, any> }) {
  const detectorNames = Object.keys(detectors)
  return <div className="mt-4 grid gap-4 lg:grid-cols-2">
    {detectorNames.length ? detectorNames.map((detector) => {
      const data = detectors[detector]
      return <article className="min-w-0 rounded-xl border border-slate-200 p-4" key={detector}>
        <h3 className="text-sm font-semibold text-slate-900">{detector}</h3>
        <details className="mt-3 rounded-lg border border-slate-200 p-3">
          <summary className="flex min-h-9 cursor-pointer items-center gap-2 text-xs font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500">{text.parameters}</summary>
          <dl className="mt-2 space-y-1 break-words text-xs text-slate-700">
            {Object.entries(data.mode_by_run ?? {}).map(([run, mode]: [string, any]) => <div key={run}><dt className="inline font-semibold">{text.run} {run}: </dt><dd className="inline">{typeof mode === 'string' ? mode.replaceAll('_', '-') : JSON.stringify(mode)}</dd></div>)}
            {data.member_configuration && <div><dt className="inline font-semibold">{text.parameters}: </dt><dd className="inline">{JSON.stringify(data.member_configuration)}</dd></div>}
          </dl>
        </details>
        {Object.keys(data.rule_evidence_aggregate ?? {}).length ? <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[460px] text-left text-xs">
            <thead><tr><th className={headerCellClass}>{text.detector}</th><th className={`${headerCellClass} text-right`}>{text.episodes}</th><th className={`${headerCellClass} text-right`}>{text.detected}</th><th className={`${headerCellClass} text-right`}>{text.detectionRate}</th></tr></thead>
            <tbody>{Object.entries(data.rule_evidence_aggregate).map(([rule, metrics]: [string, any]) => <tr key={rule}><td className={cellClass}>{rule === 'stopped_reporting' ? text.stoppedReporting : rule === 'flatline' ? text.flatline : rule}</td><td className={`${cellClass} text-right`}>{number(metrics.episode_count?.mean, 0)}</td><td className={`${cellClass} text-right`}>{number(metrics.detected_count?.mean, 0)}</td><td className={`${cellClass} text-right`}>{number(metrics.detection_rate?.mean)}</td></tr>)}</tbody>
          </table>
        </div> : <p className="mt-3 text-xs text-slate-600">{text.noStoredData}</p>}
      </article>
    }) : <p className="text-xs text-slate-600">{text.noStoredData}</p>}
  </div>
}

function TuningEvidence({ activeConfigs, hiddenConfigs, showHidden, onToggleHidden, visibleRuns, hiddenRuns }: {
  activeConfigs: TunedConfig[]
  hiddenConfigs: TunedConfig[]
  showHidden: boolean
  onToggleHidden: () => void
  visibleRuns: TuningRun[]
  hiddenRuns: TuningRun[]
}) {
  const comparisons = mergeTuningComparisons(visibleRuns)
  const hiddenCount = hiddenConfigs.length + hiddenRuns.length
  return <div className="space-y-4">
    {hiddenCount > 0 && <button type="button" className={secondaryButtonClass} aria-expanded={showHidden} onClick={onToggleHidden}>{showHidden ? text.hideSuperseded : text.showSuperseded(hiddenCount)}</button>}
    <article>
      <h3 className="text-sm font-semibold text-slate-900">{text.tuningComparison}</h3>
      {comparisons.length ? <div className="mt-3 overflow-x-auto rounded-lg">
        <table className={`${tableClass} min-w-[900px]`}>
          <thead><tr><th className={stickyHeaderClass}>{text.detector}</th><th className={`${headerCellClass} text-right`}>{text.defaultMetrics}</th><th className={`${headerCellClass} text-right`}>{text.tunedMetrics}</th><th className={`${headerCellClass} text-right`}>{text.runCount}</th><th className={headerCellClass}>{text.decision}</th><th className={headerCellClass}>{text.reason}</th></tr></thead>
          <tbody>{comparisons.map((row) => {
            const values = row.runs
            const average = (key: 'default_f1' | 'default_false_alarm_rate' | 'tuned_f1' | 'tuned_false_alarm_rate') =>
              values.length ? values.reduce((sum, item) => sum + item[key], 0) / values.length : null
            return <tr key={row.algorithm}>
              <td className={stickyCellClass}>{row.algorithm}</td>
              <td className={`${cellClass} text-right tabular-nums`}>{number(average('default_f1'))} ({number(average('default_false_alarm_rate'))})</td>
              <td className={`${cellClass} text-right tabular-nums`}>{number(average('tuned_f1'))} ({number(average('tuned_false_alarm_rate'))})</td>
              <td className={`${cellClass} text-right tabular-nums`}>{values.length}</td>
              <td className={cellClass}>{row.decision}</td><td className={cellClass}>{row.reason}</td>
            </tr>
          })}</tbody>
        </table>
      </div> : <p className="mt-2 text-xs text-slate-600">{text.noTuningEvidence}</p>}
      {(activeConfigs.length > 0 || visibleRuns.length > 0) && <p className="mt-2 text-xs leading-5 text-slate-600">{text.tuningLimitations}</p>}
    </article>

    <article>
      <h3 className="text-sm font-semibold text-slate-900">{text.tunedConfigurations}</h3>
      {activeConfigs.length ? <div className="mt-3 grid gap-3 lg:grid-cols-2">
        {activeConfigs.map((config) => <ConfigurationCard config={config} key={config.id} />)}
      </div> : <p className="mt-2 text-xs text-slate-600">{text.noTuningEvidence}</p>}
      {hiddenConfigs.length > 0 && showHidden && <>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          {activeConfigs.slice(3).map((config) => <ConfigurationCard config={config} key={config.id} />)}
          {hiddenConfigs.filter((config) => config.is_superseded).map((config) => <ConfigurationCard config={config} key={config.id} superseded />)}
        </div>
      </>}
    </article>

    {visibleRuns.length > 0 && <article>
      <h3 className="text-sm font-semibold text-slate-900">{text.tuningRunHistory}</h3>
      <div className="mt-3 overflow-x-auto rounded-lg">
        <table className={`${tableClass} min-w-[780px]`}>
          <thead><tr><th className={stickyHeaderClass}>{text.run}</th><th className={headerCellClass}>{text.seeds}</th><th className={headerCellClass}>{text.tuningObjective}</th><th className={`${headerCellClass} text-right`}>{text.runtime}</th></tr></thead>
          <tbody>{visibleRuns.map((run) => {
            return <tr key={run.id}><td className={stickyCellClass}>{text.itemNumber(run.id)}</td><td className={cellClass}>{run.data_seeds.join(', ')}</td><td className={cellClass}>{run.objective}</td><td className={`${cellClass} text-right`}>{number(run.runtime_seconds, 2)} {text.seconds}</td></tr>
          })}</tbody>
        </table>
      </div>
    </article>}
    {hiddenRuns.length > 0 && showHidden && <div className="grid gap-3 lg:grid-cols-2">{hiddenRuns.map((run) => <article key={run.id} className="rounded-lg border border-slate-200 p-4 text-xs leading-5 text-slate-700"><strong>{text.itemNumber(run.id)} · {text.superseded}</strong><p>{run.presentation?.supersession_reason ?? text.supersededReasonFallback}</p></article>)}</div>}
    {activeConfigs.length === 0 && visibleRuns.length === 0 && <p className="text-xs text-slate-600">{text.noTuningEvidence}</p>}
  </div>
}

function mergeTuningComparisons(runs: TuningRun[]) {
  const merged = new Map<string, NonNullable<TuningRun['detector_comparison']>[number]>()
  for (const run of runs) {
    for (const comparison of run.detector_comparison ?? []) {
      const previous = merged.get(comparison.algorithm)
      merged.set(comparison.algorithm, {
        ...comparison,
        runs: [...(previous?.runs ?? []), ...comparison.runs],
        decision: previous?.decision ?? comparison.decision,
        reason: previous?.reason ?? comparison.reason,
      })
    }
  }
  return [...merged.values()]
}

function ConfigurationCard({ config, superseded = false }: { config: TunedConfig; superseded?: boolean }) {
  const parameters = Object.entries(config.parameters ?? {})
  return <article className="min-w-0 rounded-xl border border-slate-200 p-4 text-xs leading-5 text-slate-700">
    <h4 className="break-words font-semibold text-slate-900">{config.name}</h4>
    <p className="mt-1">{text.algorithm}: {config.algorithm} · {text.tunedFromDevelopment}</p>
    <dl className="mt-2 grid gap-x-3 gap-y-1 sm:grid-cols-2">{parameters.map(([key, value]) => <div key={key} className="break-words"><dt className="inline font-semibold">{key} = </dt><dd className="inline">{String(value)}</dd></div>)}</dl>
    <p className="mt-2">{text.seeds}: {config.development_seeds.join(', ')}</p>
    {superseded && <p className="mt-2 rounded bg-amber-50 p-2 font-semibold text-amber-950">{text.superseded}: {config.superseded_reason?.replace(/^superseded:\s*/i, '') ?? text.supersededReasonFallback}</p>}
  </article>
}

function NasaChart({ validation, filtered, initialDetector }: { validation: Validation; filtered: boolean; initialDetector: string }) {
  const [detector, setDetector] = useState(initialDetector)
  const detectors = validation.result?.detectors ?? {}
  const detectorNames = Object.keys(detectors)
  const selectedDetector = detectors[detector] ? detector : detectors[initialDetector] ? initialDetector : detectorNames[0]
  const selected = detectors[selectedDetector]
  const bearingEntries = Object.entries(selected?.bearings ?? {}) as Array<[string, any]>
  const attributable = selected?.attributable_per_bearing !== false
  const chart = useMemo(() => {
    const points = new Map<number, Record<string, number>>()
    if (!selected || !attributable) return []
    const testStart = Date.parse(validation.result?.test_start_at ?? '')
    for (const [bearing, metrics] of bearingEntries) {
      const snapshots = metrics.snapshot_aggregation?.snapshots ?? []
      const burnIn = metrics.snapshot_burn_in?.configured_snapshots ?? 0
      const visible = filtered ? snapshots.slice(burnIn) : snapshots
      for (const snapshot of visible) {
        const timestamp = Date.parse(snapshot.timestamp)
        const hour = Number.isFinite(timestamp) && Number.isFinite(testStart)
          ? (timestamp - testStart) / 3600000 : Number(snapshot.snapshot)
        const row = points.get(hour) ?? { hour }
        row[`bearing_${bearing}`] = Number(snapshot.flagged_share ?? 0)
        points.set(hour, row)
      }
    }
    return [...points.values()].sort((a, b) => a.hour - b.hour)
  }, [validation, selected, attributable, bearingEntries, filtered])

  if (!detectorNames.length) return <p className="text-xs text-slate-600">{text.noStoredData}</p>
  const testStart = Date.parse(validation.result?.test_start_at ?? '')
  const durationHours = Number.isFinite(testStart) && validation.result?.test_end_at
    ? (Date.parse(validation.result.test_end_at) - testStart) / 3600000 : 0
  const failedBearing = validation.result?.verified_bearing_facts?.find((fact: any) => fact.is_failed)?.bearing_number
  const firstBearing = Object.values(selected?.bearings ?? {})[0] as any
  const snapshots = firstBearing?.snapshot_aggregation?.snapshots ?? []
  const burnIndex = firstBearing?.snapshot_burn_in?.configured_snapshots ?? 0
  const burnInTimestamp = snapshots[burnIndex]?.timestamp
  const burnInHours = Number.isFinite(Date.parse(burnInTimestamp ?? '')) ? (Date.parse(burnInTimestamp) - testStart) / 3600000 : 0
  const colors = ['#2563eb', '#16a34a', '#dc2626', '#9333ea']

  return <article className={`${panelClass} p-4 sm:p-5`}>
    <div className="flex flex-wrap items-end justify-between gap-3">
      <h2 className="text-sm font-semibold text-slate-900">{text.chartTitle}</h2>
      <label className={`${labelClass} min-w-48`}>{text.detector}<select className={inputClass} value={selectedDetector} onChange={(event) => setDetector(event.target.value)}>{detectorNames.map((name) => <option value={name} key={name}>{name}</option>)}</select></label>
    </div>
    {!attributable ? <p className="mt-4 rounded-lg border border-slate-300 bg-slate-50 p-3 text-xs leading-5 text-slate-800">{text.sharedModelNote}</p> : chart.length ? (
      <div className="evaluation-chart mt-4 h-80">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chart} margin={{ top: 20, right: 20, bottom: 20, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="hour" type="number" domain={[0, durationHours || 'auto']} tickFormatter={(value) => `${Number(value).toFixed(1)} ${text.hours}`} label={{ value: text.elapsedHours, position: 'insideBottom', offset: -12 }} />
            <YAxis domain={[0, 1]} tickFormatter={(value) => Number(value).toFixed(1)} label={{ value: text.flaggedSnapshotShare, angle: -90, position: 'insideLeft' }} />
            <Tooltip contentStyle={{ backgroundColor: 'var(--evaluation-tooltip-background)', borderColor: 'var(--evaluation-tooltip-border)', color: 'var(--evaluation-tooltip-text)' }} formatter={(value) => [typeof value === 'number' ? value.toFixed(3) : String(value ?? text.notStored), text.flaggedSnapshotShare]} labelFormatter={(value) => `${Number(value).toFixed(1)} ${text.hours}`} />
            <Legend />
            {durationHours > 0 && <ReferenceArea x1={0} x2={durationHours * 0.2} fill="#22c55e" fillOpacity={0.08} label={text.assumedHealthyPeriod} />}
            {burnInHours > 0 && <ReferenceArea x1={0} x2={burnInHours} fill="#94a3b8" fillOpacity={0.14} label={text.burnInZone} />}
            {failedBearing && durationHours > 0 && <ReferenceLine x={durationHours} stroke="#dc2626" strokeDasharray="5 5" label={text.failureMarker(failedBearing)} />}
            {bearingEntries.map(([bearing], index) => {
              const view = filtered ? selected.bearings[bearing]?.snapshot_aggregation?.sustained_alert?.first_detected_at : selected.bearings[bearing]?.snapshot_burn_in?.unfiltered_view?.sustained_alert?.first_detected_at
              const x = view && Number.isFinite(Date.parse(view)) ? (Date.parse(view) - testStart) / 3600000 : null
              return x === null ? null : <ReferenceLine key={bearing} x={x} stroke={colors[index % colors.length]} strokeDasharray="2 3" label={`${text.sustainedAlert} · ${text.bearing} ${bearing}`} />
            })}
            {bearingEntries.map(([bearing], index) => <Line key={bearing} type="monotone" dataKey={`bearing_${bearing}`} name={`${text.bearing} ${bearing}`} stroke={colors[index % colors.length]} dot={false} connectNulls />)}
          </LineChart>
        </ResponsiveContainer>
      </div>
    ) : <p className="mt-4 text-xs text-slate-600">{text.noStoredData}</p>}
  </article>
}
