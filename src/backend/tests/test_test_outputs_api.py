"""Test output API with real PostgreSQL and an in-memory S3 client."""
import io
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.core.security import hash_password
from test_device_schema_api import SchemaCase


class TestOutputsApiTests(SchemaCase):
    def test_upload_view_replace_delete_and_permissions(self):
        device = self.post('/api/v1/devices', {'unique_id': 'output-device'}).json()
        software = self.post('/api/v1/software', {'name': 'Output Software'}).json()
        test_id = self.post('/api/v1/tests', {'device_id': device['id'], 'software_id': software['id'],
                                              'outcome': 'pass'}).json()['id']
        base = f'/api/v1/tests/{test_id}/output'
        objects = {}
        client = Mock()
        client.put_object.side_effect = lambda **kw: objects.__setitem__(kw['Key'], kw['Body'])
        client.get_object.side_effect = lambda **kw: {'Body': io.BytesIO(objects[kw['Key']])}
        client.delete_object.side_effect = lambda **kw: objects.pop(kw['Key'], None)
        with patch.multiple(settings, object_storage_enabled=True, object_storage_bucket='output-bucket',
                            object_storage_max_bytes=32), \
                patch('app.services.test_artifact_storage.object_storage_client', return_value=client):
            empty = self.get(base).json()
            self.assertIsNone(empty['output'])
            self.assertTrue(empty['storage_enabled'])
            self.assertEqual(empty['max_bytes'], 32)

            uploaded = self.client.put(base, headers=self.headers, files={'file': ('../run.log', b'line 1\nline 2\n')})
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            self.assertEqual(uploaded.json()['filename'], 'run.log')
            self.assertEqual(list(objects), [f'tests/{test_id}/output/run.log'])
            viewed = self.get(base).json()
            self.assertEqual(viewed['text'], 'line 1\nline 2\n')
            self.assertIsNone(viewed['error'])
            listed = self.get(f'/api/v1/tests/{test_id}').json()
            self.assertEqual((listed['output_filename'], listed['output_size_bytes']), ('run.log', 14))

            # Binary or oversized files are refused before anything is stored.
            self.assertEqual(self.client.put(base, headers=self.headers, files={'file': ('bin', b'\x00\xff')}).status_code, 422)
            self.assertEqual(self.client.put(base, headers=self.headers, files={'file': ('big', b'x' * 33)}).status_code, 413)
            self.assertEqual(list(objects), [f'tests/{test_id}/output/run.log'])

            # Replacing under a new name removes the old object.
            replaced = self.client.put(base, headers=self.headers, files={'file': ('second.txt', b'two')})
            self.assertEqual(replaced.status_code, 200, replaced.text)
            self.assertEqual(list(objects), [f'tests/{test_id}/output/second.txt'])
            self.assertEqual(self.get(base).json()['text'], 'two')

            # A missing object is reported, not raised, so it can still be replaced.
            objects.clear()
            client.get_object.side_effect = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
            broken = self.get(base).json()
            self.assertEqual(broken['output']['filename'], 'second.txt')
            self.assertIn('not found', broken['error'])

            with SessionLocal() as db:
                db.add(User(username='output-reader', password_hash=hash_password('reader-password'), role='readonly'))
                db.commit()
            token = self.client.post('/api/v1/auth/login', json={'username': 'output-reader', 'password': 'reader-password'}).json()['access_token']
            reader = {'Authorization': f'Bearer {token}'}
            self.assertEqual(self.client.get(base, headers=reader).status_code, 200)
            self.assertEqual(self.client.put(base, headers=reader, files={'file': ('x', b'x')}).status_code, 403)
            self.assertEqual(self.client.delete(base, headers=reader).status_code, 403)

            self.assertEqual(self.client.delete(base, headers=self.headers).status_code, 204)
            self.assertIsNone(self.get(f'/api/v1/tests/{test_id}').json()['output_filename'])
            self.assertEqual(self.client.delete(base, headers=self.headers).status_code, 404)
            self.assertEqual(self.client.get(f'/api/v1/tests/missing/output', headers=self.headers).status_code, 404)
            self.assertEqual(self.client.get(base).status_code, 401)


class RetiredDeviceOutputFieldTests(SchemaCase):
    def test_an_existing_system_output_field_is_switched_off(self):
        """Output moved from devices to tests; an installation that already had
        the device field loses it on the next start, and the URL stays as data."""
        from sqlalchemy import select
        from app.models import DeviceFieldDefinition
        from app.services.device_schema import get_effective_fields, invalidate_cache, seed_device_schema

        with SessionLocal() as db:
            db.add(DeviceFieldDefinition(
                key='output', label='Output', field_type='text', options=[], validation={},
                sensitive=False, indexed=False, unique_value=False, opens_web_page=False,
                link_scheme='http', link_port=None, plugin_role=None, protected_system_field=True,
                enabled=True, configuration_source='system',
            ))
            db.commit()
            seed_device_schema(db)
            invalidate_cache()
            definition = db.scalar(select(DeviceFieldDefinition).where(DeviceFieldDefinition.key == 'output'))
            self.assertFalse(definition.enabled)
            self.assertNotIn('output', {field.key for field in get_effective_fields(db, None)})
