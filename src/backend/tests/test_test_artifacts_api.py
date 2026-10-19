"""Artifact API with real PostgreSQL and an in-memory S3 client."""
import io
from unittest.mock import Mock, patch

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import TestArtifact as Artifact, User
from app.core.security import hash_password
from test_device_schema_api import SchemaCase


class TestArtifactsApiTests(SchemaCase):
    def test_upload_list_download_delete_and_permissions(self):
        device = self.post('/api/v1/devices', {'unique_id': 'artifact-device'}).json()
        software = self.post('/api/v1/software', {'name': 'Artifact Software'}).json()
        test_ids = [self.post('/api/v1/tests', {'device_id': device['id'], 'software_id': software['id'],
                                             'outcome': 'pass'}).json()['id'] for _ in range(2)]
        base = f'/api/v1/tests/{test_ids[0]}/artifacts'
        objects = {}
        client = Mock()

        def put(**kwargs):
            objects[kwargs['Key']] = kwargs['Body'].read()

        def get(**kwargs):
            data = objects[kwargs['Key']]
            return {'Body': io.BytesIO(data), 'ContentLength': len(data)}

        client.put_object.side_effect = put
        client.get_object.side_effect = get
        client.delete_object.side_effect = lambda **kw: objects.pop(kw['Key'], None)
        with patch.multiple(settings, object_storage_enabled=True, object_storage_bucket='artifact-bucket',
                            artifact_max_bytes=16), patch('app.services.test_artifact_storage.object_storage_client', return_value=client):
            payload = b'\x00\xffbinary.zip'
            uploaded = self.client.post(base, headers=self.headers, files={'file': ('../report.zip', payload, 'application/zip')})
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            artifact = uploaded.json()
            self.assertEqual(artifact['filename'], 'report.zip')
            self.assertEqual(artifact['size_bytes'], len(payload))
            self.assertNotIn('object_key', artifact)
            self.assertEqual(list(objects), [f"tests/{test_ids[0]}/artifacts/{artifact['id']}/report.zip"])
            second = self.client.post(base, headers=self.headers, files={'file': ('report.zip', b'other')})
            self.assertEqual(second.status_code, 201, second.text)
            self.assertEqual(len(objects), 2)
            listed = self.get(base).json()
            self.assertEqual(len(listed['items']), 2)
            self.assertEqual(self.get(f'/api/v1/tests/{test_ids[0]}').json()['artifact_count'], 2)
            path = f"{base}/{artifact['id']}/download"
            response = self.get(path)
            self.assertEqual(response.content, payload)
            self.assertIn('attachment;', response.headers['content-disposition'])
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.get(f"/api/v1/tests/{test_ids[1]}/artifacts/{artifact['id']}/download").status_code, 404)
            oversized = self.client.post(base, headers=self.headers, files={'file': ('large', b'x' * 17)})
            self.assertEqual(oversized.status_code, 413)
            self.assertEqual(len(objects), 2)

            with SessionLocal() as db:
                db.add(User(username='artifact-reader', password_hash=hash_password('reader-password'), role='readonly'))
                db.commit()
            token = self.client.post('/api/v1/auth/login', json={'username': 'artifact-reader', 'password': 'reader-password'}).json()['access_token']
            reader = {'Authorization': f'Bearer {token}'}
            self.assertEqual(self.client.get(path, headers=reader).content, payload)
            self.assertEqual(self.client.post(base, headers=reader, files={'file': ('nope', b'x')}).status_code, 403)
            self.assertEqual(self.client.delete(f"{base}/{artifact['id']}", headers=reader).status_code, 403)
            self.assertEqual(self.client.delete(f"{base}/{artifact['id']}", headers=self.headers).status_code, 204)
            self.assertEqual(len(objects), 1)
            self.assertEqual(self.get(path).status_code, 404)
            self.assertEqual(self.get(f'/api/v1/tests/{test_ids[0]}').json()['artifact_count'], 1)
            with patch.object(settings, 'object_storage_enabled', False):
                self.assertFalse(self.get(base).json()['storage_enabled'])
                self.assertEqual(self.client.post(base, headers=self.headers, files={'file': ('disabled', b'x')}).status_code, 503)

            # Whole-test deletion drops metadata but retains S3 objects for the
            # operator's retention policy, including cascades from device/software.
            self.assertEqual(self.client.delete(f'/api/v1/tests/{test_ids[0]}', headers=self.headers).status_code, 204)
            with SessionLocal() as db:
                self.assertIsNone(db.scalar(select(Artifact).where(Artifact.test_id == test_ids[0])))
            self.assertEqual(len(objects), 1)
