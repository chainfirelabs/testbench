<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { getToken } from '../api/client'
import { escapeHtml, renderMarkdown } from '../docs/markdown'
import { deref, example, properties, referencedNames, requestPath, typeLabel, type Operation } from '../docs/openapi'

const props = defineProps<{ op: Operation; spec: any }>()

const description = computed(() => renderMarkdown(props.op.description, [], 3))
const pathParams = computed(() => props.op.parameters.filter((p) => p.in === 'path'))
const queryParams = computed(() => props.op.parameters.filter((p) => p.in === 'query'))
const bodyFields = computed(() => props.op.requestBody ? properties(props.spec, props.op.requestBody.schema) : [])
const isMultipart = computed(() => props.op.requestBody?.contentType.startsWith('multipart/') ?? false)
const isJsonBody = computed(() => props.op.requestBody?.contentType === 'application/json')
// A browser will not send a body with GET; the lists that accept one take
// the same filters as query parameters.
const canSendBody = computed(() => Boolean(props.op.requestBody) && props.op.method !== 'get')

/** A type label with each named schema linked to its definition. */
function typeHtml(label: string, refs: Iterable<string>): string {
  let html = escapeHtml(label)
  for (const name of refs) {
    html = html.replace(new RegExp(`\\b${name}\\b`, 'g'),
      `<a href="/docs/api/schemas#schema-${name}" data-doc-link>${name}</a>`)
  }
  return html
}

function schemaHtml(schema: any): string {
  return typeHtml(typeLabel(schema), referencedNames(schema))
}

function isFile(schema: any): boolean {
  const resolved = deref(props.spec, schema)
  const parts = resolved?.anyOf || [resolved]
  return parts.some((part: any) => part?.format === 'binary' || part?.contentMediaType
    || (part?.type === 'array' && (part.items?.format === 'binary' || part.items?.contentMediaType)))
}

/* ---------------- Try it ---------------- */

const open = ref(false)
const values = ref<Record<string, string>>({})
const bodyText = ref('')
const files = ref<Record<string, File[]>>({})
const sending = ref(false)
const result = ref<{ status: number; statusText: string; ms: number; contentType: string; text?: string; blobUrl?: string; size?: number } | null>(null)
const error = ref('')

function openTry() {
  open.value = !open.value
  if (open.value && canSendBody.value && isJsonBody.value && !bodyText.value) {
    bodyText.value = JSON.stringify(example(props.spec, props.op.requestBody!.schema), null, 2)
  }
}

function pickFiles(name: string, event: Event) {
  files.value = { ...files.value, [name]: Array.from((event.target as HTMLInputElement).files || []) }
}

function releaseBlob() {
  if (result.value?.blobUrl) URL.revokeObjectURL(result.value.blobUrl)
}
onBeforeUnmount(releaseBlob)

const MAX_SHOWN = 200_000

async function send() {
  error.value = ''
  const missing = pathParams.value.filter((p) => !(values.value[p.name] || '').trim()).map((p) => p.name)
  if (missing.length) {
    error.value = `Fill in ${missing.join(', ')}.`
    return
  }
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let body: BodyInit | undefined
  if (canSendBody.value && isJsonBody.value && bodyText.value.trim()) {
    try {
      body = JSON.stringify(JSON.parse(bodyText.value))
    } catch {
      error.value = 'The body is not valid JSON.'
      return
    }
    headers['Content-Type'] = 'application/json'
  } else if (canSendBody.value && isMultipart.value) {
    const form = new FormData()
    for (const field of bodyFields.value) {
      for (const file of files.value[field.name] || []) form.append(field.name, file)
      const text = values.value[`body:${field.name}`]
      if (text) form.append(field.name, text)
    }
    body = form
  }
  sending.value = true
  releaseBlob()
  result.value = null
  const started = performance.now()
  try {
    const response = await fetch(requestPath(props.op.path, values.value, props.op.parameters), {
      method: props.op.method.toUpperCase(), headers, body,
    })
    const contentType = response.headers.get('content-type') || ''
    const base = { status: response.status, statusText: response.statusText, contentType }
    if (response.status === 204) {
      result.value = { ...base, ms: performance.now() - started, text: '' }
    } else if (/json|text|csv|xml|yaml/.test(contentType)) {
      let text = await response.text()
      if (contentType.includes('json')) {
        try { text = JSON.stringify(JSON.parse(text), null, 2) } catch { /* shown as sent */ }
      }
      const cut = text.length > MAX_SHOWN
      result.value = { ...base, ms: performance.now() - started,
        text: cut ? `${text.slice(0, MAX_SHOWN)}\n… (${text.length - MAX_SHOWN} more characters)` : text }
    } else {
      const blob = await response.blob()
      result.value = { ...base, ms: performance.now() - started, blobUrl: URL.createObjectURL(blob), size: blob.size }
    }
  } catch (e: any) {
    error.value = e.message || String(e)
  } finally {
    sending.value = false
  }
}

