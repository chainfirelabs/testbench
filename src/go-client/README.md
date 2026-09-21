# TestBench Go client

A Go 1.22+ client for querying TestBench with an API key or bearer token. Uses
only the Go standard library. It covers all read operations wrapped by the
Python client: identity, inventory, overdue devices, device actions and schemas,
software and versions, vendor compatibility claims, and test results. All
paginated listings fetch every page unless a limit is set.

## Command-line executable

Build into `bin/` from the repository root (requires Go 1.22+):

```bash
mkdir -p bin
go -C src/go-client build -o ../../bin/testbench ./cmd/testbench
export TB_API_URL=https://testbench.example.com
export TB_API_KEY=tb_your_prefix_your_secret
./bin/testbench --devices
```

`go -C src/go-client` runs Go inside the client module. The `-o` output path
is relative to that directory, so `../../bin/testbench` places the executable
in the repository's `bin/` directory. The earlier `-o ../../testbench` command
placed it directly in the repository root.

If you already moved the executable into `bin/`, skip the build and run
`./bin/testbench` from the repository root. Mint the token under
**Profile → API keys**; a readonly key is sufficient for these queries.
Go is only needed to build, not to run the resulting executable on a compatible
operating system and architecture.

Optionally add the repository's `bin/` directory to your current shell's PATH
(run this from the repository root):

```bash
export PATH="$PWD/bin:$PATH"
testbench --devices
testbench --help
```

To make this permanent, add an export using the absolute path to that `bin/`
directory to your shell configuration (for example `~/.bashrc`).

Common queries (from the repository root):

```bash
./bin/testbench --types
./bin/testbench --devices --type router
./bin/testbench --devices --type uncategorized
./bin/testbench --devices --search mikrotik
./bin/testbench --devices --filter 'make=MikroTik' --filter 'location=Lab A'
./bin/testbench --devices --filter online=true --status available
./bin/testbench --devices --filter overdue=true
./bin/testbench --devices --type router --fields
./bin/testbench --devices --filter 'serial_number=SN-1' --columns unique_id,serial_number,lan_ip
./bin/testbench --devices --get dev-0042 --json

./bin/testbench --software --latest
./bin/testbench --software --search curl
./bin/testbench --software --filter 'name=curl-smoke' --sort version --order desc
./bin/testbench --software --get curl-smoke --version 3.8.5 --json
./bin/testbench --software --fields

./bin/testbench --vendor-devices --search 'ISR 4331'
./bin/testbench --vendor-devices --filter 'make=Cisco' --support supported
./bin/testbench --vendor-devices --for-software curl-smoke --version 3.8.5
./bin/testbench --vendor-devices --for-device dev-0042 --json

./bin/testbench --tests --for-device dev-0042 --outcome fail
./bin/testbench --tests --for-software curl-smoke --version 3.8.5
./bin/testbench --tests --tag automated --search nightly --limit 20
./bin/testbench --tests --filter 'notes=nightly' --sort created_at --order desc
./bin/testbench --tests --fields
./bin/testbench --tests --get TEST_UUID --json

./bin/testbench --whoami
./bin/testbench --devices --format csv --columns unique_id,device_type_key,status > devices.csv
./bin/testbench --tests --json > results.json
./bin/testbench --help
```

`--vendor-devices` searches every software's compatibility list at once, which
is the question no single software's list can answer: *does anything claim to
support this hardware?* Each row names the software and version making the
claim and reports `software_is_latest`, which is false for a claim made by a
version that has since been superseded — still a real claim, but not current
guidance. `--support` takes the vendor's own word (`supported`, `partial`,
`unsupported`, `planned`); unsupported rows are listed like any other, because
"the vendor says no" is an answer.

`--for-software` narrows to one version's own list and `--for-device` to the
claims about a device's make and model; both compose with the other filters.
Note that a claim is not evidence: `--vendor-devices --for-device dev-0042` is
what vendors say about that hardware, `--tests --for-device dev-0042` is what
has actually been run against it, and the two routinely disagree. Absence of a
test means untested, not unsupported.

