import { remoteTableParams } from './remoteTable'

type GridState = { quick_filter?: string; sort?: any[]; filter?: Record<string, any> } | undefined

/** Export the selected rows, or every row matching the grid's current filters. */
export function tableExportRequest(
  path: string,
  state: GridState,
  base: Record<string, string | number | boolean | null | undefined>,
  ids: (string | number)[],
): { path: string; options: RequestInit } {
  const params = remoteTableParams({
    startRow: 0, endRow: 1,
    search: state?.quick_filter || '',
    sortModel: state?.sort || [],
    filterModel: state?.filter || {},
  }, base)
  for (const key of ['page', 'page_size', 'sort', 'order']) params.delete(key)
  const controls = new URLSearchParams()
  const filters: Record<string, string> = {}
  for (const [key, value] of params) {
    if (['format', 'columns', 'expand_misc', 'search', 'device_type', 'overdue', 'online', 'latest_only'].includes(key)) {
      controls.set(key, value)
    } else {
      filters[key] = value
    }
  }
  return {
    path: `${path}/export?${controls}`,
    options: { method: 'POST', body: JSON.stringify({ filters, ids: ids.length ? ids : null }) },
  }
}
