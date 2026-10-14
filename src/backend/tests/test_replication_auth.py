import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app.api.replication import RunResultIn, exchange_auth, plugin_auth, record_run_result, run


class ReplicationAuthTests(unittest.TestCase):
    @patch("app.api.replication.settings")
    def test_run_result_requires_local_plugin_secret(self, settings):
        settings.replication_enabled = True
        settings.plugin_shared_secret = "local-secret"
        for plugin, secret in (("replication", "wrong"), ("other", "local-secret")):
            with self.assertRaises(HTTPException):
                plugin_auth(plugin, secret)
        self.assertIsNone(plugin_auth("replication", "local-secret"))

    @patch("app.api.replication.log_action")
    def test_completed_sync_is_audited_with_destination(self, log_action):
        db = MagicMock()
        db.scalar.return_value = None
        body = RunResultIn(run_id="run-1", trigger="scheduled", state="failed",
                           mode="push", peer_url="https://peer.example/api/v1",
                           finished_at="2026-01-01T00:00:00Z", error="peer returned 401")
        record_run_result(body, db, None)
        args = log_action.call_args.args
        self.assertEqual(args[2:5], ("replication.sync.failed", "replication", "run-1"))
        self.assertEqual(args[5]["peer_url"], "https://peer.example/api/v1")
        db.commit.assert_called_once()
        db.scalar.return_value = 42
        record_run_result(body, db, None)
        log_action.assert_called_once()

    @patch("app.api.replication.settings")
    @patch("app.api.replication.registry.request")
    @patch("app.api.replication.log_action")
    def test_manual_sync_request_is_audited_with_user_and_destination(self, log_action, plugin_request, settings):
        settings.replication_enabled = True
        settings.replication_mode = "push"
        plugin_request.return_value = {"run_id": "run-2", "mode": "push",
                                       "peer_url": "https://peer.example/api/v1"}
        db, user = MagicMock(), MagicMock()
        request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})
        run(request, db, user)
        args = log_action.call_args.args
        self.assertIs(args[1], user)
        self.assertEqual(args[2:5], ("replication.sync.request", "replication", "run-2"))
        self.assertEqual(args[5]["peer_url"], "https://peer.example/api/v1")
        db.commit.assert_called_once()

    @patch("app.api.replication.settings")
    def test_disabled_and_wrong_tokens_are_rejected(self, settings):
        settings.replication_token = "configured-secret"
        settings.replication_enabled = False
        with self.assertRaises(HTTPException):
            exchange_auth("configured-secret")
        settings.replication_enabled = True
        for provided in (None, "wrong-secret"):
            with self.subTest(provided=provided), self.assertRaises(HTTPException):
                exchange_auth(provided)
        self.assertIsNone(exchange_auth("configured-secret"))
