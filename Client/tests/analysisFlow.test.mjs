import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canAdvanceAnalysisStep,
  canBeginAnalysisSubmission,
  canCompareAnalyses,
  canSubmitAnalysisRun,
  getAnalysisTabs,
  getHistoryPanelState,
  shouldExcludeLiveReadings,
  toggleAnalysisSelection,
} from '../src/pages/analysisFlow.ts'
import { analysisStrings } from '../src/pages/analysisStrings.ts'

test('four-step requirements gate data, features, method, and final submission', () => {
  const missingDataset = { source: 'dataset', hasData: false, hasFeatures: false, hasMethod: false }
  assert.equal(canAdvanceAnalysisStep(1, missingDataset), false)
  assert.equal(canAdvanceAnalysisStep(2, missingDataset), false)
  assert.equal(canSubmitAnalysisRun(missingDataset), false)

  const readyDataset = { source: 'dataset', hasData: true, hasFeatures: true, hasMethod: true }
  assert.equal(canAdvanceAnalysisStep(1, readyDataset), true)
  assert.equal(canAdvanceAnalysisStep(2, readyDataset), true)
  assert.equal(canAdvanceAnalysisStep(3, readyDataset), true)
  assert.equal(canSubmitAnalysisRun(readyDataset), true)

  const readyEquipment = { source: 'equipment', hasData: true, hasFeatures: false, hasMethod: true }
  assert.equal(canAdvanceAnalysisStep(2, readyEquipment), true)
  assert.equal(canSubmitAnalysisRun(readyEquipment), true)
})

test('submission guard blocks a second in-flight request', () => {
  assert.equal(canBeginAnalysisSubmission(false), true)
  assert.equal(canBeginAnalysisSubmission(true), false)
})

test('history omits live readings by default and includes them when expanded or filtered', () => {
  assert.equal(shouldExcludeLiveReadings('', false), true)
  assert.equal(shouldExcludeLiveReadings('live_reading', false), false)
  assert.equal(shouldExcludeLiveReadings('', true), false)
  assert.equal(analysisStrings.summary.liveSummary(12, 1), '12 automatic per-reading runs in the last 24 hours, 1 failed')
})

test('comparison selection enforces the two-to-ten run limit', () => {
  assert.equal(canCompareAnalyses(true, []), false)
  assert.equal(canCompareAnalyses(true, [1]), false)
  assert.equal(canCompareAnalyses(true, [1, 2]), true)
  assert.equal(canCompareAnalyses(true, Array.from({ length: 10 }, (_, index) => index)), true)
  assert.equal(canCompareAnalyses(true, Array.from({ length: 11 }, (_, index) => index)), false)
  assert.equal(canCompareAnalyses(false, [1, 2]), false)
  const ten = Array.from({ length: 10 }, (_, index) => index)
  assert.deepEqual(toggleAnalysisSelection(ten, 11), ten)
  assert.deepEqual(toggleAnalysisSelection(ten, 5), ten.filter((id) => id !== 5))
})

test('role variants expose only permitted analysis tabs', () => {
  assert.deepEqual(getAnalysisTabs(true, analysisStrings.tabs).map(({ id }) => id), ['history', 'run', 'models'])
  assert.deepEqual(getAnalysisTabs(false, analysisStrings.tabs).map(({ id }) => id), ['history'])
})

test('history panel represents loading, error, empty, and result states', () => {
  assert.equal(getHistoryPanelState(true, false, false), 'loading')
  assert.equal(getHistoryPanelState(false, true, false), 'error')
  assert.equal(getHistoryPanelState(false, false, false), 'empty')
  assert.equal(getHistoryPanelState(false, false, true), 'results')
})
