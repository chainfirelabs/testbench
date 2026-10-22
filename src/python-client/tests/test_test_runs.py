"""Test runs: a pipeline's tests grouped under one run, with its report
stored once and shared by all of them."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import testbench_client
from testbench_client import TestRun, TestRunArtifact


@pytest.fixture
def fleet(api):
    api.add_device("dev-1")
    api.add_device("dev-2")
    api.add_software("RouterOS", "7.15")
    return api


def test_every_job_of_a_pipeline_joins_the_same_run(tb, fleet):
    first = tb.test_runs.start("Nightly #1234", external_id="pipeline-1234",
                               url="https://ci.example/p/1234")
    again = tb.test_runs.start("Job 2", external_id="pipeline-1234")
    assert isinstance(first, TestRun)
    assert again.id == first.id and again.name == "Nightly #1234"
    assert json.loads(fleet.requests[0].content) == {
        "name": "Nightly #1234", "external_id": "pipeline-1234", "url": "https://ci.example/p/1234"}
    with pytest.raises(ValueError, match="needs a name"):
        tb.test_runs.start("  ")


def test_tests_are_recorded_in_a_run_and_listed_by_it(tb, fleet):
    run = tb.test_runs.start("Nightly #1")
    inside = tb.tests.create(device="dev-1", software="RouterOS", outcome="pass", test_run=run)
    tb.tests.create(device="dev-2", software="RouterOS", outcome="fail", test_run=run.id)
    outside = tb.tests.create(device="dev-1", software="RouterOS", outcome="pass")
    assert (inside.test_run_id, inside.test_run_name) == (run.id, "Nightly #1")
    assert outside.test_run_id is None
    assert [t.device_unique_id for t in tb.test_runs.tests(run)] == ["dev-1", "dev-2"]
    assert [t.id for t in tb.tests.list(test_run="none")] == [outside.id]
    assert tb.test_runs.get(run.id).test_count == 2
    assert [r.name for r in tb.test_runs.list(search="nightly")] == ["Nightly #1"]


def test_a_test_moves_into_and_out_of_a_run(tb, fleet):
    run = tb.test_runs.start("Moves")
    test = tb.tests.create(device="dev-1", software="RouterOS", outcome="pass")
    with pytest.raises(Exception):  # the fake does not amend tests
        tb.tests.update(test, test_run=run)
    assert json.loads(fleet.requests[-1].content) == {"test_run_id": run.id}
    with pytest.raises(Exception):
        tb.tests.update(test, test_run=None)
    assert json.loads(fleet.requests[-1].content) == {"test_run_id": None}


def test_a_report_is_shared_once_with_every_test_in_the_run(tb, fleet, tmp_path):
    run = tb.test_runs.start("Nightly #2")
    report = tmp_path / "pytest-report.html"
    report.write_bytes(b"<html>48 passed</html>")
    shared = tb.test_runs.attach(run, report)
    assert isinstance(shared, TestRunArtifact)
    assert (shared.filename, shared.content_type) == ("pytest-report.html", "text/html")

    first = tb.tests.create(device="dev-1", software="RouterOS", outcome="pass", test_run=run,
                            artifacts=[b"pcap"])
    second = tb.tests.create(device="dev-2", software="RouterOS", outcome="pass", test_run=run)
    for test in (first, second):
        assert [a.id for a in tb.tests.shared_artifacts(test)] == [shared.id]
    assert [a.filename for a in tb.tests.artifacts(first)] == ["artifact"]
    assert tb.tests.shared_artifacts(tb.tests.create(device="dev-1", software="RouterOS", outcome="pass")) == []

    assert tb.test_runs.download_artifact(run, shared) == b"<html>48 passed</html>"
    saved = tb.test_runs.download_artifact(run, shared, tmp_path / "out")
    assert saved.read_bytes() == b"<html>48 passed</html>"
    (tmp_path / "dir").mkdir()
    assert tb.test_runs.download_artifact(run, shared, tmp_path / "dir") == tmp_path / "dir" / "pytest-report.html"
    with pytest.raises(ValueError, match="needs the TestRunArtifact"):
        tb.test_runs.download_artifact(run, shared.id, tmp_path / "dir")

    tb.test_runs.delete_artifact(run, shared)
    assert tb.test_runs.artifacts(run) == []


def test_finishing_and_deleting_a_run(tb, fleet):
    run = tb.test_runs.start("Short", started_at=datetime(2026, 10, 21, 2, 0, tzinfo=timezone(timedelta(hours=-4))))
    assert json.loads(fleet.requests[-1].content)["started_at"] == "2026-10-21T06:00:00"
    finished = tb.test_runs.finish(run, at="2026-10-21T06:41:00")
    assert finished.finished and finished.finished_at.minute == 41
    test = tb.tests.create(device="dev-1", software="RouterOS", outcome="pass", test_run=run)
    tb.test_runs.delete(run)
    assert tb.tests.get(test.id).test_run_id is None


def test_a_test_reads_its_run_from_the_api():
    test = testbench_client.Test.from_dict({"id": "t", "test_run_id": "r", "test_run_name": "Nightly",
                           "test_run_artifact_count": 2})
    assert (test.test_run_id, test.test_run_name, test.test_run_artifact_count) == ("r", "Nightly", 2)
    assert testbench_client.Test.from_dict({"id": "t"}).test_run_artifact_count == 0


GITLAB = {
    "GITLAB_CI": "true", "CI_PIPELINE_ID": "812345", "CI_PIPELINE_IID": "812",
    "CI_PIPELINE_URL": "https://gitlab.example.com/group/project/-/pipelines/812345",
    "CI_SERVER_HOST": "gitlab.example.com", "CI_PROJECT_PATH": "group/project",
    "CI_COMMIT_REF_NAME": "main",
}
GITHUB = {
    "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "9876543210", "GITHUB_RUN_NUMBER": "57",
    "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "owner/repo",
    "GITHUB_WORKFLOW": "CI", "GITHUB_REF_NAME": "main",
}


def test_every_gitlab_job_joins_its_pipelines_run(tb, fleet):
    first = tb.test_runs.start_from_ci(env=GITLAB)
    again = tb.test_runs.start_from_ci("ignored: the run exists", env=GITLAB)
    assert again.id == first.id
    assert (first.name, first.external_id, first.url) == (
        "group/project #812 (main)", "gitlab:gitlab.example.com:812345",
        "https://gitlab.example.com/group/project/-/pipelines/812345")
    other = tb.test_runs.start_from_ci(env={**GITLAB, "CI_PIPELINE_ID": "812346", "CI_PIPELINE_IID": "813"})
    assert other.id != first.id


def test_github_actions_runs_are_named_and_linked(tb, fleet):
    run = tb.test_runs.start_from_ci(notes="smoke", env=GITHUB)
    assert (run.name, run.external_id, run.url) == (
        "owner/repo CI #57 (main)", "github:github.com:9876543210",
        "https://github.com/owner/repo/actions/runs/9876543210")
    enterprise = tb.test_runs.start_from_ci("Custom", env={**GITHUB, "GITHUB_SERVER_URL": "https://ghe.example.com"})
    assert (enterprise.name, enterprise.external_id) == ("Custom", "github:ghe.example.com:9876543210")
    assert enterprise.url == "https://ghe.example.com/owner/repo/actions/runs/9876543210"


def test_starting_from_ci_outside_ci_says_so(tb, fleet):
    with pytest.raises(ValueError, match="not running in GitLab CI or GitHub Actions"):
        tb.test_runs.start_from_ci(env={"CI": "true"})
    assert fleet.test_runs == {}


def test_ci_details_without_optional_variables():
    from testbench_client.test_runs import ci_run_details
    assert ci_run_details({"GITLAB_CI": "true", "CI_PIPELINE_ID": "5"}) == {
        "name": "Pipeline #5", "external_id": "gitlab:gitlab:5", "url": None}
    assert ci_run_details({"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "7"}) == {
        "name": "Workflow #7", "external_id": "github:github.com:7", "url": None}
    assert ci_run_details({}) is None


def test_a_test_shares_files_with_its_run_as_it_is_recorded(tb, fleet, tmp_path):
    run = tb.test_runs.start("Manual")
    outline = tmp_path / "outline.md"
    outline.write_text("# plan")
    test = tb.tests.create(device="dev-1", software="RouterOS", outcome="pass", test_run=run,
                           artifacts=[b"pcap"], test_run_artifacts=[outline])
    assert [a.filename for a in tb.test_runs.artifacts(run)] == ["outline.md"]
    assert [a.filename for a in tb.tests.artifacts(test)] == ["artifact"]
    assert [a.filename for a in tb.tests.shared_artifacts(test)] == ["outline.md"]
    with pytest.raises(ValueError, match="pass test_run too"):
        tb.tests.create(device="dev-1", software="RouterOS", outcome="pass", test_run_artifacts=[b"x"])
