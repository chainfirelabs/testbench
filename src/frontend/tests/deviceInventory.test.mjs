import assert from 'node:assert/strict'
import test from 'node:test'
import {
  deviceActionUnavailableReason,
  deviceAddressUrl,
  deviceDownloadFilename,
  deviceLinkTarget,
  fieldAppearsInDeviceList,
  isDeviceAddressField,
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
  assert.match(deviceActionUnavailableReason(action, router, schemas, false), /permission to edit devices/)
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

// ---------- address fields as links ----------

test('a bare address becomes an http page', () => {
  assert.equal(deviceAddressUrl('192.168.1.20'), 'http://192.168.1.20/')
  assert.equal(deviceAddressUrl('  10.0.0.5  '), 'http://10.0.0.5/')
  assert.equal(deviceAddressUrl('switch-4.lab.example.com'), 'http://switch-4.lab.example.com/')
})

test('a port or a path on the address is kept', () => {
  assert.equal(deviceAddressUrl('10.0.0.5:8080'), 'http://10.0.0.5:8080/')
  assert.equal(deviceAddressUrl('10.0.0.5/admin'), 'http://10.0.0.5/admin')
})

test('an address that carries its own scheme is honoured as written', () => {
  assert.equal(deviceAddressUrl('https://10.0.0.5'), 'https://10.0.0.5/')
  assert.equal(deviceAddressUrl('http://10.0.0.5:8443/ui'), 'http://10.0.0.5:8443/ui')
})

test('a bare IPv6 address is bracketed, a host:port is not', () => {
  assert.equal(deviceAddressUrl('fd00::1'), 'http://[fd00::1]/')
  assert.equal(deviceAddressUrl('[fd00::1]'), 'http://[fd00::1]/')
  // One colon is host:port, which must not be mistaken for an address half.
  assert.equal(deviceAddressUrl('10.0.0.5:443'), 'http://10.0.0.5:443/')
})

test('nothing that is not a web address becomes a link', () => {
  for (const value of [
    null, undefined, '', '   ',
    'javascript:alert(1)',          // never a link, whatever it is stored as
    'file:///etc/passwd',
    'ftp://10.0.0.5',
    'not a host',                   // whitespace
    '10.0.0.5:notaport',
  ]) {
    assert.equal(deviceAddressUrl(value), null, `expected no link for ${JSON.stringify(value)}`)
  }
})

test('linking is the schema flag, not the field name or its role', () => {
  // Any field can be opted in — a "Vendor Page" URL is not an address — and an
  // address field can be opted out. The role only decides the seeded default,
  // which the backend applies once; by the time the UI sees a field the
  // answer is already on it.
  assert.equal(isDeviceAddressField({ opens_web_page: true }), true)
  assert.equal(isDeviceAddressField({ opens_web_page: false }), false)
  // Absent (an older server, or a payload that predates the column) is not a
  // reason to start linking values.
  assert.equal(isDeviceAddressField({}), false)
  assert.equal(isDeviceAddressField({ opens_web_page: undefined }), false)
})

// ---------- the scheme and port a link uses ----------

const lanIp = { key: 'lan_ip', link_scheme: 'http', link_port: null }
const httpsField = { key: 'lan_ip', link_scheme: 'https', link_port: 8443 }

test('a field with no link settings at all still resolves to plain http', () => {
  // An older server, or a payload written before the columns existed.
  assert.deepEqual(deviceLinkTarget(undefined, undefined), { scheme: 'http', port: null })
  assert.deepEqual(deviceLinkTarget({ key: 'lan_ip' }, {}), { scheme: 'http', port: null })
})

test("the field's own default is used when the device says nothing", () => {
  assert.equal(deviceAddressUrl('10.0.0.5', httpsField), 'https://10.0.0.5:8443/')
  assert.equal(deviceAddressUrl('10.0.0.5', httpsField, {}), 'https://10.0.0.5:8443/')
  // An override for a different field is not this field's business.
  assert.equal(
    deviceAddressUrl('10.0.0.5', lanIp, { wan_ip: { scheme: 'https' } }),
    'http://10.0.0.5/',
  )
})

test("a device's override beats the field's default", () => {
  assert.equal(
    deviceAddressUrl('10.0.0.5', lanIp, { lan_ip: { scheme: 'https', port: 8443 } }),
    'https://10.0.0.5:8443/',
  )
  // And in the other direction: a field that defaults to https, on one device
  // that answers plain http.
  assert.equal(
    deviceAddressUrl('10.0.0.5', httpsField, { lan_ip: { scheme: 'http', port: 80 } }),
    'http://10.0.0.5/',
  )
})

test('an override may set only one of the two, keeping the other', () => {
  assert.equal(
    deviceAddressUrl('10.0.0.5', httpsField, { lan_ip: { port: 9443 } }),
    'https://10.0.0.5:9443/',
  )
  assert.equal(
    deviceAddressUrl('10.0.0.5', httpsField, { lan_ip: { scheme: 'http' } }),
    'http://10.0.0.5:8443/',
  )
})

test('a scheme or port written into the value beats every configured default', () => {
  // The most specific thing there is, and an operator typed it.
  assert.equal(
    deviceAddressUrl('https://10.0.0.5', lanIp, { lan_ip: { scheme: 'http' } }),
    'https://10.0.0.5/',
  )
  assert.equal(
    deviceAddressUrl('10.0.0.5:8080', httpsField, { lan_ip: { port: 9443 } }),
    'https://10.0.0.5:8080/',
  )
  // Including a port that happens to be the scheme's own, which `url.port`
  // reports as blank and so cannot be used to detect it.
  assert.equal(
    deviceAddressUrl('10.0.0.5:80', lanIp, { lan_ip: { port: 8080 } }),
    'http://10.0.0.5/',
  )
})

test('an override port applies to a bare IPv6 address', () => {
  assert.equal(
    deviceAddressUrl('fd00::1', lanIp, { lan_ip: { scheme: 'https', port: 8443 } }),
    'https://[fd00::1]:8443/',
  )
  // A bracketed address that names its own port keeps it.
  assert.equal(
    deviceAddressUrl('[fd00::1]:8080', lanIp, { lan_ip: { port: 8443 } }),
    'http://[fd00::1]:8080/',
  )
})

test('an override cannot turn a value into something that is not a web page', () => {
  // The whitelist is applied to the result, so neither half can smuggle one in.
  assert.equal(deviceAddressUrl('javascript:alert(1)', lanIp, { lan_ip: { scheme: 'https' } }), null)
  assert.equal(deviceAddressUrl('10.0.0.5', lanIp, { lan_ip: { scheme: 'ftp' } }), 'http://10.0.0.5/')
  assert.equal(deviceAddressUrl('', lanIp, { lan_ip: { scheme: 'https' } }), null)
})
