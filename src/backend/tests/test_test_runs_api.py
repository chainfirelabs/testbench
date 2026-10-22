"""Test runs with real PostgreSQL and an in-memory S3 client: a pipeline's
tests grouped under one run, and a report stored once for all of them."""
import io
import json
from unittest.mock import Mock, patch

from app.config import settings
from app.core.security import hash_password
from app.db import SessionLocal
from app.models import Test, TestRun, User
from test_device_schema_api import SchemaCase


class TestRunsApiTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device = cls.post('/api/v1/devices', {'unique_id': 'run-device'}).json()
        cls.other_device = cls.post('/api/v1/devices', {'unique_id': 'run-device-2'}).json()
        cls.software = cls.post('/api/v1/software', {'name': 'Run Software', 'version': '1.0'}).json()

    def record(self, outcome='pass', run=None, device=None):
        response = self.post('/api/v1/tests', {
            'device_id': (device or self.device)['id'], 'software_id': self.software['id'],
            'outcome': outcome, 'test_run_id': run['id'] if run else None})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def storage(self):
        objects = {}
        client = Mock()

        def put(**kwargs):
            objects[kwargs['Key']] = kwargs['Body'].read() if hasattr(kwargs['Body'], 'read') else kwargs['Body']

        def get(**kwargs):
            data = objects[kwargs['Key']]
            return {'Body': io.BytesIO(data), 'ContentLength': len(data)}

        client.put_object.side_effect = put
        client.get_object.side_effect = get
        client.delete_object.side_effect = lambda **kw: objects.pop(kw['Key'], None)
        return objects, [
            patch.multiple(settings, object_storage_enabled=True, object_storage_bucket='runs-bucket',
                           artifact_max_bytes=1024, object_storage_max_bytes=1024),
            patch('app.services.test_artifact_storage.object_storage_client', return_value=client),
        ]

    def test_every_job_of_a_pipeline_joins_the_same_run(self):
        first = self.post('/api/v1/test-runs', {'name': 'Nightly #1234', 'external_id': 'pipeline-1234',
                                                'url': 'https://ci.example/p/1234'})
        self.assertEqual(first.status_code, 201, first.text)
        again = self.post('/api/v1/test-runs', {'name': 'Job 2 says otherwise', 'external_id': 'pipeline-1234'})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['id'], first.json()['id'])
        self.assertEqual(again.json()['name'], 'Nightly #1234')
        self.assertIsNotNone(first.json()['started_at'])
        # Without an external_id, every call is a run of its own.
        a = self.post('/api/v1/test-runs', {'name': 'Ad hoc'}).json()
        b = self.post('/api/v1/test-runs', {'name': 'Ad hoc', 'external_id': '  '}).json()
        self.assertNotEqual(a['id'], b['id'])
        self.assertIsNone(b['external_id'])

    def test_a_run_link_must_be_a_web_address(self):
        response = self.post('/api/v1/test-runs', {'name': 'Bad', 'url': 'javascript:alert(1)'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.post('/api/v1/test-runs', {'name': ''}).status_code, 422)

    def test_tests_belong_to_a_run_and_the_run_counts_them(self):
        run = self.post('/api/v1/test-runs', {'name': 'Counted', 'external_id': 'counted'}).json()
        passed = self.record('pass', run)
        self.record('fail', run, self.other_device)
        self.record('pass', run)
        outside = self.record('warn')
        self.assertEqual((passed['test_run_id'], passed['test_run_name']), (run['id'], 'Counted'))
        self.assertIsNone(outside['test_run_id'])

        counted = self.get(f"/api/v1/test-runs/{run['id']}").json()
        self.assertEqual((counted['test_count'], counted['pass_count'], counted['fail_count'],
                          counted['warn_count']), (3, 2, 1, 0))
        in_run = self.get(f"/api/v1/tests?test_run_id={run['id']}").json()
        self.assertEqual(in_run['total'], 3)
        outside_ids = {item['id'] for item in self.get('/api/v1/tests?test_run_id=none&page_size=1000').json()['items']}
        self.assertIn(outside['id'], outside_ids)
        self.assertNotIn(passed['id'], outside_ids)
        # The Test Run column filters like any other relationship column.
        include = json.dumps(['Counted'])
        self.assertEqual(self.get(f'/api/v1/tests?include__test_run_name={include}').json()['total'], 3)
        values = self.get(f"/api/v1/suggestions/tests/test_run_name/filter-values?test_run_id={run['id']}").json()['values']
        self.assertEqual(values, ['Counted'])

        listed = self.get('/api/v1/test-runs?search=count&sort=test_count&order=desc').json()
        self.assertEqual([item['id'] for item in listed['items']], [run['id']])

        self.assertEqual(self.post('/api/v1/tests', {
            'device_id': self.device['id'], 'software_id': self.software['id'], 'outcome': 'pass',
            'test_run_id': 'no-such-run'}).status_code, 400)

    def test_a_test_moves_between_runs_and_out_of_them(self):
        one = self.post('/api/v1/test-runs', {'name': 'One'}).json()
        two = self.post('/api/v1/test-runs', {'name': 'Two'}).json()
        test = self.record('pass', one)
        moved = self.patch_(f"/api/v1/tests/{test['id']}", {'test_run_id': two['id']}).json()
        self.assertEqual(moved['test_run_name'], 'Two')
        # Leaving other fields out leaves the run alone; null takes it out.
        kept = self.patch_(f"/api/v1/tests/{test['id']}", {'notes': 'still in two'}).json()
        self.assertEqual(kept['test_run_id'], two['id'])
        removed = self.patch_(f"/api/v1/tests/{test['id']}", {'test_run_id': None}).json()
        self.assertIsNone(removed['test_run_id'])
        self.assertEqual(self.patch_(f"/api/v1/tests/{test['id']}", {'test_run_id': 'nope'}).status_code, 400)

    def test_renaming_and_reidentifying_a_run(self):
        run = self.post('/api/v1/test-runs', {'name': 'Before', 'external_id': 'rename-me'}).json()
        self.post('/api/v1/test-runs', {'name': 'Taken', 'external_id': 'taken'})
        renamed = self.patch_(f"/api/v1/test-runs/{run['id']}", {
            'name': 'After', 'finished_at': '2026-10-21T02:41:00-04:00'}).json()
        self.assertEqual(renamed['name'], 'After')
        self.assertTrue(renamed['finished_at'].startswith('2026-10-21T06:41:00'))
        self.assertEqual(self.patch_(f"/api/v1/test-runs/{run['id']}", {'external_id': 'taken'}).status_code, 409)
        self.assertEqual(self.patch_(f"/api/v1/test-runs/{run['id']}", {'name': ' '}).status_code, 422)
        with SessionLocal() as db:
            # The bucket folder keeps the name the run was created with.
            self.assertEqual(db.get(TestRun, run['id']).storage_folder, f"test-runs/Before_{run['id']}")

    def test_shared_files_are_stored_once_in_the_runs_folder_beside_its_tests_files(self):
        objects, patches = self.storage()
        run = self.post('/api/v1/test-runs', {'name': 'Nightly #77', 'external_id': 'files-77'}).json()
        folder = f"test-runs/Nightly-77_{run['id']}"
        first, second = self.record('pass', run), self.record('fail', run, self.other_device)
        loose = self.record('pass')
        with patches[0], patches[1]:
            report = self.client.post(f"/api/v1/test-runs/{run['id']}/artifacts", headers=self.headers,
                                      files={'file': ('pytest report.html', b'<html>ok</html>', 'text/html')})
            self.assertEqual(report.status_code, 201, report.text)
            report = report.json()
            own = self.client.post(f"/api/v1/tests/{first['id']}/artifacts", headers=self.headers,
                                   files={'file': ('capture.pcap', b'pcap')}).json()
            output = self.client.put(f"/api/v1/tests/{first['id']}/output", headers=self.headers,
                                     files={'file': ('run.log', b'all good\n')})
            self.assertEqual(output.status_code, 200, output.text)
            self.client.post(f"/api/v1/tests/{loose['id']}/artifacts", headers=self.headers,
                             files={'file': ('loose.txt', b'x')})
            self.assertEqual(sorted(objects), sorted([
                f"{folder}/artifacts/{report['id']}/pytest_report.html",
                f"{folder}/tests/{first['id']}/artifacts/{own['id']}/capture.pcap",
                f"{folder}/tests/{first['id']}/output/run.log",
                next(key for key in objects if key.startswith(f"tests/{loose['id']}/artifacts/")),
            ]))

            # Each test in the run sees the one report alongside its own files.
            listed = self.get(f"/api/v1/tests/{second['id']}/artifacts").json()
            self.assertEqual(listed['items'], [])
            self.assertEqual(listed['test_run'], {'id': run['id'], 'name': 'Nightly #77'})
            self.assertEqual([item['id'] for item in listed['test_run_items']], [report['id']])
            self.assertEqual(self.get(f"/api/v1/tests/{first['id']}").json()['test_run_artifact_count'], 1)
            self.assertEqual(self.get(f"/api/v1/tests/{loose['id']}/artifacts").json()['test_run_items'], [])
            self.assertEqual(self.get(f"/api/v1/test-runs/{run['id']}").json()['artifact_count'], 1)

            path = f"/api/v1/test-runs/{run['id']}/artifacts/{report['id']}/download"
            downloaded = self.get(path)
            self.assertEqual(downloaded.content, b'<html>ok</html>')
            self.assertIn("filename*=UTF-8''pytest%20report.html", downloaded.headers['content-disposition'])
            other = self.post('/api/v1/test-runs', {'name': 'Elsewhere'}).json()
            self.assertEqual(self.get(f"/api/v1/test-runs/{other['id']}/artifacts/{report['id']}/download").status_code, 404)

            # A reader can see and download, but not change, a run.
            with SessionLocal() as db:
                db.add(User(username='run-reader', password_hash=hash_password('reader-password'), role='readonly'))
                db.commit()
            token = self.client.post('/api/v1/auth/login', json={
                'username': 'run-reader', 'password': 'reader-password'}).json()['access_token']
            reader = {'Authorization': f'Bearer {token}'}
            self.assertEqual(self.client.get(path, headers=reader).status_code, 200)
            self.assertEqual(self.client.get('/api/v1/test-runs', headers=reader).status_code, 200)
            self.assertEqual(self.client.post('/api/v1/test-runs', headers=reader, json={'name': 'x'}).status_code, 403)
            self.assertEqual(self.client.post(f"/api/v1/test-runs/{run['id']}/artifacts", headers=reader,
                                              files={'file': ('x', b'x')}).status_code, 403)
            self.assertEqual(self.client.delete(f"/api/v1/test-runs/{run['id']}", headers=reader).status_code, 403)

            # Deleting a test leaves the report for the rest of the run.
            self.assertEqual(self.client.delete(f"/api/v1/tests/{second['id']}", headers=self.headers).status_code, 204)
            self.assertEqual(self.get(path).status_code, 200)

            deleted = self.client.delete(f"/api/v1/test-runs/{run['id']}/artifacts/{report['id']}", headers=self.headers)
            self.assertEqual(deleted.status_code, 204)
            self.assertNotIn(f"{folder}/artifacts/{report['id']}/pytest_report.html", objects)
            self.assertEqual(self.get(path).status_code, 404)

            with patch.object(settings, 'object_storage_enabled', False):
                self.assertFalse(self.get(f"/api/v1/test-runs/{run['id']}/artifacts").json()['storage_enabled'])
                self.assertEqual(self.client.post(f"/api/v1/test-runs/{run['id']}/artifacts", headers=self.headers,
                                                  files={'file': ('x', b'x')}).status_code, 503)

    def test_deleting_a_run_keeps_its_tests(self):
        run = self.post('/api/v1/test-runs', {'name': 'Short lived'}).json()
        test = self.record('pass', run)
        self.assertEqual(self.client.delete(f"/api/v1/test-runs/{run['id']}", headers=self.headers).status_code, 204)
        self.assertEqual(self.get(f"/api/v1/test-runs/{run['id']}").status_code, 404)
        kept = self.get(f"/api/v1/tests/{test['id']}").json()
        self.assertIsNone(kept['test_run_id'])
        # Marked as changed, so replication carries the change to its peers.
        self.assertIsNotNone(kept['updated_at'])
        with SessionLocal() as db:
            self.assertIsNotNone(db.get(Test, test['id']))

    def test_exporting_a_runs_tests(self):
        run = self.post('/api/v1/test-runs', {'name': 'Exported'}).json()
        test = self.record('pass', run)
        self.record('pass')
        exported = self.client.post('/api/v1/tests/export?format=json', headers=self.headers,
                                    json={'filters': {'test_run_id': run['id']}, 'ids': None})
        self.assertEqual(exported.status_code, 200, exported.text)
        rows = exported.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['device_unique_id'], rows[0]['test_run_name']), (test['device_unique_id'], 'Exported'))

    def test_the_test_run_column_is_in_the_tests_catalog(self):
        fields = {field['key']: field for field in self.get('/api/v1/entity-fields/tests').json()}
        self.assertTrue(fields['test_run_name']['visible'])
        self.assertFalse(fields['test_run_name']['writable'])
