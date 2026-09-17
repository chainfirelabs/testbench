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
