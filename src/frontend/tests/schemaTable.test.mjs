import assert from 'node:assert/strict'
import test from 'node:test'

import { fieldText, fieldValue, isBlank, listFields, opensDetail } from '../src/schemaTable.ts'

const field = (key, extra = {}) => ({ key, label: key, type: 'text', storage: 'column', list_visible: true, ...extra })

test('a list keeps the schema order and drops hidden and omitted fields', () => {
  const fields = [
    field('software_name'), field('device_unique_id'), field('notes', { list_visible: false }),
    field('output'), field('lab', { storage: 'data', visible: false }), field('artifacts'),
  ]
  assert.deepEqual(listFields(fields, ['device_unique_id']).map((item) => item.key), ['software_name', 'output', 'artifacts'])
})

test('custom fields read from misc data and built-ins from the row', () => {
  const row = { tag: 'adhoc', misc_data: { lab: 'East' } }
  assert.equal(fieldValue(row, field('tag')), 'adhoc')
  assert.equal(fieldValue(row, field('lab', { storage: 'data' })), 'East')
  assert.equal(fieldValue(null, field('tag')), undefined)
})

test('cell text is blank for empty values and readable otherwise', () => {
  assert.equal(fieldText(null, field('a')), '')
  assert.equal(fieldText([], field('a')), '')
  assert.equal(fieldText(true, field('a', { type: 'boolean' })), 'Yes')
  assert.equal(fieldText(false, field('a', { type: 'boolean' })), 'No')
  assert.equal(fieldText('2026-10-17', field('a', { type: 'date' }), (v) => `day ${v}`), 'day 2026-10-17')
  assert.equal(fieldText({ a: 1 }, field('a')), '{"a":1}')
  assert.equal(fieldText(0, field('a', { type: 'number' })), '0')
})

test('long values open in a dialog', () => {
  assert.equal(opensDetail(field('notes', { type: 'textarea' })), true)
  assert.equal(opensDetail(field('misc_data', { type: 'json' })), true)
  assert.equal(opensDetail(field('tag', { type: 'select' })), false)
  assert.equal(isBlank({}), true)
  assert.equal(isBlank(0), false)
})
