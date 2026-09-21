<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRaw, watch } from 'vue'
import { useRoute } from 'vue-router'
import DataTable, { type RemoteTableRequest, type RowAction } from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import FormModal, { type FormField } from '../components/FormModal.vue'
import OverflowMenu from '../components/OverflowMenu.vue'
import SuggestCellEditor from '../components/SuggestCellEditor.vue'
import DetailModal from '../components/DetailModal.vue'
import ImportProgressModal from '../components/ImportProgressModal.vue'
import CheckoutDialog from '../components/CheckoutDialog.vue'
import PluginRunModal from '../components/PluginRunModal.vue'
import { daysFromToday, daysUntil } from '../dates'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { useImportProgress } from '../importProgress'
import { invalidateSuggestions, makeFilterValues, useSuggestions } from '../suggestions'
import { describeScan } from '../scan'
import { PERMISSION, useAuthStore } from '../stores/auth'
import { router } from '../router'
import { deviceGridColumns, optionLabel } from '../deviceColumns'
import {
  UNCATEGORIZED,
  editableFields,
  fieldByRole,
  fieldValue,
  loadDeviceSchema,
  schemaFormField,
  setFieldValue,
  toDevicePayload,
  type SchemaField,
} from '../deviceSchema'
import { invokePluginAction, usePluginActions, type PluginAction } from '../pluginActions'
import { actionConfirmation } from '../pluginActionConfirmation'
import { deviceTypes, loadDeviceTypes } from '../deviceTypes'
import { deviceActionUnavailableReason, deviceDownloadFilename } from '../deviceInventory'
import { remoteTableParams } from '../remoteTable'

const auth = useAuthStore()
const route = useRoute()
const rows = ref<any[]>([])
const toast = ref('')
const toastError = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const profiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const table = ref<InstanceType<typeof DataTable> | null>(null)
const { importState, runImport, closeImport } = useImportProgress()

/*
 * Which inventory this page is: one device type, the devices with no type, or
 * the whole fleet. Everything below is generated from the schema that answer
 * resolves to — the columns, the dialogs, the import template and which plugin
 * actions are on offer.
 */
const activeTypeKey = computed(() => route.params.typeKey as string | undefined)
const activeType = computed(() => deviceTypes.value.find((item) => item.key === activeTypeKey.value))
const pageTitle = computed(() =>
  activeTypeKey.value === UNCATEGORIZED ? 'Uncategorized Devices' : activeType.value?.label || 'Devices',
)
/** The fields of this page's type, or the global set on the all-devices page. */
const deviceFields = ref<SchemaField[]>([])
const fieldsByType = ref(new Map<string, SchemaField[]>())
const loadProgress = ref<{ loaded: number; total: number } | null>(null)
const loadError = ref('')
let loadController: AbortController | null = null
let schemaGeneration = 0
/** Every other type's own fields, offered through the column picker. */
const otherTypeFields = ref<SchemaField[]>([])
const statusField = computed(() => deviceFields.value.find((field) => field.key === 'status'))
const statusValues = computed(() => statusField.value?.options || [])
const scanEnabled = computed(() =>
  [...fieldsByType.value.values()].some((fields) => fields.some((field) =>
    field.visible && ['scan_address_wan', 'scan_address_lan'].includes(field.role || ''))),
)
/*
 * Saved views are scoped by device type: a router layout names router columns,
 * and applying it to the phone page would hide everything and show nothing.
 */
const layoutScope = computed(() => (activeTypeKey.value ? `devices:${activeTypeKey.value}` : 'devices'))
const { actions: pluginActions, loadPluginActions } = usePluginActions('devices')
const pluginRunning = ref<Set<string>>(new Set())
type PluginRunView = { title: string; pluginId: string; status: Record<string, any> }
const pluginRun = ref<PluginRunView | null>(null)
// Active dialogs remain addressable after Hide so clicking the animated icon
// reopens that run instead of creating a duplicate Kubernetes Job.
const activePluginRuns = new Map<string, PluginRunView>()
const infoConfig = ref<{ enabled: boolean; required_fields: string[]; url_field: string }>({
  enabled: false,
  required_fields: [],
  url_field: 'wan_ip',
})

// Rows with unsaved changes: row id -> set of changed fields
const dirty = ref<Map<string, Set<string>>>(new Map())
const selected = ref<any[]>([])
const bulkEditing = ref(false)
const savingBulkEdit = ref(false)
const bulkEditValues = ref<Record<string, any>>({})
const scanningIds = ref<Set<string>>(new Set())
const infoStartingIds = ref<Set<string>>(new Set())
// Brief per-row result shown after a single-device scan finishes
const scanFlash = ref<Map<string, { text: string; cls: string }>>(new Map())
const refreshTick = ref(0)
const flashTimers: Record<string, any> = {}

/*
 * The field a "View …" cell is currently showing. A checkout purpose is a
 * sentence or a paragraph, not a cell's worth of text, so the column offers it
 * rather than truncating it — the same treatment notes and test data get.
 */
const detail = ref<{ title: string; value: any } | null>(null)

function openDetail(title: string, value: any) {
  detail.value = { title, value }
}

function bumpRefresh() {
  refreshTick.value++
}

function setScanFlash(id: string, text: string, cls: string) {
  scanFlash.value.set(id, { text, cls })
  if (flashTimers[id]) clearTimeout(flashTimers[id])
  flashTimers[id] = setTimeout(() => {
    scanFlash.value.delete(id)
    bumpRefresh()
  }, 6000)
  bumpRefresh()
}

function rowBadge(row: any): { text: string; cssClass?: string; spinner?: boolean } | null {
  if (!row.id) return null
  if (scanningIds.value.has(row.id)) {
    return { text: 'Scanning…', cssClass: 'scanning', spinner: true }
  }
  const f = scanFlash.value.get(row.id)
  if (f) return { text: f.text, cssClass: f.cls }
  // Lowest priority: a scan happening right now is news, while being overdue
  // is a standing state that is still true once the scan finishes.
  const over = daysOverdue(row)
  if (over !== null) return { text: `Overdue · ${over}d`, cssClass: 'error' }
  return null
}

/**
 * How many days past its return date a device is, or null if it is not.
 *
 * The same rule the API derives `overdue` from (services/checkout.py): checked
 * out, has a due date, and the date has passed. Computed rather than read off
 * the row so it stays true as the row changes underneath — extend a due date
 * and the red goes away on the same repaint that saved it.
 */
function daysOverdue(row: any): number | null {
  if (row.data?.status !== 'checked_out' || !row.data?.checkout_due) return null
  const days = daysUntil(row.data.checkout_due)
  return days !== null && days < 0 ? -days : null
}

