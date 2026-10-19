import assert from 'node:assert/strict'
import test from 'node:test'

import { inventoryDevicesColumn, inventoryDevicesText } from '../src/inventoryDevices.ts'

test('a claim names its first matching devices and counts the rest', () => {
  assert.equal(inventoryDevicesText({ inventory_devices: ['a', 'b', 'c'], inventory_match_count: 7 }), 'a, b, c +4 more')
  assert.equal(inventoryDevicesText({ inventory_devices: ['a', 'b'], inventory_match_count: 2 }), 'a, b')
})

test('a claim nothing matches is blank, for the grid to show N/A', () => {
  assert.equal(inventoryDevicesText({ inventory_devices: [], inventory_match_count: 0 }), '')
  assert.equal(inventoryDevicesText({}), '')
  assert.equal(inventoryDevicesText(null), '')
  const column = inventoryDevicesColumn(() => {})
  assert.equal(column.cellRenderer({ data: { inventory_devices: [] } }), null)
})

test('the cell counts matching devices and the tooltip names the first few', async () => {
  const { inventoryDevicesCount } = await import('../src/inventoryDevices.ts')
  assert.equal(inventoryDevicesCount({ inventory_devices: ['a', 'b', 'c'], inventory_match_count: 7 }), '7 devices')
  assert.equal(inventoryDevicesCount({ inventory_devices: ['a'], inventory_match_count: 1 }), '1 device')
  assert.equal(inventoryDevicesCount({ inventory_devices: [] }), '')
  const column = inventoryDevicesColumn(() => {})
  const row = { inventory_devices: ['a', 'b', 'c'], inventory_match_count: 7 }
  assert.equal(column.valueGetter({ data: row }), '7 devices')
  assert.equal(column.tooltipValueGetter({ data: row }), 'a, b, c +4 more')
})
