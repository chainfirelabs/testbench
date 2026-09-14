<script setup lang="ts">
/**
 * The mobile presentation of a DataTable's rows.
 *
 * Purely presentational: it is handed the rows for the current page, the fields
 * to show and the actions each row offers, and renders them as stacked cards.
 * Filtering, sorting and pagination are not its job — DataTable keeps AG Grid
 * mounted off-screen and drives all of that through the grid's row model, so a
 * phone and a desktop agree on what "page 2 of this filtered view" means, and a
 * view saved on a phone is a real grid state that the desktop can read back.
 *
 * Which fields appear is the visible column state, in column order. The first
 * is the card's title; the rest are label/value pairs. That is deliberate — it
 * means the existing column picker already configures the card, and a saved
 * view already carries the choice.
 */
import { computed, defineComponent, h, onMounted, ref, watch, type PropType } from 'vue'
import type { RowBadge } from './DataTable.vue'

/** One button on a card. Resolved by DataTable so the grid and the cards agree. */
export interface CardAction {
  key: string
  label: string
  title: string
  /** Inline SVG markup, from DataTable's own icon set — never user content. */
  icon?: string
  cssClass: string
  disabled: boolean
  run: () => void
}

export interface CardField {
  field: string
  headerName: string
  col: any
}

const props = defineProps<{
  rows: any[]
  fields: CardField[]
  actionsFor: (row: any) => CardAction[]
  badgeFor?: ((row: any) => RowBadge | null) | null
  /** Extra CSS class for the whole card, matching the grid's row class. */
  classFor?: ((row: any) => string | null) | null
  rowKey: (row: any) => string
  /** Shown when the current filter or page has nothing in it. */
  emptyText?: string
}>()

/*
 * One deep watcher for the whole page of rows, rather than one per cell.
 *
 * Cell contents are painted imperatively (see CellValue), so they do not
 * re-render on their own when a row is mutated in place — which is exactly what
 * saving a row or finishing a scan does (`Object.assign(row, updated)`).
 * Bumping a revision here and passing it down gives every cell a single cheap
 * dependency to repaint on.
 */
const rev = ref(0)
watch(() => props.rows, () => rev.value++, { deep: true })

/*
 * Resolve each row's badge and actions once per render rather than calling the
 * supplied functions again for every place the template needs them. Both read
 * the parent view's reactive state — a scan in progress, the set of dirty rows
 * — so this recomputes when they do.
 */
const cards = computed(() =>
  props.rows.map((row) => ({
    row,
    key: props.rowKey(row),
    badge: props.badgeFor ? props.badgeFor(row) : null,
    cls: props.classFor ? props.classFor(row) : null,
    actions: props.actionsFor(row),
  })),
)

/**
 * Renders one cell the way the grid would.
 *
 * Column definitions carry `cellRenderer` (returns a detached DOM node — the
 * status dots, the links into detail pages) and `valueFormatter` (returns
 * text). Both are reused as-is so a card and a grid row never disagree about
 * how a value reads. The renderers in this app only ever touch `value` and
 * `data`, so that is what they are given.
 */
const CellValue = defineComponent({
  name: 'CellValue',
  props: {
    col: { type: Object as PropType<any>, required: true },
    row: { type: Object as PropType<any>, required: true },
    rev: { type: Number, default: 0 },
  },
  setup(cellProps) {
    const host = ref<HTMLElement | null>(null)

    function paint() {
      const el = host.value
      if (!el) return
      el.textContent = ''
      const col = cellProps.col
      const value = col.field ? cellProps.row[col.field] : undefined

      if (typeof col.cellRenderer === 'function') {
        const node = col.cellRenderer({ value, data: cellProps.row })
        if (node instanceof Node) {
          el.appendChild(node)
        } else if (node != null) {
          el.textContent = String(node)
        }
        return
      }

      if (typeof col.valueFormatter === 'function') {
        el.textContent = col.valueFormatter({ value, data: cellProps.row }) ?? ''
        return
      }

      el.textContent = value == null || value === '' ? '—' : String(value)
    }

    onMounted(paint)
    watch(() => [cellProps.col, cellProps.row, cellProps.rev], paint)

    return () => h('span', { class: 'card-cell', ref: host })
  },
})
</script>

<template>
  <div class="card-list">
    <p v-if="!cards.length" class="card-empty">{{ emptyText || 'Nothing to show.' }}</p>

    <article v-for="c in cards" :key="c.key" class="card" :class="c.cls">
      <div class="card-head">
        <div v-if="fields.length" class="card-title">
          <CellValue :col="fields[0].col" :row="c.row" :rev="rev" />
        </div>
        <span v-if="c.badge" class="row-badge" :class="c.badge.cssClass">
          <span v-if="c.badge.spinner" class="spinner"></span>
          {{ c.badge.text }}
        </span>
      </div>

      <dl v-if="fields.length > 1" class="card-fields">
        <template v-for="f in fields.slice(1)" :key="f.field">
          <dt>{{ f.headerName }}</dt>
          <dd><CellValue :col="f.col" :row="c.row" :rev="rev" /></dd>
        </template>
      </dl>

      <div v-if="c.actions.length" class="card-actions">
        <button
          v-for="a in c.actions"
          :key="a.key"
          type="button"
          class="card-action"
          :class="a.cssClass"
          :title="a.title"
          :disabled="a.disabled"
          @click="a.run()"
        >
          <!-- Icon markup comes from DataTable's own constant set, not from
               data, so there is nothing user-supplied to escape here. -->
          <span v-if="a.icon" class="card-action-icon" v-html="a.icon"></span>
          <span class="card-action-copy">
            <span>{{ a.label }}</span>
            <small v-if="a.disabled" class="card-action-reason">{{ a.title }}</small>
          </span>
        </button>
      </div>
    </article>
  </div>
</template>

<style scoped>
.card-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior-y: contain;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 4px;
}

.card-empty {
  margin: 0;
  padding: 28px 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
}

.card {
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  padding: 12px 13px;
}

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.card-title {
  font-size: 15.5px;
  font-weight: 600;
  color: var(--text);
  word-break: break-word;
  min-width: 0;
}

/* Cell renderers build links styled for the grid; in a card the title is the
   link, so it should carry the title's weight. */
.card-title :deep(.grid-link) {
  font-weight: 600;
}

.card-fields {
  display: grid;
  grid-template-columns: minmax(88px, 34%) 1fr;
  gap: 5px 12px;
  margin: 11px 0 0;
  font-size: 13.5px;
}

.card-fields dt {
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding-top: 2px;
}

.card-fields dd {
  margin: 0;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 13px;
  padding-top: 11px;
  border-top: 1px solid var(--border-soft);
}

/*
 * Labelled, finger-sized buttons — the whole point of the card list. In the
 * grid these are 28px icon-only squares that explain themselves through a
 * `title` tooltip, which touch has no way to show.
 */
.card-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  flex: 1 1 auto;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-3);
  color: var(--text);
  font-family: inherit;
  font-size: 13.5px;
  font-weight: 500;
  cursor: pointer;
}

.card-action-icon {
  display: inline-flex;
  flex-shrink: 0;
}

.card-action-icon :deep(svg) {
  width: 16px;
  height: 16px;
}

.card-action-copy {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.2;
}

.card-action-reason {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 400;
  text-align: left;
}

.card-action:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.card-action.danger {
  color: #fca5a5;
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.12);
}

.card-action.save {
  color: var(--accent);
  border-color: var(--accent-a24);
  background: var(--accent-a08);
}

.card-action.scanning {
  color: var(--yellow);
}
</style>
