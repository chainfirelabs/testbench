"""Indexed JSON prefilter preserves search results for escaped values."""

from test_device_schema_api import SchemaCase


class GlobalSearchTests(SchemaCase):
    def test_device_search_matches_plain_and_quoted_text(self):
        created = self.post("/api/v1/devices", {
            "unique_id": "search-quoted", "make": 'Acme "Desk"',
        })
        self.assertEqual(created.status_code, 201, created.text)
        plain = self.get("/api/v1/search?q=Acme").json()
        quoted = self.get('/api/v1/search?q=%22Desk%22').json()
        self.assertIn("search-quoted", {row["unique_id"] for row in plain["devices"]})
        self.assertIn("search-quoted", {row["unique_id"] for row in quoted["devices"]})

    def test_component_search_returns_the_owning_version_once(self):
        older = self.post('/api/v1/software', {
            'name': 'Search Component Suite', 'version': '1',
            'bundle_components': [
                {'name': 'Search Outlook', 'version': '16.2'},
                {'name': 'Search Outlook', 'version': '17.0'},
            ],
        }).json()
        newer_response = self.post(f"/api/v1/software/{older['id']}/versions", {'version': '2'})
        self.assertEqual(newer_response.status_code, 201, newer_response.text)
        newer = newer_response.json()
        self.assertEqual(self.patch_(f"/api/v1/software/{newer['id']}", {
            'bundle_components': [{'name': 'Search Word', 'version': '20'}],
        }).status_code, 200)

        found = self.get('/api/v1/search?q=Search%20Outlook').json()['software']
        self.assertEqual([row['id'] for row in found], [older['id']])
        self.assertFalse(found[0]['is_latest'])
        self.assertEqual({part['version'] for part in found[0]['matching_components']}, {'16.2', '17.0'})
        by_version = self.get('/api/v1/search?q=16.2').json()['software']
        self.assertIn(older['id'], {row['id'] for row in by_version})

    def test_device_search_shows_tested_component_and_unbundled_test(self):
        suite = self.post('/api/v1/software', {
            'name': 'Device Search Suite', 'version': '4',
            'bundle_components': [{'name': 'Device Search Mail', 'version': '16.2'}],
        }).json()
        device_response = self.post('/api/v1/devices', {
            'unique_id': 'search-tested-device', 'make': 'Tested Search Maker',
        })
        self.assertEqual(device_response.status_code, 201, device_response.text)
        device = device_response.json()
        with_component = self.post('/api/v1/tests', {
            'software_id': suite['id'], 'device_id': device['id'],
            'component_id': suite['bundle_components'][0]['id'], 'outcome': 'pass',
        })
        self.assertEqual(with_component.status_code, 201, with_component.text)
        without_component = self.post('/api/v1/tests', {
            'software_id': suite['id'], 'device_id': device['id'], 'outcome': 'fail',
        })
        self.assertEqual(without_component.status_code, 201, without_component.text)

        found = self.get('/api/v1/search?q=search-tested-device').json()
        result = next(row for row in found['devices'] if row['id'] == device['id'])
        self.assertEqual(result['tested_components'][0]['name'], 'Device Search Mail')
        self.assertEqual(result['tested_components'][0]['version'], '16.2')
        self.assertEqual(result['tested_components'][0]['test_id'], with_component.json()['id'])
        tests = {row['id']: row for row in found['tests']}
        self.assertEqual(tests[with_component.json()['id']]['component_name'], 'Device Search Mail')
        self.assertIsNone(tests[without_component.json()['id']]['component_name'])
        by_make = self.get('/api/v1/search?q=Tested%20Search%20Maker').json()
        self.assertIn(with_component.json()['id'], {row['id'] for row in by_make['tests']})

    def test_deletion_impact_counts_only_the_selected_version(self):
        older = self.post('/api/v1/software', {
            'name': 'Search Delete Suite', 'version': '1',
            'bundle_components': [{'name': 'Search Delete Component', 'version': '1'}],
        }).json()
        newer = self.post(f"/api/v1/software/{older['id']}/versions", {'version': '2'}).json()
        device = self.post('/api/v1/devices', {'unique_id': 'search-delete-device'}).json()
        recorded = self.post('/api/v1/tests', {
            'software_id': older['id'], 'device_id': device['id'], 'outcome': 'pass',
        })
        self.assertEqual(recorded.status_code, 201, recorded.text)
        claim = self.post(f"/api/v1/software/{older['id']}/vendor-devices", {
            'make': 'Search Claim Maker', 'model': 'Search Claim Device',
            'support_status': 'supported',
        })
        self.assertIn(claim.status_code, (200, 201), claim.text)
        impact = self.get(f"/api/v1/software/{older['id']}/deletion-impact")
        self.assertEqual(impact.status_code, 200, impact.text)
        self.assertEqual({key: impact.json()[key] for key in ('tests', 'vendor_claims', 'components')}, {
            'tests': 1, 'vendor_claims': 1, 'components': 1,
        })
        deleted = self.client.delete(f"/api/v1/software/{older['id']}", headers=self.headers)
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.get(f"/api/v1/tests/{recorded.json()['id']}").status_code, 404)
        remaining = self.get(f"/api/v1/software/{newer['id']}/versions").json()
        self.assertEqual([row['id'] for row in remaining], [newer['id']])
