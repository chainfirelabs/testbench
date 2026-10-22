"""Test runs: one execution of a pipeline or suite, grouping the tests it
recorded, with files it shares with all of them stored once.

```python
run = tb.test_runs.start_from_ci()      # GitLab CI or GitHub Actions; or start(name, ...)
tb.test_runs.attach(run, "report.html")
for target in targets:
    tb.tests.create(device=target, software="RouterOS", outcome="pass", test_run=run)
tb.test_runs.finish(run)
```
"""

from __future__ import annotations

import mimetypes
import os
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping
from urllib.parse import quote, urlparse

from .models import Test, TestRun, TestRunArtifact, to_api_datetime
from .tests import FileSource, _open, _save


class TestRuns:
    """Accessed as `tb.test_runs`."""

    __test__ = False  # not a pytest test class, despite the name

    def __init__(self, client) -> None:
        self._dm = client

    # -- writing ----------------------------------------------------------

    def start(
        self,
        name: str,
        *,
        external_id: str | None = None,
        url: str | None = None,
        started_at: datetime | str | None = None,
        notes: str | None = None,
    ) -> TestRun:
        """Start a test run, or join the one already started with `external_id`.

        Pass the CI pipeline's ID as `external_id` and every job of the
        pipeline can call this and get the same run back, whichever starts
        first; the first caller's name and link are the ones kept. Without
        one, every call starts a run of its own. `started_at` defaults to now.
        """
        if not name or not name.strip():
            raise ValueError("a test run needs a name")
        body = {
            "name": name.strip(), "external_id": external_id, "url": url,
            "started_at": to_api_datetime(started_at), "notes": notes,
        }
        return TestRun.from_dict(self._dm.request(
            "POST", "/test-runs", json={k: v for k, v in body.items() if v is not None}))

    def start_from_ci(self, name: str | None = None, *, notes: str | None = None,
                      env: Mapping[str, str] | None = None) -> TestRun:
        """Start, or join, the test run for the CI pipeline this is running in.

        Reads GitLab CI or GitHub Actions' own environment variables for the
        pipeline's ID, link and a name, so every job of one pipeline gets the
        same run without passing anything:

        ```python
        run = tb.test_runs.start_from_ci()
        ```

        - GitLab: one run per pipeline (`CI_PIPELINE_ID`), named like
          `group/project #812 (main)` and linked to `CI_PIPELINE_URL`. Retried
          jobs stay in the pipeline, so their results join the same run.
        - GitHub Actions: one run per workflow run (`GITHUB_RUN_ID`), named like
          `owner/repo CI #57 (main)` and linked to the run's page. Re-running
          jobs keeps the run ID, so their results join the same run too.

        The external ID is prefixed with the CI system and its host
        (`gitlab:gitlab.example.com:812`), so pipelines from two systems never
        share a run. `name` overrides the generated name, which only the job
        that creates the run gets to set. Raises ValueError outside either CI
        system; use `start()` there. `env` defaults to `os.environ`.
        """
        details = ci_run_details(os.environ if env is None else env)
        if details is None:
            raise ValueError("not running in GitLab CI or GitHub Actions; use tb.test_runs.start()")
        return self.start(name or details["name"], external_id=details["external_id"],
                          url=details["url"], notes=notes)

    def update(self, run: str | TestRun, **fields) -> TestRun:
        """Change a run's name, external_id, url, notes, started_at or finished_at."""
        for key in ("started_at", "finished_at"):
            if key in fields:
                fields[key] = to_api_datetime(fields[key])
        return TestRun.from_dict(self._dm.request("PATCH", _path(run), json=fields))

    def finish(self, run: str | TestRun, *, at: datetime | str | None = None) -> TestRun:
        """Mark the run finished, now unless `at` says when."""
        return self.update(run, finished_at=at or datetime.now(timezone.utc))

    def delete(self, run: str | TestRun) -> None:
        """Delete the run. Its tests are kept, no longer in a run."""
        self._dm.request("DELETE", _path(run))

    # -- reading ----------------------------------------------------------

    def get(self, run_id: str) -> TestRun:
        return TestRun.from_dict(self._dm.request("GET", _path(run_id)))

    def list(self, *, search: str | None = None, sort: str = "created_at",
             order: str = "desc", limit: int | None = None) -> list[TestRun]:
        """Test runs, newest first. `search` matches the name or external_id."""
        return list(self.iter(search=search, sort=sort, order=order, limit=limit))

    def iter(self, *, search: str | None = None, sort: str = "created_at",
             order: str = "desc", limit: int | None = None) -> Iterator[TestRun]:
        params = {"search": search, "sort": sort, "order": order}
        for row in self._dm.paginate("/test-runs", params, limit=limit):
            yield TestRun.from_dict(row)

    def tests(self, run: str | TestRun, **filters) -> list[Test]:
        """The tests recorded in the run; takes the filters `tb.tests.list()` does."""
        return self._dm.tests.list(test_run=run, **filters)

    # -- shared files -------------------------------------------------------

    def artifacts(self, run: str | TestRun) -> list[TestRunArtifact]:
        """The files the run shares with all of its tests, oldest first."""
        result = self._dm.request("GET", f"{_path(run)}/artifacts")
        return [TestRunArtifact.from_dict(row) for row in result["items"]]

    def attach(self, run: str | TestRun, file: FileSource, *,
               filename: str | None = None, content_type: str | None = None) -> TestRunArtifact:
        """Share a file (path, bytes or open binary file) with every test in the run.

        Stored once, in the run's folder in the bucket. Needs `tests.edit`, and
        object storage enabled on the server.
        """
        with ExitStack() as stack:
            name, handle = _open(file, filename, stack, default="artifact")
            kind = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
            row = self._dm.request("POST", f"{_path(run)}/artifacts",
                                   files={"file": (name, handle, kind)})
        return TestRunArtifact.from_dict(row)

    def download_artifact(self, run: str | TestRun, artifact: str | TestRunArtifact,
                          dest: str | os.PathLike[str] | BinaryIO | None = None) -> bytes | Path | None:
        """Fetch a shared file: bytes with no `dest`, else saved as
        `tb.tests.download_artifact()` saves."""
        artifact_id = artifact.id if isinstance(artifact, TestRunArtifact) else str(artifact)
        path = f"{_path(run)}/artifacts/{quote(artifact_id, safe='')}/download"
        return _save(self._dm, path, artifact.filename if isinstance(artifact, TestRunArtifact) else None,
                     dest, "TestRunArtifact")

    def delete_artifact(self, run: str | TestRun, artifact: str | TestRunArtifact) -> None:
        """Remove a shared file, from every test in the run."""
        artifact_id = artifact.id if isinstance(artifact, TestRunArtifact) else str(artifact)
        self._dm.request("DELETE", f"{_path(run)}/artifacts/{quote(artifact_id, safe='')}")


