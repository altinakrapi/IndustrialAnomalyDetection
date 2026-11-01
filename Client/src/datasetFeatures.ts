const nonMeasurementColumns = new Set(['label', 'target', 'anomaly', 'is_anomaly'])
const nasaImsMetadataColumns = new Set(['window_index', 'sample_start', 'sample_end'])

export function numericAnalysisFeatures(
  dataset: {
    file_format?: string
    column_metadata: { name: string; type: string }[]
  } | undefined,
): string[] {
  return dataset?.column_metadata
    .filter((column) => {
      const name = column.name.toLowerCase()
      return column.type === 'numeric'
        && !nonMeasurementColumns.has(name)
        && !(dataset.file_format === 'nasa_ims' && nasaImsMetadataColumns.has(name))
    })
    .map((column) => column.name) ?? []
}
