<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DocsEndpoint from '../components/DocsEndpoint.vue'
import { getToken } from '../api/client'
import { escapeHtml, renderMarkdown, type Heading } from '../docs/markdown'
import { groupOperations, properties, referencedNames, typeLabel } from '../docs/openapi'
import { formatSignature, PYTHON, type PyMember, type PyResource } from '../docs/python'
import { REST_GUIDE } from '../docs/restGuide'

/*
 * The Docs page: a Read the Docs layout over three sources — the REST guide
 * written here, the running server's OpenAPI document (so the reference is
 * always this server's), and the Python client's reference generated from its
 * code. Served by the web app itself, so nothing on the backend has to be
 * exposed to read it.
 */

const route = useRoute()
const router = useRouter()
const path = computed(() => String(route.params.path || '').replace(/^\/+|\/+$/g, ''))

/* ---------------- The API's OpenAPI document ---------------- */

const spec = ref<any>(null)
const specError = ref('')

async function loadSpec() {
  try {
    const token = getToken()
    const response = await fetch('/api/v1/openapi.json', { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!response.ok) throw new Error(`The server answered ${response.status}.`)
    spec.value = await response.json()
  } catch (e: any) {
    specError.value = e.message || String(e)
  }
}

const groups = computed(() => (spec.value ? groupOperations(spec.value) : []))
const schemaNames = computed(() => Object.keys(spec.value?.components?.schemas || {}).sort((a, b) => a.localeCompare(b)))
const operationCount = computed(() => groups.value.reduce((sum, group) => sum + group.operations.length, 0))

/* ---------------- Navigation ---------------- */

interface NavPage { path: string; title: string }
interface NavSection { caption: string; pages: NavPage[] }

const sections = computed<NavSection[]>(() => [
  { caption: 'Using the API', pages: REST_GUIDE.map((page, i) => ({ path: i === 0 ? '' : `guide/${page.slug}`, title: page.title })) },
  {
    caption: 'API reference',
    pages: [
      { path: 'api', title: 'Overview' },
      ...groups.value.map((group) => ({ path: `api/${group.slug}`, title: group.title })),
      ...(spec.value ? [{ path: 'api/schemas', title: 'Schemas' }] : []),
    ],
  },
  { caption: 'Python client', pages: PYTHON.guide.map((page) => ({ path: `python/${page.slug}`, title: page.title })) },
  {
    caption: 'Python client reference',
    pages: [
      ...PYTHON.reference.map((item) => ({ path: `python/ref/${item.slug}`, title: item.accessor })),
      { path: 'python/models', title: 'Models' },
      { path: 'python/errors', title: 'Errors & constants' },
    ],
  },
])

const flat = computed(() => sections.value.flatMap((section) => section.pages.map((page) => ({ ...page, caption: section.caption }))))
const position = computed(() => flat.value.findIndex((page) => page.path === path.value))
const current = computed(() => flat.value[position.value] || null)
const previous = computed(() => (position.value > 0 ? flat.value[position.value - 1] : null))
const next = computed(() => (position.value >= 0 && position.value < flat.value.length - 1 ? flat.value[position.value + 1] : null))

function docsLink(target: string, hash = '') {
  return { path: target ? `/docs/${target}` : '/docs', hash: hash ? `#${hash}` : '' }
}

/* ---------------- The page being read ---------------- */

interface TocEntry { id: string; text: string; level: number }

function markdownPage(markdown: string) {
  const headings: Heading[] = []
  const html = renderMarkdown(markdown, headings, -1)
  return { html, toc: headings.filter((h) => h.level <= 3).map((h) => ({ id: h.id, text: h.text, level: h.level })) }
}

const page = computed(() => {
  const p = path.value
  const guide = p === '' ? REST_GUIDE[0] : p.startsWith('guide/') ? REST_GUIDE.find((g) => `guide/${g.slug}` === p) : null
  if (guide) return { kind: 'markdown' as const, title: guide.title, ...markdownPage(guide.markdown) }
  if (p === 'api') {
    return { kind: 'api-overview' as const, title: 'API reference', toc: groups.value.map((g) => ({ id: g.slug, text: g.title, level: 2 })) }
  }
  if (p === 'api/schemas') {
    return { kind: 'schemas' as const, title: 'Schemas', toc: schemaNames.value.map((name) => ({ id: `schema-${name}`, text: name, level: 2 })) }
  }
  if (p.startsWith('api/')) {
    const group = groups.value.find((g) => `api/${g.slug}` === p)
    if (group) {
      return { kind: 'api-tag' as const, title: group.title, group,
        toc: group.operations.map((op) => ({ id: op.id, text: `${op.method.toUpperCase()} ${op.path.replace(/^\/api\/v1/, '')}`, level: 2 })) }
    }
    return { kind: 'pending' as const, title: 'API reference', toc: [] }
  }
  if (p.startsWith('python/ref/')) {
    const resource = PYTHON.reference.find((r) => `python/ref/${r.slug}` === p)
    if (resource) {
      return { kind: 'py-resource' as const, title: resource.accessor, resource,
        toc: [
          ...(resource.init_signature ? [{ id: `${resource.slug}-constructor`, text: 'Constructor', level: 2 }] : []),
          ...resource.members.map((m) => ({ id: memberId(resource, m), text: m.name, level: 2 })),
        ] }
    }
  }
  if (p === 'python/models') {
    return { kind: 'py-models' as const, title: 'Models', toc: PYTHON.models.map((m) => ({ id: `model-${m.slug}`, text: m.name, level: 2 })) }
  }
  if (p === 'python/errors') {
    return { kind: 'py-errors' as const, title: 'Errors & constants',
      toc: [...PYTHON.errors.map((e) => ({ id: `error-${e.name}`, text: e.name, level: 2 })), { id: 'constants', text: 'Constants', level: 2 }] }
  }
  if (p.startsWith('python/')) {
    const section = PYTHON.guide.find((g) => `python/${g.slug}` === p)
    if (section) return { kind: 'markdown' as const, title: section.title === 'Overview' ? 'Python client' : section.title, ...markdownPage(section.markdown) }
  }
  return { kind: 'missing' as const, title: 'Page not found', toc: [] as TocEntry[] }
})

const docHtml = (text: string) => renderMarkdown(text, [], 2)

function memberId(resource: PyResource, member: PyMember) {
  return `${resource.slug}-${member.name}`
}

/** How a member is called: `tb.tests.create(...)`, `TestBench.from_env(...)`, `tb.identity`. */
function memberCall(resource: PyResource, member: PyMember) {
  const owner = resource.accessor === 'TestBench' ? (member.kind === 'classmethod' ? 'TestBench' : 'tb') : resource.accessor
  return formatSignature(`${owner}.${member.name}`, member.signature)
}

function typeHtml(schema: any) {
  let html = escapeHtml(typeLabel(schema))
  for (const name of referencedNames(schema)) {
    html = html.replace(new RegExp(`\\b${name}\\b`, 'g'), `<a href="/docs/api/schemas#schema-${name}">${name}</a>`)
  }
  return html
}

function schemaOf(name: string) {
  return spec.value?.components?.schemas?.[name]
}

/* ---------------- Search ---------------- */

const query = ref('')
interface SearchHit { title: string; detail: string; to: ReturnType<typeof docsLink> }

const hits = computed<SearchHit[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (q.length < 2) return []
  const out: SearchHit[] = []
  const has = (...texts: (string | undefined)[]) => texts.some((text) => (text || '').toLowerCase().includes(q))
  for (const item of flat.value) if (has(item.title)) out.push({ title: item.title, detail: item.caption, to: docsLink(item.path) })
  for (const group of groups.value) {
    for (const op of group.operations) {
      if (has(op.path, op.summary, `${op.method} ${op.path}`)) {
        out.push({ title: `${op.method.toUpperCase()} ${op.path.replace(/^\/api\/v1/, '')}`, detail: op.summary || group.title, to: docsLink(`api/${group.slug}`, op.id) })
      }
    }
  }
  for (const resource of PYTHON.reference) {
    for (const member of resource.members) {
      if (has(`${resource.accessor}.${member.name}`)) {
        out.push({ title: `${resource.accessor}.${member.name}`, detail: member.doc.split('\n')[0], to: docsLink(`python/ref/${resource.slug}`, memberId(resource, member)) })
      }
    }
  }
  for (const model of PYTHON.models) if (has(model.name)) out.push({ title: model.name, detail: 'Python model', to: docsLink('python/models', `model-${model.slug}`) })
  for (const name of schemaNames.value) if (has(name)) out.push({ title: name, detail: 'API schema', to: docsLink('api/schemas', `schema-${name}`) })
  return out.slice(0, 50)
})