const curl = computed(() => {
  const url = `${window.location.origin}${requestPath(props.op.path, values.value, props.op.parameters)}`
  const lines = [`curl -X ${props.op.method.toUpperCase()} "${url}"`, '  -H "Authorization: Bearer $TB_API_KEY"']
  if (canSendBody.value && isJsonBody.value) {
    lines.push('  -H "Content-Type: application/json"', `  -d '${(bodyText.value || '{}').replace(/\s*\n\s*/g, ' ')}'`)
  } else if (canSendBody.value && isMultipart.value) {
    for (const field of bodyFields.value) lines.push(`  -F ${field.name}=${isFile(field.schema) ? '@path/to/file' : '...'}`)
  }
  return lines.join(' \\\n')
})
</script>

<template>
  <section :id="op.id" class="endpoint" :class="{ deprecated: op.deprecated }">
    <h3 class="endpoint-title">
      <span class="method" :class="`method-${op.method}`">{{ op.method.toUpperCase() }}</span>
      <code class="endpoint-path">{{ op.path }}</code>
      <a class="anchor" :href="`#${op.id}`" aria-label="Link to this endpoint">¶</a>
    </h3>
    <p v-if="op.summary" class="endpoint-summary">{{ op.summary }}<span v-if="op.deprecated" class="muted"> (deprecated)</span></p>
    <div v-if="op.description" class="doc-prose" v-html="description"></div>

    <template v-if="pathParams.length || queryParams.length">
      <h4>Parameters</h4>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>In</th><th>Type</th><th>Description</th></tr></thead>
          <tbody>
            <tr v-for="p in [...pathParams, ...queryParams]" :key="`${p.in}-${p.name}`">
              <td><code>{{ p.name }}</code><span v-if="p.required" class="required" title="Required">*</span></td>
              <td>{{ p.in }}</td>
              <td class="type-cell" v-html="schemaHtml(p.schema)"></td>
              <td>{{ p.description }}<span v-if="p.schema?.default !== undefined" class="muted"> Default <code>{{ JSON.stringify(p.schema.default) }}</code>.</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-if="op.requestBody">
      <h4>Request body <span class="muted content-type">{{ op.requestBody.contentType }}</span></h4>
      <p class="type-line">Type: <span v-html="schemaHtml(op.requestBody.schema)"></span></p>
      <div v-if="bodyFields.length" class="table-wrap">
        <table>
          <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
          <tbody>
            <tr v-for="f in bodyFields" :key="f.name">
              <td><code>{{ f.name }}</code><span v-if="f.required" class="required" title="Required">*</span></td>
              <td class="type-cell" v-html="typeHtml(f.type, f.refs)"></td>
              <td>{{ f.description }}<span v-if="f.default !== undefined" class="muted"> Default <code>{{ f.default }}</code>.</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <h4>Responses</h4>
    <ul class="responses">
      <li v-for="r in op.responses" :key="r.status">
        <span class="status" :class="`status-${r.status[0]}`">{{ r.status }}</span>
        {{ r.description }}
        <span v-if="r.schema" class="muted"> — <span v-html="schemaHtml(r.schema)"></span></span>
      </li>
    </ul>

    <div class="try">
      <button class="btn btn-mini" type="button" :aria-expanded="open" @click="openTry">
        {{ open ? 'Hide' : 'Try it' }}
      </button>
      <div v-if="open" class="try-panel">
        <p class="muted try-note">
          Sent as you, against this server's live data<span v-if="op.method !== 'get'"> — this request changes things</span>.
        </p>
        <div v-if="pathParams.length || queryParams.length" class="try-grid">
          <label v-for="p in [...pathParams, ...queryParams]" :key="`try-${p.in}-${p.name}`">
            <span>{{ p.name }}<span v-if="p.required" class="required">*</span> <span class="muted">{{ p.in }}</span></span>
            <input v-model="values[p.name]" type="text" :placeholder="p.schema?.default !== undefined ? String(p.schema.default) : typeLabel(p.schema)" />
          </label>
        </div>
        <p v-if="op.requestBody && !canSendBody" class="muted try-note">
          The optional body is not sent from here: browsers do not send one with GET. Put filters in the query
          parameters instead.
        </p>
        <label v-if="canSendBody && isJsonBody" class="try-body">
          <span>Body <span class="muted">application/json</span></span>
          <textarea v-model="bodyText" rows="8" spellcheck="false"></textarea>
        </label>
        <div v-else-if="canSendBody && isMultipart" class="try-grid">
          <label v-for="f in bodyFields" :key="`try-body-${f.name}`">
            <span>{{ f.name }}<span v-if="f.required" class="required">*</span></span>
            <input v-if="isFile(f.schema)" type="file" multiple @change="pickFiles(f.name, $event)" />
            <input v-else v-model="values[`body:${f.name}`]" type="text" />
          </label>
        </div>
        <div class="try-actions">
          <button class="btn btn-primary" type="button" :disabled="sending" @click="send">
            {{ sending ? 'Sending…' : `Send ${op.method.toUpperCase()}` }}
          </button>
        </div>
        <p v-if="error" class="login-error" role="alert">{{ error }}</p>
        <div v-if="result" class="try-result" role="status">
          <p>
            <span class="status" :class="`status-${String(result.status)[0]}`">{{ result.status }}</span>
            {{ result.statusText }} <span class="muted">· {{ Math.round(result.ms) }} ms<span v-if="result.contentType"> · {{ result.contentType }}</span></span>
          </p>
          <div v-if="result.text !== undefined" class="code-block"><pre><code>{{ result.text || '(empty)' }}</code></pre></div>
          <p v-else-if="result.blobUrl">
            {{ result.size }} bytes ·
            <a :href="result.blobUrl" download>Download response</a>
          </p>
        </div>
        <details class="try-curl">
          <summary>As curl</summary>
          <div class="code-block"><pre><code>{{ curl }}</code></pre></div>
        </details>
      </div>
    </div>
  </section>
