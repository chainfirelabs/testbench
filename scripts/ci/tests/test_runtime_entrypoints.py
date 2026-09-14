"""Check shell-free startup behavior without starting a database or server."""
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[3]


class RuntimeEntrypointTests(unittest.TestCase):
    def test_backend_migrates_before_serving_and_preserves_proxy_setting(self):
        alembic, config, uvicorn = Mock(), Mock(), Mock()
        calls = Mock()
        calls.attach_mock(alembic.command.upgrade, "migrate")
        calls.attach_mock(uvicorn.run, "serve")
        with patch.dict("sys.modules", {"alembic": alembic, "alembic.config": config, "uvicorn": uvicorn}), patch.dict(os.environ, {"TB_FORWARDED_ALLOW_IPS": "10.0.0.0/8"}):
            main = runpy.run_path(str(ROOT / "src/backend/entrypoint.py"))["main"]
            main()
            self.assertEqual([call[0] for call in calls.mock_calls], ["migrate", "serve"])
            self.assertEqual(uvicorn.run.call_args.kwargs["forwarded_allow_ips"], "10.0.0.0/8")
            alembic.command.upgrade.assert_called_once_with(config.Config.return_value, "head")
            uvicorn.run.reset_mock()
            alembic.command.upgrade.side_effect = RuntimeError("migration failed")
            with self.assertRaises(RuntimeError):
                main()
            uvicorn.run.assert_not_called()

    def test_frontend_renders_only_hsts_and_executes_nginx(self):
        main = runpy.run_path(str(ROOT / "src/frontend/entrypoint.py"))["main"]
        for hsts in ["", "max-age=31536000; includeSubDomains"]:
            with patch.dict(os.environ, {"TB_HSTS_MAX_AGE": hsts}), patch.object(Path, "read_text", return_value='https "${TB_HSTS_MAX_AGE}"; proxy_set_header Host $host;'), patch.object(Path, "write_text") as write, patch("os.execv") as execute:
                main()
                write.assert_called_once_with(f'https "{hsts}"; proxy_set_header Host $host;')
                self.assertEqual(execute.call_args.args[0], "/usr/sbin/nginx")

    def test_frontend_rejects_hsts_configuration_injection(self):
        main = runpy.run_path(str(ROOT / "src/frontend/entrypoint.py"))["main"]
        for hsts in ['"; include /tmp/evil;', "value\n", "$hostname", "value\\"]:
            with patch.dict(os.environ, {"TB_HSTS_MAX_AGE": hsts}), patch.object(Path, "read_text", return_value="template"), patch.object(Path, "write_text") as write, patch("os.execv") as execute:
                with self.assertRaises(ValueError):
                    main()
                write.assert_not_called()
                execute.assert_not_called()
