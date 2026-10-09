"""A controller restart must not erase the last known run state."""

from unittest.mock import patch

import httpx
from sqlalchemy import select

from app.api import plugins
from app.db import SessionLocal
from app.models import PluginRun

from test_device_schema_api import SchemaCase


class PluginRunPersistenceTests(SchemaCase):
    def test_missing_controller_run_is_recorded_as_interrupted_without_output(self):
        with SessionLocal() as db:
            plugins._save_run(db, "network-scan", {
                "run_id": "lost-run", "action_id": "scan", "entity_ids": ["device-1"],
                "state": "running", "scanned": 3, "total": 10,
                "output": "secret worker output",
            })
            db.commit()
            request = httpx.Request("GET", "http://plugin/plugin/v1/runs/lost-run")
            missing = httpx.HTTPStatusError("missing", request=request,
                                            response=httpx.Response(404, request=request))
            with patch.object(plugins.registry, "request", side_effect=missing):
                status = plugins.run_status("network-scan", "lost-run", db=db, user=None)
            self.assertEqual(status["state"], "interrupted")
            self.assertEqual(status["scanned"], 3)
            stored = db.scalar(select(PluginRun).where(PluginRun.run_id == "lost-run"))
            self.assertNotIn("secret worker output", str(stored.status))

    def test_temporary_controller_outage_keeps_run_active(self):
        with SessionLocal() as db:
            plugins._save_run(db, "network-scan", {"run_id": "unavailable-run", "state": "running"})
            db.commit()
            with patch.object(plugins.registry, "request", side_effect=LookupError("unavailable")):
                status = plugins.run_status("network-scan", "unavailable-run", db=db, user=None)
            self.assertEqual(status["state"], "running")
            self.assertTrue(status["status_stale"])

    def test_active_list_reconciles_a_finished_run_before_calling_it_interrupted(self):
        with SessionLocal() as db:
            plugins._save_run(db, "network-scan", {"run_id": "finished-run", "state": "running"})
            db.commit()

            def response(_plugin, _method, path):
                if path == "/plugin/v1/runs":
                    return {"runs": []}
                return {"run_id": "finished-run", "state": "completed"}

            with (patch.object(plugins.registry, "manifests", return_value=[{"id": "network-scan"}]),
                  patch.object(plugins.registry, "request", side_effect=response)):
                self.assertEqual(plugins.active_runs(db=db, user=None), [])
            stored = db.scalar(select(PluginRun).where(PluginRun.run_id == "finished-run"))
            self.assertEqual(stored.state, "completed")
