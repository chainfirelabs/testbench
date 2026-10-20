import assert from 'node:assert/strict'
import test from 'node:test'

import { validationMessage } from '../src/api/client.ts'

test('a validation error names the field it is about', () => {
  assert.equal(validationMessage({ loc: ['body', 'device_id'], msg: 'Field required' }), 'device_id: Field required')
  assert.equal(validationMessage({ loc: ['query', 'page_size'], msg: 'too big' }), 'page_size: too big')
  assert.equal(validationMessage({ loc: ['body', 'misc_data', 'lab'], msg: 'bad' }), 'misc_data.lab: bad')
  assert.equal(validationMessage({ loc: ['body'], msg: 'Invalid JSON' }), 'Invalid JSON')
  assert.equal(validationMessage({ msg: 'Field required' }), 'Field required')
})
