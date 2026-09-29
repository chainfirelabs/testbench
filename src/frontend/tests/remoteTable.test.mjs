import assert from 'node:assert/strict'
import test from 'node:test'

import { remoteTableParams } from '../src/remoteTable.ts'

test('translates a remote grid window, search, sort, and filters to API parameters', () => {
  const params = remoteTableParams({
    startRow: 200,
    endRow: 300,
    search: 'router 20',
    sortModel: [{ colId: 'unique_id', sort: 'desc' }],
    filterModel: { make: { filterType: 'text', type: 'contains', filter: 'Cisco' } },
  }, { device_type: 'router', overdue: false })

  assert.equal(params.get('page'), '3')
  assert.equal(params.get('page_size'), '100')
  assert.equal(params.get('search'), 'router 20')
  assert.equal(params.get('sort'), 'unique_id')
  assert.equal(params.get('order'), 'desc')
  assert.equal(params.get('make'), 'Cisco')
  assert.equal(params.get('device_type'), 'router')
  assert.equal(params.has('overdue'), false)
})

test('translates checklist exclusions without losing multiple values', () => {
  const params = remoteTableParams({
    startRow: 0,
    endRow: 100,
    search: '',
    sortModel: [],
    filterModel: {
      status: { filterType: 'valueChecklist', excluded: ['retired', 'repair'] },
    },
  })

  assert.deepEqual(JSON.parse(params.get('exclude__status')), ['retired', 'repair'])
  assert.equal(params.has('status'), false)
})

test('a checklist filter travels as whichever side it carries', () => {
  const excluding = remoteTableParams(
    { startRow: 0, endRow: 50, search: '', sortModel: [], filterModel: {
      make: { filterType: 'valueChecklist', excluded: ['Dell'] } } }, {})
  assert.equal(excluding.get('exclude__make'), '["Dell"]')
  assert.equal(excluding.get('include__make'), null)

  const including = remoteTableParams(
    { startRow: 0, endRow: 50, search: '', sortModel: [], filterModel: {
      make: { filterType: 'valueChecklist', included: ['Cisco'] } } }, {})
  assert.equal(including.get('include__make'), '["Cisco"]')
  assert.equal(including.get('exclude__make'), null)
})

test('an inclusion of nothing is still sent, because it means no rows', () => {
  // Unlike an empty exclusion, which means "filter off".
  const params = remoteTableParams(
    { startRow: 0, endRow: 50, search: '', sortModel: [], filterModel: {
      make: { filterType: 'valueChecklist', included: [] } } }, {})
  assert.equal(params.get('include__make'), '[]')
})

const { remoteTableQuery } = await import('../src/remoteTable.ts')

test('large checklists use a body without changing their meaning or page controls', () => {
  const selection = Array.from({ length: 1000 }, (_, i) => `vendor ${i}`)
  const params = new URLSearchParams({ page: '3', page_size: '100', search: 'router', include__make: JSON.stringify(selection), exclude__online_status: '[null]' })
  const result = remoteTableQuery('/devices', params)
  assert.equal(result.path, '/devices/query?page=3&page_size=100&search=router')
  assert.equal(result.options.method, 'POST')
  assert.deepEqual(JSON.parse(JSON.parse(result.options.body).include__make), selection)
  assert.equal(JSON.parse(result.options.body).exclude__online_status, '[null]')
  assert.equal(params.get('include__make'), JSON.stringify(selection))
})

test('ordinary selections keep the existing GET contract', () => {
  const params = new URLSearchParams({ include__online_status: '[true,false]' })
  assert.deepEqual(remoteTableQuery('/devices', params), { path: `/devices?${params}` })
})
