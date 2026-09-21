import assert from 'node:assert/strict'
import test from 'node:test'

import { loadAllPages } from '../src/pagination.ts'

for (const total of [0, 1, 1000, 1030, 2001]) {
  test(`loads all ${total} page-numbered records`, async () => {
    const source = Array.from({ length: total }, (_, id) => ({ id }))
    const requested = []
    const result = await loadAllPages(async (page) => {
      requested.push(page)
      const start = (page - 1) * 1000
      return { items: source.slice(start, start + 1000), total }
    })

    assert.deepEqual(result, { items: source, total })
    assert.deepEqual(requested, Array.from(
      { length: Math.max(1, Math.ceil(total / 1000)) }, (_, index) => index + 1,
    ))
  })
}

test('does not silently return a partial collection when paging stops', async () => {
  await assert.rejects(loadAllPages(async (page) => ({
    items: page === 1 ? [{ id: 1 }] : [], total: 2,
  })), /changed while loading/)
})

// ---------- pages are fetched concurrently ----------

test('requests pages after the first without waiting for each other', async () => {
  // The first page has to land alone — until it does there is no total, and so
  // no way to know how many more there are. Everything after it should not be
  // a queue of round trips.
  let inFlight = 0
  let peak = 0
  const total = 5000
  const source = Array.from({ length: total }, (_, id) => ({ id }))

  await loadAllPages(async (page) => {
    inFlight++
    peak = Math.max(peak, inFlight)
    await new Promise((resolve) => setTimeout(resolve, 5))
    inFlight--
    const start = (page - 1) * 1000
    return { items: source.slice(start, start + 1000), total }
  })

  assert.equal(peak > 1, true, `expected overlapping requests, peak was ${peak}`)
})

test('reassembles pages in order however they arrive', async () => {
  // Later pages deliberately resolve first.
  const total = 4000
  const source = Array.from({ length: total }, (_, id) => ({ id }))
  const result = await loadAllPages(async (page) => {
    await new Promise((resolve) => setTimeout(resolve, (6 - page) * 10))
    const start = (page - 1) * 1000
    return { items: source.slice(start, start + 1000), total }
  })
  assert.deepEqual(result.items, source)
})

test('a page size the server capped is respected', async () => {
  // The caller asks for 1000 and the server returns 500; paging must follow
  // what came back, not what was asked for.
  const total = 1200
  const source = Array.from({ length: total }, (_, id) => ({ id }))
  const requested = []
  const result = await loadAllPages(async (page) => {
    requested.push(page)
    const start = (page - 1) * 500
    return { items: source.slice(start, start + 500), total }
  })
  assert.deepEqual(result.items, source)
  assert.deepEqual(requested, [1, 2, 3])
})

test('rows added while loading are kept rather than dropped', async () => {
  const result = await loadAllPages(async (page) => ({
    items: page === 1 ? [{ id: 1 }] : [{ id: 2 }],
    total: 2,
  }))
  assert.equal(result.items.length, 2)
})