/* ---------------- Scrolling and links ---------------- */

const main = ref<HTMLElement | null>(null)
const navOpen = ref(false)

async function scrollToHash() {
  await nextTick()
  const id = decodeURIComponent(route.hash.replace(/^#/, ''))
  const target = id ? document.getElementById(id) : null
  if (target) target.scrollIntoView({ block: 'start' })
  else main.value?.scrollTo({ top: 0 })
}

watch(() => route.fullPath, () => {
  navOpen.value = false
  scrollToHash()
})
// A deep link into the reference can only land once the document has loaded.
watch(spec, () => { if (route.hash) scrollToHash() })

/** Links inside rendered markdown stay in the app rather than reloading it. */
function onContentClick(event: MouseEvent) {
  const anchor = (event.target as HTMLElement).closest('a')
  if (!anchor || anchor.target === '_blank' || event.ctrlKey || event.metaKey || event.shiftKey) return
  const href = anchor.getAttribute('href') || ''
  if (href.startsWith('#')) {
    event.preventDefault()
    router.push({ path: route.path, hash: href }).then(scrollToHash)
  } else if (href.startsWith('/docs')) {
    event.preventDefault()
    router.push(href).then(scrollToHash)
  }
}

function pickHit(hit: SearchHit) {
  query.value = ''
  router.push(hit.to).then(scrollToHash)
}

onMounted(() => {
  loadSpec()
  scrollToHash()
})
</script>

<template>
  <div class="docs" :class="{ 'nav-open': navOpen }">
    <aside class="docs-nav" aria-label="Documentation">
      <router-link class="docs-brand" :to="docsLink('')">
        <span class="docs-brand-name">TestBench Docs</span>
        <span class="docs-brand-version">API v1 · client {{ PYTHON.version }}</span>
      </router-link>
      <div class="docs-search">
        <input v-model="query" type="search" placeholder="Search docs" aria-label="Search the documentation"
          @keydown.enter.prevent="hits[0] && pickHit(hits[0])" />
      </div>
      <nav v-if="query.trim().length >= 2" class="docs-hits">
        <p v-if="!hits.length" class="docs-empty">No matches.</p>
        <button v-for="hit in hits" :key="`${hit.to.path}${hit.to.hash}${hit.title}`" type="button" class="docs-hit" @click="pickHit(hit)">
          <span class="docs-hit-title">{{ hit.title }}</span>
          <span class="docs-hit-detail">{{ hit.detail }}</span>
        </button>
      </nav>
      <nav v-else class="docs-tree">
        <div v-for="section in sections" :key="section.caption" class="docs-section">
          <p class="docs-caption">{{ section.caption }}</p>
          <ul>
            <li v-for="item in section.pages" :key="item.path" :class="{ current: item.path === path }">
              <router-link :to="docsLink(item.path)">{{ item.title }}</router-link>
              <ul v-if="item.path === path && page.toc.length" class="docs-toc">
                <li v-for="entry in page.toc" :key="entry.id" :class="`toc-level-${entry.level}`">
                  <router-link :to="docsLink(item.path, entry.id)" @click="scrollToHash">{{ entry.text }}</router-link>
                </li>
              </ul>
            </li>
            <li v-if="section.caption === 'API reference' && !spec" class="docs-pending">
              {{ specError ? 'Could not load the reference' : 'Loading the reference…' }}
            </li>
          </ul>
        </div>
      </nav>
    </aside>
    <div v-if="navOpen" class="docs-scrim" @click="navOpen = false"></div>

    <main ref="main" class="docs-main" @click="onContentClick">
      <div class="docs-topbar">
        <button class="btn btn-mini docs-nav-toggle" type="button" @click="navOpen = !navOpen">☰ Contents</button>
        <nav class="docs-breadcrumbs" aria-label="Breadcrumb">
          <router-link :to="docsLink('')">Docs</router-link>
          <template v-if="current && current.path !== ''">
            <span aria-hidden="true">»</span><span>{{ current.caption }}</span>
            <span aria-hidden="true">»</span><span class="docs-crumb-current">{{ current.title }}</span>
          </template>
        </nav>
      </div>

      <article class="docs-article">
        <h1>{{ page.title }}</h1>

        <div v-if="page.kind === 'markdown'" class="doc-prose" v-html="page.html"></div>

        <template v-else-if="page.kind === 'api-overview'">
          <div class="doc-prose">
            <p>
              Every endpoint this server offers, read from its OpenAPI document. Each one lists its parameters, body and
              responses, and has a <strong>Try it</strong> panel that sends a real request as you. Paths below are
              relative to this host; <code>/api/v1</code> is part of each one.
            </p>
            <p v-if="specError" class="login-error" role="alert">Could not load the API reference: {{ specError }}</p>
            <p v-else-if="!spec" class="muted" role="status">Loading the API reference…</p>
            <template v-else>
              <p class="muted">{{ spec.info?.title }} {{ spec.info?.version }} · {{ operationCount }} endpoints · {{ schemaNames.length }} schemas</p>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Section</th><th>Endpoints</th></tr></thead>
                  <tbody>
                    <tr v-for="group in groups" :id="group.slug" :key="group.slug">
                      <td><router-link :to="docsLink(`api/${group.slug}`)">{{ group.title }}</router-link></td>
                      <td>{{ group.operations.length }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
          </div>
        </template>

        <template v-else-if="page.kind === 'api-tag'">
          <DocsEndpoint v-for="op in page.group.operations" :key="op.id" :op="op" :spec="spec" />
        </template>

        <template v-else-if="page.kind === 'schemas'">
          <p class="muted">The shapes request bodies and responses are made of. Fields marked * are required.</p>
          <section v-for="name in schemaNames" :id="`schema-${name}`" :key="name" class="schema">
            <h3><code>{{ name }}</code><a class="anchor" :href="`#schema-${name}`" aria-label="Link to this schema">¶</a></h3>
            <div v-if="schemaOf(name)?.description" class="doc-prose" v-html="docHtml(schemaOf(name).description)"></div>
            <p v-if="schemaOf(name)?.enum" class="type-line">One of: <code v-for="value in schemaOf(name).enum" :key="String(value)" class="enum-value">{{ JSON.stringify(value) }}</code></p>
            <div v-if="properties(spec, schemaOf(name)).length" class="table-wrap">
              <table>
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody>
                  <tr v-for="field in properties(spec, schemaOf(name))" :key="field.name">
                    <td><code>{{ field.name }}</code><span v-if="field.required" class="required" title="Required">*</span></td>
                    <td class="type-cell" v-html="typeHtml(field.schema)"></td>
                    <td>{{ field.description }}<span v-if="field.default !== undefined" class="muted"> Default <code>{{ field.default }}</code>.</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </template>

        <template v-else-if="page.kind === 'py-resource'">
          <p class="muted">
            <template v-if="page.resource.accessor === 'TestBench'"><code>from testbench_client import TestBench</code></template>
            <template v-else>Reached from a client as <code>{{ page.resource.accessor }}</code> (class <code>{{ page.resource.class }}</code>).</template>
          </p>
          <div class="doc-prose" v-html="docHtml(page.resource.doc)"></div>
          <section v-if="page.resource.init_signature" :id="`${page.resource.slug}-constructor`" class="py-member">
            <h2>Constructor</h2>
            <div class="code-block py-sig"><pre><code>{{ formatSignature('TestBench', page.resource.init_signature.replace(/ -> None$/, '')) }}</code></pre></div>
          </section>
          <h2 v-if="page.resource.members.length">Methods and properties</h2>
          <section v-for="member in page.resource.members" :id="memberId(page.resource, member)" :key="member.name" class="py-member">
            <h3>
              <code>{{ member.name }}</code>
              <span v-if="member.kind !== 'method'" class="py-kind">{{ member.kind }}</span>
              <a class="anchor" :href="`#${memberId(page.resource, member)}`" aria-label="Link to this function">¶</a>
            </h3>
            <div class="code-block py-sig"><pre><code>{{ memberCall(page.resource, member) }}</code></pre></div>
            <div v-if="member.doc" class="doc-prose" v-html="docHtml(member.doc)"></div>
          </section>
        </template>

        <template v-else-if="page.kind === 'py-models'">
          <p class="muted">What the client's methods return. Each is a dataclass, importable from <code>testbench_client</code>.</p>
          <section v-for="model in PYTHON.models" :id="`model-${model.slug}`" :key="model.name" class="py-member">
            <h2><code>{{ model.name }}</code><a class="anchor" :href="`#model-${model.slug}`" aria-label="Link to this model">¶</a></h2>
            <div v-if="model.doc" class="doc-prose" v-html="docHtml(model.doc)"></div>
            <div v-if="model.fields.length" class="table-wrap">
              <table>
                <thead><tr><th>Field</th><th>Type</th><th>Default</th></tr></thead>
                <tbody>
                  <tr v-for="field in model.fields" :key="field.name">
                    <td><code>{{ field.name }}</code></td>
                    <td><code class="type-code">{{ field.type }}</code></td>
                    <td><code v-if="field.default !== null">{{ field.default }}</code><span v-else class="muted">required</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-for="member in model.members" :key="member.name" class="py-model-member">
              <div class="code-block py-sig"><pre><code>{{ formatSignature(member.kind === 'classmethod' ? `${model.name}.${member.name}` : `.${member.name}`, member.signature) }}</code></pre></div>
              <div v-if="member.doc" class="doc-prose" v-html="docHtml(member.doc)"></div>
            </div>
          </section>
        </template>

        <template v-else-if="page.kind === 'py-errors'">
          <p class="muted">
            Every error the client raises is a <code>TestBenchError</code>; catch the narrower ones for the cases you
            handle. All are importable from <code>testbench_client</code>.
          </p>
          <section v-for="item in PYTHON.errors" :id="`error-${item.name}`" :key="item.name" class="py-member">
            <h3><code>{{ item.name }}</code> <span class="py-kind">{{ item.base }}</span></h3>
            <div v-if="item.doc" class="doc-prose" v-html="docHtml(item.doc)"></div>
          </section>
          <section id="constants">
            <h2>Constants</h2>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Name</th><th>Value</th></tr></thead>
                <tbody>
                  <tr v-for="constant in PYTHON.constants" :key="constant.name">
                    <td><code>{{ constant.name }}</code></td>
                    <td><code class="type-code">{{ constant.value }}</code></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </template>

        <template v-else-if="page.kind === 'pending'">
          <p v-if="specError" class="login-error" role="alert">Could not load the API reference: {{ specError }}</p>
          <p v-else-if="!spec" class="muted" role="status">Loading the API reference…</p>
          <p v-else>There is no such section. <router-link :to="docsLink('api')">See every section</router-link>.</p>
        </template>

        <p v-else class="muted">There is no page here. <router-link :to="docsLink('')">Back to the start</router-link>.</p>
      </article>

      <footer class="docs-footer">
        <div class="docs-pager">
          <router-link v-if="previous" class="btn" :to="docsLink(previous.path)">« {{ previous.title }}</router-link>
          <span v-else></span>
          <router-link v-if="next" class="btn" :to="docsLink(next.path)">{{ next.title }} »</router-link>
        </div>
        <p class="muted">The API reference is read from this server; the Python reference from client {{ PYTHON.version }}.</p>
      </footer>
    </main>
  </div>
</template>

<style scoped>
.docs {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 290px minmax(0, 1fr);
  background: var(--surface);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-md);
  overflow: hidden;
  position: relative;
}

/* ---------- Sidebar ---------- */

.docs-nav {
  background: var(--surface-2);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-bottom: 24px;
}
.docs-brand {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 18px 18px 14px;
  background: var(--surface-2);
  color: var(--text);
  text-decoration: none;
}
/* Keep the brand consistent with the sidebar instead of the content's link colour. */
.docs .docs-brand, .docs .docs-brand:hover { color: var(--text); }
.docs-brand-name { font-size: 17px; font-weight: 650; letter-spacing: -0.01em; }
.docs-brand-version { font-size: 12px; opacity: 0.85; }
.docs-search { padding: 12px 14px; border-bottom: 1px solid var(--border); }
.docs-search input { width: 100%; }
.docs-caption {
  margin: 18px 18px 6px;
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent-soft);
}
.docs-tree ul { list-style: none; margin: 0; padding: 0; }
.docs-tree li > a {
  display: block;
  padding: 6px 18px;
  color: var(--text-muted);
  text-decoration: none;
  border-left: 3px solid transparent;
}
.docs-tree li > a:hover { color: var(--text); background: var(--surface-3); }
.docs-tree li.current > a {
  color: var(--text);
  font-weight: 600;
  background: var(--accent-a08);
  border-left-color: var(--accent);
}
.docs-toc { background: var(--surface); border-bottom: 1px solid var(--border-soft); }
.docs-toc li > a { padding: 4px 18px 4px 32px; font-size: 13px; }
.docs-toc li.toc-level-3 > a { padding-left: 46px; font-size: 12.5px; }
.docs-pending, .docs-empty { padding: 6px 18px; color: var(--text-muted); font-size: 13px; }
.docs-hits { display: flex; flex-direction: column; padding: 6px 0; }
.docs-hit {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  padding: 7px 18px;
  text-align: left;
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
  font: inherit;
}
.docs-hit:hover, .docs-hit:focus-visible { background: var(--surface-3); }
.docs-hit-title { color: var(--accent-soft); font-weight: 550; overflow-wrap: anywhere; }
.docs-hit-detail { color: var(--text-muted); font-size: 12px; overflow-wrap: anywhere; }

/* ---------- Content ---------- */

.docs-main { overflow-y: auto; overscroll-behavior: contain; min-width: 0; }
.docs-topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 32px;
  border-bottom: 1px solid var(--border-soft);
}
.docs-nav-toggle { display: none; }
.docs-breadcrumbs { display: flex; flex-wrap: wrap; gap: 8px; color: var(--text-muted); font-size: 13px; }
.docs-breadcrumbs a { color: var(--accent-soft); text-decoration: none; }
.docs-crumb-current { color: var(--text); }
.docs-article { max-width: 920px; padding: 8px 32px 24px; }
.docs-article h1 { font-size: 28px; margin: 18px 0 16px; }
.docs-footer { max-width: 920px; padding: 0 32px 32px; }
.docs-pager {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}
.docs-pager a { text-decoration: none; }
.docs-footer p { font-size: 12px; margin: 14px 0 0; }

