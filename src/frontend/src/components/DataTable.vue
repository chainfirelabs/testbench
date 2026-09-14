<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { AgGridVue } from 'ag-grid-vue3'
import {
  AllCommunityModule,
  colorSchemeDark,
  enableDevValidations,
  themeQuartz,
  type GridApi,
  type SelectionColumnDef,
} from 'ag-grid-community'
import { useIsCardList, useIsMobile } from '../breakpoints'
import CardList, { type CardAction, type CardField } from './CardList.vue'

// Keep all Community features available to dynamically generated column
// definitions. The client-side row model itself is built into AG Grid v36.
const modules = [AllCommunityModule]

if (import.meta.env.DEV) enableDevValidations()

/*
 * Dark Quartz, repainted in the app's palette.
 *
 * The palette has to go through `withParams` rather than the `--ag-inherited-*`
 * variables. Those are wired by a rule the theme generates itself:
 *
 *     :has(> :where(.ag-theme-N)):not(:where(.ag-theme-N)) {
 *       --ag-inherited-foo: var(--ag-foo);
 *     }
 *
 * which targets the *direct parent* of the element carrying the theme class —
 * and that class lands on `.ag-root-wrapper`, whose parent is the bare div
 * AgGridVue renders, not our `.ag-wrapper`. So the rule reset every inherited
 * variable to `var(--ag-foo)`, undefined at that level, which made them
 * guaranteed-invalid and fell back to stock Quartz — including its #2196f3
 * accent, the source of the blue row hover.
 *
 * Values stay as `var(--token)` references so style.css remains the single
 * source of truth for the palette.
 */
const theme = themeQuartz.withPart(colorSchemeDark).withParams({
  accentColor: 'var(--accent)',
  backgroundColor: 'var(--surface)',
  // Stock Quartz pulls IBM Plex Sans from Google Fonts; inherit the app's font
  // instead so grid text matches the rest of the UI and nothing is fetched.
  fontFamily: 'inherit',
  headerFontFamily: 'inherit',
  foregroundColor: 'var(--text)',
  textColor: 'var(--text)',
  borderColor: 'var(--border-soft)',
  chromeBackgroundColor: 'var(--surface-2)',

  // Rows
  rowHeight: '40px',
  oddRowBackgroundColor: 'transparent',
  rowHoverColor: 'var(--row-hover)',
  selectedRowBackgroundColor: 'var(--accent-a16)',
  rangeSelectionBackgroundColor: 'var(--accent-a24)',
  rowBorder: 'solid 1px var(--border-soft)',
  cellTextColor: 'var(--text)',

  // Header
  headerHeight: '42px',
  headerBackgroundColor: 'var(--surface-2)',
  headerTextColor: 'var(--text-muted)',
  headerFontWeight: 600,
  headerVerticalPaddingScale: 1.1,
  headerColumnResizeHandleColor: 'var(--accent)',

  // Inputs, menus, tooltips
  inputBackgroundColor: 'var(--surface-2)',
  inputTextColor: 'var(--text)',
  inputBorder: 'solid 1px var(--border)',
  inputFocusBorder: 'solid 1px var(--accent)',
  inputFocusShadow: 'var(--ring)',
  menuBackgroundColor: 'var(--surface-2)',
  menuTextColor: 'var(--text)',
  menuBorder: 'solid 1px var(--border)',
  tooltipBackgroundColor: 'var(--surface-3)',
  tooltipTextColor: 'var(--text)',
  focusShadow: 'var(--ring)',

  // Checkboxes
  checkboxCheckedBackgroundColor: 'var(--accent)',
  checkboxCheckedBorderColor: 'var(--accent)',
  checkboxUncheckedBorderColor: '#52525e',
  checkboxIndeterminateBackgroundColor: 'var(--accent)',
  checkboxIndeterminateBorderColor: 'var(--accent)',

  // Shape — the outer border and radius come from .ag-wrapper
  borderRadius: 6,
  wrapperBorder: false,
  wrapperBorderRadius: 0,
})

/** Stable id for the generated actions column, so it can be refreshed alone. */
const ACTIONS_COL_ID = 'tb-actions'

// Hoisted out of the template: an inline literal is a new array on every
// render, which the grid wrapper dutifully pushes back into the grid.
const PAGE_SIZES = [25, 50, 100, 250, 500]

export interface RowAction {
  label: string
  title?: string
  cssClass?: string
  /** Inline SVG markup; when set the action renders as an icon button. */
  icon?: string
  disabled?: boolean
  onClick: (row: any) => void
}

/** Small status indicator shown in the actions cell (e.g. scan in progress). */
export interface RowBadge {
  text: string
  cssClass?: string
  spinner?: boolean
}

const props = withDefaults(
  defineProps<{
    columns: any[]
    rows: any[]
    editable?: boolean
    /** Rows that have unsaved (dirty) changes, keyed by row id. */
    dirtyIds?: Set<string>
    /** Decide whether a row shows the Save button (dirty or new). */
    isRowDirty?: (row: any) => boolean
    /** Extra per-row actions offered in the actions column. */
    extraRowActions?: (row: any) => RowAction[]
    /** Status badge shown next to the actions buttons (e.g. scan progress). */
    rowBadge?: (row: any) => RowBadge | null
    /**
     * Extra CSS class for a whole row, in the grid and on its phone card —
     * for state the row carries rather than any one of its cells (an overdue
     * device is the whole device, not just its due date).
     */
    rowClass?: (row: any) => string | null
    /**
     * Veto an automatic save. Returning false lets the edit stand in the cell
     * and marks the row dirty, but withholds `save-row`, so the page can ask
     * for something first — checking a device out needs a purpose and a return
     * date, which a dropdown cannot collect — and then save the row itself.
     */
    saveGuard?: (row: any, field: string) => boolean
    /** Bump this value to force the actions cells to re-render. */
    refreshKey?: number
    defaultPageSize?: number
    /** Enable multi-row selection (checkboxes) for bulk actions. */
    selectable?: boolean
    /** Show the per-row delete button (defaults to `editable`). */
    deletable?: boolean
    /**
     * Show the per-row edit button. It only emits `edit-row`; the page decides
     * what to open, so the dialog stays the page's own (and matches its
     * detail view).
     */
    rowEditable?: boolean
  }>(),
  {
    editable: false,
    dirtyIds: () => new Set<string>(),
    isRowDirty: null,
    extraRowActions: null,
    rowBadge: null,
    rowClass: null,
    saveGuard: null,
    refreshKey: 0,
    defaultPageSize: 100,
    selectable: false,
    deletable: undefined,
    rowEditable: false,
  },
)

