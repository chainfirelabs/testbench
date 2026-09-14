/**
 * Grid cells for fields that hold more than a cell can show.
 *
 * Notes and misc data are the columns that made the grid unreadable:
 * a JSON blob squeezed onto one line is a wall of braces that says nothing at a
 * glance and cannot be read at any glance at all. These columns render a "View
 * Notes" / "View Misc Data" button instead, and the value opens in a dialog
 * that lays JSON out as fields rather than as source.
 *
 * The renderer is shared rather than repeated per view so the button, the empty
 * case and the label all read the same way in every grid — and because CardList
 * reuses each column's `cellRenderer` verbatim, this is what appears on a phone
 * too.
 */

/** True for values with nothing worth opening a dialog over. */
export function isEmptyDetail(value: any): boolean {
  if (value == null) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value).length === 0
  return false
}

/**
 * A cell renderer showing `View <label>` when the cell has a value.
 *
 * `open` is the view's own handler — cell renderers build detached DOM, outside
 * anything Vue is tracking, so the dialog is opened by calling back into the
 * component rather than by rendering it here.
 */
export function detailCellRenderer(label: string, open: (title: string, value: any) => void) {
  return (p: any) => {
    const el = document.createElement('span')
    if (isEmptyDetail(p.value)) return el
    const btn = document.createElement('button')
    btn.type = 'button'
    // Global class: scoped styles never reach DOM built out here.
    btn.className = 'cell-view-btn'
    btn.textContent = `View ${label}`
    btn.onclick = (e) => {
      // Without this the grid treats the click as a cell selection and, on a
      // second click, starts editing behind the dialog that just opened.
      e.stopPropagation()
      open(label, p.value)
    }
    el.appendChild(btn)
    return el
  }
}
