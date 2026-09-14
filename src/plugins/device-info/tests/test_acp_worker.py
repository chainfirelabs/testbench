import contextlib
import importlib.util
import io
import os
import unittest
from pathlib import Path


os.environ.setdefault("TB_INFO_RUN_PROMPT", "test prompt")
MODULE_PATH = Path(__file__).parents[1] / "device_info_plugin" / "acp_worker.py"
SPEC = importlib.util.spec_from_file_location("testbench_acp_worker", MODULE_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class ResultExtractionTests(unittest.TestCase):
    def setUp(self):
        worker.assistant_text = ""
        worker.result_emitted = False

    def capture_extract(self, allow_bare=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            worker.extract_result(allow_bare=allow_bare)
        return output.getvalue()

    def test_extracts_prefixed_streamed_result(self):
        worker.assistant_text = (
            'Done\nTESTBENCH_RESULT_JSON={"findings":{},"model":{},"artifacts":{}}'
        )
        output = self.capture_extract()
        self.assertIn('TESTBENCH_RESULT_JSON={"findings":{},"model":{},"artifacts":{}}', output)
        self.assertIn('"type":"result.received"', output)

    def test_accepts_complete_bare_contract_only_at_completion(self):
        worker.assistant_text = '{"findings":{},"model":{},"artifacts":{}}'
        self.assertEqual(self.capture_extract(), "")
        self.assertIn("TESTBENCH_RESULT_JSON=", self.capture_extract(allow_bare=True))

    def test_rejects_unrelated_json(self):
        worker.assistant_text = '{"message":"not a device result"}'
        self.assertEqual(self.capture_extract(allow_bare=True), "")

    def test_suppresses_generic_session_updates(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            worker.handle_update({"sessionUpdate": "current_mode_update"})
            worker.handle_update({"sessionUpdate": "session_info_update"})
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