function isOverdue(row: any): boolean {
  return daysOverdue(row) !== null
}

/** The whole row goes red, not just the date: it is the device that is late. */
function rowClass(row: any): string | null {
  return isOverdue(row) ? 'row-overdue' : null
}

/*
 * "Show only what is late" — the same question GET /devices/overdue answers,
 * asked of the rows already on screen. Filtered here rather than re-fetched so
 * it composes with the quick filter, the saved views and the fleet poll instead
 * of replacing what they are showing.
 */
const showOverdueOnly = ref(false)
const overdueCount = ref(0)

const scanAll = ref<{ running: boolean; scanned: number; total: number }>({
  running: false,
  scanned: 0,
  total: 0,
})
let scanPollTimer: any = null

/*
 * Columns come from the published schema. There is no list of device columns
 * in this file any more: an installation that adds a field, renames one, or
 * gives phones a column routers do not have gets all of that here without a
 * change to this page.
 *
 * On the all-devices page the other types' fields are still built, but start
 * hidden — the grid's column picker brings them back for whoever wants them,
 * and a saved view remembers the choice.
 */
const columns = computed(() =>
  deviceGridColumns([...deviceFields.value, ...otherTypeFields.value], {
    openDetail,
    isOverdue,
    navigate: (uniqueId: string) => router.push(`/devices/${encodeURIComponent(uniqueId)}`),
    deviceTypes: deviceTypes.value,
    // The page title already says Routers, Mobile Phones, and so on. Keep the
    // structural column on the mixed All Devices page where it adds meaning.
    hideDeviceType: !!activeTypeKey.value,
    hidden: (field) => !deviceFields.value.includes(field),
  }),
)

/**
 * The dialogs edit a whole device at once, so the status can move away from
 * Checked Out mid-edit. Blank the two checkout fields when it does, rather
 * than leave text sitting in a box that has just been disabled — FormModal
 * submits null for a disabled field either way, and this makes that visible.
 */
function onDeviceFieldChange(values: Record<string, any>, key: string, value: any) {
  if (key === 'status' && value !== 'checked_out') {
    const purpose = fieldByRole(deviceFields.value, 'checkout_purpose')
    const due = fieldByRole(deviceFields.value, 'checkout_due')
    if (purpose) values[purpose.key] = ''
    if (due) values[due.key] = ''
  }
}

function showToast(msg: string, isError = false) {
  toast.value = msg
  toastError.value = isError
  setTimeout(() => (toast.value = ''), 4000)
}

// A fleet export is the slowest of these, and the raw shape is slower still.
const { downloading, download } = useDownload(showToast)

// A computed (not a function called from the template): a fresh Set on every
// render would look like a change to the grid and trigger needless refreshes.
const dirtyIds = computed(() => new Set(dirty.value.keys()))

function isRowDirty(row: any): boolean {
  return !!row.id && dirty.value.has(row.id)
}

/** Re-run filter and sort over rows whose values changed underneath them. */
async function load() {
  table.value?.reapplyView()
}

/**
 * Re-read the list from the server, row count and all.
 *
 * For changes to which devices exist — one created, deleted or imported.
 * A server-paged grid cannot see those by refreshing the blocks it holds: it
 * keeps the row count it was last given, so a new device lands past the end of
 * a table that does not know it grew and a deleted one leaves a gap. This is
 * why adding a device used to need a browser reload to show it.
 */
async function reloadRows() {
  table.value?.reload()
}

/*
 * Values for the column filters' checklists.
 *
 * A device grid is server-paged, so the distinct values of a column are the
 * server's to answer. The ones this page can answer itself — a select field's
 * vocabulary, the device types it already loaded — it does, without a request.
 */
const filterValues = makeFilterValues({
  entity: 'devices',
  local: (colId) => {
    if (colId === 'device_type_id') return deviceTypes.value.map((type) => type.id)
    const field = [...deviceFields.value, ...otherTypeFields.value].find((f) => f.key === colId)
    if (field?.type === 'select') return field.options
    if (field?.type === 'boolean') return [true, false]
    return undefined
  },
  // The rest of the document as one cell, and the two columns derived from a
  // timestamp — none of them a value anyone filters by picking from a list.
  skip: ['misc_data', 'created_at', 'updated_at', 'last_scanned_at', 'last_seen_online'],
})

async function loadRemoteDevices(request: RemoteTableRequest) {
  const params = remoteTableParams(request, {
    device_type: activeTypeKey.value || undefined,
    overdue: showOverdueOnly.value || undefined,
  })
  if (!request.sortModel.length) {
    params.set('sort', 'unique_id')
    params.set('order', 'asc')
  }
  const page = await api<any>(`/devices?${params}`)
  rows.value = page.items
  return { rows: page.items, total: page.total }
}

/**
 * Fetch the schema this page renders itself from.
 *
 * On the all-devices page the other enabled types are fetched too, so their
 * fields can be offered in the column picker. Devices with no type see the
 * global schema, which is exactly what they have.
 */
async function loadSchema() {
  const generation = ++schemaGeneration
  const scope = activeTypeKey.value
  const key = scope === UNCATEGORIZED ? undefined : scope
  const schema = await loadDeviceSchema(key)
  const schemas = new Map<string, SchemaField[]>([[key || '', schema.fields]])
  const own = new Set(schema.fields.map((field) => field.key))
  const extra: SchemaField[] = []
  if (!scope) {
    const typeSchemas = await Promise.all(deviceTypes.value.map(async (type) =>
      [type.key, await loadDeviceSchema(type.key)] as const))
    for (const [typeKey, typeSchema] of typeSchemas) {
      schemas.set(typeKey, typeSchema.fields)
      for (const field of typeSchema.fields) {
        if (field.visible && field.list_visible !== false && !own.has(field.key)) {
          own.add(field.key)
          extra.push(field)
        }
      }
    }
  }
  if (generation !== schemaGeneration) return
  deviceFields.value = schema.fields
  fieldsByType.value = schemas
  otherTypeFields.value = extra
}

function onGridReady(api: any) {
  // Apply the default saved view (if any) once the grid is ready
  profiles.value?.applyDefault()
}

function onCellEdit(row: any, field: string, value: any) {
  // Remembered before the assignment below overwrites it: cancelling the
  // checkout dialog has to put the cell back, and by then the old value is
  // gone from the row and from the grid's event.
  if (field === 'status' && row.id) statusBefore.set(row.id, row.data?.status)
  const schemaField = [...deviceFields.value, ...otherTypeFields.value].find((item) => item.key === field)
  if (schemaField) setFieldValue(row, schemaField, value)
  else row[field] = value
  if (row.id && (field === 'device_type_id' || (schemaField && schemaField.writable))) {
    if (!dirty.value.has(row.id)) dirty.value.set(row.id, new Set())
    dirty.value.get(row.id)!.add(field)
  }
}

