import assert from 'node:assert/strict'
import test from 'node:test'
import {
  deviceActionUnavailableReason,
  deviceDownloadFilename,
  fieldAppearsInDeviceList,
  loadDevicePages,
} from '../src/deviceInventory.ts'

test('device downloads identify their schema scope and variant', () => {
  assert.equal(deviceDownloadFilename(undefined, 'json'), 'all-devices.json')
  assert.equal(deviceDownloadFilename('router', 'csv'), 'router-devices.csv')
  assert.equal(deviceDownloadFilename('router', 'csv', 'raw'), 'router-devices-raw.csv')
  assert.equal(deviceDownloadFilename('uncategorized', 'csv', 'template'), 'uncategorized-devices-template.csv')
})

for (const total of [0, 1, 500, 501, 1203]) {
  test(`loads all ${total} devices across page boundaries`, async () => {
    const inventory = Array.from({ length: total }, (_, id) => ({ id }))
    const offsets = []
    const progress = []
    const rows = await loadDevicePages(async (offset) => {
      offsets.push(offset)
      return { items: inventory.slice(offset, offset + 500), total }
    }, (loaded, count) => progress.push([loaded, count]))
    assert.deepEqual(rows, inventory)
    assert.deepEqual(offsets, Array.from({ length: Math.max(1, Math.ceil(total / 500)) }, (_, i) => i * 500))
    assert.deepEqual(progress.at(-1), [total, total])
  })
}

test('surfaces a failed later page instead of returning a partial inventory', async () => {
  await assert.rejects(loadDevicePages(async (offset) => {
    if (offset) throw new Error('network unavailable')
    return { items: [{ id: 1 }], total: 2 }
  }), /network unavailable/)
})

test('does not loop forever or silently truncate if pagination stops progressing', async () => {
  await assert.rejects(loadDevicePages(async () => ({ items: [], total: 3 })), /changed while loading/)
})

const field = (key, role, extra = {}) => ({ key, role, label: key, visible: true, storage: 'data', ...extra })
const action = {
  label: 'Info', device_types: ['router', 'phone'], requires_online: true,
  required_roles: ['device_password'], required_role_groups: [['scan_address_wan', 'scan_address_lan']],
}
const routerFields = [field('router_secret', 'device_password', { sensitive: true }), field('mgmt', 'scan_address_wan')]
const phoneFields = [field('phone_secret', 'device_password', { sensitive: true }), field('lan', 'scan_address_lan')]
const schemas = new Map([['', []], ['router', routerFields], ['phone', phoneFields]])

test('mixed fleet actions use the row type and allow an explicitly blank password', () => {
  const router = { device_type_key: 'router', data: { router_secret: '', mgmt: '192.0.2.1', online_status: true } }
  const phone = { device_type_key: 'phone', data: { phone_secret: 'pw', lan: '192.0.2.2', online_status: true } }
  assert.equal(deviceActionUnavailableReason(action, router, schemas, true), null)
  assert.equal(deviceActionUnavailableReason(action, phone, schemas, true), null)
  assert.match(deviceActionUnavailableReason(action, { ...router, data: phone.data }, schemas, true), /router_secret/)
})

test('type visibility overrides and permissions still block actions', () => {
  const router = { device_type_key: 'router', data: { router_secret: '', mgmt: '192.0.2.1', online_status: true } }
  const hidden = new Map(schemas)
  hidden.set('router', routerFields.map((f) => ({ ...f, visible: f.key !== 'mgmt' })))
  assert.match(deviceActionUnavailableReason(action, router, hidden, true), /add a value/)
  assert.match(deviceActionUnavailableReason(action, router, schemas, false), /write permission/)
  assert.match(deviceActionUnavailableReason(action, { ...router, device_type_key: 'unknown' }, schemas, true), /not enabled/)
})

test('one type with missing outputs does not block another type', () => {
  const scoped = { ...action, unavailable_reasons_by_type: { phone: 'Missing discovery_lan_mac' } }
  const router = { device_type_key: 'router', data: { router_secret: '', mgmt: '192.0.2.1', online_status: true } }
  assert.equal(deviceActionUnavailableReason(scoped, router, schemas, true), null)
  assert.match(deviceActionUnavailableReason(scoped, { ...router, device_type_key: 'phone' }, schemas, true), /discovery_lan_mac/)
})

test('list visibility is independent from complete field visibility', () => {
  assert.equal(fieldAppearsInDeviceList({ visible: true, list_visible: true }), true)
  assert.equal(fieldAppearsInDeviceList({ visible: true, list_visible: false }), false)
  assert.equal(fieldAppearsInDeviceList({ visible: false, list_visible: true }), false)
})