const canDelete = computed(() => props.deletable ?? props.editable)

const emit = defineEmits<{
  (e: 'cell-edit', row: any, field: string, value: any): void
  (e: 'save-row', row: any): void
  (e: 'delete-row', row: any): void
  (e: 'edit-row', row: any): void
  (e: 'grid-ready', api: any): void
  (e: 'page-size-change', size: number): void
  (e: 'selection-change', rows: any[]): void
}>()

const quickFilter = ref('')
const gridApi = ref<GridApi | null>(null)
const selectedCount = ref(0)

function rowIsDirty(row: any): boolean {
  if (props.isRowDirty) return props.isRowDirty(row)
  return !row.id || props.dirtyIds.has(row.id)
}

/** AG Grid asks per row; the page decides. */
function getRowClass(p: any): string | undefined {
  return (props.rowClass && p.data ? props.rowClass(p.data) : null) || undefined
}

/*
 * Stable row identity.
 *
 * Without `getRowId`, AG Grid throws away every row node whenever the `rows`
 * array is replaced (a reload, an import, a finished scan) and the current
 * selection goes with it. Keying rows by their id keeps nodes — and their
 * selected state — alive across refreshes.
 */
let newRowSeq = 0

function getRowId(p: any): string {
  const d = p.data
  // Rows added in the grid have no server id yet, so they get a client-side
  // key that stays with them even after a save assigns the real id (swapping
  // the key would make the grid drop and re-add the row underneath the user).
  // Non-enumerable, so it never leaks into the JSON sent back to the API.
  if (d.__rowKey) return d.__rowKey
  if (d.id != null) return String(d.id)
  Object.defineProperty(d, '__rowKey', {
    value: `new-${++newRowSeq}`,
    enumerable: false,
    writable: true,
    configurable: true,
  })
  return d.__rowKey
}

// Feather-style stroke icons (currentColor so they follow the theme)
const ICONS = {
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
  trash:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>',
  check:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
}

function iconButton(
  svg: string,
  title: string,
  cssClass: string,
  onClick: () => void,
): HTMLButtonElement {
  const b = document.createElement('button')
  b.type = 'button'
  b.className = `icon-btn${cssClass ? ' ' + cssClass : ''}`
  b.title = title
  b.innerHTML = svg
  b.onclick = (e) => {
    e.stopPropagation()
    onClick()
  }
  return b
}

/**
 * The actions a row offers, as data.
 *
 * Resolved once here so the grid's cell renderer and the card list cannot drift
 * apart about what a row can do. The grid takes `includeSave`; the card list
 * does not, because saving is inline editing's button and inline editing does
 * not exist on mobile — with no cell edits there are never dirty rows.
 */
function resolveRowActions(row: any, includeSave: boolean): CardAction[] {
  const actions: CardAction[] = []
  if (!row) return actions

  // Edit first, so it sits in the same place on every row — the buttons after
  // it come and go with the row's state.
  if (props.rowEditable && row.id) {
    actions.push({
      key: 'edit',
      label: 'Edit',
      title: 'Edit this row',
      icon: ICONS.edit,
      cssClass: 'edit',
      disabled: false,
      run: () => emit('edit-row', row),
    })
  }

  if (includeSave && props.editable && rowIsDirty(row)) {
    actions.push({
      key: 'save',
      label: 'Save',
      title: 'Save changes to this row',
      icon: ICONS.save,
      cssClass: 'save',
      disabled: false,
      run: () => emit('save-row', row),
    })
  }

  if (props.extraRowActions) {
    for (const a of props.extraRowActions(row)) {
      actions.push({
        key: a.label,
        label: a.label,
        title: a.title || a.label,
        icon: a.icon,
        cssClass: a.cssClass || '',
        disabled: !!a.disabled,
        run: () => a.onClick(row),
      })
    }
  }

  if (canDelete.value && row.id) {
    actions.push({
      key: 'delete',
      label: 'Delete',
      title: 'Delete this row',
      icon: ICONS.trash,
      cssClass: 'danger',
      disabled: false,
      run: () => emit('delete-row', row),
    })
  }

  return actions
}

/** Card-list flavour: same actions, without the inline-editing Save. */
function cardActionsFor(row: any): CardAction[] {
  return resolveRowActions(row, false)
}

/**
 * A resolved action as a grid-cell button.
 *
 * Icon actions stay 28px icon buttons here; a text-only action (one supplied
 * without an `icon`) keeps its .btn-mini pill, as before.
 */