Select exactly one resource/action. Listings follow every page by default;
`--limit` caps the number of returned records. Repeat `--filter COLUMN=VALUE`
for different columns; quote arguments containing spaces. Filters are sent to
the API, whose field types determine matching behavior; they are not a local
substring search. Use `--search` for general text search. Column keys come from
`--fields`; for devices, use `--type KEY --fields` to see type-specific fields.
Unknown column filters may be ignored by the server, so use published keys.

Table output uses a small set of common columns. `--columns` selects other
fields, including dynamic device fields and nested paths such as
`misc_data.duration_s`. JSON always includes complete records and is an array,
including for `--get`. CSV uses the selected columns with standard escaping.

`--url` and `--token` override environment variables. Prefer `TB_API_KEY` to
keep the token out of shell command arguments. `--timeout 5m` changes the entire
command's deadline (default two minutes); Ctrl+C cancels it. Exit codes are 0
for success/help, 1 for request/output failures, and 2 for invalid arguments or
configuration. Commands only query data; none modify inventory or run plugins.

## Run from this checkout

Mint a key under **Profile → API keys**. A readonly key is sufficient for queries,
subject to the account's permissions.

```bash
export TB_API_URL=https://testbench.example.com  # host or full /api/v1 prefix
export TB_API_KEY=tb_your_prefix_your_secret
cd src/go-client
go run ./examples/inventory
```

`TB_API_URL` defaults to `http://localhost:8001/api/v1`. `TB_API_KEY` is required.
Tokens are sent in the Authorization header and are never included in URLs.