def ci_run_details(env: Mapping[str, str]) -> dict[str, str] | None:
    """The name, external ID and link of the CI pipeline in `env`, or None
    when it is not GitLab CI or GitHub Actions."""
    get = lambda key: (env.get(key) or "").strip()  # noqa: E731
    if get("GITLAB_CI") == "true" and get("CI_PIPELINE_ID"):
        host = get("CI_SERVER_HOST") or urlparse(get("CI_PIPELINE_URL")).hostname or "gitlab"
        number = get("CI_PIPELINE_IID") or get("CI_PIPELINE_ID")
        name = f"{get('CI_PROJECT_PATH') or 'Pipeline'} #{number}"
        ref = get("CI_COMMIT_REF_NAME")
        return {
            "name": f"{name} ({ref})" if ref else name,
            "external_id": f"gitlab:{host}:{get('CI_PIPELINE_ID')}",
            "url": get("CI_PIPELINE_URL") or None,
        }
    if get("GITHUB_ACTIONS") == "true" and get("GITHUB_RUN_ID"):
        server = get("GITHUB_SERVER_URL") or "https://github.com"
        repository = get("GITHUB_REPOSITORY")
        host = urlparse(server).hostname or "github.com"
        parts = [repository or "Workflow", get("GITHUB_WORKFLOW")]
        name = " ".join(part for part in parts if part) + f" #{get('GITHUB_RUN_NUMBER') or get('GITHUB_RUN_ID')}"
        ref = get("GITHUB_REF_NAME")
        return {
            "name": f"{name} ({ref})" if ref else name,
            "external_id": f"github:{host}:{get('GITHUB_RUN_ID')}",
            "url": f"{server.rstrip('/')}/{repository}/actions/runs/{get('GITHUB_RUN_ID')}" if repository else None,
        }
    return None


def _path(run: str | TestRun) -> str:
    key = run.id if isinstance(run, TestRun) else str(run)
    return f"/test-runs/{quote(key, safe='')}"
