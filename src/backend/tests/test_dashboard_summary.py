"""Fleet summary counts against a real PostgreSQL database when available."""

from app.db import SessionLocal, utcnow
from app.models import Device
from test_device_schema_api import SchemaCase


class DashboardSummaryTests(SchemaCase):
    def test_fleet_summary_separates_unscanned_from_offline(self):
        ids = [self.post('/api/v1/devices', {'unique_id': f'dashboard-{n}'}).json()['id']
               for n in range(4)]
        with SessionLocal() as db:
            online, offline = (db.get(Device, id_) for id_ in ids[:2])
            online.last_scanned_at = utcnow()
            online.online_status = True
            offline.last_scanned_at = utcnow()
            offline.online_status = False
            db.commit()

        response = self.get('/api/v1/dashboard/fleet-summary')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            {key: response.json()[key] for key in ('total', 'online', 'offline', 'never_scanned')},
            {'total': 4, 'online': 1, 'offline': 1, 'never_scanned': 2},
        )
        self.assertIn('as_of', response.json())
        self.assertEqual(self.client.get('/api/v1/dashboard/fleet-summary').status_code, 401)
