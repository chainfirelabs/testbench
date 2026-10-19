"""Test file fields and the device page's schema-driven tables, with real PostgreSQL."""
from sqlalchemy import delete

from app.db import SessionLocal
from app.models import EntityField
from app.services.entity_fields import get_entity_fields, initialize_entity_fields
from test_device_schema_api import SchemaCase


class TestFileFieldTests(SchemaCase):
    def test_output_and_artifacts_are_hideable_and_never_exported_or_imported(self):
        fields = self.get('/api/v1/entity-fields/tests').json()
        by_key = {field['key']: field for field in fields}
        for key in ('output', 'artifacts'):
            self.assertTrue(by_key[key]['visible'])
            self.assertTrue(by_key[key]['list_visible'])
            self.assertFalse(by_key[key]['writable'])
            self.assertFalse(by_key[key]['list_visibility_locked'])
            self.assertEqual(by_key[key]['database_storage'], 'derived')

        layout = [{'id': field['id'], 'list_visible': field['key'] != 'output'} for field in fields]
        saved = self.put('/api/v1/entity-fields/tests', {'fields': layout})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse({f['key']: f for f in saved.json()}['output']['list_visible'])
        restore = [{'id': field['id'], 'list_visible': True} for field in fields]
        self.assertEqual(self.put('/api/v1/entity-fields/tests', {'fields': restore}).status_code, 200)

        device = self.post('/api/v1/devices', {'unique_id': 'files-device'}).json()
        software = self.post('/api/v1/software', {'name': 'Files Software'}).json()
        self.post('/api/v1/tests', {'device_id': device['id'], 'software_id': software['id'], 'outcome': 'pass'})
        exported = self.get(f"/api/v1/tests/export?format=json&device_id={device['id']}").json()
        self.assertEqual(len(exported), 1)
        self.assertNotIn('output', exported[0])
        self.assertNotIn('artifacts', exported[0])

        csv = b'device_unique_id,software_name,outcome,output,artifacts\nfiles-device,Files Software,fail,run.log,3\n'
        imported = self.client.post('/api/v1/tests/import', headers=self.headers,
                                    files={'file': ('tests.csv', csv, 'text/csv')})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['errors'], [])
        rows = self.get(f"/api/v1/tests?device_id={device['id']}&outcome=fail").json()['items']
        self.assertEqual(len(rows), 1)
        self.assertNotIn('output', rows[0]['misc_data'])
        self.assertNotIn('artifacts', rows[0]['misc_data'])

        # Filtering or searching on a file field neither errors nor narrows.
        listed = self.get(f"/api/v1/tests?device_id={device['id']}&output=run.log&search=files").json()
        self.assertEqual(listed['total'], 2)


    def test_an_upgrade_adds_the_file_fields_shown_at_the_end(self):
        with SessionLocal() as db:
            db.execute(delete(EntityField).where(
                EntityField.entity == 'tests', EntityField.key.in_(['output', 'artifacts'])))
            db.commit()
        initialize_entity_fields()
        with SessionLocal() as db:
            fields = get_entity_fields(db, 'tests')
        self.assertEqual([f.key for f in fields][-2:], ['output', 'artifacts'])
        self.assertTrue(all(f.visible and f.list_visible for f in fields[-2:]))

class DeviceClaimLayoutTests(SchemaCase):
    def test_device_claims_follow_each_software_versions_layout(self):
        device = self.post('/api/v1/devices', {'unique_id': 'claim-layout', 'data': {
            'make': 'Layout', 'model': 'Box'}}).json()
        plain = self.post('/api/v1/software', {'name': 'Plain Layout', 'version': '1'}).json()
        custom = self.post('/api/v1/software', {'name': 'Custom Layout', 'version': '1'}).json()
        for software in (plain, custom):
            response = self.post(f"/api/v1/software/{software['id']}/vendor-devices", {
                'make': 'Layout', 'model': 'Box', 'support_status': 'supported', 'source': 'datasheet'})
            self.assertEqual(response.status_code, 201, response.text)

        catalog = self.get(f"/api/v1/software/{custom['id']}/vendor-devices/schema").json()
        hidden = {'source', 'misc_data'}
        layout = sorted(catalog, key=lambda field: field['key'] != 'notes')  # Notes first.
        saved = self.put(f"/api/v1/software/{custom['id']}/vendor-devices/schema", {'fields': [
            {'id': field['id'], 'visible': field['key'] not in hidden, 'required': False} for field in layout]})
        self.assertEqual(saved.status_code, 200, saved.text)

        result = self.get(f"/api/v1/devices/{device['id']}/compatible-software").json()
        by_name = {item['software']['name']: item for item in result['items']}
        plain_keys = [field['key'] for field in by_name['Plain Layout']['fields']]
        custom_keys = [field['key'] for field in by_name['Custom Layout']['fields']]
        self.assertIn('source', plain_keys)
        self.assertNotIn('source', custom_keys)
        self.assertNotIn('misc_data', custom_keys)
        self.assertEqual(custom_keys[0], 'notes')
        self.assertIn('inventory_devices', plain_keys)
        claim = by_name['Plain Layout']['vendor_devices'][0]
        self.assertEqual(claim['inventory_devices'], ['claim-layout'])

        # The global layout applies where a version has no override of its own.
        fields = self.get('/api/v1/entity-fields/vendor_devices').json()
        self.put('/api/v1/entity-fields/vendor_devices', {'fields': [
            {'id': field['id'], 'list_visible': field['key'] != 'architecture'} for field in fields]})
        result = self.get(f"/api/v1/devices/{device['id']}/compatible-software").json()
        by_name = {item['software']['name']: item for item in result['items']}
        self.assertNotIn('architecture', [field['key'] for field in by_name['Plain Layout']['fields']])
        self.put('/api/v1/entity-fields/vendor_devices', {'fields': [
            {'id': field['id'], 'list_visible': True} for field in fields]})
