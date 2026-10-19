from test_device_schema_api import SchemaCase


class ClaimInventoryTests(SchemaCase):
    def test_grouped_counts_firmware_pagination_and_updates(self):
        software = self.post('/api/v1/software', {'name': 'Inventory Match', 'version': '1'}).json()
        base = f"/api/v1/software/{software['id']}/vendor-devices"
        claims = [self.post(base, {'make': 'Acme', 'model': 'Router', 'firmware_version': version,
                                  'support_status': 'unsupported'}).json() for version in ('1', '2')]
        for uid, version in [('match-a', '1'), ('match-b', '1'), ('match-c', '2')]:
            response = self.post('/api/v1/devices', {'unique_id': uid, 'data': {
                'make': 'Acme', 'model': 'Router', 'firmware_version': version}})
            self.assertEqual(response.status_code, 201, response.text)
        grouped = self.get(base + '/grouped').json()
        self.assertEqual(grouped['total'], 1)
        members = grouped['items'][0]['_firmwareMembers']
        self.assertEqual({r['firmware_version']: r['inventory_match_count'] for r in members}, {'1': 2, '2': 1})
        matches = self.get(f"{base}/{claims[0]['id']}/inventory-matches?page_size=1&page=2").json()
        self.assertEqual(matches['total'], 2)
        self.assertEqual([r['unique_id'] for r in matches['items']], ['match-b'])
        updated = self.client.patch(f"{base}/{claims[0]['id']}", headers=self.headers,
                                    json={'firmware_version': '3'})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['inventory_match_count'], 0)
        self.assertEqual(self.get(f"{base}/{claims[0]['id']}/inventory-matches").json()['items'], [])
        other = self.post('/api/v1/software', {'name': 'Other Software'}).json()
        self.assertEqual(self.get(f"/api/v1/software/{other['id']}/vendor-devices/{claims[0]['id']}/inventory-matches").status_code, 404)

    def test_claim_lists_name_their_matching_devices(self):
        software = self.post('/api/v1/software', {'name': 'Preview Match', 'version': '1'}).json()
        base = f"/api/v1/software/{software['id']}/vendor-devices"
        matched = self.post(base, {'make': 'Acme', 'model': 'Switch', 'support_status': 'supported'}).json()
        lonely = self.post(base, {'make': 'Nobody', 'model': 'Owns', 'support_status': 'planned'}).json()
        for uid in ('sw-d', 'sw-a', 'sw-c', 'sw-b'):
            self.post('/api/v1/devices', {'unique_id': uid, 'data': {'make': 'Acme', 'model': 'Switch'}})

        catalog = {row['id']: row for row in self.get('/api/v1/vendor-devices?software=Preview Match').json()['items']}
        self.assertEqual(catalog[matched['id']]['inventory_devices'], ['sw-a', 'sw-b', 'sw-c'])
        self.assertEqual(catalog[matched['id']]['inventory_match_count'], 4)
        self.assertEqual(catalog[lonely['id']]['inventory_devices'], [])
        self.assertEqual(catalog[lonely['id']]['inventory_match_count'], 0)
        listed = {row['id']: row for row in self.get(base).json()['items']}
        self.assertEqual(listed[matched['id']]['inventory_devices'], ['sw-a', 'sw-b', 'sw-c'])

        # A schema field, so it can be hidden like any other column.
        fields = {field['key']: field for field in self.get('/api/v1/entity-fields').json()['vendor_devices']}
        self.assertFalse(fields['inventory_devices']['writable'])
        self.assertEqual(fields['inventory_devices']['database_storage'], 'derived')

        # Derived on read: never written to an export, so never re-imported.
        exported = self.get('/api/v1/vendor-devices/export?format=json&software=Preview Match').json()
        self.assertTrue(exported)
        self.assertTrue(all('inventory_devices' not in row for row in exported))
        scoped = self.get(base + '/export?format=json').json()
        self.assertTrue(all('inventory_devices' not in row for row in scoped))