function actionButton(a: CardAction): HTMLElement {
  let button: HTMLButtonElement
  if (a.icon) {
    button = iconButton(a.icon, a.title, a.cssClass, a.run)
  } else {
    button = document.createElement('button')
    button.type = 'button'
    button.className = `btn btn-mini${a.cssClass ? ' ' + a.cssClass : ''}`
    button.textContent = a.label
    button.title = a.title
    button.onclick = (e) => {
      e.stopPropagation()
      a.run()
    }
  }
  if (!a.disabled) return button
  button.disabled = true
  // Disabled buttons do not consistently emit hover events. The wrapper owns
  // the tooltip so the reason remains available while the icon is greyed out.
  // pointer-events must also be disabled on the nested button: otherwise it
  // remains the hit-test target in Chromium even though it emits no tooltip.
  button.title = ''
  button.style.pointerEvents = 'none'
  const hint = document.createElement('span')
  hint.className = 'disabled-action-hint'
  hint.title = a.title
  hint.setAttribute('aria-label', a.title)
  hint.setAttribute('role', 'note')
  hint.tabIndex = 0
  hint.appendChild(button)
  return hint
}

function makeActionsColumn(): any {
  return {
    colId: ACTIONS_COL_ID,
    headerName: 'Actions',
    width: 215,
    minWidth: 170,
    maxWidth: 300,
    sortable: false,
    filter: false,
    editable: false,
    suppressMovable: true,
    cellRenderer: (p: any) => {
      const el = document.createElement('div')
      el.className = 'row-actions'
      const row = p.data
      if (!row) return el

      const actions = resolveRowActions(row, true)

      /*
       * The badge sits after Edit and before everything else, which is where it
       * has always been. It is not an action, so it is not in the resolved list
       * — the card list renders it in the card's header instead.
       */
      const editAction = actions.filter((a) => a.key === 'edit')
      const rest = actions.filter((a) => a.key !== 'edit')

      /*
       * "Saved", for a row that was just saved, ahead of Edit: the tick reads
       * as the outcome of the edit that came before it, so it leads the row's
       * buttons rather than sitting among them. It has the same 28px footprint
       * as an icon button, so while it is up the buttons after it sit one slot
       * to the right, and drop back when it goes.
       */
      if (props.editable && savedFlashes.has(getRowId(p))) {
        const s = document.createElement('span')
        s.className = 'icon-btn row-saved'
        s.title = 'Saved'
        s.innerHTML = ICONS.check
        el.appendChild(s)
      }

      for (const a of editAction) el.appendChild(actionButton(a))

      if (props.rowBadge) {
        const badge = props.rowBadge(row)
        if (badge) {
          const s = document.createElement('span')
          s.className = `row-badge${badge.cssClass ? ' ' + badge.cssClass : ''}`
          if (badge.spinner) {
            const sp = document.createElement('span')
            sp.className = 'spinner'
            s.appendChild(sp)
          }
          s.appendChild(document.createTextNode(badge.text))
          el.appendChild(s)
        }
      }

      for (const a of rest) el.appendChild(actionButton(a))
      return el
    },
  }
}

const hasActions = computed(
  () => props.editable || canDelete.value || props.rowEditable || !!props.extraRowActions,
)

const columnDefs = computed(() => {
  const cols: any[] = [...props.columns]
  if (hasActions.value) cols.push(makeActionsColumn())
  return cols
})

/** Community-edition equivalent of AG Grid's Enterprise Set Filter. */
class ValueChecklistFilter {
  private params: any
  private gui!: HTMLDivElement
  private list!: HTMLDivElement
  private search!: HTMLInputElement
  // Store excluded raw values so newly loaded values remain visible by default.
  private excluded = new Map<string, any>()
  private values = new Map<string, { value: any; label: string }>()

  init(params: any) {
    this.params = params
    this.gui = document.createElement('div')
    this.gui.className = 'value-checklist-filter'
    this.search = document.createElement('input')
    this.search.type = 'search'
    this.search.placeholder = 'Find value…'
    this.search.setAttribute('aria-label', 'Find filter value')
    this.search.addEventListener('input', () => this.renderList())
    this.gui.appendChild(this.search)

    const actions = document.createElement('div')
    actions.className = 'value-checklist-actions'
    actions.append(this.actionButton('Select all', () => this.setAll(true)))
    actions.append(this.actionButton('Clear', () => this.setAll(false)))
    this.gui.appendChild(actions)
    this.list = document.createElement('div')
    this.list.className = 'value-checklist-options'
    this.gui.appendChild(this.list)
    this.refreshValues()
  }

  getGui() { return this.gui }
  afterGuiAttached() { this.refreshValues(); this.search.focus() }
  isFilterActive() { return this.excluded.size > 0 }
  doesFilterPass(p: any) { return !this.excluded.has(this.key(this.params.getValue(p.node))) }
  getModel() {
    return this.isFilterActive()
      ? { filterType: 'valueChecklist', excluded: [...this.excluded.values()] }
      : null
  }
  setModel(model: any) {
    this.excluded.clear()
    for (const value of model?.excluded || []) this.excluded.set(this.key(value), value)
    if (this.list) this.renderList()
  }

  private key(value: any): string {
    if (value == null || value === '') return '__blank__'
    if (typeof value === 'object') return `object:${JSON.stringify(value)}`
    return `${typeof value}:${String(value)}`
  }

  private label(value: any): string {
    if (value == null || value === '') return '(Blanks)'
    const colDef = this.params.column?.getColDef?.()
    if (typeof colDef?.valueFormatter === 'function') {
      const formatted = colDef.valueFormatter({
        value, data: null, node: null, column: this.params.column,
        colDef, api: this.params.api, context: this.params.context,
      })
      if (formatted != null && formatted !== '') return String(formatted)
    }
    return typeof value === 'object' ? JSON.stringify(value) : String(value)
  }

  private refreshValues() {
    const next = new Map<string, { value: any; label: string }>()
    this.params.api.forEachLeafNode((node: any) => {
      const value = this.params.getValue(node)
      next.set(this.key(value), { value, label: this.label(value) })
    })
    this.values = new Map([...next].sort((a, b) => a[1].label.localeCompare(b[1].label)))
    this.renderList()
  }

  private actionButton(label: string, run: () => void): HTMLButtonElement {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = label
    button.addEventListener('click', run)
    return button
  }

