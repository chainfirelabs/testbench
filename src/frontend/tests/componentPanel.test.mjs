import assert from 'node:assert/strict'
import test from 'node:test'
import { componentSummaryLabel } from '../src/componentPanel.ts'

test('component summaries show one name or only the distinct-name count', () => {
  assert.equal(componentSummaryLabel(['Outlook']), 'Outlook')
  assert.equal(componentSummaryLabel(['Outlook', 'outlook']), 'outlook')
  assert.equal(componentSummaryLabel(['Outlook', 'Word']), '2 components')
  assert.equal(componentSummaryLabel(['Outlook', 'outlook', 'Word']), '2 components')
})
