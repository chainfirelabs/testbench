import assert from 'node:assert/strict'
import test from 'node:test'

import { collapseVendorDevices } from '../src/vendorDeviceGroups.ts'

test('collapses make, model, and hardware while retaining every firmware record', () => {
  const rows = [
    { id: 'old', make: 'Cisco', model: '9000', hardware_version: 'A', firmware_version: '9.12' },
    { id: 'new', make: 'cisco', model: '9000', hardware_version: 'a', firmware_version: '10.2' },
    { id: 'other', make: 'Cisco', model: '9000', hardware_version: 'B', firmware_version: '1.0' },
  ]
  const collapsed = collapseVendorDevices(rows)

  assert.equal(collapsed.length, 2)
  assert.equal(collapsed[0].id, 'new')
  assert.deepEqual(collapsed[0]._firmwareMembers.map((row) => row.id), ['new', 'old'])
})

test('a selected older firmware becomes the visible underlying record', () => {
  const rows = [
    { id: 'old', make: 'Cisco', model: '9000', hardware_version: 'A', firmware_version: '1.0' },
    { id: 'new', make: 'Cisco', model: '9000', hardware_version: 'A', firmware_version: '2.0' },
  ]
  const key = collapseVendorDevices(rows)[0]._groupKey
  const collapsed = collapseVendorDevices(rows, { [key]: 'old' })

  assert.equal(collapsed[0].id, 'old')
  assert.equal(collapsed[0].firmware_version, '1.0')
})
