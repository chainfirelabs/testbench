import assert from 'node:assert/strict'
import test from 'node:test'

import { groupBundleComponents } from '../src/bundleComponentGroups.ts'

test('groups component versions by name while retaining every version record', () => {
  const components = [
    { id: 'excel-1', name: 'Excel', version: '1.0' },
    { id: 'word-1', name: 'Word', version: '2.0' },
    { id: 'excel-11', name: ' excel ', version: '1.1' },
    { id: 'excel-10', name: 'EXCEL', version: '1.10' },
    { id: 'word-empty', name: 'Word', version: '' },
  ]

  const groups = groupBundleComponents(components)
  assert.deepEqual(groups.map((group) => group.name), ['Excel', 'Word'])
  assert.deepEqual(groups[0].versions.map((component) => component.id), ['excel-10', 'excel-11', 'excel-1'])
  assert.deepEqual(groups[1].versions.map((component) => component.id), ['word-1', 'word-empty'])
  assert.deepEqual(components.map((component) => component.id), [
    'excel-1', 'word-1', 'excel-11', 'excel-10', 'word-empty',
  ])
})
