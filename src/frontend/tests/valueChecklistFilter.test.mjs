import assert from 'node:assert/strict'
import test from 'node:test'

import { ValueChecklistFilter } from '../src/valueChecklistFilter.ts'

/*
 * A DOM small enough to fit in a test and honest enough to drive the filter:
 * elements with children, class names, text and event listeners, plus the two
 * input properties the checklist reads back.
 */
function installDom() {
  class El {
    constructor(tag) {
      this.tag = tag
      this.children = []
      this.listeners = {}
      this.className = ''
      this.type = ''
      this.value = ''
      this.checked = false
      this.disabled = false
      this.textContent = ''
      this.hidden = false
    }
    appendChild(child) { this.children.push(child); return child }
    append(...items) { this.children.push(...items) }
    replaceChildren(...items) { this.children = items }
    setAttribute() {}
    focus() {}
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn) }
    emit(type) { for (const fn of this.listeners[type] || []) fn() }
    /** Every descendant, so a test can find the checkboxes without walking. */
    *walk() {
      for (const child of this.children) {
        yield child
        if (child.walk) yield* child.walk()
      }
    }
  }
  globalThis.document = { createElement: (tag) => new El(tag) }
  return El
}

installDom()

/** The parts of AG Grid's filter params this filter actually touches. */
function harness({ rows = [], provider, leafRows, formatter } = {}) {
  const changes = []
  const filter = new ValueChecklistFilter()
  filter.init({
    column: { getColId: () => 'make', getColDef: () => ({ valueFormatter: formatter }) },
    colDef: { filterParams: provider ? { valuesProvider: provider } : {} },
    getValue: (node) => node.data.make,
    api: {
      // Undefined on an infinite row model, which is the case these tests care
      // about: without a provider the loaded window is all there is.
      forEachLeafNode: leafRows
        ? (fn) => leafRows.forEach((make) => fn({ data: { make } }))
        : () => {},
      forEachNode: (fn) => rows.forEach((make) => fn({ data: { make } })),
    },
    filterChangedCallback: () => changes.push(filter.getModel()),
  })
  return { filter, changes }
}

/** The option rows currently rendered, in order. */
function shown(filter) {
  const list = filter.getGui().children.find((child) => child.className === 'value-checklist-options')
  return list.children.map((row) => {
    const [checkbox, text] = row.children
    return { label: text.textContent, checked: checkbox.checked, checkbox }
  })
}

function statusText(filter) {
  return filter.getGui().children.find((c) => c.className === 'value-checklist-status').textContent
}

function typeSearch(filter, text) {
  const input = filter.getGui().children.find((child) => child.type === 'search')
  input.value = text
  input.emit('input')
}

function clickAction(filter, label) {
  const actions = filter.getGui().children.find((c) => c.className === 'value-checklist-actions')
  actions.children.find((button) => button.textContent === label).emit('click')
}

test('offers the values the loaded rows carry, sorted', () => {
  const { filter } = harness({ rows: ['Juniper', 'Cisco', 'Cisco', 'Dell'] })
  assert.deepEqual(shown(filter).map((row) => row.label), ['Cisco', 'Dell', 'Juniper'])
  assert.ok(shown(filter).every((row) => row.checked))
})

test('a provider supplies values the loaded page never showed', async () => {
  const { filter } = harness({
    rows: ['Cisco'],
    provider: async () => ['Cisco', 'Dell', 'Juniper', 'MikroTik'],
  })
  // The server's answer lands asynchronously; the panel says so meanwhile.
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.deepEqual(
    shown(filter).map((row) => row.label),
    ['Cisco', 'Dell', 'Juniper', 'MikroTik'],
  )
  assert.equal(statusText(filter), '')
})

test('a provider that fails leaves the loaded values usable, and says why', async () => {
  const { filter } = harness({
    rows: ['Cisco', 'Dell'],
    provider: async () => { throw new Error('nope') },
  })
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.deepEqual(shown(filter).map((row) => row.label), ['Cisco', 'Dell'])
  assert.match(statusText(filter), /rows loaded so far/)
})

test('a column with nothing to offer says so rather than showing a blank panel', () => {
  const { filter } = harness({ rows: [] })
  assert.deepEqual(shown(filter), [])
  assert.match(statusText(filter), /No values/)
})