// ---------- checking a device out ----------
/*
 * A checkout needs a purpose and a return date, and the API refuses the
 * transition without them. Neither can be typed into the Status dropdown, so
 * any save that would check a device out is intercepted here and the dialog
 * asks for them first.
 *
 * Two callers, one rule: `saveGuard` stops the automatic save a dropdown pick
 * triggers, and `saveRow` covers the Save button, which the grid does not route
 * through the guard.
 */
const statusBefore = new Map<string, string>()
const pendingCheckout = ref<{ row: any; previous: string } | null>(null)
const savingCheckout = ref(false)

function needsCheckoutDetails(row: any): boolean {
  return row.data?.status === 'checked_out' && (!row.data?.checkout_purpose || !row.data?.checkout_due)
}

function askForCheckoutDetails(row: any) {
  pendingCheckout.value = { row, previous: statusBefore.get(row.id) || 'available' }
}

/** Called by the grid before it auto-saves an edit. False withholds the save. */
function saveGuard(row: any): boolean {
  if (!needsCheckoutDetails(row)) return true
  askForCheckoutDetails(row)
  return false
}

function confirmCheckout(values: { checkout_purpose: string; checkout_due: string }) {
  const pending = pendingCheckout.value
  if (!pending) return
  const row = pending.row
  row.data = { ...(row.data || {}), checkout_purpose: values.checkout_purpose, checkout_due: values.checkout_due }
  if (!dirty.value.has(row.id)) dirty.value.set(row.id, new Set())
  const fields = dirty.value.get(row.id)!
  fields.add('status')
  fields.add('checkout_purpose')
  fields.add('checkout_due')
  pendingCheckout.value = null
  savingCheckout.value = true
  saveRow(row).finally(() => (savingCheckout.value = false))
}

function cancelCheckout() {
  const pending = pendingCheckout.value
  if (!pending) return
  const row = pending.row
  // Put the cell back. Left as it was, the grid would show "Checked Out" on a
  // row the server never accepted.
  row.data = { ...(row.data || {}), status: pending.previous }
  const fields = dirty.value.get(row.id)
  if (fields) {
    fields.delete('status')
    if (fields.size === 0) dirty.value.delete(row.id)
  }
  pendingCheckout.value = null
  table.value?.refreshRows([row.id])
}

// ---------- new device dialog ----------
// The device is filled in here and only reaches the API once it validates, so
// the grid never shows a half-made row.

const showNew = ref(false)
const creating = ref(false)
const newDevice = ref<Record<string, any>>({})

/*
 * Free-text columns that repeat across a fleet are offered as suggestions in
 * the dialogs, the same way the grid offers them. Which columns those are is
 * the schema's answer, not a list here: any writable text field that is not a
 * secret qualifies.
 */
const suggestibleKeys = computed(() =>
  deviceFields.value
    .filter((field) => field.type === 'text' && field.writable && !field.sensitive && field.storage === 'data')
    .map((field) => field.key),
)
const suggestionCache = new Map<string, ReturnType<typeof useSuggestions>>()
function suggestionsFor(key: string) {
  if (!suggestionCache.has(key)) suggestionCache.set(key, useSuggestions('devices', key))
  return suggestionCache.get(key)!
}

const editTarget = ref<any>(null)
const savingEdit = ref(false)
const editValues = ref<Record<string, any>>({})

/*
 * A device can be created from the all-devices page, where its type is not
 * decided until the dialog picks one — so the form re-resolves against the
 * chosen type and the required fields change under the user as they choose.
 */
const currentFormValues = computed(() => (editTarget.value ? editValues.value : newDevice.value))
const selectedFormType = computed(
  () => deviceTypes.value.find((item) => item.id === currentFormValues.value.device_type_id) || activeType.value,
)
const formFields = ref<SchemaField[]>([])

watch(
  () => selectedFormType.value?.key,
  async (key) => {
    formFields.value = (await loadDeviceSchema(key)).fields
  },
  { immediate: true },
)

const TYPE_FIELD: FormField = {
  key: 'device_type_id',
  label: 'Device Type',
  type: 'select',
}

/**
 * The dialog's fields: the device type, then everything the chosen type's
 * schema says is editable — with the two checkout fields carrying the rule
 * that they belong to a checkout and nothing else.
 */
const NEW_DEVICE_FIELDS = computed<FormField[]>(() => {
  const fields: FormField[] = [{
    ...TYPE_FIELD,
    options: [
      { value: '', label: 'Uncategorized' },
      ...deviceTypes.value.map((item) => ({ value: item.id, label: item.label })),
    ],
  }]
  for (const field of editableFields(formFields.value.length ? formFields.value : deviceFields.value)) {
    const base = schemaFormField(field)
    if (field.type === 'select') base.options = field.options.map((value) => ({ value, label: optionLabel(value) }))
    if (suggestibleKeys.value.includes(field.key)) {
      base.type = 'datalist'
      base.options = suggestionsFor(field.key).value
    }
    // Required while the status is Checked Out and disabled otherwise: the
    // same dialog creates available devices, which have nothing to say about a
    // checkout, and the API rejects either field on one. FormModal re-reads
    // both predicates on every change, so ticking the status to Checked Out
    // opens the fields and marks them required in place.
    if (field.role === 'checkout_due') {
      base.type = 'date'
      base.max = daysFromToday(7)
      base.required = (v: Record<string, any>) => v.status === 'checked_out'
      base.disabled = (v: Record<string, any>) => v.status !== 'checked_out'
      base.hint = 'When a checked-out device is due back, no more than 7 days from today.'
    }
    if (field.role === 'checkout_purpose') {
      base.required = (v: Record<string, any>) => v.status === 'checked_out'
      base.disabled = (v: Record<string, any>) => v.status !== 'checked_out'
      base.hint = 'Why the device is out. Cleared when it is checked back in.'
    }
    fields.push(base)
  }
  fields.push({ key: 'misc_data', label: 'Misc data (JSON)', type: 'json', placeholder: '{}' })
  return fields
})

function openNew() {
  newDevice.value = Object.fromEntries(NEW_DEVICE_FIELDS.value.map((f) => [f.key, '']))
  if (statusValues.value.includes('available')) newDevice.value.status = 'available'
  newDevice.value.device_type_id = activeType.value?.id || ''
  newDevice.value.misc_data = '{}'
  showNew.value = true
}

