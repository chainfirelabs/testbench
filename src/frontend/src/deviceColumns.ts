/**
 * Grid columns generated from a device type's effective schema.
 *
 * One builder for every device page. A router page and a phone page differ
 * because their schemas differ, not because they are different components —
 * which is also why an installation can invent a device type this code has
 * never heard of and get a working inventory page for it.
 *
 * A field's *role* is consulted before its key. An installation is free to
 * call its WAN address `mgmt_ip`; what makes it the address a scan reaches is
 * the `scan_address_wan` role, and the same is true of every column below that
 * needs more than a text box.
 */
import SuggestCellEditor from './components/SuggestCellEditor.vue'
import { detailCellRenderer } from './detail'
import { dateColumn, daysFromToday, fmtDate } from './dates'
import { fieldValue, setFieldValue, type SchemaField } from './deviceSchema'
import type { DeviceType } from './deviceTypes'
import { deviceAddressUrl, fieldAppearsInDeviceList, isDeviceAddressField } from './deviceInventory'

export interface ColumnContext {
  /** Opens the "View …" modal for values too long for a cell. */
  openDetail: (title: string, value: any) => void
  /** Whether this row is past its return date, for the red treatment. */
  isOverdue: (row: any) => boolean
  /** In-tab navigation for the identity column's link. */
  navigate: (uniqueId: string) => void
  deviceTypes: DeviceType[]
  /** A type-filtered inventory already communicates this in its page title. */
  hideDeviceType?: boolean
  /** Columns to start hidden — the all-devices page hides type-specific ones. */
  hidden?: (field: SchemaField) => boolean
}

const fmtDateTime = (p: any) => (p.value ? new Date(p.value).toLocaleString() : '')