</template>

<style scoped>
.endpoint { padding: 8px 0 18px; border-bottom: 1px solid var(--border-soft); }
.endpoint.deprecated { opacity: 0.7; }
.endpoint-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 22px 0 6px; font-size: 16px; }
.endpoint-path { font-size: 14px; overflow-wrap: anywhere; }
.endpoint-summary { margin: 4px 0 8px; font-weight: 550; }
.method {
  display: inline-block;
  min-width: 62px;
  padding: 2px 8px;
  border-radius: var(--r-sm);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-align: center;
  color: #0b0b0d;
}
.method-get { background: #5fb3f9; }
.method-post { background: var(--success); }
.method-put { background: var(--accent); }
.method-patch { background: var(--accent-soft); }
.method-delete { background: var(--danger); color: #fff; }
.content-type { font-size: 12px; text-transform: none; letter-spacing: 0; font-weight: 400; }
.type-line { margin: 4px 0 8px; }
.responses { list-style: none; padding: 0; margin: 6px 0 12px; }
.responses li { padding: 3px 0; }
.status {
  display: inline-block;
  min-width: 40px;
  padding: 0 6px;
  border-radius: var(--r-sm);
  font-weight: 650;
  font-size: 12px;
  text-align: center;
  border: 1px solid var(--border);
}
.status-2 { color: var(--success); border-color: rgba(34, 197, 94, 0.45); }
.status-3 { color: #5fb3f9; }
.status-4 { color: var(--warn); border-color: rgba(234, 179, 8, 0.45); }
.status-5 { color: var(--danger); border-color: rgba(239, 68, 68, 0.45); }
.try { margin-top: 8px; }
.try-panel {
  margin-top: 10px;
  padding: 14px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}
.try-note { margin: 0 0 10px; font-size: 13px; }
.try-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; margin-bottom: 10px; }
.try-grid label, .try-body { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.try-body { margin-bottom: 10px; }
.try-body textarea {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  min-height: 140px;
  resize: vertical;
}
.try-actions { display: flex; gap: 8px; }
.try-result { margin-top: 12px; }
.try-result pre { max-height: 420px; overflow: auto; }
.try-curl { margin-top: 10px; font-size: 13px; }
.try-curl summary { cursor: pointer; color: var(--text-muted); }
</style>
