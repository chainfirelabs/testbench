import assert from 'node:assert/strict'
import test from 'node:test'

import { fileSizeLabel, newTestFileFields } from '../src/testArtifacts.ts'

const catalog = [
  { key: 'outcome', label: 'Outcome', visible: true },
  { key: 'output', label: 'Run Log', visible: true },
  { key: 'artifacts', label: 'Artifacts', visible: true },
]
const storage = { storage_enabled: true, output_max_bytes: 1048576, artifact_max_bytes: 104857600 }

test('new tests offer output and artifacts as optional file fields', () => {
  const [output, artifacts] = newTestFileFields(storage, catalog)
  assert.equal(output.key, 'output')
  assert.equal(output.label, 'Run Log')
  assert.equal(output.type, 'file')
  assert.equal(output.multiple, undefined)
  assert.equal(output.maxBytes, 1048576)
  assert.equal(artifacts.multiple, true)
  assert.equal(artifacts.maxBytes, 104857600)
  for (const field of [output, artifacts]) {
    assert.ok(!field.required)
    assert.equal(field.disabled, false)
    assert.match(field.hint, /^Optional\./)
  }
})

test('without storage the fields stay on show, disabled, saying why', () => {
  for (const fields of [newTestFileFields({ ...storage, storage_enabled: false }, catalog), newTestFileFields(null, catalog)]) {
    assert.equal(fields.length, 2)
    assert.ok(fields.every((field) => field.disabled === true && /not configured/.test(field.hint)))
  }
})

test('a file field turned off in the schema is not offered', () => {
  const fields = newTestFileFields(storage, catalog.map((field) => field.key === 'output' ? { ...field, visible: false } : field))
  assert.deepEqual(fields.map((field) => field.key), ['artifacts'])
  assert.deepEqual(newTestFileFields(storage, [{ key: 'outcome', label: 'Outcome' }]), [])
})

test('sizes read in binary units', () => {
  assert.equal(fileSizeLabel(512), '512 B')
  assert.equal(fileSizeLabel(1536), '1.5 KiB')
  assert.equal(fileSizeLabel(104857600), '100.0 MiB')
})

test('the artifacts button counts the files a test run shares', async () => {
  const { artifactsLabel } = await import('../src/testArtifacts.ts')
  assert.equal(artifactsLabel({ artifact_count: 2 }), 'Artifacts (2)')
  assert.equal(artifactsLabel({ artifact_count: 0, test_run_artifact_count: 1 }), 'Artifacts (0 + 1 shared)')
  assert.equal(artifactsLabel(null), 'Artifacts (0)')
})
