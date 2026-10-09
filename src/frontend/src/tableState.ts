/** Saved state uses the same search value as the remote datasource. */
export function captureTableState(api: any, search: string, defaultPageSize = 100) {
  const column = api.getColumnState()
  return {
    filter: api.getFilterModel() || {},
    sort: column.filter((item: any) => item.sort)
      .sort((a: any, b: any) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))
      .map((item: any) => ({ colId: item.colId, sort: item.sort })),
    column,
    quick_filter: search,
    page_size: api.paginationGetPageSize() || defaultPageSize,
  }
}

export function restoreTableState(api: any, state: any, remote: boolean) {
  if (state.column?.length) api.applyColumnState({ state: state.column, applyOrder: true })
  if (Array.isArray(state.sort)) {
    api.applyColumnState({
      state: state.sort.map((item: any, index: number) => ({ ...item, sortIndex: index })),
      defaultState: { sort: null, sortIndex: null },
    })
  }
  api.setFilterModel(state.filter || {})
  if (!remote) api.setGridOption('quickFilterText', state.quick_filter || '')
  if (state.page_size) api.setGridOption('paginationPageSize', state.page_size)
  api.paginationGoToFirstPage()
}

/** Schema-shown relationship columns take precedence over an older saved view. */
export function showSchemaColumns(state: any, columnIds: string[]): any {
  if (!state?.column?.length || !columnIds.length) return state
  const shown = new Set(columnIds)
  return {
    ...state,
    column: state.column.map((column: any) =>
      shown.has(column.colId) ? { ...column, hide: false } : column),
  }
}