test('unticking a value excludes it, and the model carries the raw value', () => {
  const { filter, changes } = harness({ rows: ['Cisco', 'Dell'] })
  const dell = shown(filter).find((row) => row.label === 'Dell')
  dell.checkbox.checked = false
  dell.checkbox.emit('change')

  assert.deepEqual(filter.getModel(), { filterType: 'valueChecklist', excluded: ['Dell'] })
  assert.equal(filter.isFilterActive(), true)
  assert.equal(changes.length, 1)
  assert.equal(filter.doesFilterPass({ node: { data: { make: 'Dell' } } }), false)
  assert.equal(filter.doesFilterPass({ node: { data: { make: 'Cisco' } } }), true)
})

test('search narrows the list without dropping what is ticked', () => {
  const { filter } = harness({ rows: ['Cisco', 'Dell', 'Juniper'] })
  typeSearch(filter, 'ci')
  assert.deepEqual(shown(filter).map((row) => row.label), ['Cisco'])
  typeSearch(filter, '')
  assert.deepEqual(shown(filter).map((row) => row.label), ['Cisco', 'Dell', 'Juniper'])
})

test('Clear applies to what the search box is showing, not the whole column', () => {
  const { filter } = harness({ rows: ['Cisco', 'Dell', 'Juniper'] })
  typeSearch(filter, 'de')
  clickAction(filter, 'Clear')
  typeSearch(filter, '')

  // Only "Dell" matched "de"; the other two are untouched.
  assert.deepEqual(filter.getModel(), { filterType: 'valueChecklist', excluded: ['Dell'] })
  assert.deepEqual(
    shown(filter).map((row) => [row.label, row.checked]),
    [['Cisco', true], ['Dell', false], ['Juniper', true]],
  )
})

test('Select all puts back only what the search box is showing', () => {
  const { filter } = harness({ rows: ['Cisco', 'Dell', 'Juniper'] })
  clickAction(filter, 'Clear')
  typeSearch(filter, 'dell')
  clickAction(filter, 'Select all')
  typeSearch(filter, '')

  assert.deepEqual(
    shown(filter).map((row) => [row.label, row.checked]),
    [['Cisco', false], ['Dell', true], ['Juniper', false]],
  )
})

test('a blank is a value like any other, and is labelled as one', () => {
  const { filter } = harness({ rows: ['Cisco', '', null] })
  const labels = shown(filter).map((row) => row.label)
  assert.deepEqual(labels, ['(Blanks)', 'Cisco'])

  const blank = shown(filter)[0]
  blank.checkbox.checked = false
  blank.checkbox.emit('change')
  assert.equal(filter.doesFilterPass({ node: { data: { make: null } } }), false)
  assert.equal(filter.doesFilterPass({ node: { data: { make: '' } } }), false)
})

test('values are listed under the label the grid shows for them', () => {
  const { filter } = harness({
    rows: ['checked_out', 'available'],
    formatter: ({ value }) => ({ checked_out: 'Checked Out', available: 'Available' })[value],
  })
  assert.deepEqual(shown(filter).map((row) => row.label), ['Available', 'Checked Out'])
})

test('a client-side grid is read from the whole row model, not the rendered page', () => {
  const { filter } = harness({ rows: ['Cisco'], leafRows: ['Cisco', 'Dell', 'Juniper'] })
  assert.deepEqual(shown(filter).map((row) => row.label), ['Cisco', 'Dell', 'Juniper'])
})

test('a saved view restores the values it had unticked', () => {
  const { filter } = harness({ rows: ['Cisco', 'Dell'] })
  filter.setModel({ filterType: 'valueChecklist', excluded: ['Dell'] })
  assert.deepEqual(
    shown(filter).map((row) => [row.label, row.checked]),
    [['Cisco', true], ['Dell', false]],
  )
  filter.setModel(null)
  assert.equal(filter.isFilterActive(), false)
})

// ---------- a filter must always be able to undo itself ----------

