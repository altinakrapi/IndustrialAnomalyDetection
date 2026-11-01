import { formatAggregateMetric, type EvaluationResultFixture } from './evaluationPayload'

// Real Evaluation #4 Z-score run shape. Metrics are nested under "metrics".
export const evaluation4Fixture: EvaluationResultFixture = {
  result: {
    detectors: {
      z_score: {
        mode_by_run: {
          'it-1793219118340944000': 'batch',
        },
        runs: [
          {
            run_id: 'it-1793219118340944000',
            mode: 'batch',
            metrics: {
              precision: 0.972972972972973,
              recall: 0.4897959183673469,
              f1: 0.65158371040724,
              roc_auc: 0.8048026781690595,
              pr_auc: 0.7841720560916132,
              false_alarm_rate: 0.006309148264984227,
            },
            detection_delays: [],
          },
        ],
        aggregate: {
          precision: { mean: 0.972972972972973, spread_stddev: 0, run_count: 1 },
        },
        per_fault_type_aggregate: {},
      },
    },
  },
}

export const aggregateFormatFixture: string = formatAggregateMetric({
  mean: 0.771,
  spread_stddev: 0,
  run_count: 1,
})

if (aggregateFormatFixture !== '0.771 ± 0.000 (n=1)') {
  throw new Error(`Unexpected aggregate formatting: ${aggregateFormatFixture}`)
}