.py-member, .schema { padding: 6px 0 10px; border-bottom: 1px solid var(--border-soft); }
.py-member h2, .py-member h3, .schema h3 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.py-kind {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--accent-soft);
  border: 1px solid var(--accent-a45);
  border-radius: var(--r-pill);
  padding: 1px 8px;
}
.py-model-member { margin-top: 8px; }
.enum-value { margin-right: 6px; }
.docs-scrim { display: none; }

/* Rendered markdown and the endpoint component share these. */
.docs :deep(.doc-prose) { line-height: 1.65; }
.docs :deep(.doc-prose p), .docs :deep(.doc-prose li) { max-width: 78ch; }
.docs :deep(h2) { font-size: 21px; margin: 30px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border-soft); }
.docs :deep(h3) { font-size: 17px; margin: 22px 0 8px; }
.docs :deep(h4) { font-size: 14px; margin: 16px 0 6px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.docs :deep(a) { color: var(--accent-soft); }
.docs :deep(a:hover) { color: var(--accent); }
.docs :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  padding: 0 4px;
  overflow-wrap: anywhere;
}
.docs :deep(.code-block) {
  position: relative;
  margin: 10px 0 14px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent-2);
  border-radius: var(--r-sm);
}
.docs :deep(.code-block pre) { margin: 0; padding: 12px 14px; overflow-x: auto; }
.docs :deep(.code-block code) { background: none; border: 0; padding: 0; font-size: 13px; line-height: 1.55; overflow-wrap: normal; white-space: pre; }
.docs :deep(.code-lang) {
  position: absolute;
  top: 4px;
  right: 8px;
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
}
.docs :deep(.tok-comment) { color: #7d7d88; font-style: italic; }
.docs :deep(.tok-string) { color: #9fd18b; }
.docs :deep(.tok-key) { color: var(--accent-soft); }
.docs :deep(.tok-number) { color: #f0b86e; }
.docs :deep(.tok-keyword) { color: #ff9a5c; font-weight: 600; }
.docs :deep(.tok-def) { color: #ffd27a; }
.docs :deep(.table-wrap) { overflow-x: auto; margin: 10px 0 16px; }
.docs :deep(table) { border-collapse: collapse; width: 100%; font-size: 13px; }
.docs :deep(th), .docs :deep(td) { border: 1px solid var(--border); padding: 7px 10px; text-align: left; vertical-align: top; }
.docs :deep(th) { background: var(--surface-2); font-weight: 600; }
.docs :deep(tbody tr:nth-child(even)) { background: rgba(255, 255, 255, 0.015); }
.docs :deep(.admonition) {
  margin: 12px 0;
  padding: 10px 14px;
  background: var(--accent-a08);
  border-left: 3px solid var(--accent);
  border-radius: var(--r-sm);
}
.docs :deep(.anchor) { color: var(--text-muted); text-decoration: none; opacity: 0; font-size: 0.8em; }
.docs :deep(h2:hover .anchor), .docs :deep(h3:hover .anchor) { opacity: 1; }
.docs :deep(.required) { color: var(--danger); margin-left: 2px; }
.docs :deep(.type-code) { white-space: normal; }

/* Below MOBILE the contents slide in over the page, as on Read the Docs. */
@media (max-width: 899px) {
  .docs { grid-template-columns: minmax(0, 1fr); }
  .docs-nav {
    position: absolute;
    inset: 0 auto 0 0;
    width: min(300px, 85%);
    z-index: 3;
    transform: translateX(-102%);
    transition: transform var(--dur) var(--ease);
  }
  .nav-open .docs-nav { transform: none; }
  .nav-open .docs-scrim { display: block; position: absolute; inset: 0; z-index: 2; background: rgba(0, 0, 0, 0.5); }
  .docs-nav-toggle { display: inline-flex; }
  .docs-topbar { padding: 10px 16px; }
  .docs-article { padding: 4px 16px 20px; }
  .docs-footer { padding: 0 16px 24px; }
  .docs-article h1 { font-size: 23px; }
}
</style>