  private setAll(selected: boolean) {
    this.excluded.clear()
    if (!selected) for (const [key, item] of this.values) this.excluded.set(key, item.value)
    this.renderList()
    this.params.filterChangedCallback()
  }

  private renderList() {
    if (!this.list) return
    this.list.replaceChildren()
    const query = this.search?.value.trim().toLocaleLowerCase() || ''
    for (const [key, item] of this.values) {
      if (query && !item.label.toLocaleLowerCase().includes(query)) continue
      const row = document.createElement('label')
      row.className = 'value-checklist-option'
      const checkbox = document.createElement('input')
      checkbox.type = 'checkbox'
      checkbox.checked = !this.excluded.has(key)
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) this.excluded.delete(key)
        else this.excluded.set(key, item.value)
        this.params.filterChangedCallback()
      })
      const text = document.createElement('span')
      text.textContent = item.label
      row.append(checkbox, text)
      this.list.appendChild(row)
    }
  }
}

const defaultColDef = computed(() => ({
  flex: 1,
  minWidth: 90,
  filter: ValueChecklistFilter,
  sortable: true,
  // Never editable in card mode: the grid is off-screen there and a cell edit
  // has no way to be seen, let alone saved.
  editable: () => props.editable && !isCardList.value,
}))

/*
 * Selection is desktop-only.
 *
 * The checkbox column is a fixed 52px pinned to the left, which is a seventh of
 * a 375px screen spent on a control that only exists to feed bulk actions. On a
 * phone that space is worth more as data, so below MOBILE the column, the
 * selection pill and the bulk-action slot all go, and rows can only be acted on
 * one at a time. Multi-select and bulk Delete are desktop features.
 */
const isMobile = useIsMobile()
const selectionEnabled = computed(() => props.selectable && !isMobile.value)

/* ---------- card list (mobile) ----------
 *
 * Below CARDS the rows are rendered as cards instead of a grid — fourteen
 * columns at their 90px minimum need about 1,527px, and a phone has 375.
 *
 * The grid itself stays mounted, parked off-screen, and keeps doing the work it
 * is good at: quick filter, column filters, sort and pagination all run in its
 * row model, and the cards render whatever that model currently holds. That is
 * worth the odd-looking arrangement for two reasons. Reimplementing AG Grid's
 * filter model faithfully would be a lot of subtly wrong code; and `getState()`
 * has to keep returning a real grid state, because a view saved on a phone must
 * be one the desktop grid can read back.
 */
const isCardList = useIsCardList()
const cardRows = ref<any[]>([])
const cardPage = ref({ current: 0, total: 0, rowCount: 0 })

/** Pull the current page out of the grid's post-filter, post-sort row model. */
function syncCardRows() {
  const api = gridApi.value
  if (!api || !isCardList.value) return

  const pageSize = api.paginationGetPageSize?.() ?? props.defaultPageSize
  const current = api.paginationGetCurrentPage?.() ?? 0
  const rowCount = api.paginationGetRowCount?.() ?? 0

  const first = current * pageSize
  const last = Math.min(first + pageSize, rowCount)
  const out: any[] = []
  for (let i = first; i < last; i++) {
    const node = api.getDisplayedRowAtIndex?.(i)
    if (node?.data) out.push(node.data)
  }

  cardRows.value = out
  cardPage.value = {
    current,
    total: api.paginationGetTotalPages?.() ?? 0,
    rowCount,
  }
}

/**
 * The fields a card shows: visible columns, in column order.
 *
 * Read from the grid's column state rather than from `props.columns`, so the
 * column picker and any applied saved view decide what a card contains — which
 * is what lets a per-platform default view configure the mobile layout without
 * a second mechanism.
 */
const cardFields = ref<CardField[]>([])

/*
 * What a phone falls back to when the user has never saved a mobile default
 * view for this entity.
 *
 * Worth having rather than assuming everyone configures one: `require_write`
 * guards the saved-view write endpoints, so a readonly user cannot save any
 * view at all and this is the only mobile default they will ever get. Without
 * it they would meet all fourteen device fields stacked on every card.
 */
const CARD_FALLBACK_FIELDS = 4
const CARD_FALLBACK_PAGE_SIZE = 25

/*
 * True once something has deliberately chosen what this table shows — a saved
 * view being applied, or the user working the field picker. Until then the
 * fallback above is in charge; afterwards it never second-guesses the choice.
 */
const viewApplied = ref(false)

/*
 * What the fallback changed, so leaving card mode can put it back.
 *
 * The cap is applied to the real column state rather than kept as a private
 * fact about the card's rendering. It has to be: the field picker reads column
 * state to tick its boxes, so a presentation-only cap made the picker report
 * every field as shown while the card showed four. One source of truth for
 * "which fields", and the picker is honest by construction.
 *
 * The trade is that this writes to state the desktop grid shares, so both
 * values are recorded and restored on the way out — and the record is dropped
 * the moment anything deliberate happens, because from then on the choice is
 * the user's and must not be undone behind their back.
 */
let pageSizeBeforeCards: number | null = null
let columnsHiddenByCap: string[] | null = null

function applyCardFallback() {
  const api = gridApi.value
  if (!api || viewApplied.value) return

  if (pageSizeBeforeCards == null) {
    const current = api.paginationGetPageSize?.() ?? props.defaultPageSize
    if (current > CARD_FALLBACK_PAGE_SIZE) {
      pageSizeBeforeCards = current
      api.setGridOption('paginationPageSize', CARD_FALLBACK_PAGE_SIZE)
    }
  }

  if (columnsHiddenByCap == null) {
    const visible = (api.getColumnState?.() ?? []).filter(
      (c: any) => c.colId && c.colId !== ACTIONS_COL_ID && !c.hide,
    )
    const surplus = visible.slice(CARD_FALLBACK_FIELDS).map((c: any) => c.colId)
    if (surplus.length) {
      columnsHiddenByCap = surplus
      api.applyColumnState({ state: surplus.map((colId: string) => ({ colId, hide: true })) })
    }
  }
}

