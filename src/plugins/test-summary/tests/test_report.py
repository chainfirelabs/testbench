from test_summary_plugin import report


def sample(tests=None, shared=None):
    return {
        "run": {"id": "run-1", "name": "Nightly 42", "url": "https://ci.example/42"},
        "test_fields": [{"key": "throughput", "label": "Throughput (Mbps)"}, {"key": "tag", "label": "Tag"}],
        "device_fields": [{"key": "make", "label": "Make"}, {"key": "firmware_version", "label": "Firmware"}],
        "devices": {
            "d1": {"unique_id": "rtr-1", "device_type": "router", "make": "Acme", "firmware_version": "2.1",
                   "misc_data": {"rack": "B2"}},
            "d2": {"unique_id": "phone-7", "device_type": "phone", "make": "Fone", "firmware_version": None,
                   "misc_data": {}},
        },
        "tests": tests if tests is not None else [
            {"id": "t1", "device_id": "d1", "outcome": "fail", "software_name": "Agent", "software_version": "5.0",
             "throughput": 12, "tag": "automated", "notes": "dropped link",
             "output": {"filename": "out.txt", "text": "boot ok\nERROR link down password=hunter2\n"},
             "artifacts": [{"filename": "trace.bin", "content_type": "application/octet-stream",
                            "size_bytes": 99, "note": "binary file; contents not read"}]},
            {"id": "t2", "device_id": "d2", "outcome": "pass", "software_name": "Agent", "software_version": "5.0",
             "component_name": "VoIP", "component_version": "1.2", "throughput": None, "tag": "automated",
             "other_values": {"jitter_ms": 3}, "output": None, "artifacts": []},
            {"id": "t3", "device_id": "d2", "outcome": "warn", "software_name": "Agent", "software_version": "5.1",
             "output": None, "artifacts": []},
        ],
        "shared_files": shared if shared is not None else [
            {"filename": "report.xml", "content_type": "application/xml", "size_bytes": 20, "text": "<ok/>"},
        ],
    }


def test_stats_are_exact_and_cover_every_device_type():
    stats = report.compute_stats(sample())
    assert stats["tests"] == 3
    assert stats["outcomes"] == {"fail": 1, "pass": 1, "warn": 1}
    assert stats["pass_rate_percent"] == 33.3
    assert stats["device_types"] == ["phone", "router"]
    assert stats["by_device"]["phone-7"] == {"pass": 1, "warn": 1, "total": 2}
    assert stats["by_software"]["Agent 5.0"]["total"] == 2
    assert stats["by_component"] == {"VoIP 1.2": {"pass": 1, "total": 1}}
    assert stats["files"] == {"shared_files": 1, "test_artifacts": 1, "test_outputs": 1,
                              "read_as_text": 2, "not_read": 1}


def test_a_test_view_carries_every_recorded_value_under_its_label():
    data = sample()
    view = report.test_view(data, data["tests"][1], 1000)
    assert view["device"] == "phone-7"
    assert view["component"] == "VoIP 1.2"
    assert view["values"] == {"Tag": "automated", "jitter_ms": 3}
    devices = report.device_views(data)
    assert devices["rtr-1"] == {"unique_id": "rtr-1", "device_type": "router", "Make": "Acme",
                                "Firmware": "2.1", "rack": "B2"}


def test_credentials_are_redacted_before_they_reach_the_model():
    text = ("password=hunter2 token: abc123 Authorization: Bearer xyz\n"
            "https://admin:s3cret@10.0.0.1/ eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\nlink is up")
    cleaned = report.redact(text)
    for secret in ("hunter2", "abc123", "xyz", "s3cret", "signaturepart", "MIIE"):
        assert secret not in cleaned
    assert "link is up" in cleaned
    assert "admin:[REDACTED]@10.0.0.1" in cleaned
    data = sample()
    assert "hunter2" not in str(report.test_view(data, data["tests"][0], 1000))


def test_trimming_keeps_the_start_the_end_and_the_errors():
    lines = [f"step {i} ok" for i in range(200)]
    lines[100] = "Traceback: AssertionError in link test"
    trimmed = report.trim("\n".join(lines), 1500)
    assert len(trimmed) <= 1500
    assert "step 0 ok" in trimmed and "step 199 ok" in trimmed
    assert "AssertionError" in trimmed
    assert "step 60 ok" not in trimmed


def test_a_small_run_is_read_in_one_request():
    data = sample()
    planned = report.plan(data, report.compute_stats(data), 100_000, 12)
    assert planned["mode"] == "single"
    assert len(planned["context"]["tests"]) == 3
    assert planned["context"]["shared_files"][0]["text"] == "<ok/>"


def test_a_large_run_is_read_in_parts_failures_first_within_the_call_budget():
    tests = [{"id": f"t{i}", "device_id": "d1" if i < 40 else "d2", "outcome": "pass" if i else "fail",
              "notes": None, "output": {"filename": "o", "text": "x" * 3000}, "artifacts": []}
             for i in range(80)]
    data = sample(tests=tests, shared=[])
    planned = report.plan(data, report.compute_stats(data), 20_000, 3)
    assert planned["mode"] == "parts"
    assert len(planned["parts"]) == 2
    assert planned["parts"][0]["tests"][0]["outcome"] == "fail"
    assert planned["omitted_tests"] > 0


def test_the_prompt_is_replaceable_but_the_rules_always_apply():
    assert report.system_prompt(None).startswith(report.DEFAULT_PROMPT)
    custom = report.system_prompt("Write for managers, in three bullets.")
    assert custom.startswith("Write for managers")
    assert report.GUARD in custom and report.DEFAULT_PROMPT not in custom


def test_the_saved_report_has_exact_tables_after_the_narrative():
    data = sample()
    stats = report.compute_stats(data)
    markdown = report.render(data, stats, "## Summary\nOne failure on rtr-1.", model="gpt-test",
                             generated_at="2026-10-23 12:00 UTC", notes=["note one"])
    assert markdown.startswith("# AI summary: Nightly 42")
    assert "with gpt-test" in markdown
    assert markdown.index("One failure on rtr-1.") < markdown.index("## Results")
    assert "| fail | 1 | 33.3% |" in markdown
    assert "| rtr-1 | 0 | 1 | 0 | 1 |" in markdown
    assert "## By component" in markdown
    assert "- note one" in markdown
    assert "Pipeline: https://ci.example/42" in markdown


def test_table_cells_cannot_break_the_table():
    data = sample()
    data["devices"]["d1"]["unique_id"] = "rtr|1"
    markdown = report.render(data, report.compute_stats(data), "x", model=None, generated_at="now", notes=[])
    assert "| rtr\\|1 |" in markdown