test('a restored filter offers what it is excluding, with nothing else to go on', () => {
  // The state that used to be a dead end: a column whose values come from the
  // rows (no provider, or one the page lists in `skip`), filtered narrow
  // enough to leave none, and then rebuilt. The panel offered an empty list
  // while still hiding every row, and there was no checkbox to untick.
  const { filter } = harness({ rows: [] })
  filter.setModel({ filterType: 'valueChecklist', excluded: ['Backup-Restore', 'NetGuard'] })

  assert.deepEqual(shown(filter).map((o) => o.label), ['Backup-Restore', 'NetGuard'])
  assert.deepEqual(shown(filter).map((o) => o.checked), [false, false])
  assert.equal(filter.isFilterActive(), true)
})

test('and unticking one of them lets its rows back', () => {
  const { filter, changes } = harness({ rows: [] })
  filter.setModel({ filterType: 'valueChecklist', excluded: ['Backup-Restore', 'NetGuard'] })

  const option = shown(filter).find((o) => o.label === 'NetGuard')
  option.checkbox.checked = true
  option.checkbox.emit('change')

  assert.deepEqual(changes.at(-1), { filterType: 'valueChecklist', excluded: ['Backup-Restore'] })
})

test('the blank entry can be let back too', () => {
  const { filter } = harness({ rows: [] })
  filter.setModel({ filterType: 'valueChecklist', excluded: [null] })
  assert.deepEqual(shown(filter).map((o) => o.label), ['(Blanks)'])
})

test('restoring a filter does not duplicate values the rows already supplied', () => {
  const { filter } = harness({ rows: ['Cisco', 'Juniper'] })
  filter.setModel({ filterType: 'valueChecklist', excluded: ['Cisco'] })
  assert.deepEqual(shown(filter).map((o) => o.label), ['Cisco', 'Juniper'])
  assert.deepEqual(shown(filter).map((o) => o.checked), [false, true])
})

// ---------- the model sends the shorter side ----------

test('unticking a few sends those few as exclusions', () => {
  const { filter, changes } = harness({ rows: ['Cisco', 'Juniper', 'Arista', 'Dell'] })
  const dell = shown(filter).find((o) => o.label === 'Dell')
  dell.checkbox.checked = false
  dell.checkbox.emit('change')
  assert.deepEqual(changes.at(-1), { filterType: 'valueChecklist', excluded: ['Dell'] })
})

test('clearing and ticking one sends that one as an inclusion', () => {
  // The case that broke: excluding the other 499 of 500 is sixteen kilobytes
  // of URL, which the proxy rejects long before the API sees it.
  const { filter, changes } = harness({ rows: ['Cisco', 'Juniper', 'Arista', 'Dell'] })
  clickAction(filter, 'Clear')
  const cisco = shown(filter).find((o) => o.label === 'Cisco')
  cisco.checkbox.checked = true
  cisco.checkbox.emit('change')
  assert.deepEqual(changes.at(-1), { filterType: 'valueChecklist', included: ['Cisco'] })
})

test('the shorter side wins whichever it is', () => {
  const values = Array.from({ length: 20 }, (_, i) => `v${i}`)
  const { filter, changes } = harness({ rows: values })
  clickAction(filter, 'Clear')
  // Tick three back: three included beats seventeen excluded.
  for (const label of ['v1', 'v2', 'v3']) {
    const option = shown(filter).find((o) => o.label === label)
    option.checkbox.checked = true
    option.checkbox.emit('change')
  }
  const model = changes.at(-1)
  assert.deepEqual(model.included, ['v1', 'v2', 'v3'])
  assert.equal(model.excluded, undefined)
})

test('an inclusion model restores as the same selection', () => {
  const { filter } = harness({ rows: ['Cisco', 'Juniper', 'Arista'] })
  filter.setModel({ filterType: 'valueChecklist', included: ['Cisco'] })
  assert.deepEqual(
    shown(filter).map((o) => [o.label, o.checked]),
    [['Arista', false], ['Cisco', true], ['Juniper', false]],
  )
  assert.equal(filter.isFilterActive(), true)
})

test('an inclusion naming a value the rows never showed still restores', () => {
  const { filter } = harness({ rows: [] })
  filter.setModel({ filterType: 'valueChecklist', included: ['Backup-Restore'] })
  assert.deepEqual(shown(filter).map((o) => [o.label, o.checked]), [['Backup-Restore', true]])
})
