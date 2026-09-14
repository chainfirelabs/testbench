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
import { fieldAppearsInDeviceList } from './deviceInventory'

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
