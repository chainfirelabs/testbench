# AI Test Summary plugin

This optional TestBench plugin adds **Summarize with AI** to every test run. It
is packaged as its own image and enabled with `plugins.testSummary.enabled`.
Unlike the device plugins it is global: it is never enabled per device type,
because one test run can cover several.

A run of the action:

1. reads the test run back from TestBench
   (`GET /plugin-host/test-summary/test-runs/{id}/report`): every test with all
   of its fields, notes and output; each tested device without sensitive
   fields; and the text of the run's shared files and each test's artifacts.
   The backend reads object storage on the plugin's behalf, with limits per
   file and per report, failures first. Earlier `ai-summary-*` reports are left
   out;
2. removes text that looks like a credential, and computes the result tables
   exactly (`report.py`);
3. asks the configured model for the narrative: in one request, or for a large
   run one request per part (grouped by device, failures first) and a final one
   that combines them (`TB_TEST_SUMMARY_MAX_INPUT_CHARS`,
   `TB_TEST_SUMMARY_MAX_AI_CALLS`);
4. saves the report as a new `ai-summary-<UTC time>.md` shared file on the run
   (`POST /plugin-host/test-summary/test-runs/{id}/summaries`), owned by the
   person who started it. Every version is kept.

The provider and model come from the AI Providers page, sent by the backend with
each run, or from Helm (`TB_TEST_SUMMARY_AI_URL` / `_MODEL`, with the key in
`TB_TEST_SUMMARY_AI_API_KEY`). `TB_TEST_SUMMARY_PROMPT` replaces the default
instructions; the rules that keep the model to the run's data are always added.

Run the tests with any environment that has FastAPI, httpx and pytest, from
this directory: `python -m pytest tests`.
