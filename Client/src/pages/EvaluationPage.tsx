/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { download, request } from '../api'
import { buttonClass, ErrorMessage, panelClass } from '../components/ui'
import { PageTitle } from '../components/PageTitle'
import { formatAggregateMetric, perRunDetectionDelay, perRunMetric } from '../evaluationPayload'
import '../evaluationPayload.fixture'

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

const number = (value: unknown, digits = 3) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : '-'
}
const metricNames = ['precision', 'recall', 'f1', 'roc_auc', 'pr_auc', 'false_alarm_rate']
const aggregateMetricNames = ['precision', 'recall', 'f1', 'roc_auc', 'pr_auc', 'false_alarm_rate']

export function EvaluationPage({ userRole, onAction }: { userRole: string; onAction: Action }) {
  const [tab, setTab] = useState<'simulator' | 'nasa'>('simulator')
  const [evaluations, setEvaluations] = useState<Evaluation[]>([])
  const [validations, setValidations] = useState<Validation[]>([])
  const [evaluationId, setEvaluationId] = useState('')
  const [validationId, setValidationId] = useState('')
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [validation, setValidation] = useState<Validation | null>(null)
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

  useEffect(() => {
    if (userRole !== 'administrator' && userRole !== 'data_analyst') return
    Promise.all([
      request<TuningRun[]>('/analyses/evaluations/tuning-runs'),
      request<TunedConfig[]>('/analyses/evaluations/tuned-configs'),
    ]).then(([runs, configs]) => {
      setTuningRuns(runs)
      setTunedConfigs(configs)
    }).catch((caught) => setError(caught instanceof Error ? caught.message : 'Unable to load tuning evidence'))
  }, [userRole])

  const runTuning = async () => {
    const ids = tuningRunIds.split(',').map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0)
    if (!ids.length) {
      setError('Enter at least one development ground-truth run ID.')
      return
    }
    await onAction(async () => {
      const created = await request<TuningRun>('/analyses/evaluations/tuning-runs', {
        method: 'POST',
        body: JSON.stringify({ ground_truth_run_ids: ids, false_alarm_cap: 0.05 }),
      })
      setTuningRuns((items) => [created, ...items])
      setError('')
    }, 'Development tuning completed')
  }

  const saveTunedConfig = async () => {
    const latestRun = tuningRuns[0]
    if (!latestRun || !saveName.trim()) {
      setError('Choose the tuning run, enter a name, and select a detector.')
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
      const configs = await request<TunedConfig[]>('/analyses/evaluations/tuned-configs')
      setTunedConfigs(configs)
      setSaveName('')
      setError('')
    }, 'Named tuned configuration saved')
  }

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [evaluationList, validationList] = await Promise.all([
        request<Evaluation[]>('/analyses/evaluations'),
        request<Validation[]>('/datasets/nasa-ims/evaluations'),
      ])
      setEvaluations(evaluationList)
      setValidations(validationList)
      if (!evaluationId && evaluationList[0]) setEvaluationId(String(evaluationList[0].id))
      if (!validationId) {
        const completed = validationList.find((item) => item.status === 'completed')
        const defaultValidation = completed ?? validationList[0]
        if (defaultValidation) setValidationId(String(defaultValidation.id))
      }
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load evaluations')
    } finally {
      setLoading(false)
    }
  }, [evaluationId, validationId])

  useEffect(() => {
    if (userRole !== 'administrator' && userRole !== 'data_analyst') return
    request<Array<{ id: number; run_id: string; seed: number }>>('/analyses/evaluations/ground-truth')
      .then(setGroundTruthRuns)
      .catch((caught) => setError(caught instanceof Error ? caught.message : 'Unable to load simulator runs'))
  }, [userRole])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void refresh())
    return () => window.cancelAnimationFrame(frame)
  }, [refresh])

  useEffect(() => {
    if (!evaluationId) return
    request<Evaluation>(`/analyses/evaluations/${evaluationId}`).then(setEvaluation).catch((caught) => setError(caught instanceof Error ? caught.message : 'Unable to load evaluation'))
  }, [evaluationId])

  useEffect(() => {
    if (!validationId) return
    request<Validation>(`/datasets/nasa-ims/evaluations/${validationId}`).then(setValidation).catch((caught) => setError(caught instanceof Error ? caught.message : 'Unable to load validation'))
  }, [validationId])

  const exportSelected = async (kind: 'csv' | 'pdf') => {
    await onAction(async () => {
      if (tab === 'simulator' && evaluationId) {
        await download(`/analyses/evaluations/${evaluationId}/${kind}`, `simulator-evaluation-${evaluationId}.${kind}`)
      } else if (tab === 'nasa' && validationId && validation?.status === 'completed') {
        await download(`/datasets/nasa-ims/evaluations/${validationId}/${kind}`, `nasa-validation-${validationId}.${kind}`)
      }
    }, `${kind.toUpperCase()} export created`)
  }

  const createEvaluation = async () => {
    const runIds = analysisRunIds.split(',').map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0)
    if (!groundTruthId || !runIds.length) {
      setError('Select a simulator ground-truth run and enter at least one analysis run ID.')
      return
    }
    await onAction(async () => {
      const created = await request<Evaluation>('/analyses/evaluations', {
        method: 'POST',
        body: JSON.stringify({ runs: [{ ground_truth_run_id: Number(groundTruthId), analysis_run_ids: runIds }], tolerance_readings: 5 }),
      })
      setEvaluations((items) => [created, ...items])
      setEvaluationId(String(created.id))
      setError('')
    }, 'Simulator evaluation created')
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

  return (
    <>
      <PageTitle title="Evaluation evidence" description="Simulator labels and NASA statistical evidence are presented separately and never treated as fault diagnosis." />
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <section className={panelClass}>
        <div className="flex flex-wrap gap-2 border-b border-slate-100 pb-4">
          <button className={`${buttonClass} ${tab !== 'simulator' ? '!bg-slate-100 !text-slate-700' : ''}`} onClick={() => setTab('simulator')}>Simulator evaluation</button>
          <button className={`${buttonClass} ${tab !== 'nasa' ? '!bg-slate-100 !text-slate-700' : ''}`} onClick={() => setTab('nasa')}>NASA IMS validation</button>
        </div>

        {tab === 'simulator' ? (
          <>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label className="min-w-52">Evaluation
                <select value={evaluationId} onChange={(event) => setEvaluationId(event.target.value)}>
                  {evaluations.map((item) => <option key={item.id} value={item.id}>Evaluation #{item.id} - {item.source_type}</option>)}
                </select>
              </label>
              <button className={buttonClass} disabled={!evaluationId} onClick={() => void exportSelected('csv')}>Export CSV</button>
              <button className={buttonClass} disabled={!evaluationId} onClick={() => void exportSelected('pdf')}>Export PDF</button>
            </div>
            {(userRole === 'administrator' || userRole === 'data_analyst') && <details className="mt-4 rounded-lg border border-slate-200 p-4">
              <summary className="cursor-pointer text-xs font-semibold text-slate-700">Create simulator evaluation</summary>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <label>Ground-truth run<select value={groundTruthId} onChange={(event) => setGroundTruthId(event.target.value)}>
                  <option value="">Select run</option>{groundTruthRuns.map((run) => <option key={run.id} value={run.id}>{run.run_id} (seed {run.seed})</option>)}
                </select></label>
                <label>Analysis run IDs<input value={analysisRunIds} onChange={(event) => setAnalysisRunIds(event.target.value)} placeholder="123,124,125" /></label>
                <button className={buttonClass} onClick={() => void createEvaluation()}>Create evaluation</button>
              </div>
            </details>}
            {(userRole === 'administrator' || userRole === 'data_analyst') && <details className="mt-4 rounded-lg border border-slate-200 p-4">
              <summary className="cursor-pointer text-xs font-semibold text-slate-700">Development-only detector tuning</summary>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <label>Development ground-truth run IDs<input value={tuningRunIds} onChange={(event) => setTuningRunIds(event.target.value)} placeholder="11,12" /></label>
                <button className={buttonClass} onClick={() => void runTuning()}>Run tuning (5% false-alarm cap)</button>
              </div>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <label>Configuration algorithm
                  <select value={saveAlgorithm} onChange={(event) => setSaveAlgorithm(event.target.value)}>
                    {['z_score', 'ewma', 'cusum'].map((algorithm) => <option key={algorithm} value={algorithm}>{algorithm}</option>)}
                  </select>
                </label>
                <label>Configuration name<input value={saveName} onChange={(event) => setSaveName(event.target.value)} placeholder="development-tuned-isolation-forest" /></label>
                <button className={buttonClass} onClick={() => void saveTunedConfig()}>Save best selected candidate</button>
              </div>
              <p className="mt-3 text-xs text-slate-600">Tuning uses only development-purpose runs loaded through the development gate. Evaluation data and NASA IMS data are excluded.</p>
            </details>}
            {tuningRuns.length > 0 && <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <caption className="pb-2 text-left font-semibold text-slate-700">Tuning runs (development data only)</caption>
                <thead><tr><th>Run</th><th>Seeds</th><th>Objective / cap</th><th>Runtime</th><th>Chosen parameters</th></tr></thead>
                <tbody>{tuningRuns.map((run) => {
                  const chosen = run.result?.chosen ?? {}
                  const parameters = chosen.parameters && typeof chosen.parameters === 'object' ? chosen.parameters as Record<string, unknown> : {}
                  const algorithm = String(parameters.algorithm ?? chosen.algorithm ?? 'ensemble')
                  const parameterText = Object.entries(parameters).filter(([key]) => key !== 'algorithm').map(([key, value]) => `${key}=${String(value)}`).join(', ')
                  return <tr key={run.id} className="border-t border-slate-100">
                    <td className="py-2">#{run.id}</td>
                    <td>{run.data_seeds.join(', ')}</td>
                    <td>{run.objective}; FAR &le; {number(run.false_alarm_cap)}</td>
                    <td>{number(run.runtime_seconds, 2)} s</td>
                    <td>
                      {run.presentation?.superseded_by_run_7 && <span className="mb-2 block rounded bg-amber-100 px-2 py-1 font-semibold text-amber-800">Superseded by run 7: earlier metric version<br />{run.presentation.supersession_reason}</span>}
                      <strong>{Object.keys(run.result?.selected ?? {}).length > 1 ? 'Best candidate overall' : 'Best candidate'}</strong><br />
                      Algorithm: {algorithm}<br />
                      Parameters: {parameterText || '-'}<br />
                      Precision {number(chosen.precision)}; recall {number(chosen.recall)}; F1 {number(chosen.f1)}; false-alarm rate {number(chosen.false_alarm_rate)}<br />
                      TP {String(chosen.true_positive ?? '-')} / FP {String(chosen.false_positive ?? '-')} / FN {String(chosen.false_negative ?? '-')} / TN {String(chosen.true_negative ?? '-')}
                    </td>
                  </tr>
                })}</tbody>
              </table>
              {Array.from(new Set(tuningRuns.map((run) => run.limitations).filter(Boolean))).map((limitations) => <p key={limitations} className="mt-2 text-xs text-slate-600">{limitations}</p>)}
              {!tuningRuns.some((run) => run.id === 3) && <p className="mt-2 text-xs text-slate-600">Run 3 is absent from the stored tuning-run records; it was not added or reconstructed.</p>}
              {tuningRuns.some((run) => run.detector_comparison) && tuningRuns.map((run) => run.detector_comparison && (
                <table key={`comparison-${run.id}`} className="mt-4 w-full text-left text-xs">
                  <caption className="pb-2 text-left font-semibold text-slate-700">Run #{run.id} detector comparison (development data only)</caption>
                  <thead><tr><th>Detector</th><th>Default &rarr; tuned, seed 101</th><th>Default &rarr; tuned, seed 202</th><th>Decision</th><th>Reason</th></tr></thead>
                  <tbody>{run.detector_comparison.map((row) => {
                    const run101 = row.runs.find((item) => item.seed === 101)
                    const run202 = row.runs.find((item) => item.seed === 202)
                    const metric = (item: typeof run101) => item ? `${number(item.default_f1)} (${number(item.default_false_alarm_rate)}) -> ${number(item.tuned_f1)} (${number(item.tuned_false_alarm_rate)})` : '-'
                    return <tr key={row.algorithm} className="border-t border-slate-100">
                      <td className="py-2 font-semibold">{row.algorithm}</td>
                      <td>{metric(run101)} F1 (FAR)</td>
                      <td>{metric(run202)} F1 (FAR)</td>
                      <td>{row.decision}</td>
                      <td>{row.reason}</td>
                    </tr>
                  })}</tbody>
                </table>
              ))}
            </div>}
            {tunedConfigs.length > 0 && <div className="mt-4">
              <h3 className="text-sm font-semibold">Saved tuned configurations</h3>
              {tunedConfigs.filter((config) => !config.is_superseded).map((config) => <div key={config.id} className="mt-2 rounded border border-slate-200 p-3 text-xs">
                <strong>{config.name}</strong> — {config.algorithm}; parameters {JSON.stringify(config.parameters)}<br />
                Development seeds: {config.development_seeds.join(', ')}. {config.limitations}
              </div>)}
              {tunedConfigs.filter((config) => config.is_superseded).length > 0 && <>
                <button type="button" className="mt-3 rounded border border-slate-300 px-3 py-1 text-xs" onClick={() => setShowSupersededConfigs((shown) => !shown)}>
                  {showSupersededConfigs ? 'Hide superseded' : `Show superseded (${tunedConfigs.filter((config) => config.is_superseded).length})`}
                </button>
                {showSupersededConfigs && tunedConfigs.filter((config) => config.is_superseded).map((config) => <div key={config.id} className="mt-2 rounded border border-amber-200 bg-amber-50 p-3 text-xs">
                  <strong>{config.name}</strong> — {config.algorithm}; parameters {JSON.stringify(config.parameters)}<br />
                  <span className="font-semibold text-amber-800">Superseded: {config.superseded_reason?.replace(/^superseded:\s*/i, '') ?? 'computed with an incorrect metric'}</span>
                </div>)}
              </>}
            </div>}
            {evaluation && <>
              <div className="mt-4 flex flex-wrap gap-2 text-xs">
                <span className="rounded-full bg-violet-100 px-3 py-1 font-semibold text-violet-700">Simulated</span>
                <span>Purpose: {evaluation.purpose}</span>
                <span>Tolerance: {evaluation.result?.tolerance_readings} readings</span>
              </div>
              {evaluation.limitations && <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">{evaluation.limitations}</div>}
              {loading ? <p className="mt-4 text-xs text-slate-500">Loading evaluation...</p> : detectorRows.length ? (
                <div className="mt-5 overflow-x-auto">
                  <table className="w-full min-w-[1000px] text-left text-xs">
                    <caption className="pb-2 text-left font-semibold text-slate-700">Per-run metrics (stored in each run's `metrics` object)</caption>
                    <thead><tr className="border-b border-slate-200 text-slate-500"><th className="py-2">Detector</th><th>Mode</th><th>Run</th>{[...metricNames, 'detection_delay'].map((metric) => <th key={metric}>{metric.replaceAll('_', ' ')}</th>)}</tr></thead>
                    <tbody>{detectorRows.map((row, index) => (
                      <tr className="border-b border-slate-100" key={`${row.detector}-${row.run_id ?? index}`}>
                        <td className="py-2 font-semibold">{row.detector}</td>
                        <td>{row.mode ?? evaluation.result.detectors[row.detector]?.mode_by_run?.[String(row.run_id ?? '')] ?? '-'}</td>
                        <td>{row.run_id ?? row.run ?? index + 1}</td>
                        {metricNames.map((metric) => <td key={metric}>{perRunMetric(row, metric) === null ? 'not stored for this run' : number(perRunMetric(row, metric))}</td>)}
                        <td>{perRunDetectionDelay(row) === null ? 'not stored for this run' : number(perRunDetectionDelay(row), 2)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              ) : <p className="mt-5 text-xs text-slate-500">No stored detector metrics are available for this evaluation.</p>}
              <AggregateMetricTables detectors={evaluation.result.detectors ?? {}} />
              {evaluation.result?.detectors && <SimulatorEvidence detectors={evaluation.result.detectors} />}
              {evaluation.result?.detectors && <SimulatorCharts detectors={evaluation.result.detectors} />}
            </>}
          </>
        ) : (
          <>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label className="min-w-52">Validation record
                <select value={validationId} onChange={(event) => setValidationId(event.target.value)}>
                  {validations.map((item) => <option key={item.id} value={item.id}>#{item.id} - {String(item.configuration?.test_set ?? 'NASA IMS')} - {item.status}</option>)}
                </select>
              </label>
              <button className={buttonClass} disabled={validation?.status !== 'completed'} onClick={() => void exportSelected('csv')}>Export CSV</button>
              <button className={buttonClass} disabled={validation?.status !== 'completed'} onClick={() => void exportSelected('pdf')}>Export PDF</button>
            </div>
            {validations.filter((item) => item.status === 'failed' || item.status === 'cancelled').map((item) => (
              <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700" key={item.id}>
                Validation #{item.id}: <strong>{item.status}</strong>{item.error_message ? ` - ${item.error_message}` : ''}
              </div>
            ))}
            {!validation && loading && <p className="mt-4 text-xs text-slate-500">Loading validation...</p>}
            {validation?.status !== 'completed' && validation && <p className="mt-4 text-xs text-slate-500">No result table is available until this validation completes.</p>}
            {validation?.status === 'completed' && validation.result && <>
              <p className="mt-4 text-xs text-slate-600">2nd_test was used to choose the snapshot rule and burn-in. Results are statistical evidence for human review, not a fault diagnosis.</p>
              {validation.limitations && <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">{validation.limitations}</div>}
              <label className="mt-4 flex items-center gap-2 text-xs font-medium text-slate-700">
                <input type="checkbox" checked={filtered} onChange={(event) => setFiltered(event.target.checked)} /> Burn-in-filtered view (unfiltered secondary view when off)
              </label>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[900px] text-left text-xs">
                  <thead><tr className="border-b border-slate-200 text-slate-500"><th className="py-2">Detector</th><th>Bearing</th><th>Sustained alert</th><th>First alert</th><th>Lead hours</th><th>Healthy FA proxy</th><th>Separation</th></tr></thead>
                  <tbody>{nasaRows.map((row, index) => {
                    const view = filtered ? row.snapshot_burn_in ?? row : row
                    return <tr className="border-b border-slate-100" key={`${row.detector}-${row.bearing}-${index}`}>
                      <td className="py-2 font-semibold">{row.detector}</td>
                      <td>{row.attributable ? row.bearing : `${row.bearing} (not attributable per bearing)`}</td>
                      <td>{String(view?.sustained_alert ?? view?.sustained_alert_reached ?? row.sustained_alert?.reached ?? '-')}</td>
                      <td>{view?.first_alert_time ?? row.sustained_alert?.first_alert_snapshot_time ?? '-'}</td>
                      <td>{number(view?.lead_hours ?? row.lead_time_to_test_end_hours)}</td>
                      <td>{number(row.healthy_period?.false_alarm_proxy)}</td>
                      <td>{row.attributable ? number(row.separation) : '-'}</td>
                    </tr>
                  })}</tbody>
                </table>
              </div>
              <NasaChart validation={validation} filtered={filtered} />
            </>}
          </>
        )}
      </section>
      <p className="text-[10px] text-slate-400">Signed-in role: {userRole}. Result exports are audited.</p>
    </>
  )
}

function SimulatorEvidence({ detectors }: { detectors: Record<string, any> }) {
  return <div className="mt-6 grid gap-4 lg:grid-cols-2">
    {Object.entries(detectors).map(([detector, data]: [string, any]) => (
      <article className="rounded-lg border border-slate-100 p-4" key={detector}>
        <h3 className="text-sm font-semibold text-slate-800">{detector}</h3>
        {detector.includes('ensemble') && <p className="mt-1 text-xs text-slate-600">walk-forward ensemble: members are batch-fitted on the growing history prefix at each scoring step. Stored per-member settings distinguish it from standalone Isolation Forest and Z-score runs.</p>}
        <p className="mt-2 text-xs text-slate-600">Modes: {Object.entries(data.mode_by_run ?? {}).map(([run, mode]) => {
          const rawMode = typeof mode === 'object' && mode !== null ? mode as Record<string, unknown> : { execution_mode: String(mode) }
          const executionMode = String(rawMode.execution_mode ?? rawMode.mode ?? mode ?? '')
          const parameters = Object.entries(rawMode).filter(([key]) => !['execution_mode', 'mode'].includes(key)).map(([key, value]) => `${key}=${String(value)}`).join(', ')
          return `${run}: ${executionMode.replaceAll('_', '-')}${parameters ? ` (${parameters})` : ''}`
        }).join(', ') || '-'}</p>
        {data.member_configuration && <p className="mt-2 break-words text-xs text-slate-600">{JSON.stringify(data.member_configuration)}</p>}
        {Object.entries(data.rule_evidence_aggregate ?? {}).length > 0 && <pre className="mt-2 max-h-40 overflow-auto rounded bg-slate-50 p-2 text-[10px]">{JSON.stringify(data.rule_evidence_aggregate, null, 2)}</pre>}
      </article>
    ))}
  </div>
}

function AggregateMetricTables({ detectors }: { detectors: Record<string, any> }) {
  return <>
    <article className={`${panelClass} mt-5`}>
      <h3 className="text-sm font-semibold text-slate-800">Aggregate table (mean and spread across runs)</h3>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[900px] text-left text-xs">
          <thead><tr className="border-b border-slate-200 text-slate-500"><th className="py-2">Detector</th>{aggregateMetricNames.map((metric) => <th key={metric}>{metric.replaceAll('_', ' ')}</th>)}</tr></thead>
          <tbody>{Object.entries(detectors).map(([detector, value]: [string, any]) => <tr className="border-b border-slate-100" key={detector}>
            <td className="py-2 font-semibold">{detector}</td>
            {aggregateMetricNames.map((metric) => {
              const aggregate = value.aggregate?.[metric]
              return <td key={metric}>{formatAggregateMetric(aggregate)}</td>
            })}
          </tr>)}</tbody>
        </table>
      </div>
    </article>
    <article className={`${panelClass} mt-5`}>
      <h3 className="text-sm font-semibold text-slate-800">Per-fault-type aggregate table</h3>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[900px] text-left text-xs">
          <thead><tr className="border-b border-slate-200 text-slate-500"><th className="py-2">Detector</th><th>Fault type</th>{aggregateMetricNames.map((metric) => <th key={metric}>{metric.replaceAll('_', ' ')}</th>)}</tr></thead>
          <tbody>{Object.entries(detectors).flatMap(([detector, value]: [string, any]) =>
            Object.entries(value.per_fault_type_aggregate ?? {}).map(([faultType, metrics]: [string, any]) => <tr className="border-b border-slate-100" key={`${detector}-${faultType}`}>
              <td className="py-2 font-semibold">{detector}</td><td>{faultType}</td>
              {aggregateMetricNames.map((metric) => <td key={metric}>{formatAggregateMetric(metrics?.[metric])}</td>)}
            </tr>)
          )}</tbody>
        </table>
      </div>
      {!Object.values(detectors).some((value: any) => Object.keys(value.per_fault_type_aggregate ?? {}).length) && <p className="mt-3 text-xs text-slate-500">No per-fault-type aggregate metrics are stored for this evaluation.</p>}
    </article>
  </>
}

function NasaChart({ validation, filtered }: { validation: Validation, filtered: boolean }) {
  const [detector, setDetector] = useState('')
  const detectors = validation.result?.detectors ?? {}
  const detectorNames = Object.keys(detectors)
  const selected = detector && detectors[detector] ? detectors[detector] : detectors[detectorNames[0]]
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

  if (!detectorNames.length) return <p className="mt-5 text-xs text-slate-500">No stored detector data is available for the selected NASA validation.</p>
  const testStart = Date.parse(validation.result?.test_start_at ?? '')
  const durationHours = Number.isFinite(testStart) && validation.result?.test_end_at
    ? (Date.parse(validation.result.test_end_at) - testStart) / 3600000 : 0
  const failedBearing = (validation.result?.verified_bearing_facts ?? []).find((fact: any) => fact.is_failed)?.bearing_number
  const burnInTimestamp = selected?.bearings?.['1']?.snapshot_aggregation?.snapshots?.[selected?.bearings?.['1']?.snapshot_burn_in?.configured_snapshots ?? 0]?.timestamp
  const burnInHours = Number.isFinite(Date.parse(burnInTimestamp ?? '')) ? (Date.parse(burnInTimestamp) - testStart) / 3600000 : 0
  const colors = ['#2563eb', '#16a34a', '#dc2626', '#9333ea']

  return <article className={`${panelClass} mt-5`}>
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><h3 className="text-sm font-semibold">Snapshot anomaly rate by bearing</h3><p className="mt-1 text-xs text-slate-500">{filtered ? 'Burn-in filtered view' : 'Unfiltered secondary view'}</p></div>
      <label>Detector<select value={detector || detectorNames[0]} onChange={(event) => setDetector(event.target.value)}>{detectorNames.map((name) => <option value={name} key={name}>{name}</option>)}</select></label>
    </div>
    {!attributable ? <p className="mt-4 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">Shared-model result is not drawn per bearing and is not attributable per bearing.</p> : chart.length ? (
      <div className="mt-4 h-96">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chart} margin={{ top: 20, right: 20, bottom: 20, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="hour" type="number" domain={[0, durationHours || 'auto']} tickFormatter={(value) => `${Number(value).toFixed(1)} h`} label={{ value: 'Elapsed time (hours)', position: 'insideBottom', offset: -12 }} />
            <YAxis domain={[0, 1]} tickFormatter={(value) => `${Number(value).toFixed(1)}`} label={{ value: 'Flagged snapshot share', angle: -90, position: 'insideLeft' }} />
            <Tooltip formatter={(value) => [typeof value === 'number' ? value.toFixed(3) : String(value ?? '-'), 'snapshot share']} labelFormatter={(value) => `${Number(value).toFixed(1)} elapsed hours`} />
            <Legend />
            {durationHours > 0 && <ReferenceArea x1={0} x2={durationHours * 0.2} fill="#22c55e" fillOpacity={0.08} label="Healthy-period shading" />}
            {burnInHours > 0 && <ReferenceArea x1={0} x2={burnInHours} fill="#94a3b8" fillOpacity={0.14} label="Burn-in zone" />}
            {failedBearing && durationHours > 0 && <ReferenceLine x={durationHours} stroke="#dc2626" strokeDasharray="5 5" label={`Bearing ${failedBearing} failure marker (end of test)`} />}
            {bearingEntries.map(([bearing], index) => {
              const view = filtered ? selected.bearings[bearing]?.snapshot_aggregation?.sustained_alert?.first_detected_at : selected.bearings[bearing]?.snapshot_burn_in?.unfiltered_view?.sustained_alert?.first_detected_at
              const x = view && Number.isFinite(Date.parse(view)) ? (Date.parse(view) - testStart) / 3600000 : null
              return x === null ? null : <ReferenceLine key={bearing} x={x} stroke={colors[index % colors.length]} strokeDasharray="2 3" label={`Sustained B${bearing}`} />
            })}
            {bearingEntries.map(([bearing], index) => <Line key={bearing} type="monotone" dataKey={`bearing_${bearing}`} name={`Bearing ${bearing}`} stroke={colors[index % colors.length]} dot={false} connectNulls />)}
          </LineChart>
        </ResponsiveContainer>
      </div>
    ) : <p className="mt-4 text-xs text-slate-500">No snapshot chart data is stored for this detector view.</p>}
  </article>
}

function SimulatorCharts({ detectors }: { detectors: Record<string, any> }) {
  const detectorNames = Object.keys(detectors)
  const [matrixDetector, setMatrixDetector] = useState(detectorNames[0] ?? '')
  const [curveDetectors, setCurveDetectors] = useState<string[]>(detectorNames)
  const matrixRun = detectors[matrixDetector]?.runs?.[0]?.metrics
  const confusion = matrixRun?.confusion_matrix
  const delayData = Object.entries(detectors[matrixDetector]?.detection_delay_aggregate ?? {}).map(([faultType, value]: [string, any]) => ({
    faultType,
    meanDelayReadings: value.delay_readings?.mean ?? null,
    meanDelaySeconds: value.delay_seconds?.mean ?? null,
  }))

  if (!detectorNames.length) return <p className="mt-5 text-xs text-slate-500">No stored simulator detector results are available.</p>
  return <>
    <article className={`${panelClass} mt-5`}>
      <label>Confusion-matrix detector<select value={matrixDetector} onChange={(event) => setMatrixDetector(event.target.value)}>{detectorNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
      {confusion ? <div className="mt-4 grid max-w-md grid-cols-3 gap-2 text-center text-xs">
        <span /><strong>Predicted normal</strong><strong>Predicted fault</strong>
        <strong>Actual normal</strong><span className="rounded bg-emerald-50 p-4 text-lg font-semibold">{confusion.true_negative}</span><span className="rounded bg-rose-50 p-4 text-lg font-semibold">{confusion.false_positive}</span>
        <strong>Actual fault</strong><span className="rounded bg-rose-50 p-4 text-lg font-semibold">{confusion.false_negative}</span><span className="rounded bg-emerald-50 p-4 text-lg font-semibold">{confusion.true_positive}</span>
      </div> : <p className="mt-4 text-xs text-slate-500">No confusion matrix is stored for this detector.</p>}
    </article>
    <article className={`${panelClass} mt-5`}>
      <h3 className="text-sm font-semibold">ROC and precision-recall curves</h3>
      <div className="mt-3 flex flex-wrap gap-4 text-xs">
        {detectorNames.map((name) => <label key={name} className="flex items-center gap-2"><input type="checkbox" checked={curveDetectors.includes(name)} onChange={(event) => setCurveDetectors((current) => event.target.checked ? [...current, name] : current.filter((item) => item !== name))} />{name}</label>)}
      </div>
      {!curveDetectors.length ? <p className="mt-4 text-xs text-slate-500">Select at least one detector.</p> : <div className="grid gap-4 lg:grid-cols-2">
        <CurveChart title="ROC curve" detectors={detectors} selected={curveDetectors} field="roc_curve" xField="false_positive_rate" yField="true_positive_rate" xLabel="False-positive rate" yLabel="True-positive rate" />
        <CurveChart title="Precision-recall curve" detectors={detectors} selected={curveDetectors} field="precision_recall_curve" xField="recall" yField="precision" xLabel="Recall" yLabel="Precision" />
      </div>}
    </article>
    <article className={`${panelClass} mt-5`}>
      <h3 className="text-sm font-semibold">Mean detection delay by fault type</h3>
      {delayData.length ? <div className="mt-4 h-72"><ResponsiveContainer width="100%" height="100%">
        <BarChart data={delayData} margin={{ bottom: 30 }}>
          <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="faultType" angle={-20} textAnchor="end" interval={0} label={{ value: 'Fault type', position: 'insideBottom', offset: -22 }} /><YAxis label={{ value: 'Mean delay (readings)', angle: -90, position: 'insideLeft' }} /><Tooltip /><Bar dataKey="meanDelayReadings" name="Mean delay readings" fill="#2563eb" />
        </BarChart>
      </ResponsiveContainer></div> : <p className="mt-4 text-xs text-slate-500">No detection-delay data is stored for this detector.</p>}
    </article>
  </>
}

function CurveChart({ title, detectors, selected, field, xField, yField, xLabel, yLabel }: { title: string; detectors: Record<string, any>; selected: string[]; field: string; xField: string; yField: string; xLabel: string; yLabel: string }) {
  const colors = ['#2563eb', '#16a34a', '#dc2626', '#9333ea', '#ea580c', '#0891b2']
  return <div><h4 className="mt-4 text-xs font-semibold text-slate-700">{title}</h4><div className="mt-2 h-72"><ResponsiveContainer width="100%" height="100%">
    <LineChart margin={{ bottom: 20 }}>
      <CartesianGrid strokeDasharray="3 3" /><XAxis type="number" dataKey={xField} domain={[0, 1]} label={{ value: xLabel, position: 'insideBottom', offset: -12 }} /><YAxis type="number" domain={[0, 1]} label={{ value: yLabel, angle: -90, position: 'insideLeft' }} /><Tooltip /><Legend />
      {selected.map((name, index) => {
        const points = detectors[name]?.runs?.[0]?.metrics?.[field]
        return points?.length ? <Line key={name} data={points.map((point: any) => ({ ...point }))} type="monotone" dataKey={yField} name={`${name} (${title})`} stroke={colors[index % colors.length]} dot={false} /> : null
      })}
    </LineChart>
  </ResponsiveContainer></div>{selected.filter((name) => !detectors[name]?.runs?.[0]?.metrics?.[field]?.length).map((name) => <p key={`missing-${name}`} className="mt-2 text-xs text-slate-500">no curve data stored for {name}</p>)}</div>
}
