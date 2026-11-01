import assert from 'node:assert/strict'
import test from 'node:test'
import { applyMiscData } from '../src/miscData.ts'

test('removed misc keys become deletions while other fields survive', () => {
  const data = { notes: 'Keep' }
  applyMiscData(data, '{"keep":"updated","new":false}', { remove: 1, keep: 'old' })
  assert.deepEqual(data, { notes: 'Keep', remove: null, keep: 'updated', new: false })
})

test('clearing misc data deletes every previous misc key', () => {
  for (const value of ['{}', '', {}]) {
    const data = {}
    applyMiscData(data, value, { first: 1, second: { nested: true } })
    assert.deepEqual(data, { first: null, second: null })
  }
})

test('fields exposed after a layout change retain their form values', () => {
  const data = { promoted: 'edited' }
  applyMiscData(data, '{}', { promoted: 'old', removed: 'old' })
  assert.deepEqual(data, { promoted: 'edited', removed: null })
})

test('misc values retain replacements, explicit deletions and falsy values', () => {
  const data = { shared: 'field' }
  applyMiscData(data, { shared: 'misc', removed: null, zero: 0 }, { removed: 1 })
  assert.deepEqual(data, { shared: 'misc', removed: null, zero: 0 })
})

test('invalid JSON and non-object misc data are rejected before mutation', () => {
  for (const value of ['broken', 'null', '[]', '3', null, []]) {
    const data = { notes: 'Keep' }
    assert.throws(() => applyMiscData(data, value, { remove: 1 }))
    assert.deepEqual(data, { notes: 'Keep' })
  }
})
