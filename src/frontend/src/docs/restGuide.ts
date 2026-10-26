/**
 * The REST API guide on the Docs page: how to use the API, as opposed to the
 * per-endpoint reference, which is read from the running server's OpenAPI
 * document. Written as markdown and rendered by ./markdown.ts.
 */

export interface GuidePage { slug: string; title: string; markdown: string }

export const REST_GUIDE: GuidePage[] = [
  {
    slug: 'introduction',
    title: 'Introduction',
    markdown: `
Everything the TestBench web app does, it does through a JSON API, and the
same API is open to scripts, CI pipelines and other tools. These docs cover
both ways in:

- **The REST API** — plain HTTP from any language. The guide pages explain
  authentication, lists, filters and files; **API reference** lists every
  endpoint the server offers, read live from this server, with a **Try it**
  panel on each.
- **The Python client** (\`testbench-client\`) — a typed wrapper built for test
  frameworks: reserve a device, run a suite, record what happened. Its guide
  and a reference of every function and option follow the API sections.

## Base URL

Every endpoint lives under \`/api/v1\` on the same host as the web app:

\`\`\`bash
https://testbench.example.com/api/v1/devices
\`\`\`

Nothing else needs to be exposed: the web app's own proxy forwards \`/api\` to
the backend, so a script uses the address you use in the browser.

## Where to start

1. Mint an API key under **Profile → API keys** (see *Authentication*).
2. Check it with \`GET /api/v1/auth/me\`.
3. Pick the endpoint you need from the **API reference**, or install the
   Python client if you are writing a test framework in Python.
`,
  },
  {
    slug: 'authentication',
    title: 'Authentication',
    markdown: `
Every request except the login itself sends a bearer token:

\`\`\`http
Authorization: Bearer tb_1ba207b36474_...
\`\`\`

## API keys

For scripts and pipelines, use an **API key**. Mint one in the web app under
**Profile → API keys**; the full key is shown once, at creation, and has the
form \`tb_<prefix>_<secret>\`.

- A key carries a **role**, chosen when it is minted, and defaults to
  \`readonly\`. It can never be given more than its owner has.
- A key may **expire** (1 to 3650 days) or not; revoke it any time from the
  same page.
- Actions taken with a key are recorded in the audit log under the owner's
  name, with the key's label — label keys so that stays useful.

Keys can also be minted through the API, with an existing token:

\`\`\`bash
curl -X POST "$TB/api/v1/auth/api-keys" \\
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
  -d '{"label": "nightly CI", "role": "readonly", "expires_in_days": 90}'
\`\`\`

## Logging in

A username and password exchange for a short-lived token, which is what the
web app itself uses. Prefer API keys for anything automated: login tokens
expire within hours.

\`\`\`bash
curl -X POST "$TB/api/v1/auth/login" -H "Content-Type: application/json" \\
  -d '{"username": "alice", "password": "..."}'
# {"access_token": "eyJ...", "token_type": "bearer", "user": {...}}
\`\`\`

Accounts that sign in through SSO have no password here; they use API keys.

## Permissions

Reading needs only a valid token. Changing things needs the matching
permission on the token's role:

| Permission | Allows |
| --- | --- |
| \`devices.edit\` | Creating, editing and checking devices in and out |
| \`software.edit\` | Software, versions, components and vendor claims |
| \`tests.edit\` | Recording, editing and deleting tests, test runs and their files |
| \`views.save\` | Saving shared table views |
| \`audit.view\` | Reading the audit log |
| \`users.manage\` | Users and roles |
| \`schema.manage\` | Device types and custom fields |
| \`plugins.manage\` | Device plugins |
| \`settings.manage\` | AI providers and replication |

\`GET /api/v1/auth/me\` returns the account and its effective permissions —
check that rather than a role's name, since roles are defined per
installation.
`,
  },
  {
    slug: 'requests',
    title: 'Lists, filters & errors',
    markdown: `
## Pagination

List endpoints return one page at a time:

\`\`\`json
{"items": [...], "total": 412, "page": 1, "page_size": 50}
\`\`\`

- \`page\` starts at 1; \`page_size\` defaults to 50 and is capped (1000 on most
  lists). Keep requesting pages until you have \`total\` items.
- \`GET /devices\` also takes \`offset\`, counted in rows, which wins over
  \`page\` when both are sent.

## Sorting

\`sort\` names a column and \`order\` is \`asc\` or \`desc\`:
\`GET /api/v1/tests?sort=run_at&order=desc\`.

## Filtering

- \`search\` matches free text across a list's main columns.
- Named filters — \`status\`, \`outcome\`, \`tag\`, \`software_id\` and the like —
  are listed on each endpoint in the reference.
- **Any column**, including your installation's custom fields, filters as
  \`field=value\`: an exact match for indexed fields, a case-insensitive
  substring for text.
- **Checklists**: \`include__field\` and \`exclude__field\` take a JSON array of
  values — \`include__make=["Cisco","Dell"]\` — the same filters the web app's
  column checklists send. \`null\` or \`""\` in the array matches empty values.

\`\`\`bash
curl -G "$TB/api/v1/devices" -H "Authorization: Bearer $TB_API_KEY" \\
  --data-urlencode 'include__make=["Cisco"]' --data-urlencode 'status=available'
\`\`\`

Devices and other records can be addressed by their human identifier where
the reference says so — \`GET /api/v1/devices/dev-0042\`,
\`GET /api/v1/software/nmap\` (a bare software name means its current version).

## Errors

Failures return a status code and a \`detail\`:

\`\`\`json
{"detail": "Device with unique_id 'dev-0004' already exists"}
\`\`\`

| Status | Meaning |
| --- | --- |
| 400 | The request makes no sense for this record |
| 401 | Missing, expired or revoked token |
| 403 | The token's role lacks the permission |
| 404 | No such record |
| 409 | Conflict — a duplicate, or a device someone else has checked out |
| 422 | Validation failed; \`detail\` is a list naming each field (\`loc\`) and the problem (\`msg\`) |
`,
  },
  {
    slug: 'import-export',
    title: 'Import & export',
    markdown: `
Devices, software, tests and vendor claims each have the same three endpoints:

| Endpoint | Does |
| --- | --- |
| \`GET /<list>/template\` | A blank CSV with the columns an import accepts |
| \`POST /<list>/import\` | Upload a \`.csv\` or \`.json\` file (multipart field \`file\`); rows with an \`id\` update, others create |
| \`GET\` or \`POST /<list>/export\` | Download what a filter selects, as \`format=json\` or \`format=csv\` |

An export takes the list's own filters, as query parameters or — to export
exactly what a table shows — in a body:

\`\`\`bash
curl -X POST "$TB/api/v1/tests/export?format=csv" \\
  -H "Authorization: Bearer $TB_API_KEY" -H "Content-Type: application/json" \\
  -d '{"filters": {"software_id": "…", "include__outcome": "[\\"fail\\"]"}, "ids": null}' \\
  -o failures.csv
\`\`\`

\`ids\` exports exactly those rows instead. Exports stream, so they suit large
lists; the import reports \`created\`, \`updated\` and per-row \`errors\`.

## Tests with their devices

\`/tests/export?include_device=true\` adds each test's device to its row —
\`device_*\` columns in CSV, a \`device\` object in JSON — and the test's \`id\`.
Sensitive device fields such as passwords are never included. This is what
**Software → Tested Devices → Export tests** downloads.
`,
  },
  {
    slug: 'files',
    title: 'Test files & runs',
    markdown: `
A test can carry files, stored in the object storage an administrator
configures:

- **Artifacts** — any files: \`POST /api/v1/tests/{test_id}/artifacts\` with a
  multipart \`file\`; list, download and delete under the same path.
- **Output** — the run's log text: \`PUT /api/v1/tests/{test_id}/output\` with a
  multipart \`file\`, read back with \`GET\` on the same path.

\`\`\`bash
curl -X POST "$TB/api/v1/tests/$TEST/artifacts" \\
  -H "Authorization: Bearer $TB_API_KEY" -F file=@report.html
\`\`\`

## Test runs

A **test run** groups the tests of one pipeline or suite execution:

1. \`POST /api/v1/test-runs\` with a \`name\` — and an \`external_id\` such as the
   CI pipeline's ID, so every job of the pipeline joins the same run.
2. Record tests with its \`test_run_id\`.
3. Attach files every test in the run shares to
   \`/api/v1/test-runs/{run_id}/artifacts\`; they are stored once.
4. \`PATCH /api/v1/test-runs/{run_id}\` with \`finished_at\` when it is done.

Upload limits and whether storage is enabled are in
\`GET /api/v1/tests/file-storage\`.
`,
  },
  {
    slug: 'examples',
    title: 'Examples',
    markdown: `
Set the address and key once:

\`\`\`bash
export TB=https://testbench.example.com
export TB_API_KEY=tb_1ba207b36474_...
\`\`\`

## Find available devices

\`\`\`bash
curl -s "$TB/api/v1/devices?status=available&page_size=100" \\
  -H "Authorization: Bearer $TB_API_KEY"
\`\`\`

## Record a test

A test names its software and device by ID; look them up by name first:

\`\`\`bash
SW=$(curl -s "$TB/api/v1/software/nmap" -H "Authorization: Bearer $TB_API_KEY" | jq -r .id)
DEV=$(curl -s "$TB/api/v1/devices/dev-0042" -H "Authorization: Bearer $TB_API_KEY" | jq -r .id)
curl -X POST "$TB/api/v1/tests" -H "Authorization: Bearer $TB_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d "{\\"software_id\\": \\"$SW\\", \\"device_id\\": \\"$DEV\\", \\"outcome\\": \\"pass\\", \\"tag\\": \\"automated\\"}"
\`\`\`

## Every failure of a version, as CSV

\`\`\`bash
curl -G "$TB/api/v1/tests/export" -H "Authorization: Bearer $TB_API_KEY" \\
  --data-urlencode "software_id=$SW" --data-urlencode "outcome=fail" \\
  --data-urlencode "format=csv" --data-urlencode "include_device=true" -o failures.csv
\`\`\`

## The same in Python

\`\`\`python
import httpx

tb = httpx.Client(base_url="https://testbench.example.com/api/v1",
                  headers={"Authorization": f"Bearer {key}"})
page = tb.get("/devices", params={"status": "available"}).json()
for device in page["items"]:
    print(device["unique_id"], device["make"], device["model"])
\`\`\`

For anything more than a few calls, the **Python client** does the paging,
name lookups, retries and error types for you.
`,
  },
]
