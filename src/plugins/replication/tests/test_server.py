import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from replication_plugin import server


def item(identity: str, time: str, value: str, entity: str = "devices") -> dict:
    return {"entity": entity, "id": identity, "changed_at": time,
            "deleted": False, "data": {"value": value}}


class ReplicationTests(unittest.TestCase):
    @patch.object(server, "sync_once", side_effect=RuntimeError("peer returned 401"))
    @patch.object(server.httpx, "Client")
    def test_failed_scheduled_sync_reports_audit_result_and_destination(self, client, _sync):
        response = client.return_value.__enter__.return_value.post.return_value
        with patch.object(server, "LOCAL_URL", "http://local/api/v1"), \
             patch.object(server, "PEER_URL", "https://peer.example/api/v1"), \
             patch.object(server, "PLUGIN_SECRET", "local-secret"):
            server._run("run-1", "scheduled")
        args, kwargs = client.return_value.__enter__.return_value.post.call_args
        self.assertEqual(args[0], "http://local/api/v1/replication/run-result")
        self.assertEqual(kwargs["headers"]["X-TestBench-Plugin-Secret"], "local-secret")
        self.assertEqual(kwargs["json"]["peer_url"], "https://peer.example/api/v1")
        self.assertEqual(kwargs["json"]["state"], "failed")
        self.assertEqual(server.LAST_RUN["state"], "failed")
        response.raise_for_status.assert_called_once()

    @patch.object(server.time, "sleep")
    @patch.object(server.logger, "exception")
    @patch.object(server, "sync_once", return_value={"entities": {}, "finished_at": "2026-01-01T00:00:00Z"})
    @patch.object(server.httpx, "Client")
    def test_sync_status_shows_audit_failure_after_retries(self, client, _sync, logged, sleep):
        client.return_value.__enter__.return_value.post.side_effect = OSError("backend unavailable")
        with patch.object(server, "LOCAL_URL", "http://local/api/v1"):
            server._run("run-2", "manual")
        self.assertEqual(client.return_value.__enter__.return_value.post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(server.LAST_RUN["state"], "succeeded")
        self.assertIn("backend unavailable", server.LAST_RUN["audit_error"])
        logged.assert_called_once()

    def test_plugin_control_requires_shared_secret(self):
        with patch.object(server, "PLUGIN_SECRET", "local-secret"):
            with self.assertRaises(HTTPException):
                server._auth("replication", "wrong")
            with self.assertRaises(HTTPException):
                server._auth("network-scan", "local-secret")
            self.assertIsNone(server._auth("replication", "local-secret"))

    def test_newest_edit_wins_in_both_directions(self):
        old = item("a", "2026-01-01T00:00:00+00:00", "old")
        new = item("a", "2026-01-02T00:00:00+00:00", "new")
        self.assertEqual(server.select_winners([old], [new], "push_pull"), [new])
        self.assertEqual(server.select_winners([old], [new], "push"), [old])
        self.assertEqual(server.select_winners([old], [], "push_pull"), [old])

    def test_tombstone_can_win_and_equal_timestamps_converge(self):
        live = item("a", "2026-01-01T00:00:00+00:00", "old")
        deleted = {"entity": "devices", "id": "a", "deleted": True,
                   "changed_at": "2026-01-02T00:00:00+00:00"}
        self.assertEqual(server.select_winners([live], [deleted], "push_pull"), [deleted])
        a = item("a", "2026-01-01T00:00:00+00:00", "one")
        b = item("a", "2026-01-01T00:00:00+00:00", "two")
        self.assertEqual(server.select_winners([a], [b], "push_pull"),
                         server.select_winners([b], [a], "push_pull"))

    @patch.object(server, "_apply")
    @patch.object(server, "_get_all")
    @patch.object(server.httpx, "Client")
    def test_push_only_never_writes_to_local_instance(self, client, get_all, apply):
        client.return_value.__enter__.return_value = MagicMock()
        source = item("a", "2026-01-02T00:00:00+00:00", "source")
        peer = item("a", "2026-01-01T00:00:00+00:00", "peer")
        get_all.side_effect = lambda _client, base, entity: [source if base == "local" else peer] if entity == "devices" else []
        apply.return_value = {"applied": 1, "skipped": 0}
        with patch.object(server, "MODE", "push"), patch.object(server, "LOCAL_URL", "local"), patch.object(server, "PEER_URL", "peer"), patch.object(server, "TOKEN", "token"):
            server.sync_once()
        self.assertTrue(all(call.args[1] == "peer" for call in apply.call_args_list))
        self.assertEqual(apply.call_args_list[0].args[2], [source])

    @patch.object(server, "_apply")
    @patch.object(server, "_get_all")
    @patch.object(server.httpx, "Client")
    def test_push_pull_receives_newer_peer_edit(self, client, get_all, apply):
        client.return_value.__enter__.return_value = MagicMock()
        old = item("a", "2026-01-01T00:00:00+00:00", "old")
        new = item("a", "2026-01-02T00:00:00+00:00", "new")
        get_all.side_effect = lambda _client, base, entity: [old if base == "local" else new] if entity == "devices" else []
        apply.return_value = {"applied": 1, "skipped": 0}
        with patch.object(server, "MODE", "push_pull"), patch.object(server, "LOCAL_URL", "local"), patch.object(server, "PEER_URL", "peer"), patch.object(server, "TOKEN", "token"):
            server.sync_once()
        local_calls = [call for call in apply.call_args_list if call.args[1] == "local"]
        self.assertEqual(local_calls[0].args[2], [new])
        self.assertFalse(local_calls[0].kwargs.get("force", False))


if __name__ == "__main__":
    unittest.main()
