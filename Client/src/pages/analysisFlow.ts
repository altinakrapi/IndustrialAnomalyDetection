export type AnalysisTab = 'history' | 'run' | 'models'

export type AnalysisFlowRequirements = {
  source: 'dataset' | 'equipment'
  hasData: boolean
  hasFeatures: boolean
  hasMethod: boolean
}

export function getAnalysisTabs(
  canEdit: boolean,
  labels: { history: string; run: string; models: string },
): { id: AnalysisTab; label: string }[] {
  return [
    { id: 'history', label: labels.history },
    ...(canEdit
      ? [
          { id: 'run' as const, label: labels.run },
          { id: 'models' as const, label: labels.models },
        ]
      : []),
  ]
}

export function canAdvanceAnalysisStep(step: number, requirements: AnalysisFlowRequirements): boolean {
  if (step === 1) return requirements.hasData
  if (step === 2) return requirements.source === 'equipment' || requirements.hasFeatures
  if (step === 3) return requirements.hasMethod
  return true
}

export function canSubmitAnalysisRun(requirements: AnalysisFlowRequirements): boolean {
  return requirements.hasData
    && requirements.hasMethod
    && (requirements.source === 'equipment' || requirements.hasFeatures)
}

export function canBeginAnalysisSubmission(isSubmitting: boolean): boolean {
  return !isSubmitting
}

export function canCompareAnalyses(canEdit: boolean, selectedIds: number[]): boolean {
  return canEdit && selectedIds.length >= 2 && selectedIds.length <= 10
}

export function toggleAnalysisSelection(previous: number[], id: number): number[] {
  return previous.includes(id)
    ? previous.filter((value) => value !== id)
    : previous.length < 10 ? [...previous, id] : previous
}

export function shouldExcludeLiveReadings(runKind: string, expanded: boolean): boolean {
  return !runKind && !expanded
}

export function getHistoryPanelState(
  isLoading: boolean,
  hasError: boolean,
  hasRows: boolean,
): 'loading' | 'error' | 'empty' | 'results' {
  if (isLoading && !hasRows) return 'loading'
  if (hasError && !hasRows) return 'error'
  return hasRows ? 'results' : 'empty'
}
