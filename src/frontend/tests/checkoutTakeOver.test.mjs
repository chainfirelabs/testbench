import assert from 'node:assert/strict'
import test from 'node:test'

import { checkoutConflict, devicePatchPath, saveWithTakeOver, takeOverQuestion } from '../src/checkoutTakeOver.ts'

const conflict = (canTakeOver) => Object.assign(new Error('dut-1 is already checked out by alice.'), {
  status: 409,
  detail: {
    code: 'checked_out_by_other', message: 'dut-1 is already checked out by alice.', holder: 'alice',
    checkout_due: '2026-10-26', checkout_purpose: 'soak test', can_take_over: canTakeOver,
  },
})

test('only the checkout refusal counts as a conflict', () => {
  assert.equal(checkoutConflict(conflict(false)).holder, 'alice')
  assert.equal(checkoutConflict(Object.assign(new Error('x'), { status: 409, detail: 'Device exists' })), null)
  assert.equal(checkoutConflict(Object.assign(new Error('x'), { status: 422, detail: { code: 'checked_out_by_other' } })), null)
  assert.equal(checkoutConflict(null), null)
})

test('the question names the holder, the date and the purpose', () => {
  const question = takeOverQuestion('dut-1', conflict(true).detail)
  assert.match(question, /checked out by alice until 2026-10-26 for: soak test/)
  assert.match(question, /alice will be notified/)
})

test('a save that goes through is not asked about anything', async () => {
  let asked = false
  const result = await saveWithTakeOver(async () => ({ ok: true }), 'dut-1', () => (asked = true))
  assert.deepEqual(result, { ok: true })
  assert.equal(asked, false)
})

test('someone who may not take over gets the refusal as thrown', async () => {
  let asked = false
  await assert.rejects(
    saveWithTakeOver(async () => { throw conflict(false) }, 'dut-1', () => (asked = true)),
    /checked out by alice/,
  )
  assert.equal(asked, false)
})

test('an admin who agrees retries with the take-over flag', async () => {
  const sent = []
  const result = await saveWithTakeOver(async (takeOver) => {
    sent.push(takeOver)
    if (!takeOver) throw conflict(true)
    return { holder: 'admin' }
  }, 'dut-1', () => true)
  assert.deepEqual(sent, [false, true])
  assert.deepEqual(result, { holder: 'admin' })
})

test('an admin who declines saves nothing', async () => {
  const sent = []
  const result = await saveWithTakeOver(async (takeOver) => {
    sent.push(takeOver)
    throw conflict(true)
  }, 'dut-1', () => false)
  assert.deepEqual(sent, [false])
  assert.equal(result, null)
})

test('other errors pass straight through', async () => {
  await assert.rejects(
    saveWithTakeOver(async () => { throw Object.assign(new Error('Server error'), { status: 500 }) }, 'dut-1', () => true),
    /Server error/,
  )
})

test('the take-over flag goes on the query string', () => {
  assert.equal(devicePatchPath('abc', false), '/devices/abc')
  assert.equal(devicePatchPath('abc', true), '/devices/abc?take_over=true')
})
