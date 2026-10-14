import assert from 'node:assert/strict'
import test from 'node:test'
import {
  componentSearchLabel, deviceSearchUrl, softwareSearchUrl, testSearchUrl,
} from '../src/globalSearch.ts'

test('an older component hit opens the exact software version', () => {
  assert.equal(softwareSearchUrl({ name: 'Office Suite', version: '1.2', is_latest: false }),
    '/software/Office%20Suite/1.2')
  assert.equal(softwareSearchUrl({ name: 'Office Suite', version: '2', is_latest: true }),
    '/software/Office%20Suite')
  assert.equal(softwareSearchUrl({ id: 'old-id', name: 'Office Suite', version: '', is_latest: false }),
    '/software/old-id')
  assert.equal(componentSearchLabel({ name: 'Outlook', version: '16.2' }), 'Outlook 16.2')
})

test('a test hit opens its device test history and identifies the test', () => {
  assert.equal(testSearchUrl({ device_unique_id: 'router/7', id: 'test 42' }),
    '/devices/router%2F7?tab=tests&test=test%2042')
  assert.equal(deviceSearchUrl({ unique_id: 'router/7', tested_components: [{ name: 'Outlook' }] }),
    '/devices/router%2F7?tab=tests')
  assert.equal(deviceSearchUrl({ unique_id: 'router/7', tested_components: [] }),
    '/devices/router%2F7')
})