/** Flat dialog values become the `{unique_id, device_type_id, data}` envelope. */
function devicePayload(values: Record<string, any>): Record<string, any> {
  const parsed = { ...values }
  if (typeof parsed.misc_data === 'string') {
    try {
      parsed.misc_data = JSON.parse(parsed.misc_data || '{}')
    } catch {
      throw new Error('Misc data must be valid JSON')
    }
  }
  return toDevicePayload(parsed, formFields.value.length ? formFields.value : deviceFields.value)
}

async function createDevice(values: Record<string, any>) {
  creating.value = true
  try {
    const created = await api<any>('/devices', { method: 'POST', body: JSON.stringify(devicePayload(values)) })
    // A device that did not exist a moment ago: the grid has to re-read the
    // list rather than refresh the rows it is holding, or the new row is
    // simply not in the window it knows about.
    await reloadRows()
    // The values just written are the ones the next device is most likely to
    // want offered.
    invalidateSuggestions('devices')
    showNew.value = false
    showToast(`Device ${created.unique_id} created`)
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creating.value = false
  }
}

// ---------- edit device dialog ----------
// The same fields as the device page's Edit, in the dialog the grid already
// uses for a new device — so a row can be edited in full without leaving the
// list. Grid cell editing still handles a one-field change.

function openEdit(row: any) {
  // Read from the row, so unsaved cell edits carry into the dialog rather
  // than being silently reverted by it.
  const known = [...deviceFields.value, ...formFields.value]
  editValues.value = {
    ...Object.fromEntries(NEW_DEVICE_FIELDS.value.map((f) => {
      if (f.key === 'device_type_id') return [f.key, row.device_type_id || '']
      const config = known.find((field) => field.key === f.key)
      return [f.key, (config ? fieldValue(row, config) : row[f.key]) ?? '']
    })),
    unique_id: row.unique_id,
    // The API's own projection: the document minus the fields this device's
    // type accounts for. A device may carry values whose field is no longer on
    // its layout, and an edit must not be how they disappear. Deriving it here
    // as well is what let the two disagree — the server subtracted a frozen
    // list of legacy names and reported `username` as unexplained data.
    misc_data: JSON.stringify(row.misc_data || {}, null, 2),
  }
  editTarget.value = row
}

