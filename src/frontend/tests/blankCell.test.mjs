import assert from 'node:assert/strict'
import test from 'node:test'

import { BLANK_CLASS, BLANK_TEXT, isBlankRender, isBlankValue, withBlankPlaceholder } from '../src/blankCell.ts'

test('nothing, empty text and empty containers are blank; zero and false are not', () => {
  for (const value of [null, undefined, '', '   ', [], {}]) assert.equal(isBlankValue(value), true)
  for (const value of [0, false, 'x', [1], { a: 1 }]) assert.equal(isBlankValue(value), false)
})

test('a plain column says N/A for a blank and leaves values alone', () => {
  const col = withBlankPlaceholder({ field: 'make' })
  assert.equal(col.valueFormatter({ value: '' }), BLANK_TEXT)
  assert.equal(col.valueFormatter({ value: null }), BLANK_TEXT)
  assert.equal(col.valueFormatter({ value: 'Acme' }), 'Acme')
  assert.equal(col.valueFormatter({ value: 0 }), '0')
  assert.equal(col.cellClassRules[BLANK_CLASS]({ value: '' }), true)
  assert.equal(col.cellClassRules[BLANK_CLASS]({ value: 'Acme' }), false)
})

test("a column's own wording for an empty value wins over the placeholder", () => {
  const col = withBlankPlaceholder({ field: 'version', valueFormatter: (p) => p.value || '(unversioned)' })
  assert.equal(col.valueFormatter({ value: '' }), '(unversioned)')
  assert.equal(col.cellClassRules[BLANK_CLASS]({ value: '' }), false)
})

test('a formatter that returns nothing is filled in', () => {
  const col = withBlankPlaceholder({ field: 'last_login_at', valueFormatter: (p) => (p.value ? 'then' : '') })
  assert.equal(col.valueFormatter({ value: null }), BLANK_TEXT)
})

test('existing cell class rules are kept', () => {
  const overdue = () => true
  const col = withBlankPlaceholder({ field: 'due', cellClassRules: { 'cell-overdue': overdue } })
  assert.equal(col.cellClassRules['cell-overdue'], overdue)
  assert.ok(col.cellClassRules[BLANK_CLASS])
})

test('opting out removes our key and changes nothing else', () => {
  const render = () => null
  const col = withBlankPlaceholder({ field: 'is_online', blankPlaceholder: false, cellRenderer: render })
  assert.equal('blankPlaceholder' in col, false)
  assert.equal(col.cellRenderer, render)
})

test('boolean and named-renderer columns keep their own empty state', () => {
  assert.equal(withBlankPlaceholder({ field: 'a', cellDataType: 'boolean' }).valueFormatter, undefined)
  assert.equal(withBlankPlaceholder({ field: 'b', cellRenderer: 'agCheckboxCellRenderer' }).valueFormatter, undefined)
})

// Just enough of a DOM node for the checks: text, plus whether it holds
// anything visual.
function node(text, visual = false) {
  return { textContent: text, matches: () => false, querySelector: () => (visual ? {} : null) }
}

test('a renderer output is blank only when there is nothing to see', () => {
  assert.equal(isBlankRender(null), true)
  assert.equal(isBlankRender(''), true)
  assert.equal(isBlankRender(node('')), true)
  assert.equal(isBlankRender(node('  ')), true)
  assert.equal(isBlankRender(node('View Notes')), false)
  assert.equal(isBlankRender(node('', true)), false)
  assert.equal(isBlankRender({ textContent: '', matches: () => true, querySelector: () => null }), false)
  assert.equal(isBlankRender('text'), false)
})

test('a row still loading draws a loading bar in every column, never N/A', () => {
  const loadingRow = { node: {}, data: undefined, value: undefined }
  const realRow = { node: {}, data: { make: '' }, value: '' }
  const cols = [
    withBlankPlaceholder({ field: 'make' }),
    withBlankPlaceholder({ field: 'type', valueFormatter: (p) => p.value || 'Uncategorized' }),
    withBlankPlaceholder({ field: 'tag', cellRenderer: () => 'x' }),
    withBlankPlaceholder({ field: 'ok', cellRenderer: 'agCheckboxCellRenderer' }),
    withBlankPlaceholder({ field: 'dot', blankPlaceholder: false }),
  ]
  for (const col of cols) {
    assert.ok(col.cellRendererSelector(loadingRow)?.component, col.field)
    assert.equal(col.cellRendererSelector(realRow), undefined, col.field)
  }
  assert.equal(cols[0].valueFormatter(loadingRow), '')
  assert.equal(cols[0].cellClassRules[BLANK_CLASS](loadingRow), false)
  assert.equal(cols[0].valueFormatter(realRow), BLANK_TEXT)
  assert.equal('blankPlaceholder' in cols[4], false)
})
