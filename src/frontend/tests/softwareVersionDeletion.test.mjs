import assert from 'node:assert/strict'
import test from 'node:test'
import { softwareVersionDeletionUrl } from '../src/softwareVersionDeletion.ts'

test('deleting an older version returns to the surviving current version', () => {
  assert.equal(softwareVersionDeletionUrl('Office Suite', '1.2', 2), '/software/Office%20Suite')
})

test('deleting the last version returns to the list with a success message', () => {
  assert.equal(softwareVersionDeletionUrl('Office Suite', '1.2', 0),
    '/software?deleted_version=Office%20Suite%201.2')
})
