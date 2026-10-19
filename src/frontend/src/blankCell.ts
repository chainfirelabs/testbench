/**
 * What a list shows for a field with nothing in it.
 *
 * An empty cell is ambiguous — not set, still loading, or a rendering bug all
 * look the same — so every grid and card list says so in words instead.
 * `DataTable` applies this to every column it is given, which is why the views
 * themselves never need to spell out a fallback.
 */
export const BLANK_TEXT = 'N/A'

/** CSS class for a placeholder, so it reads as an absence rather than a value. */
export const BLANK_CLASS = 'cell-blank'

/** CSS class for a cell whose row has not arrived from the server yet. */
export const LOADING_CLASS = 'cell-loading'

/*
 * A row the grid has made room for but not yet received.
 *
 * A server-paged grid holds a placeholder row while a page is in flight — one
 * for a first load, a page's worth after paging, filtering or searching. It
 * has a node but no data, and left to the columns it reads as a real row of
 * blanks: N/A, "Uncategorized", "(unversioned)". Such a row is drawn as a
 * loading bar instead, and no column's own wording is consulted for it.
 */
export function isLoadingRow(p: any): boolean {
  return !!p?.node && p.data == null
}

/** True for values a list has nothing to show for. `0` and `false` are values. */
export function isBlankValue(value: any): boolean {
  if (value == null) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.keys(value).length === 0
  }
  return false
}

/*
 * Anything a renderer builds that carries meaning without text: a link icon, a
 * button, a picker. A cell holding one of these is not empty even when it has
 * no text of its own.
 */
const VISUAL = 'a, button, select, input, svg, img'

/** True when a cell renderer produced nothing a reader could see. */
export function isBlankRender(out: any): boolean {
  if (out == null) return true
  if (typeof out === 'string') return out.trim() === ''
  if (typeof out !== 'object' || !('textContent' in out)) return false
  if ((out.textContent || '').trim() !== '') return false
  if (typeof out.matches === 'function' && out.matches(VISUAL)) return false
  if (typeof out.querySelector === 'function' && out.querySelector(VISUAL)) return false
  return true
}

function loadingNode() {
  const el = document.createElement('span')
  el.className = LOADING_CLASS
  el.setAttribute('aria-label', 'Loading')
  return el
}

function blankNode() {
  const el = document.createElement('span')
  el.className = BLANK_CLASS
  el.textContent = BLANK_TEXT
  return el
}

/**
 * The column as a list should show it: blanks read `N/A`.
 *
 * A column whose cell is meaningful while empty — a status dot, whose colour is
 * the value — opts out with `blankPlaceholder: false`. That key is ours, not AG
 * Grid's, so it is removed here before the grid's validation sees it.
 *
 * Formatters and renderers are wrapped rather than replaced, so a column's own
 * wording for an empty value ("Uncategorized", "(unversioned)", "None") still
 * wins: the placeholder only fills what the column left empty.
 *
 * A row still on its way from the server is drawn as a loading bar in every
 * column, opted out or not (see `isLoadingRow`).
 */
export function withBlankPlaceholder<T extends Record<string, any>>(col: T): T {
  const { blankPlaceholder, ...rest } = col as any
  // Takes precedence over the column's renderer, and defers to it for real rows.
  const loading = { cellRendererSelector: (p: any) => (isLoadingRow(p) ? { component: loadingNode } : undefined) }
  if (blankPlaceholder === false) return { ...rest, ...loading }

  if (typeof rest.cellRenderer === 'function') {
    const render = rest.cellRenderer
    return {
      ...rest,
      ...loading,
      cellRenderer: (p: any) => {
        const out = render(p)
        return isBlankRender(out) ? blankNode() : out
      },
    }
  }

  // A string renderer ('agCheckboxCellRenderer' and the like) draws its own
  // empty state, as does a boolean column's checkbox.
  if (rest.cellRenderer || rest.cellDataType === 'boolean') return { ...rest, ...loading }

  const format = rest.valueFormatter
  const formatted = (p: any) => (typeof format === 'function' ? format(p) : p.value)
  return {
    ...rest,
    ...loading,
    valueFormatter: (p: any) => {
      if (isLoadingRow(p)) return ''
      const text = formatted(p)
      if (isBlankValue(text)) return BLANK_TEXT
      return typeof text === 'object' ? JSON.stringify(text) : String(text)
    },
    cellClassRules: {
      ...rest.cellClassRules,
      [BLANK_CLASS]: (p: any) => !isLoadingRow(p) && isBlankValue(formatted(p)),
    },
  }
}
