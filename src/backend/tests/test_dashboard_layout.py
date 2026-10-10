"""Shared dashboard editing against PostgreSQL, skipped when unavailable."""

from sqlalchemy import select

from app.db import SessionLocal
from app.api.dashboard import DashboardWidget
from app.models import AuditLog
from test_device_schema_api import SchemaCase


class DashboardLayoutTests(SchemaCase):
    def test_widgets_can_be_saved_and_reordered(self):
        endpoint = '/api/v1/dashboard/layout'
        revision = self.get(endpoint).json()['revision']
        widgets = [
            {'id': 'devices-by-make', 'type': 'devices_by_make', 'title': 'Makes', 'description': ''},
            {'id': 'most-tested-devices', 'type': 'most_tested_devices', 'title': 'Most tested', 'description': '', 'top_n': 5},
        ]
        saved = self.put(endpoint, {'revision': revision, 'widgets': widgets})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual([item['type'] for item in saved.json()['widgets']],
                         ['devices_by_make', 'most_tested_devices'])
        reordered = self.put(endpoint, {'revision': revision + 1, 'widgets': list(reversed(widgets))})
        self.assertEqual(reordered.status_code, 200, reordered.text)
        self.assertEqual(reordered.json()['widgets'][0]['type'], 'most_tested_devices')
        duplicate = self.put(endpoint, {'revision': revision + 2, 'widgets': [widgets[0], widgets[0]]})
        self.assertEqual(duplicate.status_code, 422)

    def test_admin_can_select_and_edit_the_fleet_widget(self):
        endpoint = '/api/v1/dashboard/layout'
        initial = self.get(endpoint)
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.json()['revision'], 0)
        self.assertEqual(initial.json()['widgets'][0]['type'], 'fleet_summary')
        legacy = DashboardWidget.model_validate({
            'id': 'fleet-summary', 'type': 'fleet_summary',
            'title': 'Fleet summary', 'description': '', 'width': 'half',
        })
        self.assertEqual(legacy.width_percent, 50)

        selected = [{
            'id': 'fleet-summary', 'type': 'fleet_summary',
            'title': '  Lab fleet  ', 'description': '  Current devices  ',
            'width_percent': 50, 'height_px': 420, 'show_breakdown': False,
        }]
        saved = self.client.put(endpoint, headers=self.headers, json={
            'revision': 0, 'widgets': selected,
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['revision'], 1)
        self.assertEqual(saved.json()['widgets'][0]['title'], 'Lab fleet')
        self.assertEqual(saved.json()['widgets'][0]['description'], 'Current devices')
        self.assertEqual(saved.json()['widgets'][0]['width_percent'], 50)
        self.assertEqual(saved.json()['widgets'][0]['height_px'], 420)
        self.assertFalse(saved.json()['widgets'][0]['show_breakdown'])
        self.assertEqual(self.get(endpoint).json(), saved.json())
        with SessionLocal() as db:
            audit = db.scalar(select(AuditLog).where(AuditLog.action == 'dashboard.layout.update'))
            self.assertIsNotNone(audit)
            self.assertEqual(audit.detail['revision'], 1)

        for role in ('readonly', 'tester'):
            with self.subTest(role=role):
                username = f'dashboard_{role}'
                created = self.post('/api/v1/users', {
                    'username': username, 'password': 'dashboard-password-123', 'role': role,
                })
                self.assertEqual(created.status_code, 201, created.text)
                login = self.client.post('/api/v1/auth/login', json={
                    'username': username, 'password': 'dashboard-password-123',
                })
                self.assertEqual(login.status_code, 200, login.text)
                headers = {'Authorization': f"Bearer {login.json()['access_token']}"}
                self.assertEqual(self.client.get(endpoint, headers=headers).status_code, 200)
                self.assertEqual(self.client.post('/api/v1/dashboard/data', headers=headers, json={
                    'widgets': selected,
                }).status_code, 200)
                self.assertEqual(self.client.put(endpoint, headers=headers, json={
                    'revision': 1, 'widgets': [],
                }).status_code, 403)
                if role == 'readonly':
                    reader_headers = headers

        stale = self.client.put(endpoint, headers=self.headers, json={
            'revision': 0, 'widgets': [],
        })
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self.get(endpoint).json()['revision'], 1)

        for bad_widgets in [
            [{'id': 'other', 'type': 'fleet_summary', 'title': 'Other', 'description': ''}],
            [{**selected[0], 'title': '   '}],
            [{**selected[0], 'width_percent': 10}],
            [{**selected[0], 'height_px': 100}],
            [selected[0], selected[0]],
        ]:
            with self.subTest(widgets=bad_widgets):
                response = self.client.put(endpoint, headers=self.headers, json={
                    'revision': 1, 'widgets': bad_widgets,
                })
                self.assertEqual(response.status_code, 422, response.text)

        removed = self.client.put(endpoint, headers=self.headers, json={
            'revision': 1, 'widgets': [],
        })
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json(), {'revision': 2, 'widgets': []})
        self.assertEqual(self.client.get(endpoint, headers=reader_headers).json(), removed.json())