function restoreCardFallback() {
  const api = gridApi.value
  if (!api) return
  if (pageSizeBeforeCards != null) {
    api.setGridOption('paginationPageSize', pageSizeBeforeCards)
    pageSizeBeforeCards = null
  }
  if (columnsHiddenByCap?.length) {
    api.applyColumnState({
      state: columnsHiddenByCap.map((colId) => ({ colId, hide: false })),
    })
  }
  columnsHiddenByCap = null
}

/** Hand the current state to the user: keep it, and stop tracking it. */
function keepCurrentState() {
  viewApplied.value = true
  pageSizeBeforeCards = null
  columnsHiddenByCap = null
}

function syncCardFields() {
  const api = gridApi.value
  if (!api) return
  const byField = new Map<string, any>()
  for (const c of props.columns) if (c.field) byField.set(c.field, c)

  const fields: CardField[] = []
  for (const state of api.getColumnState?.() ?? []) {
    if (state.hide || !state.colId) continue
    // The actions and selection columns are chrome, not data; the card renders
    // actions itself and has no selection.
    if (state.colId === ACTIONS_COL_ID) continue
    const col = byField.get(state.colId)
    if (!col) continue
    fields.push({ field: state.colId, headerName: col.headerName || state.colId, col })
  }

  cardFields.value = fields
}

function syncCards() {
  syncCardFields()
  syncCardRows()
}

// Entering card mode after the grid is already up needs a first fill; leaving
// it drops the rows so nothing stale is held onto.
watch(isCardList, (cards) => {
  if (!cards) {
    cardRows.value = []
    restoreCardFallback()
    return
  }
  // The sort select has to show whatever the grid was already sorted by,
  // otherwise it reads "Unsorted" over a list that plainly is not.
  syncCardSort()
  applyCardFallback()
  syncCards()
})

/* ---------- card list sorting ----------
 * Cards have no column headers to click, so sorting moves to a select. It is
 * applied through the grid's column state, which means it is the same sort the
 * desktop would apply and it is captured by getState() unchanged.
 */
const sortableFields = computed(() =>
  props.columns.filter((c) => c.field && c.sortable !== false),
)

const cardSort = ref<{ colId: string; dir: 'asc' | 'desc' }>({ colId: '', dir: 'asc' })

function syncCardSort() {
  const api = gridApi.value
  if (!api) return
  const sorted = (api.getColumnState?.() ?? []).find((c: any) => c.sort)
  cardSort.value = sorted
    ? { colId: sorted.colId, dir: sorted.sort }
    : { colId: '', dir: 'asc' }
}

function applyCardSort(colId: string, dir: 'asc' | 'desc') {
  cardSort.value = { colId, dir }
  gridApi.value?.applyColumnState({
    state: colId ? [{ colId, sort: dir }] : [],
    defaultState: { sort: null },
  })
}

function toggleCardSortDir() {
  applyCardSort(cardSort.value.colId, cardSort.value.dir === 'asc' ? 'desc' : 'asc')
}

/*
 * Selection options.
 *
 * Built as a computed rather than inline in the template: an object literal in
 * the template is a new object on every render, and the grid wrapper pushes
 * every "changed" prop back into the grid, churning selection config for no
 * reason.
 */
const rowSelection = computed<any>(() =>
  selectionEnabled.value
    ? {
        mode: 'multiRow',
        checkboxes: true,
        headerCheckbox: true,
        // Row clicks stay free for cell editing; selection is checkbox-driven
        // (shift-click a checkbox to select a range).
        enableClickSelection: false,
      }
    : { mode: 'singleRow', checkboxes: false, enableClickSelection: false },
)

/*
 * Toggle the row from anywhere in the selection cell, not just the 16px
 * checkbox graphic. AG Grid marks clicks that land on the checkbox itself as
 * already handled and skips the cell's mouse listeners, so this fires only for
 * the surrounding area — the two never both run on one click.
 */
function toggleRowFromCell(p: any) {
  const node = p.node
  if (!node || node.selectable === false) return
  const newValue = !node.isSelected()
  // setSelectedParams also carries shift-click range selection, so dragging a
  // range works the same whether you hit the checkbox or the space around it.
  if (typeof node.setSelectedParams === 'function') {
    node.setSelectedParams({
      newValue,
      rangeSelect: !!p.event?.shiftKey,
      source: 'checkboxSelected',
    })
  } else {
    node.setSelected(newValue, false, 'checkboxSelected')
  }
}

// A little wider than the 50px default so the checkbox is comfortable to hit,
// and pinned so it stays put when a wide grid is scrolled sideways.
const selectionColumnDef: SelectionColumnDef = {
  width: 52,
  minWidth: 52,
  maxWidth: 52,
  pinned: 'left',
  resizable: false,
  suppressMovable: true,
  cellClass: 'tb-select-cell',
  headerClass: 'tb-select-header',
  onCellClicked: toggleRowFromCell,
}

/*
 * Rows currently flashing "Saved" (see "Enter saves the row" below), by row id.
 *
 * Declared up here because `actionsRefreshKey` reads it and `watch` evaluates
 * its source immediately: left below, the watch ran before this line and died
 * in the temporal dead zone, taking the whole component — and every row in it
 * — with it.
 */
const savedFlashes = reactive(new Set<string>())

/*
 * Only the actions column depends on dirty/refresh state, so refresh just that
 * column. A blanket `refreshCells({ force: true })` also tore down and rebuilt
 * the selection checkboxes; when that landed between a mousedown and a mouseup
 * the browser never fired the click and the tick was silently dropped — the
 * "sometimes it selects, sometimes it doesn't" behaviour.
 *
 * The key is a plain string so a caller passing a fresh `Set` on every render
 * doesn't trigger a refresh unless its contents actually changed.
 */