/** "checked_out" reads as "Checked Out" to a person; the value is unchanged. */
export function optionLabel(value: string): string {
  return String(value)
    .split(/[_-]/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/** Purpose and Due Back describe one checkout, so only a checkout has them. */
function isCheckedOut(p: any): boolean {
  return p.data?.status === 'checked_out'
}

function identityColumn(field: SchemaField, ctx: ColumnContext) {
  return {
    field: field.key,
    headerName: field.label,
    minWidth: 150,
    editable: false,
    valueGetter: (p: any) => p.data?.unique_id,
    cellRenderer: (p: any) => {
      const el = document.createElement('div')
      if (!p.value) return el
      const link = document.createElement('a')
      link.className = 'grid-link'
      link.textContent = p.value
      link.href = `/devices/${encodeURIComponent(p.value)}`
      link.onclick = (e) => {
        e.stopPropagation()
        // Plain left click navigates in-tab; middle click, Ctrl/Cmd+click and
        // "Open in new tab" keep the browser's native behaviour.
        if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
          e.preventDefault()
          ctx.navigate(p.value)
        }
      }
      el.appendChild(link)
      return el
    },
  }
}

function onlineColumn(field: SchemaField) {
  return {
    field: field.key,
    headerName: field.label,
    editable: false,
    valueGetter: (p: any) => fieldValue(p.data, field),
    cellRenderer: (p: any) => {
      // green = online, red = scanned but down, gray = never scanned / no address
      const state = p.value ? 'online' : p.data?.data?.last_scanned_at ? 'offline' : 'unknown'
      const el = document.createElement('span')
      el.className = `status-dot ${state}`
      el.title = state === 'online' ? 'Online' : state === 'offline' ? 'Offline' : 'Unknown (never scanned)'
      return el
    },
  }
}

/** The structural Device Type column, which is not a schema field at all. */
export function deviceTypeColumn(types: DeviceType[], hidden = false) {
  return {
    field: 'device_type_id',
    headerName: 'Device Type',
    hide: hidden,
    cellEditor: 'agSelectCellEditor',
    cellEditorParams: { values: ['', ...types.map((item) => item.id)] },
    valueFormatter: (p: any) => types.find((item) => item.id === p.value)?.label || 'Uncategorized',
  }
}

/*
 * Opens the device's own web page, in a new tab.
 *
 * A small link beside the address rather than the address itself, because the
 * cell is editable: a link spanning the cell would mean the first click of the
 * double-click that starts an edit opens a browser tab. That is the same
 * conflict `detailCellRenderer` solves the same way, and it is why the address
 * text is left alone here while the detail page — where nothing is editable
 * until you press Edit — links the address itself.
 *
 * An anchor, not a button: middle-click, Ctrl/Cmd-click and "Open in new tab"
 * should all behave the way they do on any other link.
 */
const OPEN_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>'

function addressColumn(column: any, field: SchemaField) {
  return {
    ...column,
    cellRenderer: (p: any) => {
      const el = document.createElement('span')
      el.className = 'address-cell'
      const text = document.createElement('span')
      text.textContent = p.value == null ? '' : String(p.value)
      el.appendChild(text)

      // The row carries its own link overrides, so the icon opens what this
      // device answers on rather than what the field defaults to.
      const url = deviceAddressUrl(p.value, field, p.data?.link_overrides)
      if (!url) return el

      const link = document.createElement('a')
      // Global class: a cell renderer builds detached DOM, which scoped styles
      // never reach.
      link.className = 'address-open'
      link.href = url
      link.target = '_blank'
      link.rel = 'noopener noreferrer'
      link.title = `Open ${url}`
      link.setAttribute('aria-label', `Open ${url}`)
      link.innerHTML = OPEN_ICON
      // Without this the grid also treats the click as a cell selection, and
      // the next one starts an edit behind the tab that just opened.
      link.onclick = (e) => e.stopPropagation()
      el.appendChild(link)
      return el
    },
  }
}

function baseColumn(field: SchemaField) {
  return {
    field: field.key,
    headerName: field.label,
    editable: field.writable && field.storage !== 'derived',
    valueGetter: (p: any) => fieldValue(p.data, field),
    valueSetter: (p: any) => {
      setFieldValue(p.data, field, p.newValue)
      return true
    },
  }
}

export function deviceColumn(field: SchemaField, ctx: ColumnContext): any {
  if (field.role === 'identifier' || field.key === 'unique_id') return identityColumn(field, ctx)
  if (field.role === 'scan_state') return onlineColumn(field)

  const column: any = baseColumn(field)

  if (field.key === 'misc_data') {
    // Not a value: the rest of the document, offered as one openable cell.
    return {
      ...column,
      editable: false,
      valueGetter: (p: any) => p.data?.misc_data,
      cellRenderer: detailCellRenderer(field.label, ctx.openDetail),
      valueFormatter: (p: any) => (p.value && Object.keys(p.value).length ? JSON.stringify(p.value) : ''),
    }
  }

  if (field.role === 'checkout_due') {
    return {
      ...dateColumn({
        ...column,
        // The date turns red on the day it is missed. A cell class is
        // re-evaluated on every save and every poll, so an extended deadline
        // clears the red without the page asking it to.
        cellClassRules: { 'cell-overdue': (p: any) => ctx.isOverdue(p.data) },
        cellEditorParams: () => ({ max: daysFromToday(7) }),
      }),
      editable: field.writable && isCheckedOut,
    }
  }
  if (field.role === 'checkout_purpose') {
    return {
      ...column,
      cellRenderer: detailCellRenderer(field.label, ctx.openDetail),
      editable: field.writable && isCheckedOut,
    }
  }
  if (field.role === 'checkout_started' || field.key === 'checked_out_at') {
    return { ...column, editable: false, valueFormatter: fmtDate }
  }
  if (field.role === 'last_seen' || field.key === 'last_scanned_at') {
    return { ...column, editable: false, valueFormatter: fmtDateTime }
  }

  switch (field.type) {
    case 'select':
      return {
        ...column,
        cellEditor: 'agSelectCellEditor',
        // The leading blank clears an optional field: once a value has been
        // picked, the editor is the only way back to "not set".
        cellEditorParams: { values: field.required ? field.options : ['', ...field.options] },
        valueFormatter: (p: any) => (p.value ? optionLabel(p.value) : ''),
      }
    case 'boolean':
      return { ...column, cellDataType: 'boolean' }
    case 'date':
      return dateColumn(column)
    case 'number':
      return { ...column, cellDataType: 'number' }
    case 'json':
    case 'textarea':
      // A paragraph is not a cell's worth of text, so the column offers it
      // rather than truncating it. Double-click still edits in place.
      return { ...column, cellRenderer: detailCellRenderer(field.label, ctx.openDetail) }
    default:
      // An address the device answers on also opens in a browser. Applied
      // last so the column keeps whatever editing the branches below give it:
      // the link is added to the cell, it does not replace it.
      if (isDeviceAddressField(field)) {
        if (field.sensitive || !field.writable) return addressColumn(column, field)
        return addressColumn({
          ...column,
          cellEditor: SuggestCellEditor,
          cellEditorParams: { entity: 'devices', field: field.key },
        }, field)
      }
      if (field.sensitive || !field.writable) return column
      // Free-text columns repeat across a fleet — one rack holds many devices —
      // so they edit with the values already in the column offered. Still free
      // text: something not on the list is accepted.
      return {
        ...column,
        cellEditor: SuggestCellEditor,
        cellEditorParams: { entity: 'devices', field: field.key },
      }
  }
}

/**
 * Every column for one page: the device type, then the schema's own fields.
 *
 * `hidden` starts a column collapsed rather than dropping it, so the grid's
 * column picker can bring it back — which is how the all-devices page offers
 * type-specific fields without showing forty columns by default.
 */
export function deviceGridColumns(fields: SchemaField[], ctx: ColumnContext): any[] {
  const columns = [deviceTypeColumn(ctx.deviceTypes, !!ctx.hideDeviceType)]
  for (const field of fields) {
    if (!fieldAppearsInDeviceList(field)) continue
    const column = deviceColumn(field, ctx)
    columns.push(ctx.hidden?.(field) ? { ...column, hide: true } : column)
  }
  return columns
}
