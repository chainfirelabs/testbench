import assert from 'node:assert/strict'
import test from 'node:test'
import { captureTableState, restoreTableState } from '../src/tableState.ts'

test('saved remote search comes from the input, not the unused grid quick filter', () => {
  const state = captureTableState({
    getColumnState: () => [{ colId: 'make', sort: 'asc', sortIndex: 0 }],
    getFilterModel: () => ({ online_status: { filterType: 'valueChecklist', included: [true, false] } }),
    paginationGetPageSize: () => 250,
    getQuickFilter: () => 'stale search',
  }, 'current search')
  assert.equal(state.quick_filter, 'current search')
  assert.equal(state.page_size, 250)
  const models = [], options = [], columns = []
  let firstPage = false
  restoreTableState({
    applyColumnState: s => columns.push(s),
    setFilterModel: s => models.push(s),
    setGridOption: (...args) => options.push(args),
    paginationGoToFirstPage: () => { firstPage = true },
  }, state, true)
  assert.deepEqual(models, [state.filter])
  assert.deepEqual(options, [['paginationPageSize', 250]])
  assert.equal(firstPage, true)
  assert.deepEqual(columns[1].state, [{ colId: 'make', sort: 'asc', sortIndex: 0 }])
})

test('a view without filters clears the previous view', () => {
  let model
  restoreTableState({
    setFilterModel: value => { model = value },
    setGridOption: () => {}, paginationGoToFirstPage: () => {},
  }, {}, false)
  assert.deepEqual(model, {})
})