const actionsRefreshKey = computed(
  () =>
    `${props.refreshKey}|${[...props.dirtyIds].sort().join(',')}|${[...savedFlashes]
      .sort()
      .join(',')}`,
)

watch(actionsRefreshKey, () => {
  if (!hasActions.value) return
  gridApi.value?.refreshCells({ force: true, columns: [ACTIONS_COL_ID] })
})

/* ---------- Enter saves the row ----------
 *
 * Committing a cell with Enter also saves the row it is in. So does picking an
 * option from a dropdown cell (`agSelectCellEditor`): a value chosen from a
 * closed list is a finished edit the moment it is chosen — there is nothing
 * more to type — so it commits and saves the same way Enter does, whether it
 * was picked with the mouse or the keyboard. Free-text cells, including the
 * suggesting ones, are not dropdowns for this purpose: a suggestion can be
 * taken and then typed past, so those still wait for Enter.
 *
 * The Save button stays for the mouse, and for edits committed some other way
 * (Tab to the next cell, clicking off the row) — those still only mark the row
 * dirty, so a run of cells can be filled in and saved once, by the Enter that
 * ends it.
 *
 * Which key committed the edit is not in the grid's event: `cellValueChanged`
 * reports the value, not how it was entered, and the custom editors commit
 * Enter by blurring their input, so the grid's own Enter handling never runs
 * for them anyway. So the key is read where it is unambiguous — a capture
 * listener on the wrapper, capture because an editor handling Enter itself
 * gets the event after us — and a value committing within a few ticks of an
 * Enter belongs to that Enter.
 */
const ENTER_COMMIT_MS = 300
/** How long "Saved" stays up, and how long a save has to land to earn it. */
const SAVED_FLASH_MS = 1500
const SAVE_WAIT_MS = 10000

let lastEnterAt = 0

function onWrapperKeyDown(e: KeyboardEvent) {
  if (e.key === 'Enter') lastEnterAt = Date.now()
}

/**
 * Flash "Saved" on a row, once the save it was given has actually landed.
 *
 * The row belongs to the page and so does the save, so all this component can
 * see of it is the row going clean — which is what a page does on success, and
 * what it does not do when the API rejects the write (it raises the error
 * toast instead). Waiting for that keeps the flash honest; a save that never
 * lands stops being waited on rather than flashing "Saved" over a row the
 * server never took.
 */
function flashWhenSaved(row: any) {
  const key = getRowId({ data: row })
  const stop = watch(
    () => rowIsDirty(row),
    (dirty) => {
      if (dirty) return
      clearTimeout(giveUp)
      stop()
      savedFlashes.add(key)
      setTimeout(() => savedFlashes.delete(key), SAVED_FLASH_MS)
    },
  )
  const giveUp = setTimeout(stop, SAVE_WAIT_MS)
}

function onValueChanged(e: any) {
  const field = e.column?.colDef?.field
  if (!field || !e.data) return
  emit('cell-edit', e.data, field, e.newValue)

  const fromDropdown = e.column?.colDef?.cellEditor === 'agSelectCellEditor'
  if (!fromDropdown && Date.now() - lastEnterAt > ENTER_COMMIT_MS) return
  lastEnterAt = 0
  // The page marks the row dirty from `cell-edit` above, synchronously, so by
  // here the row already knows whether it has anything to save.
  if (!props.editable || !rowIsDirty(e.data)) return
  // The page may need to collect something before this can be saved. The edit
  // stays in the cell and the row stays dirty either way, so a vetoed save is
  // the page's to finish — or to undo.
  if (props.saveGuard && !props.saveGuard(e.data, field)) return
  emit('save-row', e.data)
  flashWhenSaved(e.data)
}

function onGridReady(e: any) {
  gridApi.value = e.api
  if (isCardList.value) applyCardFallback()
  syncCards()
  syncCardSort()
  emit('grid-ready', e.api)
}

function onPaginationChanged(e: any) {
  // v32: getPaginationModel() was removed; read the page size directly
  const size = e.api.paginationGetPageSize?.()
  if (size) emit('page-size-change', size)
  syncCardRows()
}

/*
 * The cards mirror the row model, so they have to be re-read whenever it
 * changes. `modelUpdated` is the catch-all — it fires for new row data, a quick
 * filter, a column filter and a sort — and pagination is handled above.
 */
function onModelUpdated() {
  syncCardRows()
}

function onSortChanged() {
  syncCardSort()
  syncCardRows()
}

/* Hiding or reordering a column changes what a card shows, not just the grid. */
function onColumnStateChanged() {
  if (!isCardList.value) return
  syncCardFields()
}

/** Card-list pagination: the grid owns the paging, these just drive it. */
function cardPrevPage() {
  gridApi.value?.paginationGoToPreviousPage?.()
}

function cardNextPage() {
  gridApi.value?.paginationGoToNextPage?.()
}

function onSelectionChanged() {
  const rows = gridApi.value?.getSelectedRows() || []
  selectedCount.value = rows.length
  emit('selection-change', rows)
}

function clearSelection() {
  gridApi.value?.deselectAll()
}

/*
 * Narrowing the window past MOBILE with rows selected would leave the parent
 * holding a selection it can no longer see or clear — the pill and the bulk
 * buttons are both gone by then. Dropping it keeps what the view thinks is
 * selected and what the user can see in agreement.
 */
watch(selectionEnabled, (enabled) => {
  if (enabled || !selectedCount.value) return
  clearSelection()
  selectedCount.value = 0
  emit('selection-change', [])
})

// ---------- column visibility picker ----------
const showColPicker = ref(false)
const colVisible = ref<Record<string, boolean>>({})
const columnChoices = ref<{ colId: string; label: string }[]>([])

