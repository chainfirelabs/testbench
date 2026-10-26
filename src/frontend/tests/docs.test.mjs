import assert from 'node:assert/strict'
import test from 'node:test'

import { highlight, inline, renderMarkdown } from '../src/docs/markdown.ts'
import { example, groupOperations, requestPath, typeLabel } from '../src/docs/openapi.ts'
import { formatSignature, splitParameters } from '../src/docs/signature.ts'

test('markdown never lets markup through', () => {
  const html = renderMarkdown('Hello <script>alert(1)</script> `<b>` [x](javascript:alert(1))')
  assert.ok(!html.includes('<script>'))
  assert.ok(html.includes('&lt;script&gt;'))
  assert.ok(html.includes('<code>&lt;b&gt;</code>'))
  assert.ok(!html.includes('javascript:'))
  assert.ok(!renderMarkdown('```\n<img src=x onerror=1>\n```').includes('<img'))
})

test('markdown renders the blocks the docs use', () => {
  const headings = []
  const html = renderMarkdown('## Install\n\nText **bold**.\n\n- one\n  continued\n- two\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n```bash\npip install x\n```', headings, -1)
  assert.deepEqual(headings, [{ id: 'install', text: 'Install', level: 2 }])
  assert.ok(html.includes('<h2 id="install">Install</h2>'))
  assert.ok(html.includes('<strong>bold</strong>'))
  assert.ok(html.includes('<li>one continued</li>'))
  assert.ok(html.includes('<td>1</td>'))
  assert.ok(html.includes('<span class="code-lang">bash</span>'))
})

test('links keep to safe targets and identifiers keep their underscores', () => {
  assert.equal(inline('[docs](https://example.com)'), '<a href="https://example.com" target="_blank" rel="noopener noreferrer">docs</a>')
  assert.equal(inline('[api](/docs/api)'), '<a href="/docs/api">api</a>')
  assert.equal(inline('test_run_id and *note*'), 'test_run_id and <em>note</em>')
})

test('code is highlighted without losing its escaping', () => {
  const html = highlight('def f(x):  # "<x>"\n    return "a<b"', 'python')
  assert.ok(html.includes('<span class="tok-keyword">def</span>'))
  assert.ok(html.includes('<span class="tok-comment"># &quot;&lt;x&gt;&quot;</span>'))
  assert.ok(html.includes('<span class="tok-string">&quot;a&lt;b&quot;</span>'))
})

const SPEC = {
  paths: {
    '/api/v1/tests': {
      get: { tags: ['tests'], summary: 'List tests', parameters: [{ name: 'page', in: 'query', schema: { type: 'integer', default: 1 } }],
        responses: { 200: { description: 'OK', content: { 'application/json': { schema: { $ref: '#/components/schemas/Page' } } } } } },
      post: { tags: ['tests'], requestBody: { required: true, content: { 'application/json': { schema: { $ref: '#/components/schemas/TestCreate' } } } }, responses: {} },
    },
    '/api/v1/devices/{device_id}': {
      get: { tags: ['devices'], parameters: [{ name: 'device_id', in: 'path', required: true, schema: { type: 'string' } }], responses: {} },
    },
  },
  components: { schemas: {
    Page: { type: 'object', properties: { items: { type: 'array', items: { $ref: '#/components/schemas/TestOut' } } } },
    TestCreate: { type: 'object', required: ['outcome'], properties: {
      outcome: { type: 'string' }, notes: { anyOf: [{ type: 'string' }, { type: 'null' }] }, tag: { type: 'string', default: 'adhoc' } } },
  } },
}

test('operations are grouped by tag, devices before tests', () => {
  const groups = groupOperations(SPEC)
  assert.deepEqual(groups.map((g) => g.title), ['Devices', 'Tests'])
  assert.deepEqual(groups[1].operations.map((op) => op.id), ['get-api-v1-tests', 'post-api-v1-tests'])
  assert.equal(groups[1].operations[1].requestBody.contentType, 'application/json')
})

test('types read the way the schema means them', () => {
  assert.equal(typeLabel({ anyOf: [{ type: 'string' }, { type: 'null' }] }), 'string | null')
  assert.equal(typeLabel({ type: 'array', items: { $ref: '#/components/schemas/TestOut' } }), 'array of TestOut')
  assert.deepEqual(example(SPEC, { $ref: '#/components/schemas/TestCreate' }), { outcome: '', notes: '', tag: 'adhoc' })
})

test('a try-it request fills its path and query', () => {
  const params = [{ name: 'device_id', in: 'path' }, { name: 'page', in: 'query' }, { name: 'q', in: 'query' }]
  assert.equal(requestPath('/api/v1/devices/{device_id}', { device_id: 'dev 1', page: '2', q: '' }, params),
    '/api/v1/devices/dev%201?page=2')
})

test('long signatures go one parameter per line', () => {
  assert.deepEqual(splitParameters('a: dict[str, int], *, b: str = "x, y"'), ['a: dict[str, int]', '*', 'b: str = "x, y"'])
  assert.equal(formatSignature('tb.get', '(name: str) -> Software'), 'tb.get(name: str) -> Software')
  const long = formatSignature('tb.tests.create', `(*, device: str | Device, software: str | Software, outcome: str, tag: str = 'adhoc', notes: str | None = None) -> Test`)
  assert.equal(long.split('\n')[0], 'tb.tests.create(')
  assert.ok(long.includes("\n    tag: str = 'adhoc',\n"))
  assert.ok(long.endsWith('\n) -> Test'))
})

test('an escaped pipe stays inside its table cell', () => {
  const html = renderMarkdown('| Device | Pass |\n|---|---|\n| rtr\\|1 | 3 |')
  assert.ok(html.includes('<td>rtr|1</td><td>3</td>'))
})
