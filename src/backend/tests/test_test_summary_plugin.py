"""AI Test Summary: a global plugin acting on test runs rather than devices.

Real PostgreSQL, an in-memory S3 client and a stand-in for the plugin service:
what the backend offers, refuses, sends, compiles and keeps.
"""
import io
from unittest.mock import Mock, patch

from sqlalchemy import select

from app.api import plugins
from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, PluginRun, TestRunArtifact as RunFile
from test_device_schema_api import SchemaCase

MANIFEST = {
    "id": "test-summary", "label": "AI Test Summary", "version": "1.0.0", "protocol_version": 1,
    "global": True, "endpoint": "http://test-summary:8080",
    "ai_configuration": {"locked": False, "provider_type": "openai-compatible", "url": "", "model": ""},
    "actions": [{"id": "test-summary.summarize", "entity": "test_runs", "scope": "row",
                 "label": "Summarize with AI", "risk": "read_only"}],
}
PLUGIN_HEADERS = {"X-TestBench-Plugin": "test-summary", "X-TestBench-Plugin-Secret": "shared"}


class TestSummaryPluginTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name, role in (("tess", "tester"), ("rhonda", "readonly")):
            cls.post("/api/v1/users", {"username": name, "password": "password123", "role": role})
        cls.tester = cls.sign_in("tess")
        cls.reader = cls.sign_in("rhonda")
        created = cls.post("/api/v1/device-fields", {
            "key": "api_secret", "label": "API Secret", "field_type": "text", "sensitive": True})
        assert created.status_code == 201, created.text
        cls.router = cls.post("/api/v1/devices", {
            "unique_id": "sum-router", "device_type": "router",
            "data": {"make": "Acme", "api_secret": "hunter2"}}).json()
        cls.phone = cls.post("/api/v1/devices", {
            "unique_id": "sum-phone", "device_type": "mobile", "data": {"make": "Fone"}}).json()
        cls.software = cls.post("/api/v1/software", {"name": "Agent", "version": "5.0"}).json()

    @classmethod
    def sign_in(cls, username):
        token = cls.client.post("/api/v1/auth/login", json={
            "username": username, "password": "password123"}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def setUp(self):
        self.patches = [
            patch.object(plugins.registry, "manifests", return_value=[MANIFEST]),
            patch.object(plugins.registry, "manifest",
                         side_effect=lambda plugin_id: MANIFEST if plugin_id == "test-summary" else None),
            patch.multiple(settings, plugins="test-summary=http://test-summary:8080,network-scan=http://scan:8080",
                           plugin_shared_secret="shared", credential_encryption_key="test-key"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def run_with_tests(self, name):
        run = self.post("/api/v1/test-runs", {"name": name}).json()
        tests = [self.post("/api/v1/tests", {
            "device_id": device["id"], "software_id": self.software["id"], "outcome": outcome,
            "test_run_id": run["id"], "notes": note,
            "misc_data": {"jitter_ms": 4}}).json()
            for device, outcome, note in ((self.phone, "pass", None), (self.router, "fail", "link dropped"))]
        return run, tests

    def storage(self):
        objects = {}
        client = Mock()

        def put(**kwargs):
            body = kwargs["Body"]
            objects[kwargs["Key"]] = body.read() if hasattr(body, "read") else body

        client.put_object.side_effect = put
        client.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(objects[kw["Key"]]),
                                                      "ContentLength": len(objects[kw["Key"]])}
        return objects, [
            patch.multiple(settings, object_storage_enabled=True, object_storage_bucket="runs-bucket",
                           artifact_max_bytes=10_000, object_storage_max_bytes=10_000),
            patch("app.services.test_artifact_storage.object_storage_client", return_value=client),
        ]

    def configure_ai(self):
        profile = self.post("/api/v1/ai/providers", {
            "name": "Summary AI", "provider_type": "openai-compatible", "base_url": "https://ai.example/v1",
            "api_key": "sk-test", "custom_headers": {}, "manual_models": ["gpt-test"], "enabled": True})
        self.assertEqual(profile.status_code, 201, profile.text)
        saved = self.client.put("/api/v1/ai/plugin-defaults/test-summary", headers=self.headers, json={
            "profile_id": profile.json()["id"], "model": "gpt-test"})
        self.assertEqual(saved.status_code, 200, saved.text)

    # -- offered ----------------------------------------------------------

    def test_offered_on_every_run_to_whoever_records_tests(self):
        offered = self.client.get("/api/v1/plugins/actions?entity=test_runs", headers=self.tester).json()
        self.assertEqual([action["id"] for action in offered], ["test-summary.summarize"])
        self.assertNotIn("unavailable_reason", offered[0])
        self.assertEqual(offered[0]["device_types"], [])
        refused = self.client.get("/api/v1/plugins/actions?entity=test_runs", headers=self.reader).json()
        self.assertEqual(refused[0]["unavailable_reason"], "Permission to record tests is required")
        on_devices = self.client.get("/api/v1/plugins/actions?entity=devices", headers=self.tester).json()
        self.assertEqual(on_devices, [])

    def test_not_listed_among_the_plugins_a_device_type_enables(self):
        router_type = next(item for item in self.get("/api/v1/device-types").json()
                           if item["key"] == "router")
        listed = self.get(f"/api/v1/device-schema/types/{router_type['id']}/plugins").json()["plugins"]
        self.assertNotIn("test-summary", [row["plugin_id"] for row in listed])

    # -- invoked ----------------------------------------------------------

    def test_a_tester_starts_a_summary_with_the_ai_providers_page_settings(self):
        self.configure_ai()
        run, _ = self.run_with_tests("Invoke run")
        sent = {}

        def request(plugin_id, method, path, payload=None):
            sent.update(plugin_id=plugin_id, path=path, payload=payload)
            return {"run_id": "sum-run-1", "state": "starting", "entity_ids": [run["id"]]}

        with patch.object(plugins.registry, "request", side_effect=request):
            started = self.client.post("/api/v1/plugins/test-summary/actions/test-summary.summarize/invoke",
                                       headers=self.tester, json={"entity": "test_runs", "entity_id": run["id"]})
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(sent["path"], "/plugin/v1/actions/test-summary.summarize/invoke")
        entity = sent["payload"]["entities"][0]
        self.assertEqual((entity["type"], entity["id"], entity["name"]), ("test_run", run["id"], "Invoke run"))
        self.assertEqual(entity["_ai_configuration"]["model"], "gpt-test")
        self.assertEqual(entity["_ai_configuration"]["api_key"], "sk-test")
        self.assertEqual(sent["payload"]["actor"]["username"], "tess")
        with SessionLocal() as db:
            stored = db.scalar(select(PluginRun).where(PluginRun.run_id == "sum-run-1"))
            self.assertEqual(stored.status["entity_ids"], [run["id"]])
            audit = db.scalar(select(AuditLog).where(AuditLog.action == "plugin.action.invoke",
                                                     AuditLog.entity_id == run["id"]))
            self.assertEqual(audit.entity_type, "test_run")

    def test_without_an_ai_model_the_summary_is_refused_before_the_plugin_is_called(self):
        run, _ = self.run_with_tests("No AI run")
        with SessionLocal() as db:
            from app.models import AiPluginDefault
            db.query(AiPluginDefault).filter(AiPluginDefault.plugin_id == "test-summary").delete()
            db.commit()
        with patch.object(plugins.registry, "request") as request:
            refused = self.client.post("/api/v1/plugins/test-summary/actions/test-summary.summarize/invoke",
                                       headers=self.tester, json={"entity": "test_runs", "entity_id": run["id"]})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("No enabled AI provider profile", refused.json()["detail"])
        request.assert_not_called()

    def test_refusals(self):
        run, _ = self.run_with_tests("Refused run")
        path = "/api/v1/plugins/test-summary/actions/test-summary.summarize/invoke"
        with patch.object(plugins.registry, "request") as request:
            reader = self.client.post(path, headers=self.reader, json={"entity": "test_runs", "entity_id": run["id"]})
            self.assertEqual(reader.status_code, 403)
            wrong_entity = self.client.post(path, headers=self.tester, json={"entity": "devices",
                                                                             "entity_id": self.router["id"]})
            self.assertEqual(wrong_entity.status_code, 422)
            missing = self.client.post(path, headers=self.tester, json={"entity": "test_runs", "entity_id": "nope"})
            self.assertEqual(missing.status_code, 404)
            unknown = self.client.post(path, headers=self.tester, json={"entity": "software", "entity_id": "x"})
            self.assertEqual(unknown.status_code, 422)
            request.assert_not_called()

    def test_stopping_a_summary_takes_the_permission_starting_it_did(self):
        with SessionLocal() as db:
            plugins._save_run(db, "test-summary", {"run_id": "stop-me", "action_id": "test-summary.summarize",
                                                   "state": "running"})
            db.commit()
        with patch.object(plugins.registry, "request", return_value={"run_id": "stop-me", "state": "cancelled"}):
            refused = self.client.delete("/api/v1/plugins/test-summary/runs/stop-me", headers=self.reader)
            self.assertEqual(refused.status_code, 403)
            stopped = self.client.delete("/api/v1/plugins/test-summary/runs/stop-me", headers=self.tester)
            self.assertEqual(stopped.status_code, 200, stopped.text)
            self.assertEqual(stopped.json()["state"], "cancelled")

    # -- what the plugin reads ---------------------------------------------

    def test_the_report_has_every_test_device_output_and_file_but_no_secrets(self):
        run, (passed, failed) = self.run_with_tests("Report run")
        objects, storage = self.storage()
        for item in storage:
            item.start()
        try:
            def upload(path, filename, content, content_type="text/plain", method="post"):
                send = getattr(self.client, method)
                response = send(path, headers=self.headers, files={"file": (filename, content, content_type)})
                self.assertIn(response.status_code, (200, 201), response.text)

            upload(f"/api/v1/tests/{failed['id']}/output", "out.txt", b"boot\nERROR link down\n", method="put")
            upload(f"/api/v1/tests/{failed['id']}/artifacts", "trace.log", b"retry 1\nretry 2\n")
            upload(f"/api/v1/tests/{failed['id']}/artifacts", "shot.png", b"\x89PNG....", "image/png")
            upload(f"/api/v1/test-runs/{run['id']}/artifacts", "junit.xml", b"<testsuite failures='1'/>",
                   "application/xml")
            upload(f"/api/v1/test-runs/{run['id']}/artifacts", "ai-summary-2026-01-01T00-00-00Z.md",
                   b"# old summary", "text/markdown")

            unsigned = self.client.get(f"/api/v1/plugin-host/test-summary/test-runs/{run['id']}/report")
            # The plugin headers are required, so an unsigned call never gets in.
            self.assertEqual(unsigned.status_code, 422)
            other = {"X-TestBench-Plugin": "network-scan", "X-TestBench-Plugin-Secret": "shared"}
            self.assertEqual(self.client.get(f"/api/v1/plugin-host/test-summary/test-runs/{run['id']}/report",
                                             headers=other).status_code, 403)
            response = self.client.get(f"/api/v1/plugin-host/test-summary/test-runs/{run['id']}/report",
                                       headers=PLUGIN_HEADERS)
        finally:
            for item in reversed(storage):
                item.stop()
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["run"]["name"], "Report run")
        # Failures first, so a large run keeps their text.
        self.assertEqual([test["outcome"] for test in report["tests"]], ["fail", "pass"])
        first = report["tests"][0]
        self.assertEqual(first["software_name"], "Agent")
        self.assertEqual(first["notes"], "link dropped")
        self.assertEqual(first["other_values"], {"jitter_ms": 4})
        self.assertEqual(first["output"]["text"], "boot\nERROR link down\n")
        files = {file["filename"]: file for file in first["artifacts"]}
        self.assertEqual(files["trace.log"]["text"], "retry 1\nretry 2\n")
        self.assertNotIn("text", files["shot.png"])
        self.assertIn("binary", files["shot.png"]["note"])
        self.assertEqual([file["filename"] for file in report["shared_files"]], ["junit.xml"])
        self.assertEqual(report["shared_files"][0]["text"], "<testsuite failures='1'/>")
        devices = {device["unique_id"]: device for device in report["devices"].values()}
        self.assertEqual(set(devices), {"sum-router", "sum-phone"})
        self.assertEqual({devices["sum-router"]["device_type"], devices["sum-phone"]["device_type"]},
                         {"router", "mobile"})
        self.assertNotIn("hunter2", response.text)
        self.assertNotIn("api_secret", [field["key"] for field in report["device_fields"]])

    # -- what the plugin keeps ---------------------------------------------

    def test_every_summary_is_kept_as_a_new_shared_file(self):
        run, _ = self.run_with_tests("Saved run")
        with SessionLocal() as db:
            from app.models import User
            tess_id = db.scalar(select(User.id).where(User.username == "tess"))
        objects, storage = self.storage()
        for item in storage:
            item.start()
        try:
            path = f"/api/v1/plugin-host/test-summary/test-runs/{run['id']}/summaries"
            body = {"run_id": "plugin-run-1", "actor_id": tess_id, "markdown": "# Summary one", "model": "gpt-test"}
            first = self.client.post(path, headers=PLUGIN_HEADERS, json=body)
            self.assertEqual(first.status_code, 201, first.text)
            again = self.client.post(path, headers=PLUGIN_HEADERS, json=body)
            self.assertEqual(again.json(), first.json())
            second = self.client.post(path, headers=PLUGIN_HEADERS,
                                      json={**body, "run_id": "plugin-run-2", "markdown": "# Summary two"})
            self.assertEqual(second.status_code, 201, second.text)
        finally:
            for item in reversed(storage):
                item.stop()
        self.assertTrue(first.json()["filename"].startswith("ai-summary-"))
        self.assertTrue(first.json()["filename"].endswith(".md"))
        with SessionLocal() as db:
            files = list(db.scalars(select(RunFile).where(RunFile.test_run_id == run["id"])))
            self.assertEqual(len(files), 2)
            self.assertEqual({file.content_type for file in files}, {"text/markdown"})
            self.assertEqual({file.created_by for file in files}, {tess_id})
            self.assertEqual(sorted(objects[file.object_key] for file in files),
                             [b"# Summary one", b"# Summary two"])
            audit = db.scalar(select(AuditLog).where(AuditLog.action == "test_run.artifact.upload",
                                                     AuditLog.entity_id == run["id"]))
            self.assertEqual(audit.detail["via"], "plugin")
            self.assertEqual(audit.detail["plugin_id"], "test-summary")
        listed = self.get(f"/api/v1/test-runs/{run['id']}/artifacts").json()["items"]
        self.assertEqual(len(listed), 2)

    def test_without_object_storage_the_summary_cannot_be_kept(self):
        run, _ = self.run_with_tests("Unsaved run")
        with patch.object(settings, "object_storage_enabled", False):
            refused = self.client.post(f"/api/v1/plugin-host/test-summary/test-runs/{run['id']}/summaries",
                                       headers=PLUGIN_HEADERS, json={"run_id": "x", "markdown": "# no"})
        self.assertEqual(refused.status_code, 503)
