import contextlib
import importlib.util
import io
import os
import unittest
from pathlib import Path


os.environ.setdefault("TB_REBOOT_RUN_PROMPT", "test prompt")
MODULE_PATH = Path(__file__).parents[1] / "reboot_plugin" / "acp_worker.py"
SPEC = importlib.util.spec_from_file_location("testbench_reboot_acp_worker", MODULE_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class RebootAcpWorkerTests(unittest.TestCase):
    def setUp(self):
        worker.assistant_text = ""
        worker.result_emitted = False

    def capture_extract(self, allow_bare=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            worker.extract_result(allow_bare=allow_bare)
        return output.getvalue()

    def test_extracts_reboot_result(self):
        worker.assistant_text = (
            'TESTBENCH_REBOOT_JSON={"method":"web","verified_online":true,'
            '"model":"model","successful_steps":[{"step":1,"action":"click",'
            '"target":"Reboot button","outcome":"Reboot initiated","yielded_fields":[]}]}'
        )
        output = self.capture_extract()
        self.assertIn("TESTBENCH_REBOOT_JSON=", output)
        self.assertIn('"type":"result.received"', output)

    def test_rejects_wrong_contract(self):
        worker.assistant_text = '{"verified_online":true}'
        self.assertEqual(self.capture_extract(allow_bare=True), "")

    def test_rejects_shorthand_string_steps(self):
        worker.assistant_text = (
            '{"method":"web","verified_online":true,"model":"model",'
            '"successful_steps":["clicked reboot"]}'
        )
        self.assertEqual(self.capture_extract(allow_bare=True), "")

    def test_suppresses_generic_session_updates(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            worker.handle_update({"sessionUpdate": "session_info_update"})
            worker.handle_update({"sessionUpdate": "current_mode_update"})
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