async function saveEdit(values: Record<string, any>) {
  const row = editTarget.value
  if (!row) return
  savingEdit.value = true
  try {
    const updated = await api<any>(`/devices/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify(devicePayload(values)),
    })
    Object.assign(row, updated)
    // Checking a device out stamps who and when server-side, so a save can
    // change cells the user never touched; the grid has to be told.
    table.value?.refreshRows([row.id])
    invalidateSuggestions('devices')
    // The dialog wrote every field, so nothing is left pending on the row.
    dirty.value.delete(row.id)
    editTarget.value = null
    showToast(`Device ${updated.unique_id} saved`)
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    savingEdit.value = false
  }
}

async function saveRow(row: any) {
  // Every row in the grid is a saved device: new ones are created in the dialog.
  if (!row.id) return
  const fields = dirty.value.get(row.id)
  if (!fields || fields.size === 0) return
  // The Save button does not go through the grid's guard, so the same rule is
  // applied here. The row stays dirty; the dialog finishes the save.
  if (needsCheckoutDetails(row)) {
    askForCheckoutDetails(row)
    return
  }
  // Only the cells that actually changed are sent: a partial write leaves the
  // rest of the document alone, including values whose field is not on this
  // page's layout at all.
  const payload: Record<string, any> = { data: {} }
  const known = [...deviceFields.value, ...otherTypeFields.value]
  for (const key of fields) {
    if (key === 'device_type_id') payload.device_type_id = row.device_type_id || null
    else if (key === 'unique_id') payload.unique_id = row.unique_id
    else {
      const field = known.find((item) => item.key === key)
      payload.data[key] = field ? fieldValue(row, field) : row[key]
      if (payload.data[key] === '' && !field?.sensitive) payload.data[key] = null
    }
  }
  try {
    const updated = await api<any>(`/devices/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    Object.assign(row, updated)
    // Same as the dialog: the response carries the checkout stamp the server
    // just applied, and those cells only repaint if the grid is told to.
    table.value?.refreshRows([row.id])
    invalidateSuggestions('devices')
    dirty.value.delete(row.id)
    showToast(`Device ${row.unique_id} saved`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteRow(row: any) {
  if (!row.id) return
  if (!confirm(`Delete device "${row.unique_id}"?`)) return
  try {
    await api(`/devices/${row.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter((r) => toRaw(r) !== toRaw(row))
    dirty.value.delete(row.id)
    // The grid keeps the selection, so a deleted row stays selected — and
    // counted — unless it is told. See clearSelection in DataTable.
    table.value?.clearSelection()
    await reloadRows()
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteSelected() {
  const ids = selected.value.filter((r) => r.id).map((r) => r.id)
  if (!ids.length) return
  if (!confirm(`Delete ${ids.length} selected device${ids.length > 1 ? 's' : ''}?`)) return
  try {
    await api('/devices/delete', { method: 'POST', body: JSON.stringify({ ids }) })
    rows.value = rows.value.filter((r) => !ids.includes(r.id))
    for (const id of ids) dirty.value.delete(id)
    // Emptying this page's copy is not enough: the selection lives in the
    // grid, and rows left ticked there are added to whatever is ticked next —
    // which is how deleting 100 and then selecting 100 more read as 200.
    table.value?.clearSelection()
    selected.value = []
    await reloadRows()
    showToast(`Deleted ${ids.length} device${ids.length > 1 ? 's' : ''}`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

// A bulk edit may span device types. Offer only writable fields whose key,
// storage, type and choices agree in every selected device's effective schema.
//
// The device type itself is not among them and is added separately below: it
// is not a schema field but the thing that *chooses* the schema, so it has no
// entry to agree on and would be dropped by the very intersection it decides.
const commonBulkDeviceFields = computed<SchemaField[]>(() => {
  if (!selected.value.length) return []
  const schemas = selected.value.map((row) => fieldsByType.value.get(row.device_type_key || '') || [])
  const first = schemas[0].filter((field) =>
    field.visible && field.writable && field.storage !== 'derived'
    && !['unique_id', 'device_type', 'device_type_id', 'misc_data'].includes(field.key))
  return first.filter((field) => schemas.every((schema) => {
    const match = schema.find((candidate) => candidate.key === field.key)
    return !!match && match.visible && match.writable && match.storage === field.storage
      && match.type === field.type && JSON.stringify(match.options || []) === JSON.stringify(field.options || [])
  }))
})

const BULK_DEVICE_FIELDS = computed<FormField[]>(() => {
  // First: categorising a batch of devices is the common reason to open this
  // dialog at all. The button that opens it is already behind devices.edit.
  const fields: FormField[] = [{
    ...TYPE_FIELD,
    options: [
      { value: '', label: 'Uncategorized' },
      ...deviceTypes.value.map((item) => ({ value: item.id, label: item.label })),
    ],
    hint: 'Moving a device to a type checks it against that type\'s schema. One '
      + 'missing a value the new type requires is reported and left as it was.',
  }]
  for (const field of commonBulkDeviceFields.value) {
    const form = schemaFormField(field)
    // Existing rows already satisfy required constraints; bulk edit validates
    // only fields the operator explicitly opts into changing.
    form.required = false
    if (field.type === 'select') {
      form.options = field.options.map((value) => ({ value, label: optionLabel(value) }))
    }
    fields.push(form)
  }
  return fields
})

function sharedDeviceValue(field: SchemaField): any {
  const values = selected.value.map((row) => fieldValue(row, field))
  const encoded = values.map((value) => JSON.stringify(value ?? null))
  return encoded.every((value) => value === encoded[0]) ? values[0] ?? '' : ''
}

/** The type every selected device already has, or blank where they disagree. */
function sharedDeviceTypeId(): string {
  const ids = selected.value.map((row) => row.device_type_id || '')
  return ids.every((id) => id === ids[0]) ? ids[0] : ''
}

function openBulkEdit() {
  bulkEditValues.value = {
    ...Object.fromEntries(
      commonBulkDeviceFields.value.map((field) => [field.key, sharedDeviceValue(field)]),
    ),
    device_type_id: sharedDeviceTypeId(),
  }
  bulkEditing.value = true
}

async function saveBulkEdit(values: Record<string, any>) {
  savingBulkEdit.value = true
  let updated = 0
  const errors: string[] = []
  for (const row of selected.value) {
    try {
      const result = await api<any>(`/devices/${row.id}`, {
        method: 'PATCH',
        body: JSON.stringify(toDevicePayload(values, commonBulkDeviceFields.value)),
      })
      Object.assign(row, result)
      dirty.value.delete(row.id)
      updated++
    } catch (e: any) {
      errors.push(`${row.unique_id}: ${e.message}`)
    }
  }
  table.value?.refreshRows(selected.value.map((row) => row.id))
  invalidateSuggestions('devices')
  savingBulkEdit.value = false
  if (errors.length) {
    showToast(`Updated ${updated}; ${errors.length} failed — ${errors.slice(0, 3).join('; ')}`, true)
  } else {
    bulkEditing.value = false
    showToast(`Updated ${updated} selected device${updated === 1 ? '' : 's'}`)
  }
}

async function scanDevice(row: any) {
  if (!row.id || scanningIds.value.has(row.id)) return
  scanningIds.value.add(row.id)
  bumpRefresh()
  try {
    const result = await api<any>(`/devices/${row.id}/scan`, { method: 'POST' })
    if (result.device) {
      Object.assign(row, result.device)
      // The row object changed under the grid, which cannot see that by
      // itself — without this the dot keeps its old colour until a reload.
      table.value?.refreshRows([row.id])
    }
    // The badge names which address answered; the toast spells out the services.
    const scan = describeScan(result)
    setScanFlash(row.id, scan.badge, scan.cls)
    showToast(`${row.unique_id}: ${scan.detail}`, !!result.error)
  } catch (e: any) {
    setScanFlash(row.id, 'Scan failed', 'error')
    showToast(e.message, true)
  } finally {
    scanningIds.value.delete(row.id)
    bumpRefresh()
  }
}

/*
 * The columns a scan writes, and the only ones a poll is allowed to touch.
 *
 * A poll that reloaded whole rows would throw away whatever the user is
 * part-way through typing elsewhere in the row — a sweep of a large fleet runs
 * for minutes, and the grid stays editable throughout. Patching just these
 * three leaves every other cell, dirty or not, exactly as the user left it.
 */
const SCAN_FIELDS = ['online_status', 'last_seen_online', 'last_scanned_at'] as const

/**
 * Land a scan result on a row.
 *
 * `/scan/results` is deliberately narrow — it is polled every couple of seconds
 * — and returns these three columns flattened. They live in the document like
 * every other value, so they are written there, in one assignment rather than
 * three so the row's `data` object is replaced once per poll.
 */
function patchScanFields(row: any, values: Record<string, any>): void {
  row.data = { ...(row.data || {}), ...values }
  Object.assign(row, values)
}

/**
 * Pull the fleet's online state and paint the rows it changed.
 *
 * Rows the response does not mention (added since the page loaded, deleted
 * since) are left alone, and only rows that actually changed are repainted.
 */
async function patchOnlineState() {
  const states = await api<any[]>('/devices/scan/results')
  const byId = new Map(states.map((s) => [s.id, s]))
  const changed: string[] = []
  for (const row of rows.value) {
    const s = byId.get(row.id)
    if (!s) continue
    const updates: Record<string, any> = {}
    for (const f of SCAN_FIELDS) {
      if (row.data?.[f] !== s[f]) updates[f] = s[f]
    }
    if (Object.keys(updates).length) {
      patchScanFields(row, updates)
      changed.push(row.id)
    }
  }
  if (changed.length) table.value?.refreshRows(changed)
}

/**
 * The same, for a poll tick: at most one of these is in flight at a time.
 *
 * Ticks are 2s apart and do not wait for each other, so on a slow link they
 * would otherwise pile up, every one of them fetching the same thing. Skipping
 * a tick costs nothing — the next one reads the same state, only fresher.
 */
let onlineStateInFlight = false

async function refreshOnlineState() {
  if (onlineStateInFlight) return
  onlineStateInFlight = true
  try {
    await patchOnlineState()
  } finally {
    onlineStateInFlight = false
  }
}

function stopScanPoll() {
  if (scanPollTimer) clearInterval(scanPollTimer)
  scanPollTimer = null
}

function pollScanStatus() {
  if (scanPollTimer) return
  scanPollTimer = setInterval(async () => {
    try {
      const s = await api<any>('/devices/scan/status')
      // Read before the assignment below overwrites it: this is what tells the
      // last tick of a sweep apart from the ticks of one still running.
      const wasRunning = scanAll.value.running
      scanAll.value = { running: s.running, scanned: s.scanned, total: s.total }
      if (!s.running && wasRunning) {
        stopScanPoll()
        // Unskippable, unlike the per-tick refresh: this is the one that lands
        // the tail of the sweep, and there is no tick after it to retry.
        await patchOnlineState()
        showToast(`Scan finished — ${s.scanned} device${s.scanned === 1 ? '' : 's'} scanned`)
      } else {
        await refreshOnlineState()
      }
    } catch {
      /* ignore transient errors while polling */
    }
  }, 2000)
}

/* Refresh when the user returns, without continually repainting visible rows. */
let fleetInFlight = false

async function patchFleet() {
  table.value?.reapplyView()
}

async function refreshFleet() {
  // Nothing to keep fresh for a tab nobody is looking at, and one poll at a
  // time is enough.
  if (document.hidden || fleetInFlight) return
  // A cell open for editing is the one thing that must not be written to
  // underneath the user; the next tick picks it up once they commit.
  if (table.value?.isEditing()) return
  fleetInFlight = true
  try {
    await patchFleet()
  } catch {
    /* a poll that fails is a poll skipped; the next one tries again */
  } finally {
    fleetInFlight = false
  }
}

function onVisibilityChange() {
  // Coming back to the tab is exactly when the rows are most out of date.
  if (!document.hidden) refreshFleet()
}

function startFleetPoll() {
  document.addEventListener('visibilitychange', onVisibilityChange)
}

function stopFleetPoll() {
  document.removeEventListener('visibilitychange', onVisibilityChange)
}

async function startScanAll() {
  try {
    await api('/devices/scan', { method: 'POST' })
    scanAll.value = { running: true, scanned: 0, total: 0 }
    pollScanStatus()
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function loadInfoConfig() {
  try {
    infoConfig.value = await api('/devices/info/config')
  } catch {
    infoConfig.value.enabled = false
  }
}

function hasInfoFields(row: any): boolean {
  return infoConfig.value.required_fields.every((key) => {
    const value = row.data?.[key] ?? row[key]
    return value !== null && value !== undefined && value !== ''
  })
}

async function startDeviceInfo(row: any) {
  if (!row.id || infoStartingIds.value.has(row.id)) return
  infoStartingIds.value.add(row.id)
  bumpRefresh()
  try {
    await api(`/devices/${row.id}/info`, { method: 'POST' })
    showToast(`Information lookup started for ${row.unique_id}`)
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    infoStartingIds.value.delete(row.id)
    bumpRefresh()
  }
}

// Feather-style wifi icon (currentColor so it follows the theme)
const WIFI_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>'
const INFO_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
const POWER_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>'

/**
 * Why an action cannot run on this row, for the tooltip.
 *
 * The backend decides this again on every invocation — see
 * `/devices/{id}/actions` and the invoke endpoint — so this is about telling
 * somebody what to fix, not about safety. Type policy comes first: an action
 * the device's type has not been given is not the user's to fix by filling in
 * a field.
 */
function pluginUnavailableReason(action: PluginAction, row: any): string | null {
  return deviceActionUnavailableReason(action, row, fieldsByType.value, auth.can(PERMISSION.devicesEdit))
}

/** Expensive agents and disruptive actions deserve a question before launch. */
function confirmPluginAction(action: PluginAction, row?: any): boolean {
  const message = actionConfirmation(action, row?.unique_id)
  return !message || confirm(message)
}

async function runPluginAction(action: PluginAction, row?: any) {
  const key = `${action.plugin_id}:${action.id}:${row?.id || 'collection'}`
  if (!pluginRunning.value.has(key) && !confirmPluginAction(action, row)) return
  if (pluginRunning.value.has(key)) {
    const active = activePluginRuns.get(key)
    if (active) pluginRun.value = active
    return
  }
  pluginRunning.value.add(key)
  bumpRefresh()
  try {
    const result = await invokePluginAction(action, row ? { entity_id: row.id } : { entity_ids: [] })
    showToast(`${action.label} started${row ? ` for ${row.unique_id}` : ''}`)
    if (result.run_id) {
      // Every Job-backed plugin uses the same run dialog so it can expose
      // progress and a confirmed Stop action, including Scan and Scan all.
      const showRun = true
      if (showRun) {
        const view: PluginRunView = {
          title: `${action.label} — ${row?.unique_id || 'devices'}`,
          pluginId: action.plugin_id,
          status: { ...result, state: 'starting', output: '' },
        }
        activePluginRuns.set(key, view)
        pluginRun.value = view
      }
      const deadline = Date.now() + 30 * 60 * 1000
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 2000))
        const status = await api<any>(`/plugins/${action.plugin_id}/runs/${result.run_id}`)
        if (showRun) {
          const active = activePluginRuns.get(key)
          if (active?.status.run_id === result.run_id) {
            const updated = { ...active, status }
            activePluginRuns.set(key, updated)
            if (pluginRun.value?.status.run_id === result.run_id) pluginRun.value = updated
          }
        }
        if (status.state === 'completed') {
          await load()
          showToast(`${action.label} completed${row ? ` for ${row.unique_id}` : ''}`)
          break
        }
        if (status.state === 'cancelled') {
          showToast(`${action.label} stopped${row ? ` for ${row.unique_id}` : ''}`)
          break
        }
        if (status.state === 'failed') throw new Error(status.error || `${action.label} failed`)
      }
    }
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    pluginRunning.value.delete(key)
    activePluginRuns.delete(key)
    bumpRefresh()
  }
}

async function monitorRestoredPluginRun(key: string, action: PluginAction, view: PluginRunView) {
  try {
    const runId = view.status.run_id
    const deadline = Date.now() + 30 * 60 * 1000
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2000))
      const status = await api<any>(`/plugins/${action.plugin_id}/runs/${runId}`)
      const current = activePluginRuns.get(key) || view
      const updated = { ...current, status }
      activePluginRuns.set(key, updated)
      if (pluginRun.value?.status.run_id === status.run_id) pluginRun.value = updated
      if (['completed', 'failed', 'cancelled'].includes(status.state)) {
        if (status.state === 'completed') await load()
        break
      }
    }
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    pluginRunning.value.delete(key)
    activePluginRuns.delete(key)
    bumpRefresh()
  }
}

async function restoreActivePluginRuns() {
  try {
    const runs = await api<any[]>('/plugins/runs/active')
    for (const run of runs) {
      const action = pluginActions.value.find((candidate) =>
        candidate.plugin_id === run.plugin_id && candidate.id === run.action_id)
      if (!action) continue
      const entityId = run.entity_ids?.[0]
      const row = entityId ? rows.value.find((candidate) => String(candidate.id) === String(entityId)) : undefined
      const key = `${action.plugin_id}:${action.id}:${entityId || 'collection'}`
      const view: PluginRunView = {
        title: `${action.label} — ${row?.unique_id || (entityId ? 'device' : 'devices')}`,
        pluginId: action.plugin_id,
        status: run,
      }
      pluginRunning.value.add(key)
      activePluginRuns.set(key, view)
      void monitorRestoredPluginRun(key, action, view)
    }
    bumpRefresh()
  } catch {
    // Plugin discovery is retried by the backend and the next page load can
    // reattach; a temporary controller outage must not block the device list.
  }
}

async function cancelPluginRun() {
  const view = pluginRun.value
  if (!view || !confirm(`Stop ${view.title}? The running worker Pod will be terminated.`)) return
  try {
    const status = await api<any>(`/plugins/${view.pluginId}/runs/${view.status.run_id}`, { method: 'DELETE' })
    const updated = { ...view, status }
    pluginRun.value = updated
    showToast(`${view.title} stopped`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

function pluginIcon(action: PluginAction): string {
  if (action.icon === 'wifi') return WIFI_ICON
  if (action.icon === 'power') return POWER_ICON
  return INFO_ICON
}

function extraActions(row: any): RowAction[] {
  if (!row.id) return []
  const actions: RowAction[] = []
  for (const action of pluginActions.value.filter((item) => item.scope === 'row')) {
    const key = `${action.plugin_id}:${action.id}:${row.id}`
    const running = pluginRunning.value.has(key)
    // On All Devices the catalog contains actions enabled for at least one
    // type. A row whose type is not in that policy should not advertise the
    // action at all. Keep a running action visible so its output remains
    // reachable if policy changes while the worker is active.
    if (!running && action.device_types && !action.device_types.includes(row.device_type_key)) continue
    // An active action always remains clickable so its hidden output can be
    // reopened, even if the device goes offline while that Job is running.
    const unavailableReason = running ? null : pluginUnavailableReason(action, row)
    actions.push({
      label: action.label,
      title: running ? `View ${action.label} output…` : (unavailableReason || action.title || action.label),
      icon: pluginIcon(action),
      cssClass: running ? 'scanning' : '',
      disabled: !!unavailableReason,
      onClick: () => runPluginAction(action, row),
    })
  }
  return actions
}

/*
 * A type page exports that type's columns; the all-devices page exports the
 * union of every type's, so a mixed fleet does not lose a column on the way
 * out. `columns=data` is the lossless third option, offered in the menu.
 */
function exportAs(format: 'json' | 'csv', columns: 'type' | 'all' | 'data' = 'type') {
  const shape = activeTypeKey.value || columns !== 'type' ? columns : 'all'
  const variant = columns === 'data' ? 'raw' : undefined
  /*
   * The grid's own search, sort and column filters, not just the device type.
   *
   * The export endpoint has always taken the list endpoint's filters — its
   * docstring promises that what you see in a filtered table is what you get
   * in the file — but this page only ever sent the type from the route. So
   * narrowing the grid to three devices and exporting handed back the whole
   * fleet, silently and plausibly, which is the worst way for an export to be
   * wrong. Built from the same `remoteTableParams` the loader uses, so the two
   * cannot read the same grid differently.
   */
  const state = table.value?.getState()
  const params = remoteTableParams(
    {
      startRow: 0,
      endRow: 1,
      search: state?.quick_filter || '',
      sortModel: state?.sort || [],
      filterModel: state?.filter || {},
    },
    {
      format,
      columns: shape,
      device_type: activeTypeKey.value || undefined,
      overdue: showOverdueOnly.value || undefined,
    },
  )
  // An export returns the whole filtered set, in its own order: a window into
  // it means nothing, and the endpoint orders by creation date whatever it is
  // told. Dropped rather than left to be ignored — anything the export does
  // not recognise it treats as a column filter, so an installation with a
  // field actually named `sort` would get a quietly wrong file.
  for (const key of ['page', 'page_size', 'sort', 'order']) params.delete(key)
  download(
    `/devices/export?${params}`,
    deviceDownloadFilename(activeTypeKey.value, format, variant),
    'export',
  )
}

/** A blank CSV carrying exactly the columns an import of this page accepts. */
function downloadTemplate() {
  const scope = activeTypeKey.value && activeTypeKey.value !== UNCATEGORIZED
    ? `?device_type=${encodeURIComponent(activeTypeKey.value)}`
    : ''
  download(
    `/devices/template${scope}`,
    deviceDownloadFilename(activeTypeKey.value, 'csv', 'template'),
    'template',
  )
}

async function onImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const result = await runImport('/devices/import', file)
    // An import adds rows, so the row count has moved: re-read the list.
    if (result) await reloadRows()
  } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}

onMounted(async () => {
  await loadDeviceTypes()
  await Promise.all([
    loadSchema(),
    loadInfoConfig(),
    loadPluginActions(activeTypeKey.value === UNCATEGORIZED ? undefined : activeTypeKey.value),
  ])
  await load()
  try {
    const overdue = await api<any>('/devices/overdue?page_size=1')
    overdueCount.value = overdue.total
  } catch { /* the overdue shortcut is optional */ }
  await restoreActivePluginRuns()
  startFleetPoll()
  /*
   * A sweep may already be under way — started on the interval timer, or from
   * another session — so pick it up on arrival rather than showing a page of
   * dots that quietly go stale while it runs.
   */
  try {
    const s = await api<any>('/devices/scan/status')
    if (s.running) {
      scanAll.value = { running: true, scanned: s.scanned, total: s.total }
      pollScanStatus()
    }
  } catch {
    /* the page is perfectly usable without knowing this */
  }
})

watch(activeTypeKey, async () => {
  loadController?.abort()
  rows.value = []
  fieldsByType.value = new Map()
  // A different inventory: different columns, a different plugin allowlist and
  // a different saved layout, so all of it is re-resolved before the rows are.
  await loadSchema()
  await loadPluginActions(activeTypeKey.value === UNCATEGORIZED ? undefined : activeTypeKey.value)
  // A different inventory is a different row set, not the same rows with
  // different values, so the count has to be re-read with them.
  await reloadRows()
  profiles.value?.applyDefault()
})

watch(showOverdueOnly, () => reloadRows())

// Leaving the page ends the polling with it; nothing here outlives the view.
onBeforeUnmount(() => {
  loadController?.abort()
  schemaGeneration++
  stopScanPoll()
  stopFleetPoll()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2>{{ pageTitle }}</h2>
      <div class="toolbar">
        <!-- The primary action stays outside the overflow: it is the one thing
             on this page you most often want, and it should be one tap. -->
        <button v-if="auth.can(PERMISSION.devicesEdit)" class="btn btn-primary" @click="openNew">+ New device</button>
        <!-- Only offered when there is something to see: a button reading
             "Overdue (0)" is a permanent reminder of nothing. -->
        <button
          v-if="overdueCount"
          class="btn btn-overdue"
          :class="{ active: showOverdueOnly }"
          :title="showOverdueOnly ? 'Show every device' : 'Show only devices past their return date'"
          @click="showOverdueOnly = !showOverdueOnly"
        >
          Overdue ({{ overdueCount }})
        </button>
        <!-- Outside the menu on purpose — see OverflowMenu's note: the panel
             closes on click, and a file input unmounted mid-picker never fires
             `change`. -->
        <input ref="fileInput" type="file" accept=".json,.csv" style="display: none" @change="onImportFile" />
        <OverflowMenu>
          <template v-if="auth.can(PERMISSION.devicesEdit)">
            <button class="btn" @click="fileInput?.click()">Import</button>
            <button
              v-if="scanEnabled"
              class="btn"
              title="Download a blank CSV with the columns an import accepts"
              :disabled="downloading"
              @click="downloadTemplate"
            >
              Template
            </button>
            <button
              v-for="action in pluginActions.filter((item) => item.scope === 'collection' && !item.unavailable_reason)"
              :key="`${action.plugin_id}:${action.id}`"
              class="btn"
              :class="{
                'btn-danger': action.risk === 'disruptive',
                scanning: pluginRunning.has(`${action.plugin_id}:${action.id}:collection`),
              }"
              :title="pluginRunning.has(`${action.plugin_id}:${action.id}:collection`)
                ? `View ${action.label} output`
                : action.title"
              @click="runPluginAction(action)"
            >
              {{ pluginRunning.has(`${action.plugin_id}:${action.id}:collection`)
                ? `View ${action.label} output`
                : action.label }}
            </button>
          </template>
          <button class="btn" :disabled="downloading" @click="exportAs('json')">
            {{ downloading ? 'Preparing…' : 'Export JSON' }}
          </button>
          <!-- One CSV, because there is now one answer. The second button
               existed because a field-column export silently dropped link
               overrides, so restoring a fleet needed the document-in-one-cell
               shape; that shape carries a JSON blob per row and is unusable in
               a spreadsheet, which made choosing between them a trap. The
               field-column export carries the overrides now and round-trips
               exactly, so there is nothing left to choose. `columns=data` is
               still on the API for callers that want the whole document. -->
          <button
            class="btn"
            title="One column per field — opens in a spreadsheet, and re-imports exactly as exported"
            :disabled="downloading"
            @click="exportAs('csv')"
          >
            {{ downloading ? 'Preparing…' : 'Export CSV' }}
          </button>
        </OverflowMenu>
      </div>
    </div>
    <p v-if="loadProgress" role="status">
      Loading devices: {{ loadProgress.loaded }}<template v-if="loadProgress.total"> of {{ loadProgress.total }}</template>…
    </p>
    <p v-if="loadError" role="alert">{{ loadError }} <button class="btn" @click="load">Retry</button></p>
    <DataTable
      ref="table"
      :columns="columns"
      :rows="rows"
      :remote-loader="loadRemoteDevices"
      :filter-values="filterValues"
      :editable="auth.can(PERMISSION.devicesEdit)"
      :selectable="auth.can(PERMISSION.devicesEdit)"
      :dirty-ids="dirtyIds"
      :is-row-dirty="isRowDirty"
      :row-editable="auth.can(PERMISSION.devicesEdit)"
      :extra-row-actions="extraActions"
      :row-badge="rowBadge"
      :row-class="rowClass"
      :save-guard="saveGuard"
      :refresh-key="refreshTick"
      @cell-edit="onCellEdit"
      @edit-row="openEdit"
      @save-row="saveRow"
      @delete-row="deleteRow"
      @selection-change="(r: any[]) => (selected = r)"
      @grid-ready="onGridReady"
    >
      <template #table-actions>
        <FilterProfilesMenu
          ref="profiles"
          :key="layoutScope"
          :entity="layoutScope"
          :get-state="() => table?.getState()"
          :apply-state="(s) => table?.applyState(s)"
        />
      </template>
      <template #selection-actions>
        <button
          v-if="auth.can(PERMISSION.devicesEdit) && selected.length"
          class="btn"
          title="Edit the device type, and any field the selected devices share"
          :disabled="!BULK_DEVICE_FIELDS.length"
          @click="openBulkEdit"
        >
          Edit
        </button>
        <button
          v-if="auth.can(PERMISSION.devicesEdit) && selected.length"
          class="btn btn-danger"
          title="Delete the selected devices"
          @click="deleteSelected"
        >
          Delete
        </button>
      </template>
    </DataTable>
    <FormModal
      v-if="bulkEditing"
      :title="`Edit ${selected.length} selected devices`"
      :fields="BULK_DEVICE_FIELDS"
      :values="bulkEditValues"
      :busy="savingBulkEdit"
      selective
      submit-label="Apply changes"
      @submit="saveBulkEdit"
      @cancel="bulkEditing = false"
    />
    <FormModal
      v-if="editTarget"
      :title="`Edit Device — ${editTarget.unique_id}`"
      :fields="NEW_DEVICE_FIELDS"
      :values="editValues"
      :busy="savingEdit"
      submit-label="Save changes"
      @change="(k: string, v: any) => onDeviceFieldChange(editValues, k, v)"
      @submit="saveEdit"
      @cancel="editTarget = null"
    />
    <FormModal
      v-if="showNew"
      title="New Device"
      :fields="NEW_DEVICE_FIELDS"
      :values="newDevice"
      :busy="creating"
      @change="(k: string, v: any) => onDeviceFieldChange(newDevice, k, v)"
      @submit="createDevice"
      @cancel="showNew = false"
    />
    <CheckoutDialog
      v-if="pendingCheckout"
      :device-label="pendingCheckout.row.unique_id"
      :purpose="pendingCheckout.row.checkout_purpose || ''"
      :due="pendingCheckout.row.checkout_due || ''"
      :busy="savingCheckout"
      @submit="confirmCheckout"
      @cancel="cancelCheckout"
    />
    <DetailModal
      v-if="detail"
      :title="detail.title"
      :value="detail.value"
      @close="detail = null"
    />
    <ImportProgressModal :state="importState" @close="closeImport" />
    <PluginRunModal
      v-if="pluginRun"
      :title="pluginRun.title"
      :run="pluginRun.status"
      @cancel="cancelPluginRun"
      @close="pluginRun = null"
    />

    <div v-if="toast" class="toast" :class="{ 'toast-error': toastError }">{{ toast }}</div>
  </div>
</template>