To use the unpublished module from another local Go project, add a local replace
(the filesystem path is relative to that project's go.mod):

```bash
go mod edit -require=gitlab.joedoes.tech/chainfirelabs/testbench/src/go-client@v0.0.0
go mod edit -replace=gitlab.joedoes.tech/chainfirelabs/testbench/src/go-client=/absolute/path/to/testbench/src/go-client
go mod tidy
```

## Querying

```go
import (
    "context"
    "net/url"

    testbench "gitlab.joedoes.tech/chainfirelabs/testbench/src/go-client"
)

client, err := testbench.New("https://testbench.example.com", token, testbench.Options{
    Retries: 2,
})
if err != nil {
    return err
}
defer client.Close()
ctx := context.Background() // supply your own deadline/cancellation when appropriate

rows, err := client.Devices.List(ctx, testbench.ListOptions{
    Query: url.Values{
        "device_type": {"router"},
        "status":      {"available"},
        "serial_number": {"SN-1"}, // installation-defined field
    },
    Limit: 100,
})
if err != nil {
    return err
}
for _, device := range rows {
    id := device.String("unique_id")
    address := device.Field("lan_ip")
    // Use id and address in your application.
    _, _ = id, address
}
```

`Record` is a `map[string]any` retaining the complete response, including nested
`all_tests`, `verified_tests`, `data`, `misc_data`, and future API fields.
`String(key)` reads a string; `Field(key)` checks the dynamic `data` document
first, then the top-level record. Numbers decode as `json.Number` to avoid
rounding large integers. Timestamps and dates remain the API's strings; parse
them as needed (timestamps without an offset represent UTC).

## Python read-operation equivalents

| Python | Go |
|---|---|
| `whoami()` / `identity` | `Whoami(ctx)` |
| `entity_fields()` | `EntityFields(ctx)` |
| `device_types(include_disabled=True)` | `DeviceTypes(ctx, true)` |
| `device_schema(type_key)` | `DeviceSchema(ctx, typeKey)`; empty key = global |
| `device_schema_revision()` | `DeviceSchemaRevision(ctx)` |
| `devices.list(...)` / `devices.iter(...)` | `Devices.List(ctx, options)` / `Devices.Iterate(ctx, options, callback)` |
| `devices.get(id)` / `devices.exists(id)` | `Devices.Get(ctx, id)` / `Devices.Exists(ctx, id)` |
| `devices.resolve(id)` | `Devices.Resolve(ctx, id)` |
| `devices.overdue(min_days=3, limit=10)` | `Devices.Overdue(ctx, 3, 10)` |
| `devices.actions(id)` / `devices.schema(id)` | `Devices.Actions(ctx, id)` / `Devices.Schema(ctx, id)` |
| `software.list(...)` / `software.iter(...)` | `Software.List(ctx, options)` / `Software.Iterate(ctx, options, callback)` |
| `software.get(name, version)` | `Software.Get(ctx, name, version)`; empty version = current |
| `software.versions(name)` | `Software.Versions(ctx, name)` |
| `software.resolve(name, version)` | `Software.Resolve(ctx, name, version)` |
| `tests.list(...)` / `tests.iter(...)` | `Tests.List(ctx, options)` / `Tests.Iterate(ctx, options, callback)` |
| `tests.get(id)` / `tests.for_device(id)` | `Tests.Get(ctx, id)` / `Tests.ForDevice(ctx, id, limit)` |
| `vendor_devices.list(...)` / `.iter(...)` | `VendorDevices.List(ctx, options)` / `VendorDevices.Iterate(ctx, options, callback)` |
| `vendor_devices.for_software(name, version)` | `VendorDevices.ForSoftware(ctx, name, version, options)` |
| `vendor_devices.for_device(id)` | `VendorDevices.ForDevice(ctx, id, options)` |
| `request(...)` | `Request(ctx, method, path, query, body, &result)` |

Devices accept unique IDs or UUIDs. Software accepts names or UUIDs, with an
optional version. Identifiers containing slashes use the API's query-based
lookup routes. Resolution always queries the current API; unlike Python's
write-oriented resolution cache, it does not pin a software version for a run.

Filters use API names in `url.Values`, including `search`, `sort`, `order`,
`online`, `overdue`, `latest_only`, and arbitrary dynamic field keys. Boolean
values are strings (`"true"` / `"false"`). The API validates filters; use valid
schema values because some invalid filter values simply produce an empty page.

Vendor claims take their own named filters, which combine with anything in
`Query`. A filter supplied twice — once named and once raw — is refused rather
than silently resolved, because a dropped filter reads as a narrower answer
instead of an error:

```go
claims, err := client.VendorDevices.List(ctx, testbench.VendorDeviceListOptions{
    Search:        "ISR 4331",
    SupportStatus: "supported",
    ListOptions:   testbench.ListOptions{Limit: 50},
})

// One version's own list, and the claims about one device's hardware. Both
// take the same options, so the filters carry through the narrowing.
own, err := client.VendorDevices.ForSoftware(ctx, "curl-smoke", "3.8.5", testbench.VendorDeviceListOptions{})
about, err := client.VendorDevices.ForDevice(ctx, "dev-0042", testbench.VendorDeviceListOptions{
    SupportStatus: "supported",
})
```

`SupportStatus` is checked before the request: the list endpoints do not
validate filter values, so an unknown one would return an empty page and read
as "nothing supports it" rather than as an error.

Test filters can resolve human-readable identifiers before listing:

```go
results, err := client.Tests.List(ctx, testbench.TestListOptions{
    Device: "dev-0042",
    Software: "curl-smoke",
    SoftwareVersion: "3.8.5", // omit for current
    ListOptions: testbench.ListOptions{
        Query: url.Values{"outcome": {"fail"}},
        Limit: 20,
    },
})
```

`Limit: 0` means unlimited in Go; negative limits are rejected. `PageSize`
defaults to 200 and is capped at 1000. Iteration delivers rows as pages arrive,
skips duplicate IDs across pages, and stops on callback errors or context
cancellation. A `List` call may return partial rows alongside an error; always
check the error before treating the result as complete.

## Errors and connection behavior

Use `errors.As(err, &apiError)` with `var apiError *testbench.APIError` to inspect
`StatusCode`, `Detail`, `Method`, and `Path`. HTTP errors retain the API's detail
value, including structured validation errors. Network and context errors are
returned as Go errors. A 404 becomes `false, nil` only for `Devices.Exists`.

GET/HEAD requests optionally retry transport failures and 5xx responses;
`Options.Retries` defaults to zero. Authentication failures and writes are not
retried. The default request timeout is 30 seconds. Supply `Options.HTTPClient`
for custom timeouts or TLS settings. Redirects are refused to keep API credentials
on the configured endpoint. The client can be shared by goroutines; do not modify
caller-owned query maps while a request is using them.

This package provides read helpers, as requested. Python's checkout/reservation
and result-writing convenience workflows are not ported. `Request` can call
other endpoints, including writes authorized by the token, without losing access
to API features that do not yet have a helper.

## Verification

```bash
go test -race ./...
go vet ./...
```

Tests use local HTTP servers; they do not need a live TestBench or real token.
