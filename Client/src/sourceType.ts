export type SourceTypeFilter = 'all' | 'manual' | 'sensor' | 'simulated' | 'dataset'

export const sourceTypes: SourceTypeFilter[] = [
  'all',
  'manual',
  'sensor',
  'simulated',
  'dataset',
]

export function matchesSourceType(
  selected: SourceTypeFilter,
  sourceType?: string | null,
): boolean {
  return selected === 'all' || (sourceType || 'manual') === selected
}
