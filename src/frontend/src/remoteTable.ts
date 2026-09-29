import type { RemoteTableRequest } from './components/DataTable.vue'

/** Translate AG Grid's window, search, sort and simple filters to list API parameters. */
export function remoteTableParams(
  request: RemoteTableRequest,
  base: Record<string, string | number | boolean | null | undefined> = {},
): URLSearchParams {
  const size = Math.max(1, request.endRow - request.startRow)
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(base)) {
    if (value === false) continue
    if (value !== null && value !== undefined && value !== '') params.set(key, String(value))
  }
  params.set('page', String(Math.floor(request.startRow / size) + 1))
  params.set('page_size', String(size))
  if (request.search) params.set('search', request.search)
  const sort = request.sortModel[0]
  if (sort?.colId && sort.colId !== 'tb-actions') {
    params.set('sort', sort.colId)
    params.set('order', sort.sort === 'desc' ? 'desc' : 'asc')
  }
  for (const [field, model] of Object.entries<any>(request.filterModel || {})) {
    if (model?.filterType === 'valueChecklist') {
      // Preserve the selected mode: converting between inclusion and exclusion
      // using a partial option list changes which rows match.
      if (Array.isArray(model.included)) {
        params.set(`include__${field}`, JSON.stringify(model.included))
        continue
      }
      const excluded = model.excluded || []
      if (excluded.length) params.set(`exclude__${field}`, JSON.stringify(excluded))
      continue
    }
    const value = model?.filter ?? model?.value
    if (value !== undefined && value !== null && value !== '') params.set(field, String(value))
  }
  return params
}

/** Keep large checklist selections out of proxy-limited request URLs. */
export function remoteTableQuery(path: string, params: URLSearchParams): { path: string; options?: RequestInit } {
  const url = `${path}?${params}`
  if (url.length < 6000) return { path: url }
  const controls = new URLSearchParams(params)
  const filters: Record<string, string> = {}
  for (const [key, value] of params) {
    if (!key.startsWith('include__') && !key.startsWith('exclude__')) continue
    filters[key] = value
    controls.delete(key)
  }
  return {
    path: `${path}/query?${controls}`,
    options: { method: 'POST', body: JSON.stringify(filters) },
  }
}
