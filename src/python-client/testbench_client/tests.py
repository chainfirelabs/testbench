"""Test results: recording evidence that software ran against a device.

The module is named for the domain entity, not for this package's own test
suite — those live in `python-client/tests/`.
"""

from __future__ import annotations

import io
import mimetypes
import os
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from typing import IO, BinaryIO, Iterable, Iterator, Union
from urllib.parse import quote

from .errors import TestBenchError
from .models import (
    TEST_OUTCOMES, TEST_TAGS, Device, Software, Test, TestArtifact, TestOutput, to_api_date,
)

# A file to upload: a path on disk, the bytes themselves, or an open binary file.
FileSource = Union[str, "os.PathLike[str]", bytes, BinaryIO]


class Tests:
    """Accessed as `tb.tests`."""

    def __init__(self, client) -> None:
        self._dm = client

    # -- writing ----------------------------------------------------------

    def create(
        self,
        *,
        device: str | Device,
        software: str | Software,
        outcome: str,
        tag: str = "adhoc",
        software_version: str | None = None,
        misc_data: dict | None = None,
        notes: str | None = None,
        run_at: date | str | None = None,
        artifacts: Iterable[FileSource] | None = None,
        output: str | None = None,
        output_file: FileSource | None = None,
    ) -> Test:
        """Record one run, optionally with its files.

        ```python
        tb.tests.create(device="dev-0042", software="nmap", outcome="pass",
                        tag="acceptance", misc_data={"duration_s": 12.4})
        ```

        `device` and `software` are the names you already have; the UUIDs the
        API wants are resolved and cached for you.

        Leaving `software_version` unset records the version the software is on
        now, which is almost always right — the run exercised whatever is
        current. Set it when the run exercised a build the catalogue has moved
        past.

        `misc_data` is free-form JSON: durations, throughput, log URLs, exit codes.
        It is the field that makes these results worth querying later, so put
        the numbers in it rather than in `notes`.

        `artifacts` attaches files (paths, bytes or open binary files) and
        `output` the run's text output — or `output_file` to read it from disk.
        The run is recorded first and the files uploaded after it, so an upload
        that fails leaves the run recorded: the exception carries it as
        `.test`, for a retry with `upload_artifact()` or `set_output()`.
        """
        if output is not None and output_file is not None:
            raise ValueError("pass output (text) or output_file, not both")
        _check_enum("outcome", outcome, TEST_OUTCOMES)
        _check_enum("tag", tag, TEST_TAGS)
        body = {
            "device_id": self._dm.devices.resolve(device),
            "software_id": self._dm.software.resolve(software, software_version),
            "outcome": outcome,
            "tag": tag,
            "misc_data": misc_data or {},
            "notes": notes,
            "run_at": to_api_date(run_at),
            "software_version": software_version,
        }
        result = Test.from_dict(self._dm.request("POST", "/tests", json=body))
        files = list(artifacts or ())
        if not files and output is None and output_file is None:
            return result
        try:
            for source in files:
                self.upload_artifact(result, source)
            if output is not None:
                self.set_output(result, output)
            elif output_file is not None:
                self.upload_output(result, output_file)
        except TestBenchError as exc:
            exc.test = result
            raise
        # Re-read so the counts and output name reflect what was attached.
        return self.get(result.id)

    def record(self, device, software, passed: bool, **kwargs) -> Test:
        """`create()` for the common case where a framework has a boolean.

        Maps True to "pass" and False to "fail". "warn" has no boolean, so
        reach for `create()` when you want it.
        """
        return self.create(device=device, software=software,
                           outcome="pass" if passed else "fail", **kwargs)

    def update(self, test: str | Test, **fields) -> Test:
        """Amend a recorded result (outcome, tag, misc_data, notes, run_at)."""
        key = test.id if isinstance(test, Test) else str(test)
        if "run_at" in fields:
            fields["run_at"] = to_api_date(fields["run_at"])
        return Test.from_dict(self._dm.request("PATCH", f"/tests/{quote(key, safe='')}", json=fields))

    def delete(self, test: str | Test) -> None:
        key = test.id if isinstance(test, Test) else str(test)
        self._dm.request("DELETE", f"/tests/{quote(key, safe='')}")

    # -- reading ----------------------------------------------------------

    def list(
        self,
        *,
        device: str | Device | None = None,
        software: str | Software | None = None,
        software_version: str | None = None,
        outcome: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        fields: dict[str, str | int | float | bool] | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int | None = None,
    ) -> list[Test]:
        """Recorded runs, newest first, across as many pages as it takes."""
        return list(self.iter(device=device, software=software,
                              software_version=software_version, outcome=outcome,
                              tag=tag, search=search, fields=fields, sort=sort, order=order, limit=limit))

    def iter(
        self,
        *,
        device: str | Device | None = None,
        software: str | Software | None = None,
        software_version: str | None = None,
        outcome: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        fields: dict[str, str | int | float | bool] | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int | None = None,
    ) -> Iterator[Test]:
        if outcome is not None:
            _check_enum("outcome", outcome, TEST_OUTCOMES)
        if tag is not None:
            _check_enum("tag", tag, TEST_TAGS)
        params = {**(fields or {}),
            "device_id": self._dm.devices.resolve(device) if device is not None else None,
            "software_id": (
                self._dm.software.resolve(software, software_version)
                if software is not None else None
            ),
            "outcome": outcome,
            "tag": tag,
            "search": search,
            "sort": sort,
            "order": order,
        }
        for row in self._dm.paginate("/tests", params, limit=limit):
            yield Test.from_dict(row)

    def get(self, test_id: str) -> Test:
        return Test.from_dict(self._dm.request("GET", f"/tests/{quote(test_id, safe='')}"))

    def for_device(self, device: str | Device, *, limit: int | None = None) -> list[Test]:
        """Every recorded run against one device, newest first."""
        return self.list(device=device, limit=limit)


    # -- artifacts ----------------------------------------------------------

    def artifacts(self, test: str | Test) -> list[TestArtifact]:
        """The files attached to a run, oldest first."""
        result = self._dm.request("GET", f"{_path(test)}/artifacts")
        return [TestArtifact.from_dict(row) for row in result["items"]]

    def upload_artifact(self, test: str | Test, file: FileSource, *,
                        filename: str | None = None,
                        content_type: str | None = None) -> TestArtifact:
        """Attach one file to a run. Repeat for more; duplicate names are fine.

        ```python
        tb.tests.upload_artifact(result, "capture.pcap")
        tb.tests.upload_artifact(result, png_bytes, filename="screen.png")
        ```

        Needs `tests.edit`, and object storage enabled on the server.
        """
        with ExitStack() as stack:
            name, handle = _open(file, filename, stack, default="artifact")
            kind = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
            row = self._dm.request("POST", f"{_path(test)}/artifacts",
                                   files={"file": (name, handle, kind)})
        return TestArtifact.from_dict(row)

    def download_artifact(self, test: str | Test, artifact: str | TestArtifact,
                          dest: str | os.PathLike[str] | BinaryIO | None = None) -> bytes | Path | None:
        """Fetch an artifact's bytes.

        With no `dest`, returns the bytes. With a directory, saves under the
        artifact's own filename (which needs the `TestArtifact`, not just its
        id) and returns the path; with a file path, saves there and returns it;
        with an open binary file, writes into it and returns None.
        """
        artifact_id = artifact.id if isinstance(artifact, TestArtifact) else str(artifact)
        path = f"{_path(test)}/artifacts/{quote(artifact_id, safe='')}/download"
        if dest is None:
            buffer = io.BytesIO()
            self._dm.download(path, buffer)
            return buffer.getvalue()
        if not isinstance(dest, (str, os.PathLike)):
            self._dm.download(path, dest)
            return None
        target = Path(dest)
        if target.is_dir():
            if not isinstance(artifact, TestArtifact):
                raise ValueError("saving into a directory needs the TestArtifact, for its filename")
            target = target / Path(artifact.filename).name
        with open(target, "wb") as handle:
            self._dm.download(path, handle)
        return target

    def delete_artifact(self, test: str | Test, artifact: str | TestArtifact) -> None:
        artifact_id = artifact.id if isinstance(artifact, TestArtifact) else str(artifact)
        self._dm.request("DELETE", f"{_path(test)}/artifacts/{quote(artifact_id, safe='')}")

    # -- output -------------------------------------------------------------

    def output(self, test: str | Test) -> TestOutput | None:
        """The run's text output, or None when it has none."""
        result = self._dm.request("GET", f"{_path(test)}/output")
        return TestOutput.from_dict(result) if result.get("output") else None

    def set_output(self, test: str | Test, text: str, *, filename: str = "output.txt") -> TestOutput:
        """Store `text` as the run's output, replacing any it had.

        Output is ASCII text the TestBench UI shows in a viewer; anything else is
        refused with a ValidationError, as is a file over the server's limit
        (1 MiB unless configured otherwise).
        """
        try:
            data = text.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("output must be ASCII text") from exc
        return self.upload_output(test, data, filename=filename)

    def upload_output(self, test: str | Test, file: FileSource, *,
                      filename: str | None = None) -> TestOutput:
        """Store a file (path, bytes or open binary file) as the run's output."""
        with ExitStack() as stack:
            name, handle = _open(file, filename, stack, default="output.txt")
            meta = self._dm.request("PUT", f"{_path(test)}/output",
                                    files={"file": (name, handle, "text/plain")})
        return TestOutput.from_dict({"output": meta})

    def delete_output(self, test: str | Test) -> None:
        """Remove the run's output. Raises NotFound when it has none."""
        self._dm.request("DELETE", f"{_path(test)}/output")


def _check_enum(field: str, value: str, allowed: tuple[str, ...]) -> None:
    # Caught here rather than at the API: the round trip is wasted either way,
    # and a filter with a bad value returns an empty page instead of an error,
    # which reads as "no such results exist".
    if value not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)}, got '{value}'")


def _path(test: str | Test) -> str:
    key = test.id if isinstance(test, Test) else str(test)
    return f"/tests/{quote(key, safe='')}"


def _open(source: FileSource, filename: str | None, stack: ExitStack, *,
          default: str) -> tuple[str, IO[bytes]]:
    """A name and a readable handle for an upload, closing what it opened."""
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        return filename or path.name, stack.enter_context(open(path, "rb"))
    if isinstance(source, (bytes, bytearray)):
        return filename or default, io.BytesIO(bytes(source))
    named = getattr(source, "name", None)
    guessed = Path(named).name if isinstance(named, str) else None
    return filename or guessed or default, source
