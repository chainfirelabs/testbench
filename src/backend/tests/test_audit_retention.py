"""Retention settings and batched deletion against PostgreSQL."""

from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import func, select

from test_device_schema_api import SchemaCase


class AuditRetentionTests(SchemaCase):
    def test_worker_recovers_queued_job_after_request_process_exits(self):
        from app.db import SessionLocal, utcnow
        from app.models import AuditCleanupJob, AuditLog
        from app.services.audit_cleanup_jobs import process_pending_cleanup_jobs

        old = utcnow() - timedelta(days=30)
        with SessionLocal() as db:
            db.add(AuditLog(timestamp=old, username="system", action="recovery.fixture"))
            job = AuditCleanupJob(before=utcnow() - timedelta(days=20),
                                  delete_device_changelogs=False, requested_by="admin", total=1)
            db.add(job)
            db.commit()
            job_id = job.id
        process_pending_cleanup_jobs()
        with SessionLocal() as db:
            recovered = db.get(AuditCleanupJob, job_id)
            self.assertEqual((recovered.state, recovered.deleted), ("completed", 1))
            self.assertEqual(db.scalar(select(func.count()).select_from(AuditLog)
                                       .where(AuditLog.action == "recovery.fixture")), 0)

    def test_retention_and_manual_cleanup(self):
        from app.db import SessionLocal, utcnow
        from app.models import AuditLog
        from app.services.audit_retention import run_scheduled_cleanup

        endpoint = "/api/v1/audit_logs"
        self.assertEqual(self.get(f"{endpoint}/retention").json(), {
            "days": 0, "delete_device_changelogs": False,
        })
        device = self.post("/api/v1/devices", {
            "unique_id": "retention-device", "make": "Dell", "model": "R640",
        }).json()
        old = utcnow() - timedelta(days=30)
        with SessionLocal() as db:
            db.add_all([
                AuditLog(timestamp=old + timedelta(seconds=i), username="system", action="retention.fixture")
                for i in range(503)
            ])
            db.add(AuditLog(timestamp=old, username="system", action="retention.device.fixture",
                            entity_type="device", entity_id=device["id"]))
            db.commit()
            self.assertEqual(run_scheduled_cleanup(db), 0)

        payload = {"before": (utcnow() - timedelta(days=20)).isoformat()}
        preview = self.post(f"{endpoint}/cleanup/preview", payload).json()
        self.assertEqual(preview["eligible"], 503)
        self.assertEqual(preview["protected"], 1)
        prior_manual_batches = self.get(f"{endpoint}?action=audit_logs.cleanup.manual").json()["total"]
        result = self.post(f"{endpoint}/cleanup", payload)
        self.assertEqual(result.status_code, 202, result.text)
        job = self.get(f"{endpoint}/cleanup/jobs/{result.json()['id']}").json()
        self.assertEqual(job["state"], "completed")
        self.assertEqual(job["deleted"], 503)
        self.assertEqual(self.get(f"{endpoint}?action=retention.fixture").json()["total"], 0)
        self.assertEqual(self.get(f"{endpoint}?action=audit_logs.cleanup.manual").json()["total"], prior_manual_batches + 2)

        with SessionLocal() as db:
            db.add_all([
                AuditLog(timestamp=old, username="system", action="retention.auto.fixture")
                for _ in range(2)
            ])
            db.commit()
        changed = self.put(f"{endpoint}/retention", {"days": 1})
        self.assertEqual(changed.status_code, 200, changed.text)
        with SessionLocal() as db:
            self.assertEqual(run_scheduled_cleanup(db), 2)
        self.assertEqual(self.get(f"{endpoint}?action=retention.auto.fixture").json()["total"], 0)
        self.assertEqual(self.get(f"{endpoint}?action=audit_logs.cleanup.auto").json()["total"], 1)
        self.assertEqual(self.get(f"{endpoint}?action=retention.device.fixture").json()["total"], 1)
        changelog = self.get(f"/api/v1/devices/{device['id']}/changelog").json()["items"]
        self.assertIn("retention.device.fixture", {item["action"] for item in changelog})

        unsafe = self.post(f"{endpoint}/cleanup", {
            "before": (utcnow() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(unsafe.status_code, 422)

        old_limit = self.post(f"{endpoint}/cleanup", {**payload, "max_rows": 1})
        self.assertEqual(old_limit.status_code, 422)

        cannot_override_helm = self.put(f"{endpoint}/retention", {
            "days": 0, "delete_device_changelogs": True,
        })
        self.assertEqual(cannot_override_helm.status_code, 422)

    def test_helm_opt_in_allows_deleting_changelog(self):
        from app.db import SessionLocal, utcnow
        from app.models import AuditLog
        from app.config import settings

        device = self.post("/api/v1/devices", {
            "unique_id": "retention-opt-in", "make": "Dell", "model": "R650",
        }).json()
        with SessionLocal() as db:
            db.add(AuditLog(timestamp=utcnow() - timedelta(days=40), username="system",
                            action="retention.opt_in.fixture", entity_type="device", entity_id=device["id"]))
            db.commit()
        payload = {"before": (utcnow() - timedelta(days=35)).isoformat()}
        self.assertEqual(self.post("/api/v1/audit_logs/cleanup/preview", payload).json()["eligible"], 0)
        with patch.object(settings, "audit_delete_device_changelogs", True):
            preview = self.post("/api/v1/audit_logs/cleanup/preview", payload).json()
            self.assertEqual(preview["eligible"], 1)
            self.assertTrue(preview["delete_device_changelogs"])
            response = self.post("/api/v1/audit_logs/cleanup", payload)
            deleted = self.get(f"/api/v1/audit_logs/cleanup/jobs/{response.json()['id']}").json()["deleted"]
            self.assertEqual(deleted, 1)
        self.assertEqual(self.get("/api/v1/audit_logs?action=retention.opt_in.fixture").json()["total"], 0)

    def test_readonly_cannot_manage_logs(self):
        self.post("/api/v1/users", {
            "username": "audit-reader", "password": "password123", "role": "readonly",
        })
        token = self.client.post("/api/v1/auth/login", json={
            "username": "audit-reader", "password": "password123",
        }).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.put(
            "/api/v1/audit_logs/retention", headers=headers, json={"days": 1},
        ).status_code, 403)
        self.assertEqual(self.client.post(
            "/api/v1/audit_logs/cleanup", headers=headers,
            json={"before": "2020-01-01T00:00:00Z"},
        ).status_code, 403)
