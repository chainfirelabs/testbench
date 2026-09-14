<script setup lang="ts">
/**
 * One field's full value, laid out to be read.
 *
 * JSON arrives as an object, not as text, so it is rendered as labelled fields:
 * `{"throughput_mbps": 941, "errors": []}` reads as two rows rather than as a
 * line of punctuation. Nesting indents. A raw view stays available through
 * `showRaw`, because a friendly rendering is a reading aid and the stored value
 * is still the thing of record.
 *
 * Split out of DetailModal so the detail pages, which have room to show these
 * fields in place, render them the same way the dialog does.
 */
import { computed } from 'vue'
import { isEmptyDetail } from '../detail'

const props = withDefaults(
  defineProps<{
    value: any
    showRaw?: boolean
  }>(),
  { showRaw: false },
)

const isStructured = computed(() => props.value != null && typeof props.value === 'object')
const isEmpty = computed(() => isEmptyDetail(props.value))

/** The value as text, for the raw view and for plain string fields. */
const asText = computed(() =>
  isStructured.value ? JSON.stringify(props.value, null, 2) : String(props.value ?? ''),
)

interface Row {
  depth: number
  key: string
  /** Present on leaves only; a branch is a heading for the rows under it. */
  text?: string
  /** Shown next to a branch: "3 items", "empty". */
  note?: string
  /** Set for an {old, new} pair, rendered as "before → after". */
  change?: { from: string; to: string }
}

function describe(v: any): string {
  if (Array.isArray(v)) return v.length ? `${v.length} item${v.length === 1 ? '' : 's'}` : 'empty'
  const n = Object.keys(v).length
  return n ? `${n} field${n === 1 ? '' : 's'}` : 'empty'
}

/**
 * True for `{"old": ..., "new": ...}` — the shape `field_diff` produces, and
 * what almost every audit entry is made of.
 *
 * Rendered as one row rather than a branch with two children: "available →
 * checked_out" is the whole point of the entry, and burying it two rows deep
 * under a "2 fields" heading is the raw JSON problem in a nicer font.
 */
function isChangePair(v: any): boolean {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) return false
  const keys = Object.keys(v)
  if (keys.length !== 2 || !keys.includes('old') || !keys.includes('new')) return false
  // Only when both sides are scalars: a diff of two objects still deserves the
  // nesting, since one line cannot show what changed inside them.
  return !isObject(v.old) && !isObject(v.new)
}

function isObject(v: any): boolean {
  return v !== null && typeof v === 'object'
}

function leafText(v: any): string {
  if (v === null) return '—'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'string') return v === '' ? '—' : v
  return String(v)
}

/**
 * Depth-first walk producing one row per key, flat.
 *
 * Flat rather than a recursive component: the indent is the only thing nesting
 * needs to convey here, and a flat list keeps this a single scrollable column
 * on a phone instead of a tree that indents itself off the screen.
 */
function flatten(v: any, depth = 0, out: Row[] = []): Row[] {
  const entries: [string, any][] = Array.isArray(v)
    ? v.map((item, i) => [`${i + 1}.`, item] as [string, any])
    : Object.entries(v)
  for (const [key, val] of entries) {
    if (isChangePair(val)) {
      out.push({ depth, key, change: { from: leafText(val.old), to: leafText(val.new) } })
    } else if (isObject(val)) {
      out.push({ depth, key, note: describe(val) })
      flatten(val, depth + 1, out)
    } else {
      out.push({ depth, key, text: leafText(val) })
    }
  }
  return out
}

const rows = computed<Row[]>(() => (isStructured.value ? flatten(props.value) : []))

/** Keys arrive as column names; `throughput_mbps` reads better as a label. */
function labelFor(key: string): string {
  if (/^\d+\.$/.test(key)) return key
  return key.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

defineExpose({ isStructured, hasRows: computed(() => rows.value.length > 0), asText })
</script>

<template>
  <p v-if="isEmpty" class="muted">Nothing recorded.</p>
  <dl v-else-if="isStructured && !showRaw" class="detail-list">
    <template v-for="(r, i) in rows" :key="`${i}-${r.key}`">
      <dt :style="{ paddingLeft: `${r.depth * 16}px` }">{{ labelFor(r.key) }}</dt>
      <dd v-if="r.change">
        <span class="detail-from">{{ r.change.from }}</span>
        <span class="detail-arrow" aria-hidden="true">→</span>
        <span class="detail-to">{{ r.change.to }}</span>
      </dd>
      <dd v-else-if="r.text !== undefined">{{ r.text }}</dd>
      <dd v-else class="muted detail-branch">{{ r.note }}</dd>
    </template>
  </dl>
  <pre v-else-if="isStructured" class="code-block detail-raw">{{ asText }}</pre>
  <!-- Prose, not source: notes keep their line breaks but are set in the body
       face and wrapped, not in a monospace scroller. -->
  <p v-else class="detail-text">{{ asText }}</p>
</template>

<style scoped>
.detail-list {
  display: grid;
  /* The label column sizes to the longest label but never crowds the value. */
  grid-template-columns: minmax(120px, max-content) 1fr;
  gap: 6px 16px;
  margin: 0;
  align-items: baseline;
}

.detail-list dt {
  font-size: 12.5px;
  color: var(--text-muted);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.detail-list dd {
  margin: 0;
  font-size: 13px;
  /* Values are the reason this exists: keep newlines, wrap the rest. */
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* A field change reads left-to-right: what it was, then what it became. The
   old value is de-emphasised so the new one is what the eye lands on. */
.detail-from {
  color: var(--text-muted);
  text-decoration: line-through;
  text-decoration-color: var(--border);
}

.detail-arrow {
  margin: 0 7px;
  color: var(--text-muted);
}

.detail-to {
  font-weight: 600;
}

.detail-branch {
  font-size: 12px;
}

.detail-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.detail-raw {
  margin: 0;
  max-height: 60vh;
  overflow: auto;
}

/* Below the stacking breakpoint the two columns become label-over-value:
   a 120px label column leaves nothing usable for the value on a phone. */
@media (max-width: 639px) {
  .detail-list {
    grid-template-columns: 1fr;
    gap: 2px;
  }

  .detail-list dd {
    margin-bottom: 10px;
  }
}
</style>
