# testbench-client

Python client for the TestBench API. Built for automated test frameworks:
list the fleet, take a device for the duration of a run, record what happened.

```python
from testbench_client import TestBench

with TestBench.from_env() as tb:
    with tb.devices.reserved("dev-0042", purpose="Automated suite", due="2026-12-31") as device:
        passed, metrics = run_my_suite(device.wan_ip)
        tb.tests.record(device, "curl-smoke", passed, misc_data=metrics)
```

The device is checked back in when the block ends — including when it ends by
raising, which is the case that otherwise leaves hardware stranded as
`checked_out` while someone works out who has it.

## Install

```bash
pip install -e src/python-client        # from a checkout
pip install testbench-client   # once it is published
```

One dependency: `httpx`. Python 3.10+.

## Authenticating

Mint an API key in the web app under **Profile → API keys** and export it. The
full key is shown once, at creation.

```bash
export TB_API_URL=http://localhost:8001   # /api/v1 is appended if you omit it
export TB_API_KEY=tb_1ba207b36474_...
```

```python
tb = TestBench.from_env()                       # reads those two variables
tb = TestBench("http://localhost:8001", key)    # or pass them directly
```

Pick the **lowest role that does the job**. Reading needs no permission at all;
checking a device out needs `devices.edit` and recording a result needs
`tests.edit`. A key never grants more than its owner does, and it can be revoked
from the same page without disturbing the account it belongs to — so give each
framework its own and label it.

Roles are defined per installation, so the role's *name* tells you nothing
portable: check the permissions. `tb.identity` carries both, and is worth
asserting at the top of a run rather than discovering an hour in:

```python
from testbench_client import DEVICES_EDIT, TESTS_EDIT

if not tb.identity.can_write:                   # tests.edit and devices.edit
    sys.exit(f"key is {tb.identity.role}, which cannot run a suite")

if not tb.identity.can(TESTS_EDIT):             # or ask for one permission
    sys.exit("this key cannot record results")
```

An older TestBench that predates definable roles reports only a role name;
`can()` answers from what those three fixed roles granted, so this keeps
working against one.

## Devices

Addressed by `unique_id` ("dev-0042") everywhere. UUIDs work in the same place
but you never need one.

```python
tb.devices.list()                              # the whole fleet, all pages
tb.devices.list(status="available")            # available / checked_out / inventory / missing / broken
tb.devices.list(location="Lab A", limit=10)
tb.devices.list(search="mikrotik")             # unique_id, make, model, location, IPs
tb.devices.iter()                              # same, streamed instead of buffered

device = tb.devices.get("dev-0042")            # detail record, with test history
device.wan_ip, device.architecture, device.firmware_version
device.online                                  # from network scanning
device.is_available, device.is_checked_out     # from the workflow status
device.tests                                   # every recorded run against it
```

`status` and `online` are unrelated: a checked-out device can be answering
pings, and an available one can be dark.

Every timestamp comes back timezone-aware in UTC, so comparing one to
`datetime.now(timezone.utc)` works rather than raising.

## Checking devices in and out

```python
tb.devices.check_out("dev-0042", purpose="Testing", due="2026-12-31")               # raises DeviceUnavailable if taken
tb.devices.check_out("dev-0042", purpose="Testing", due="2026-12-31", force=True)   # take it anyway
tb.devices.check_in("dev-0042")
tb.devices.set_status("dev-0042", "broken")    # any status, plus other fields

with tb.devices.reserved("dev-0042", purpose="Automated suite", due="2026-12-31") as device:
    ...                                        # released however the block ends
```

Every checkout requires a purpose and return date; choose a date appropriate to your run.

Checking out a device you already hold is not an error, so retries and reruns
are safe.

**The API does not enforce exclusivity, and this client cannot invent it.**
`PATCH status=checked_out` on a device someone else holds is a silent no-op that
returns 200 with their name still on it. So `check_out()` reads the device
first, refuses if it is taken, and *re-reads afterwards* — if another run
claimed it in between, the second read catches it and raises rather than letting
your suite proceed on hardware it does not own.

That is a guard, not a lock. Two clients calling it microseconds apart can still
both believe they won. If the hardware is genuinely contended, serialise
somewhere that has a real lock to offer — a CI job queue, a pytest resource
plugin — and use this to record the outcome.

`check_in()` leaves a device alone unless it is checked out *by you*, and
`reserved()` leaves behind any other status the block set. A run that discovered
the device is broken and marked it so does not get that quietly undone on the
way out.

## Recording results

```python
tb.tests.create(
    device="dev-0042",
    software="curl-smoke",          # bare name = the current version
    outcome="pass",                 # pass / fail / warn
    tag="acceptance",               # adhoc / acceptance / end-to-end / automated
    misc_data={"duration_s": 12.4, "mbps": 940, "log": "https://ci/…/42"},
    notes="nightly run",
)

tb.tests.record(device, "curl-smoke", passed=True, misc_data=metrics)   # boolean shorthand
```

`misc_data` is free-form JSON and is the field that makes results worth querying
later — put the numbers there, not in `notes`.

You pass names; the UUIDs the API wants are resolved and cached for you, so a
suite recording a result per test case makes one request per result rather than
three.

Leaving `software_version` unset records whatever version the software is on
*now*, which is almost always what a run exercised. Pass it explicitly to pin an
older build — an unknown version tells you which ones exist:

```
UnknownSoftware: curl-smoke has no version '0.1'. Known versions: 3.8.6, 3.8.5.
```

