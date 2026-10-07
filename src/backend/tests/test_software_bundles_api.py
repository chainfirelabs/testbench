"""Software bundles against a real PostgreSQL (skipped when unavailable)."""

import json

from test_device_schema_api import SchemaCase


class SoftwareBundleApiTests(SchemaCase):
    def test_component_filters_use_effective_status_on_the_same_version(self):
        software = self.post('/api/v1/software', {
            'name': 'Filter Component Matrix', 'version': '1',
            'bundle_components': [
                {'name': 'Outlook', 'version': '16.2'},
                {'name': 'Teams', 'version': '2.1'},
            ],
        }).json()
        outlook = software['bundle_components'][0]
        endpoint = f"/api/v1/software/{software['id']}/vendor-devices"
        created = self.post(endpoint, {
            'make': 'SampleCo', 'model': 'FilterBox-1', 'support_status': 'supported',
            'component_support': [{
                'component_id': outlook['id'], 'support_status': 'unsupported',
            }],
        })
        self.assertEqual(created.status_code, 201, created.text)
        query = '?model=FilterBox-1&component_name=Teams&component_version=2.1&component_status=supported'
        catalog = self.get('/api/v1/vendor-devices' + query).json()
        self.assertEqual(catalog['total'], 1)
        self.assertEqual(catalog['items'][0]['matching_components'][0]['component_name'], 'Teams')
        self.assertTrue(catalog['items'][0]['matching_components'][0]['inherited'])
        scoped = self.get(endpoint + '?component_name=Outlook&component_status=unsupported').json()
        self.assertEqual(scoped['total'], 1)
        self.assertFalse(scoped['items'][0]['matching_components'][0]['inherited'])
        grouped = self.get(endpoint + '/grouped?component_name=Teams&component_status=supported').json()
        self.assertEqual(grouped['total'], 1)
        self.assertEqual(grouped['items'][0]['matching_components'][0]['component_name'], 'Teams')
        self.assertEqual(self.get('/api/v1/vendor-devices?model=FilterBox-1&component_name=Outlook&component_status=supported').json()['total'], 0)
        self.assertEqual(self.get('/api/v1/vendor-devices?model=FilterBox-1&component_name=Teams&component_status=unsupported').json()['total'], 0)
        self.assertEqual(self.get('/api/v1/vendor-devices?model=FilterBox-1&search=Outlook').json()['total'], 1)
        self.assertEqual(self.get(endpoint + '?search=Teams').json()['total'], 1)
        self.assertEqual(len(self.get('/api/v1/vendor-devices/export?format=json&model=FilterBox-1&component_name=Teams&component_status=supported').json()), 1)
        self.assertEqual(self.get('/api/v1/vendor-devices?component_status=unknown').status_code, 422)

    def test_adding_another_component_to_same_claim_preserves_the_first(self):
        software = self.post('/api/v1/software', {
            'name': 'Separate Component Claims', 'version': '1',
            'bundle_components': [
                {'name': 'Outlook', 'version': '16.2'},
                {'name': 'Teams', 'version': '2.1'},
            ],
        }).json()
        endpoint = f"/api/v1/software/{software['id']}/vendor-devices"
        first, second = software['bundle_components']
        device = {'make': 'Acme', 'model': 'R1', 'firmware_version': '3.2',
                  'hardware_version': 'Rev A'}
        created = self.post(endpoint, {**device, 'support_status': 'partial',
            'component_support': [{'component_id': first['id'], 'support_status': 'supported'}]})
        self.assertEqual(created.status_code, 201, created.text)
        added = self.post('/api/v1/vendor-devices', {
            **device, 'software_name': software['name'], 'software_version': '1',
            'support_status': 'supported',
            'component_support': [{'component_id': second['id'], 'support_status': 'unsupported'}],
        })
        self.assertEqual(added.status_code, 200, added.text)
        self.assertEqual(added.json()['id'], created.json()['id'])
        self.assertEqual(added.json()['support_status'], 'partial')
        self.assertEqual({(item['component_name'], item['component_version'], item['support_status'])
                          for item in added.json()['component_support']},
                         {('Outlook', '16.2', 'supported'), ('Teams', '2.1', 'unsupported')})
        updated = self.post(endpoint, {**device,
            'component_support': [{'component_id': second['id'], 'support_status': 'supported'}]})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual({(item['component_name'], item['support_status'])
                          for item in updated.json()['component_support']},
                         {('Outlook', 'supported'), ('Teams', 'supported')})
        self.assertEqual(self.get(endpoint).json()['total'], 1)

    def test_separate_import_rows_merge_component_versions(self):
        software = self.post('/api/v1/software', {
            'name': 'Import Component Claims', 'version': '1',
            'bundle_components': [
                {'name': 'Outlook', 'version': '16.2'},
                {'name': 'Teams', 'version': '2.1'},
            ],
        }).json()
        rows = [
            {'make': 'Acme', 'model': 'R1', 'component_support': [
                {'component_name': name, 'component_version': version,
                 'support_status': status}]}
            for name, version, status in [('Outlook', '16.2', 'supported'),
                                          ('Teams', '2.1', 'unsupported')]
        ]
        endpoint = f"/api/v1/software/{software['id']}/vendor-devices"
        imported = self.client.post(f'{endpoint}/import', headers=self.headers,
            files={'file': ('claims.json', json.dumps(rows).encode(), 'application/json')})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['errors'], [])
        claim = self.get(endpoint).json()['items'][0]
        self.assertEqual(len(claim['component_support']), 2)
        catalog_rows = [{**row, 'software_name': software['name'], 'software_version': '1'}
                        for row in rows]
        imported = self.client.post('/api/v1/vendor-devices/import', headers=self.headers,
            files={'file': ('claims.json', json.dumps(catalog_rows).encode(), 'application/json')})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['errors'], [])
        claim = self.get(endpoint).json()['items'][0]
        self.assertEqual({item['component_name'] for item in claim['component_support']},
                         {'Outlook', 'Teams'})

    def test_vendor_claim_support_can_differ_by_component_version_or_be_general(self):
        software = self.post('/api/v1/software', {
            'name': 'Component Matrix', 'version': '1.0',
            'bundle_components': [
                {'name': 'Engine', 'version': '1'},
                {'name': 'Engine', 'version': '2'},
            ],
        }).json()
        endpoint = f"/api/v1/software/{software['id']}/vendor-devices"
        general = self.post(endpoint, {
            'make': 'Acme', 'model': 'R1', 'support_status': 'supported',
        })
        self.assertEqual(general.status_code, 201, general.text)
        self.assertEqual(general.json()['component_support'], [])
        components = software['bundle_components']
        updated = self.patch_(f"{endpoint}/{general.json()['id']}", {
            'component_support': [
                {'component_id': components[0]['id'], 'support_status': 'unsupported'},
                {'component_id': components[1]['id'], 'support_status': 'partial'},
            ],
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['support_status'], 'supported')
        self.assertEqual({(item['component_version'], item['support_status'])
                          for item in updated.json()['component_support']},
                         {('1', 'unsupported'), ('2', 'partial')})
        catalog = self.get('/api/v1/vendor-devices').json()['items']
        self.assertEqual(len(next(item for item in catalog if item['id'] == general.json()['id'])['component_support']), 2)
        clone = self.post(f"/api/v1/software/{software['id']}/versions", {
            'version': '2.0', 'copy_vendor_devices': True,
        })
        self.assertEqual(clone.status_code, 201, clone.text)
        copied = self.get(f"/api/v1/software/{clone.json()['id']}/vendor-devices").json()['items'][0]
        self.assertEqual({(item['component_version'], item['support_status'])
                          for item in copied['component_support']},
                         {('1', 'unsupported'), ('2', 'partial')})
        self.assertTrue(all(item['component_id'] in
                            {component['id'] for component in clone.json()['bundle_components']}
                            for item in copied['component_support']))
        exported = self.get(f'{endpoint}/export?format=json')
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(len(exported.json()[0]['component_support']), 2)
        imported = self.client.post(
            f'{endpoint}/import', headers=self.headers,
            files={'file': ('claims.json', exported.content, 'application/json')},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['errors'], [])
        software_export = self.get('/api/v1/software/export?format=json')
        self.assertEqual(software_export.status_code, 200, software_export.text)
        software_rows = [row for row in software_export.json()
                         if row['name'] == 'Component Matrix' and row['version'] == '1.0']
        self.assertEqual(len(software_rows), 1)
        self.assertEqual(len(software_rows[0]['vendor_devices'][0]['component_support']), 2)
        software_import = self.client.post(
            '/api/v1/software/import', headers=self.headers,
            files={'file': ('software.json', json.dumps(software_rows).encode(), 'application/json')},
        )
        self.assertEqual(software_import.status_code, 200, software_import.text)
        self.assertEqual(software_import.json()['errors'], [])
        restored = {**software_rows[0], 'name': 'Restored Component Matrix'}
        restored_import = self.client.post(
            '/api/v1/software/import', headers=self.headers,
            files={'file': ('restored.json', json.dumps([restored]).encode(), 'application/json')},
        )
        self.assertEqual(restored_import.status_code, 200, restored_import.text)
        self.assertEqual(restored_import.json()['errors'], [])
        restored_software = self.get('/api/v1/software/lookup/by-name?name=Restored%20Component%20Matrix').json()
        restored_claim = self.get(f"/api/v1/software/{restored_software['id']}/vendor-devices").json()['items'][0]
        self.assertEqual(len(restored_claim['component_support']), 2)
        cleared = self.patch_(f"{endpoint}/{general.json()['id']}", {'component_support': []})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()['component_support'], [])
    def test_vendor_claim_rejects_component_from_another_software_version(self):
        first = self.post('/api/v1/software', {
            'name': 'Scoped Matrix', 'version': '1',
            'bundle_components': [{'name': 'Engine', 'version': '1'}],
        }).json()
        second = self.post(f"/api/v1/software/{first['id']}/versions", {
            'version': '2', 'copy_vendor_devices': False,
        }).json()
        response = self.post(f"/api/v1/software/{first['id']}/vendor-devices", {
            'make': 'Acme', 'model': 'R2',
            'component_support': [{
                'component_id': second['bundle_components'][0]['id'],
                'support_status': 'unsupported',
            }],
        })
        self.assertEqual(response.status_code, 422, response.text)
        invalid = self.post(f"/api/v1/software/{first['id']}/vendor-devices", {
            'make': 'Acme', 'model': 'R2',
            'component_support': [{
                'component_id': first['bundle_components'][0]['id'],
                'support_status': 'sometimes',
            }],
        })
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_catalog_claim_accepts_optional_component_support(self):
        software = self.post('/api/v1/software', {
            'name': 'Catalog Component Matrix', 'version': '3',
            'bundle_components': [{'name': 'Scanner', 'version': '4'}],
        }).json()
        component = software['bundle_components'][0]
        created = self.post('/api/v1/vendor-devices', {
            'software_name': software['name'], 'software_version': software['version'],
            'make': 'Acme', 'model': 'C1', 'support_status': 'supported',
            'component_support': [{
                'component_id': component['id'], 'support_status': 'unsupported',
            }],
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()['component_support'][0]['component_name'], 'Scanner')
        exported = self.get('/api/v1/vendor-devices/export?format=json')
        rows = [row for row in exported.json() if row['software_name'] == software['name']]
        self.assertEqual(len(rows[0]['component_support']), 1)
        imported = self.client.post(
            '/api/v1/vendor-devices/import', headers=self.headers,
            files={'file': ('claims.json', json.dumps(rows).encode(), 'application/json')},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['errors'], [])

    def test_software_without_components_accepts_a_general_vendor_claim(self):
        software = self.post('/api/v1/software', {
            'name': 'Standalone Claim Matrix', 'version': '1',
        }).json()
        created = self.post(f"/api/v1/software/{software['id']}/vendor-devices", {
            'make': 'Acme', 'model': 'S1', 'support_status': 'partial',
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()['component_support'], [])

    def test_software_template_contains_only_flat_software_fields(self):
        response = self.get("/api/v1/software/template")
        self.assertEqual(response.status_code, 200, response.text)
        header = response.text.splitlines()[0].split(",")
        self.assertIn("name", header)
        self.assertIn("version", header)
        self.assertNotIn("bundle_components", header)
        self.assertNotIn("vendor_devices", header)

    def test_components_are_scoped_to_the_suite(self):
        response = self.post("/api/v1/software", {
            "name": "Microsoft 365",
            "version": "2026.1",
            "bundle_components": [
                {"name": "Microsoft Outlook", "version": "16.2"},
                {"name": "Microsoft Word", "version": "16.4"},
            ],
        })
        self.assertEqual(response.status_code, 201, response.text)
        bundle = response.json()
        self.assertEqual(
            [(item["name"], item["version"]) for item in bundle["bundle_components"]],
            [("Microsoft Outlook", "16.2"), ("Microsoft Word", "16.4")],
        )
        outlook = self.get("/api/v1/software/lookup/by-name?name=Microsoft%20Outlook")
        self.assertEqual(outlook.status_code, 404, outlook.text)

    def test_test_import_keeps_components_under_the_suite(self):
        software = self.post("/api/v1/software", {
            "name": "Microsoft 365 Import", "version": "2026.1",
        }).json()
        self.post("/api/v1/devices", {"unique_id": "suite-device"})
        content = (
            "device_unique_id,software_name,software_version,component_name,component_version,outcome\n"
            "suite-device,Microsoft 365 Import,2026.1,Microsoft Outlook,16.2,pass\n"
            "suite-device,Microsoft 365 Import,2026.1,Microsoft Word,16.4,fail\n"
        )
        imported = self.client.post(
            "/api/v1/tests/import", headers=self.headers,
            files={"file": ("tests.csv", content, "text/csv")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["created"], 2)
        self.assertEqual(imported.json()["errors"], [])

        tests = self.get(f"/api/v1/tests?software_id={software['id']}").json()["items"]
        self.assertEqual(
            {(item["software_name"], item["component_name"]) for item in tests},
            {("Microsoft 365 Import", "Microsoft Outlook"), ("Microsoft 365 Import", "Microsoft Word")},
        )
        tested_page = self.get(
            f"/api/v1/software/{software['id']}/tested-devices?page=1&page_size=1"
        ).json()
        self.assertEqual(tested_page["total"], 2)
        self.assertEqual(len(tested_page["devices"]), 1)
        tested = self.get(f"/api/v1/software/{software['id']}/tested-devices").json()["devices"]
        self.assertEqual(
            {item["component"]["name"] for item in tested},
            {"Microsoft Outlook", "Microsoft Word"},
        )
        self.assertEqual(
            self.get("/api/v1/software/lookup/by-name?name=Microsoft%20Outlook").status_code,
            404,
        )

    def test_test_template_includes_optional_component_columns(self):
        response = self.get("/api/v1/tests/template")
        self.assertEqual(response.status_code, 200, response.text)
        header = response.text.splitlines()[0].split(",")
        self.assertEqual(header[0:5], [
            "device_unique_id", "software_name", "software_version",
            "component_name", "component_version",
        ])


class VendorDeviceSchemaApiTests(SchemaCase):
    def test_grouped_vendor_devices_page_without_splitting_firmware(self):
        software = self.post("/api/v1/software", {
            "name": "Grouped Matrix", "version": "1.0",
        }).json()
        endpoint = f"/api/v1/software/{software['id']}/vendor-devices"
        for firmware in ("9.12", "10.2", "10.3"):
            response = self.post(endpoint, {
                "make": "Acme", "model": "R1", "hardware_version": "A",
                "firmware_version": firmware,
            })
            self.assertEqual(response.status_code, 201, response.text)
        self.post(endpoint, {
            "make": "Acme", "model": "R2", "hardware_version": "B",
            "firmware_version": "1.0",
        })

        page = self.get(f"{endpoint}/grouped?page=1&page_size=1").json()
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["firmware_version"], "10.3")
        self.assertEqual(len(page["items"][0]["_firmwareMembers"]), 3)

    def test_custom_field_can_be_required_for_one_software(self):
        software = self.post("/api/v1/software", {
            "name": "Router Manager", "version": "1.0",
        }).json()
        created = self.post("/api/v1/entity-fields/vendor_devices", {
            "key": "license_tier", "label": "License Tier", "field_type": "select",
            "options": ["standard", "enterprise"],
        })
        self.assertEqual(created.status_code, 201, created.text)
        field_id = created.json()["id"]
        schema = self.get(
            f"/api/v1/software/{software['id']}/vendor-devices/schema"
        ).json()
        for field in schema:
            if field["id"] == field_id:
                field["required"] = True
        updated = self.put(
            f"/api/v1/software/{software['id']}/vendor-devices/schema",
            {"fields": [
                {"id": field["id"], "visible": field["visible"], "required": field["required"]}
                for field in schema
            ]},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        missing = self.post(f"/api/v1/software/{software['id']}/vendor-devices", {
            "make": "Acme", "model": "R1",
        })
        self.assertEqual(missing.status_code, 422)
        accepted = self.post(f"/api/v1/software/{software['id']}/vendor-devices", {
            "make": "Acme", "model": "R1", "misc_data": {"license_tier": "enterprise"},
        })
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["misc_data"]["license_tier"], "enterprise")


class TestedDeviceChecklistTests(SchemaCase):
    def test_inclusions_and_exclusions_filter_the_same_devices(self):
        software = self.post('/api/v1/software', {'name': 'Filter suite', 'version': '1'}).json()
        for name in ('cisco', 'dell'):
            self.post('/api/v1/devices', {'unique_id': name, 'make': name})
        content = ('device_unique_id,software_name,software_version,outcome\n'
                   'cisco,Filter suite,1,pass\n'
                   'dell,Filter suite,1,fail\n')
        response = self.client.post('/api/v1/tests/import', headers=self.headers,
                                    files={'file': ('tests.csv', content, 'text/csv')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['created'], 2)
        endpoint = f"/api/v1/software/{software['id']}/tested-devices"
        for query in ('include__make=["cisco"]', 'exclude__make=["dell"]'):
            response = self.get(f'{endpoint}?{query}')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['total'], 1)
            self.assertEqual(response.json()['devices'][0]['device']['unique_id'], 'cisco')
        self.assertEqual(self.get(f'{endpoint}?include__make=[]').json()['total'], 0)
