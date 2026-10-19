"""Artifacts and output attached to recorded runs."""

import io

import pytest

from testbench_client import NotFound, TestArtifact, ValidationError


@pytest.fixture
def run(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap", "7.94")
    return tb.tests.create(device="dev-0042", software="nmap", outcome="pass")


def test_artifacts_round_trip_from_path_bytes_and_file(tb, api, run, tmp_path):
    report = tmp_path / "report.html"
    report.write_bytes(b"<h1>ok</h1>")
    first = tb.tests.upload_artifact(run, report)
    assert (first.filename, first.content_type, first.size_bytes) == ("report.html", "text/html", 11)
    tb.tests.upload_artifact(run, b"\x00\xffpcap", filename="capture.pcap")
    tb.tests.upload_artifact(run.id, io.BytesIO(b"raw"))

    listed = tb.tests.artifacts(run)
    assert [a.filename for a in listed] == ["report.html", "capture.pcap", "artifact"]
    assert all(isinstance(a, TestArtifact) for a in listed)
    assert tb.tests.get(run.id).artifact_count == 3

    assert tb.tests.download_artifact(run, listed[1]) == b"\x00\xffpcap"
    saved = tb.tests.download_artifact(run, listed[0], tmp_path / "out")
    assert saved == tmp_path / "out"
    assert saved.read_bytes() == b"<h1>ok</h1>"
    (tmp_path / "dir").mkdir()
    into_dir = tb.tests.download_artifact(run, listed[0], tmp_path / "dir")
    assert into_dir == tmp_path / "dir" / "report.html"
    assert into_dir.read_bytes() == b"<h1>ok</h1>"
    sink = io.BytesIO()
    assert tb.tests.download_artifact(run, listed[2].id, sink) is None
    assert sink.getvalue() == b"raw"

    tb.tests.delete_artifact(run, listed[0])
    assert [a.filename for a in tb.tests.artifacts(run)] == ["capture.pcap", "artifact"]


def test_saving_into_a_directory_needs_the_artifact_record(tb, run, tmp_path):
    artifact = tb.tests.upload_artifact(run, b"x", filename="x.bin")
    with pytest.raises(ValueError, match="filename"):
        tb.tests.download_artifact(run, artifact.id, tmp_path)


def test_a_missing_artifact_is_not_found(tb, run):
    with pytest.raises(NotFound):
        tb.tests.download_artifact(run, "nope")


def test_output_text_is_set_read_replaced_and_deleted(tb, run, tmp_path):
    assert tb.tests.output(run) is None
    saved = tb.tests.set_output(run, "line 1\nline 2\n")
    assert (saved.filename, saved.size_bytes) == ("output.txt", 14)
    read = tb.tests.output(run)
    assert read.text == "line 1\nline 2\n"
    assert str(read) == read.text

    log = tmp_path / "run.log"
    log.write_text("replaced\n")
    tb.tests.upload_output(run, log)
    assert tb.tests.output(run).filename == "run.log"
    record = tb.tests.get(run.id)
    assert record.has_output and record.output_size_bytes == 9

    tb.tests.delete_output(run)
    assert tb.tests.output(run) is None
    with pytest.raises(NotFound):
        tb.tests.delete_output(run)


def test_output_must_be_ascii_and_within_the_limit(tb, run):
    with pytest.raises(ValueError, match="ASCII"):
        tb.tests.set_output(run, "café")
    with pytest.raises(ValidationError):
        tb.tests.upload_output(run, b"\xff\xfe")
    with pytest.raises(Exception, match="limit"):
        tb.tests.set_output(run, "x" * 65)


def test_create_attaches_files_and_returns_the_updated_run(tb, api, tmp_path):
    api.add_device("dev-0042")
    api.add_software("nmap", "7.94")
    log = tmp_path / "scan.log"
    log.write_text("open 22/tcp\n")
    result = tb.tests.create(device="dev-0042", software="nmap", outcome="pass",
                             artifacts=[b"pcap", log], output="scan finished\n")
    assert result.artifact_count == 2
    assert result.output_filename == "output.txt"
    assert tb.tests.output(result).text == "scan finished\n"

    recorded = tb.tests.record("dev-0042", "nmap", True, output_file=log)
    assert recorded.output_filename == "scan.log"


def test_a_failed_upload_still_hands_back_the_recorded_run(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap", "7.94")
    with pytest.raises(ValidationError) as error:
        tb.tests.create(device="dev-0042", software="nmap", outcome="fail", output_file=b"\xff")
    assert error.value.test.id == api.tests[-1]["id"]


def test_output_and_output_file_are_exclusive(tb):
    with pytest.raises(ValueError, match="not both"):
        tb.tests.create(device="d", software="s", outcome="pass", output="a", output_file=b"b")


def test_a_download_retries_a_500_before_any_bytes(tb, api, run):
    artifact = tb.tests.upload_artifact(run, b"payload", filename="p.bin")
    api.failures.append((500, {"detail": "boom"}))
    assert tb.tests.download_artifact(run, artifact) == b"payload"
