import assert from 'node:assert/strict'
import test from 'node:test'

import { MAX_VISIBLE_OPTIONS, matchOptions, visibleOptions } from '../src/suggestOptions.ts'

const fleet = Array.from({ length: 2000 }, (_, i) => ({
  value: `dev-${String(i).padStart(4, '0')}`,
  label: `dev-${String(i).padStart(4, '0')}`,
}))

test('an empty query matches everything but renders only a screenful', () => {
  // The case that made the dialog freeze: the box is empty when it opens, so
  // every device matched and every device became a DOM node.
  assert.equal(matchOptions(fleet, '').length, 2000)
  const { visible, hidden } = visibleOptions(fleet, '')
  assert.equal(visible.length, MAX_VISIBLE_OPTIONS)
  assert.equal(hidden, 2000 - MAX_VISIBLE_OPTIONS)
})

test('the count is what is held back, not a flag', () => {
  // "and 4 more" and "and 1,900 more" are different messages; the second says
  // typing is not optional.
  assert.equal(visibleOptions(fleet.slice(0, 104), '').hidden, 4)
  assert.equal(visibleOptions(fleet, '').hidden, 1900)
})

test('nothing is hidden once the query narrows enough', () => {
  const { visible, hidden } = visibleOptions(fleet, 'dev-0007')
  assert.equal(hidden, 0)
  assert.deepEqual(visible.map((o) => o.value), ['dev-0007'])
})

test('matching is a case-insensitive substring', () => {
  const options = [{ value: 'Rack R2' }, { value: 'rack r20' }, { value: 'Shelf 9' }]
  assert.deepEqual(matchOptions(options, 'r2').map((o) => o.value), ['Rack R2', 'rack r20'])
  assert.deepEqual(matchOptions(options, 'R2').map((o) => o.value), ['Rack R2', 'rack r20'])
  assert.deepEqual(matchOptions(options, '  shelf  ').map((o) => o.value), ['Shelf 9'])
})

test('the visible options are the first of the matches, in order', () => {
  const { visible } = visibleOptions(fleet, '', 3)
  assert.deepEqual(visible.map((o) => o.value), ['dev-0000', 'dev-0001', 'dev-0002'])
})

test('a non-string value is matched by its text', () => {
  const options = [{ value: 42 }, { value: 420 }, { value: 7 }]
  assert.deepEqual(matchOptions(options, '42').map((o) => o.value), [42, 420])
})

test('no matches means nothing visible and nothing hidden', () => {
  assert.deepEqual(visibleOptions(fleet, 'nothing-matches-this'), { visible: [], hidden: 0 })
})

test('an empty option list is handled', () => {
  assert.deepEqual(visibleOptions([], ''), { visible: [], hidden: 0 })
  assert.deepEqual(visibleOptions([], 'q'), { visible: [], hidden: 0 })
})
