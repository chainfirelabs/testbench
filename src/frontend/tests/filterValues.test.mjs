import assert from 'node:assert/strict'
import test from 'node:test'

// The decision only — which source answers for a column — with the fetcher
// supplied, so the test can also assert that the server was not asked.
import { resolveFilterValues } from '../src/filterValues.ts'

function recorder(values = ['from-server']) {
  const calls = []
  return {
    calls,
    fetch: async (entity, field) => {
      calls.push([entity, field])
      return values
    },
  }
}

test('a value list the page already has is used without asking the server', async () => {
  const server = recorder()
  const source = { entity: 'devices', local: (colId) => (colId === 'status' ? ['available', 'broken'] : undefined) }

  assert.deepEqual(await resolveFilterValues(source, 'status', server.fetch), ['available', 'broken'])
  assert.deepEqual(server.calls, [])
})

test('a column the page cannot answer falls through to the server', async () => {
  const server = recorder(['Rack 4', 'Rack 5'])
  const source = { entity: 'devices', local: () => undefined }

  assert.deepEqual(await resolveFilterValues(source, 'location', server.fetch), ['Rack 4', 'Rack 5'])
  assert.deepEqual(server.calls, [['devices', 'location']])
})

test('a skipped column costs no request and offers nothing', async () => {
  const server = recorder()
  const source = { entity: 'devices', skip: ['misc_data'] }

  assert.deepEqual(await resolveFilterValues(source, 'misc_data', server.fetch), [])
  assert.deepEqual(server.calls, [])
})

test('with no collection behind it, nothing is fetched', async () => {
  const server = recorder()

  assert.deepEqual(await resolveFilterValues({}, 'anything', server.fetch), [])
  assert.deepEqual(server.calls, [])
})

test('a local answer of false or zero still counts as an answer', async () => {
  const server = recorder()
  const source = { entity: 'devices', local: (colId) => (colId === 'online' ? [true, false] : undefined) }

  assert.deepEqual(await resolveFilterValues(source, 'online', server.fetch), [true, false])
  assert.deepEqual(server.calls, [])
})