Reading results back:

```python
tb.tests.for_device("dev-0042")
tb.tests.list(software="curl-smoke", outcome="fail", limit=20)
tb.tests.update(result, notes="retried, flaky network")
tb.tests.delete(result)
```

### One run, one build

A bare software name resolves once per client and stays resolved. If someone
adds a new version halfway through your suite, results keep attaching to the
build the run started against — one run, one set of evidence, rather than a
suite split across two versions at an arbitrary point. Call `tb.clear_cache()`
if you want the opposite.

## Software

Read-only. Recording a result needs software that already exists; a test
framework inventing catalogue rows is how a catalogue fills up with typos.

```python
tb.software.list(latest_only=True)
tb.software.get("curl-smoke")                  # current version
tb.software.get("curl-smoke", "3.8.5")         # a specific one
tb.software.versions("curl-smoke")             # all of them, newest first
```

## Vendor devices

Hardware a vendor **claims** their software supports — their published
compatibility list. Read-only, and not the same question as `tests`:

- a vendor device is a **claim**. Not evidence, and not necessarily hardware
  this fleet owns.
- a test is **evidence**. Absence of one means untested, not unsupported.

Code that treats them as interchangeable is the mistake this section exists to
prevent; `tb.vendor_devices.for_device(...)` and `tb.tests.for_device(...)`
answer different questions and routinely disagree.

```python
# Every compatibility list at once — the question no single software answers.
tb.vendor_devices.list(search="ISR 4331")
tb.vendor_devices.list(make="Cisco", support_status="supported")

# One software's own list. A bare name gives the current version's.
tb.vendor_devices.for_software("curl-smoke")
tb.vendor_devices.for_software("curl-smoke", "3.8.5")

# Who claims to support a device in the fleet, found by its make and model.
for claim in tb.vendor_devices.for_device("dev-0042", supported_only=True):
    print(claim)        # Cisco ISR 4331: supported (Backup-Restore 2.0)
```

Every row names the software and version making the claim. `software_is_latest`
is false for a claim made by a version that has since been superseded — still a
real claim, but not current guidance:

```python
current = [c for c in tb.vendor_devices.for_device("dev-0042") if c.software_is_latest]
```

`support_status` is the vendor's own word — `supported`, `partial`,
`unsupported` or `planned`. Unsupported rows come back like any other, because
"the vendor says no" is an answer; `supported_only=` and `.is_supported` filter
to the unqualified yes.

## Errors

Every exception subclasses `TestBenchError` and carries `.status` and
`.detail`. The API's own `detail` strings are passed through rather than
replaced — they usually say what to do next.

| Exception | When |
|---|---|
| `AuthenticationError` | 401 — key missing, expired or revoked |
| `PermissionDenied` | 403 — the key's role is too low |
| `NotFound` / `UnknownDevice` / `UnknownSoftware` | 404, the latter two with `.suggestions` |
| `Conflict` / `DeviceUnavailable` | the device is held, or not available |
| `ValidationError` | 400/422 |
| `ServerError` | 5xx |
| `TransportError` | never reached the API |

`UnknownDevice` includes near misses, which is usually enough to spot a typo
from a CI log alone:

```
UnknownDevice: No device 'dev-00'. Did you mean: dev-0001, dev-0002, dev-0003?
```

Bad enum values are caught before the request goes out. This matters more than
it looks: the API does not validate *filter* values, so `status="braken"`
returns an empty page — which reads as "there are no broken devices". A wrong
answer is worse than an error.

## Retries

GETs retry twice through transport failures and 5xx, with jittered backoff.
Writes are never retried: a `POST /tests` that may have landed must not be sent
twice, because a duplicate result is worse than a failure you can see. 401 and
403 are never retried either — a revoked key does not become valid by asking
again.

Tune with `TestBench(..., retries=0)`.

## Concurrency

`httpx.Client` is thread-safe and the resolution cache is locked, so one client
can be shared across threads. Prefer one per process: it holds the connection
pool and the cache, which is most of why this is faster than shelling out to
curl. Under `pytest-xdist` each worker is its own process and gets its own.

## Examples

- `examples/inventory.py` — the fleet, grouped by status
- `examples/run_suite.py` — reserve, run, record
- `examples/coverage_gap.py` — vendor claims with no test evidence behind them
- `examples/pytest_conftest.py` — fixtures that reserve a device for a session
  and record a result per test from its actual outcome

## Testing this library

```bash
pip install -e '.[dev]'
pytest
```

The suite runs against an in-process fake (`tests/conftest.py`), so it needs no
server. The fake reproduces the server behaviours that matter, including the
silent no-op on a redundant status change — which is what the mid-flight
checkout test depends on.

## Not included

- Creating or editing devices and software — `POST /devices`, `POST /software`
  and friends exist; they are deliberately not wrapped, because an automated
  framework should be recording evidence, not editing the inventory.
- Bulk and CSV import (`/devices/import`, `/tests/import`).
- Editing vendor compatibility lists — reading them is `tb.vendor_devices`;
  writing one is an import from a vendor's datasheet, done in the web app.
- Saved filters, users, roles, audit log.

Each is a short method following the pattern in `devices.py` if you need it.

## A note on the audit log

Actions taken with an API key are audited under the owner's username with the
key's id and label attached. Two consequences worth knowing: results recorded by
your framework are attributed to a person, and revoking a key answers "what did
it do?" and not only "who owned it?". Label keys so that stays useful.