function syncColVisible() {
  const api = gridApi.value
  if (!api) return
  const state: Record<string, boolean> = {}
  // AG Grid v32 column state uses `hide` (true = hidden), not `visible`.
  for (const c of api.getColumnState()) {
    if (c.colId && c.colId !== 'agGroupRowColumn') state[c.colId] = !c.hide
  }
  colVisible.value = state
  // Enumerate the columns AG Grid actually resolved rather than the input
  // definitions. Dynamic mixed-device schemas can be reconciled by the grid;
  // if a column is on screen, this guarantees it is offered here too.
  columnChoices.value = (api.getColumns() || []).flatMap((column) => {
    const definition = column.getColDef()
    const colId = column.getColId()
    if (!definition.field || colId === ACTIONS_COL_ID) return []
    return [{ colId, label: definition.headerName || definition.field }]
  })
}

function toggleColumn(colId: string, visible: boolean) {
  gridApi.value?.setColumnsVisible([colId], visible)
  colVisible.value = { ...colVisible.value, [colId]: visible }
  // Working the picker is a deliberate choice of fields, so the fallback cap
  // stands down from here on and what is on screen is now the user's.
  keepCurrentState()
  // In card mode the picker chooses the card's fields, so the cards have to
  // follow immediately rather than waiting for a grid event.
  syncCardFields()
}

function showAllColumns() {
  const api = gridApi.value
  if (!api) return
  api.applyColumnState({
    state: api.getColumnState().map((column: any) => ({
      colId: column.colId,
      hide: false,
    })),
  })
  syncColVisible()
  keepCurrentState()
  syncCardFields()
}

function openColPicker() {
  syncColVisible()
  showColPicker.value = !showColPicker.value
}

function closeColPicker() {
  showColPicker.value = false
}

// Close the picker on outside tap / Escape
/*
 * Both `click` and `touchstart`.
 *
 * iOS Safari does not bubble a click to `document` when the tap lands on an
 * element that is not itself interactive — no handler of its own, no
 * `cursor: pointer` — so a document-level click listener misses taps on plain
 * text and empty space, and the panel stays open. Listening for `touchstart`
 * as well covers those. Both firing is harmless: the handler checks whether the
 * tap was inside the panel before it closes anything, so a tap on the toggle
 * button is ignored by this and handled by the button.
 */
function onDocClick(e: Event) {
  if (showColPicker.value && !(e.target as HTMLElement)?.closest?.('.col-picker')) {
    closeColPicker()
  }
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') closeColPicker()
}
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('touchstart', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})
watch(showColPicker, (open) => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('touchstart', onDocClick)
  document.removeEventListener('keydown', onKeydown)
  if (open) {
    document.addEventListener('click', onDocClick)
    document.addEventListener('touchstart', onDocClick, { passive: true })
    document.addEventListener('keydown', onKeydown)
  }
})

// ---------- state capture / restore (used by filter profiles) ----------

function getState(): any {
  const api = gridApi.value
  if (!api) return null
  const column = api.getColumnState()
  // Keep every part of a saved view explicit and JSON-friendly.
  return {
    filter: api.getFilterModel?.() || {},
    // AG Grid 36 keeps sort in column state; preserve the saved-filter wire
    // shape as a compact, ordered sort model for existing API consumers.
    sort: column
      .filter((item) => item.sort)
      .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))
      .map((item) => ({ colId: item.colId, sort: item.sort })),
    column,
    quick_filter: api.getQuickFilter?.() || '',
    page_size: api.paginationGetPageSize?.() || props.defaultPageSize,
  }
}

function applyState(s: any) {
  const api = gridApi.value
  if (!api || !s) return
  // Column state carries order, widths, visibility and sort
  if (s.column?.length) api.applyColumnState({ state: s.column, applyOrder: true })
  if (s.filter) api.setFilterModel(s.filter)
  quickFilter.value = s.quick_filter || ''
  api.setGridOption('quickFilterText', s.quick_filter || '')
  if (s.page_size) api.setGridOption('paginationPageSize', s.page_size)
  // A view has now said what this table should show, so the fallback stops
  // owning any of it.
  keepCurrentState()
  // A saved view can change every one of the things a card shows — which
  // fields, their order, the sort and the page size.
  syncCardSort()
  syncCards()
}

/**
 * Repaint rows whose data changed underneath the grid.
 *
 * A row object mutated in place — a scan writing its result back into the row
 * the page already holds — is a change the grid has no way to notice: it was
 * handed that object and has no reason to render it again. The cards do
 * notice, because they render through Vue; only the grid needs telling.
 *
 * Ids are the row ids (`getRowId`), and unknown ones are skipped: a row on
 * another page of a filtered grid has no node to refresh.
 */
function refreshRows(ids: string[]) {
  const api = gridApi.value
  if (!api) return
  const nodes = ids.map((id) => api.getRowNode(String(id))).filter(Boolean)
  if (nodes.length) {
    api.refreshCells({ rowNodes: nodes, force: true })
    // Cell classes are re-evaluated by the refresh above; a ROW class is not —
    // it is applied when the row is rendered and then left alone. A device
    // that just went overdue (or stopped being overdue) has to redraw for the
    // row to change colour. Only the rows that changed, and only when the page
    // uses row classes at all. Callers already avoid this mid-edit; a redraw
    // would end the edit with the row it rebuilds.
    if (props.rowClass) api.redrawRows({ rowNodes: nodes })
  }
  reapplyView()
}

