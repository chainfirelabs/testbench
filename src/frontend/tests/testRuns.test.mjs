import assert from 'node:assert/strict'
import test from 'node:test'

import { runDuration, runPayload, runTimeLabel } from '../src/testRuns.ts'

test('a new run sends blanks as null', () => {
  assert.deepEqual(runPayload({ name: ' Nightly #1 ', external_id: '  ', url: '', notes: 'n' }), {
    name: 'Nightly #1', external_id: null, url: null, notes: 'n',
  })
})

test('run times are read as UTC', () => {
  assert.equal(runTimeLabel(null), '')
  assert.equal(runTimeLabel('2026-10-21T06:00:00'), new Date('2026-10-21T06:00:00Z').toLocaleString())
  assert.equal(runTimeLabel('2026-10-21T06:00:00+00:00'), new Date('2026-10-21T06:00:00Z').toLocaleString())
  assert.equal(runTimeLabel('not a time'), 'not a time')
})

test('a finished run says how long it took', () => {
  assert.equal(runDuration('2026-10-21T02:00:00', '2026-10-21T02:41:05'), '41m 5s')
  assert.equal(runDuration('2026-10-21T02:00:00', '2026-10-21T04:03:00'), '2h 3m')
  assert.equal(runDuration('2026-10-21T02:00:00', '2026-10-21T02:00:09'), '9s')
  assert.equal(runDuration('2026-10-21T02:00:00', null), '')
})

test('a typed run name joins the newest run of that name, or starts one', async () => {
  const { matchRun, newTestRunFields } = await import('../src/testRuns.ts')
  const runs = [
    { id: 'new', name: 'Nightly', started_at: '2026-10-21T06:00:00' },
    { id: 'old', name: 'Nightly', started_at: '2026-10-20T06:00:00' },
    { id: 'other', name: 'Smoke' },
  ]
  assert.equal(matchRun(' nightly ', runs)?.id, 'new')
  assert.equal(matchRun('Release', runs), null)
  assert.equal(matchRun('', runs), null)

  const opts = { storageEnabled: true, maxBytes: 1024, noStorage: 'no storage' }
  const [run, files] = newTestRunFields(runs, { test_run_name: 'Release' }, opts)
  assert.deepEqual(run.options.map((o) => o.value), ['Nightly', 'Smoke'])
  assert.match(run.hint, /new test run named "Release"/)
  assert.match(newTestRunFields(runs, { test_run_name: 'nightly' }, opts)[0].hint, /^Joins the run started/)
  assert.equal(files.multiple, true)
  assert.equal(files.disabled({ test_run_name: 'Release' }), false)
  assert.equal(files.disabled({ test_run_name: ' ' }), true, 'no run, nowhere to share files')
  assert.equal(newTestRunFields(runs, {}, { ...opts, storageEnabled: false })[1].hint, 'no storage')
})

test('on a run page the run is fixed and its files can be added', async () => {
  const { newTestRunFields } = await import('../src/testRuns.ts')
  const fixedRun = { id: 'r', name: 'Nightly' }
  const [run, files] = newTestRunFields([fixedRun], {}, { fixedRun, storageEnabled: true, noStorage: '' })
  assert.equal(run.disabled, true)
  assert.equal(files.disabled({}), false)
})