/**
 * Re-run filter and sort over data that changed underneath them.
 *
 * The row model decides what a filter matches and where a sort puts it once,
 * when the data arrives; a value changed afterwards leaves a row sitting in a
 * list it no longer belongs to — a device just marked available still showing
 * under a "checked out" filter, an offline one still at the top of a sort by
 * Online. Re-running the filter stage re-runs the sort and the mapping after
 * it, so the row leaves, moves or arrives on its own.
 *
 * A row being edited is left alone by the refresh itself, but if the change
 * filters it out the grid ends the edit with it — which is the honest outcome:
 * the row is no longer on screen to edit.
 */
function reapplyView() {
  const api = gridApi.value
  if (!api) return
  api.refreshClientSideRowModel('filter')
  // The cards read the row model rather than the grid, so they have to be
  // re-read after it has been rebuilt.
  syncCards()
}

/** Is a cell editor open? Callers use this to hold off on writing to rows. */
function isEditing(): boolean {
  return (gridApi.value?.getEditingCells?.() || []).length > 0
}

defineExpose({
  getState,
  applyState,
  gridApi,
  refreshRows,
  reapplyView,
  isEditing,
  getSelectedRows: () => gridApi.value?.getSelectedRows() || [],
})
</script>

<template>
  <div class="data-table">
    <div class="table-toolbar">
      <input v-model="quickFilter" class="quick-filter" placeholder="Quick filter..." />
      <span v-if="selectionEnabled && selectedCount" class="selection-count">
        {{ selectedCount }} selected
        <button class="selection-clear" title="Clear selection" @click="clearSelection">✕</button>
      </span>
      <!-- Bulk actions live beside the quick filter, next to the selection
           count they act on. Owned by the parent view, and gone with the rest
           of selection below MOBILE — the watcher above has already emptied
           the parent's selection by then, but the slot is gated too so a view
           that renders something unconditionally cannot leak one through. -->
      <slot v-if="selectionEnabled" name="selection-actions" :count="selectedCount" />
      <!-- Cards have no column headers to click, so the sort moves here. -->
      <div v-if="isCardList" class="card-sort">
        <select
          :value="cardSort.colId"
          aria-label="Sort by"
          @change="applyCardSort(($event.target as HTMLSelectElement).value, cardSort.dir)"
        >
          <option value="">Unsorted</option>
          <option v-for="c in sortableFields" :key="c.field" :value="c.field">
            {{ c.headerName || c.field }}
          </option>
        </select>
        <button
          class="btn btn-mini"
          type="button"
          :disabled="!cardSort.colId"
          :title="cardSort.dir === 'asc' ? 'Ascending — tap for descending' : 'Descending — tap for ascending'"
          @click="toggleCardSortDir"
        >
          {{ cardSort.dir === 'asc' ? '↑' : '↓' }}
        </button>
      </div>
      <div class="col-picker">
        <button class="btn btn-mini" @click="openColPicker">
          {{ isCardList ? 'Fields' : 'Columns' }}
        </button>
        <div v-if="showColPicker" class="col-picker-panel">
          <div class="col-picker-header">
            <span>{{ isCardList ? 'Fields on each card' : 'Visible columns' }}</span>
            <button class="btn btn-mini" @click="showAllColumns">Show all</button>
          </div>
          <label v-for="c in columnChoices" :key="c.colId" class="col-picker-item">
            <input
              type="checkbox"
              :checked="colVisible[c.colId] !== false"
              @change="toggleColumn(c.colId, ($event.target as HTMLInputElement).checked)"
            />
            <span>{{ c.label }}</span>
          </label>
        </div>
      </div>
    </div>
    <!--
      In card mode this is parked off-screen rather than unmounted: it is still
      the filter, sort and pagination engine, and getState() still has to return
      a real grid state so a view saved on a phone opens correctly on a desktop.
    -->
    <div
      class="ag-wrapper"
      :class="{ 'ag-offscreen': isCardList }"
      :aria-hidden="isCardList"
      @keydown.capture="onWrapperKeyDown"
    >
      <AgGridVue
        style="width: 100%; height: 100%"
        :modules="modules"
        :columnDefs="columnDefs"
        :rowData="rows"
        rowModelType="clientSide"
        :theme="theme"
        :loadThemeGoogleFonts="false"
        :defaultColDef="defaultColDef"
        :getRowId="getRowId"
        :getRowClass="getRowClass"
        :quickFilterText="quickFilter"
        :pagination="true"
        :paginationPageSize="defaultPageSize"
        :paginationPageSizeSelector="PAGE_SIZES"
        :suppressCellFocus="true"
        :animateRows="true"
        :rowSelection="rowSelection"
        :selectionColumnDef="selectionColumnDef"
        @cellValueChanged="onValueChanged"
        @paginationChanged="onPaginationChanged"
        @selectionChanged="onSelectionChanged"
        @modelUpdated="onModelUpdated"
        @sortChanged="onSortChanged"
        @columnVisible="onColumnStateChanged"
        @columnMoved="onColumnStateChanged"
        @gridReady="onGridReady"
      />
    </div>

    <template v-if="isCardList">
      <CardList
        :rows="cardRows"
        :fields="cardFields"
        :actions-for="cardActionsFor"
        :badge-for="rowBadge"
        :class-for="rowClass"
        :row-key="(row: any) => getRowId({ data: row })"
        :empty-text="rows.length ? 'No rows match the current filter.' : 'Nothing here yet.'"
      />
      <div v-if="cardPage.total > 1" class="card-paging">
        <button
          class="btn btn-mini"
          type="button"
          :disabled="cardPage.current === 0"
          @click="cardPrevPage"
        >
          ‹ Prev
        </button>
        <span class="card-paging-label">
          Page {{ cardPage.current + 1 }} of {{ cardPage.total }}
          <small>({{ cardPage.rowCount }} rows)</small>
        </span>
        <button
          class="btn btn-mini"
          type="button"
          :disabled="cardPage.current >= cardPage.total - 1"
          @click="cardNextPage"
        >
          Next ›
        </button>
      </div>
    </template>
  </div>
</template>
