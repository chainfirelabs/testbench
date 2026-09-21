<script setup lang="ts">
import { computed, onMounted, ref, toRaw } from 'vue'
import DataTable, { type RemoteTableRequest } from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import FormModal, { type FormField } from '../components/FormModal.vue'
import OverflowMenu from '../components/OverflowMenu.vue'
import JsonCellEditor from '../components/JsonCellEditor.vue'
import DetailModal from '../components/DetailModal.vue'
import ImportProgressModal from '../components/ImportProgressModal.vue'
import { detailCellRenderer } from '../detail'
import { dateColumn } from '../dates'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { useImportProgress } from '../importProgress'
import { PERMISSION, useAuthStore } from '../stores/auth'
import { router } from '../router'
import { customColumn, customFormField, dataValue, mergeCustomValues, useEntityFields } from '../entityFields'
import { loadAllPages } from '../pagination'
import { remoteTableParams } from '../remoteTable'
import { makeFilterValues } from '../suggestions'

const auth = useAuthStore()
const rows = ref<any[]>([])
const devices = ref<any[]>([])
const software = ref<any[]>([])
const toast = ref('')
const toastError = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const profiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const table = ref<InstanceType<typeof DataTable> | null>(null)
const { fields: testFields, loadFields } = useEntityFields('tests')
const { importState, runImport, closeImport } = useImportProgress()

/*
 * The field a "View …" cell is currently showing. Notes and misc data are the
 * two columns that made this grid unreadable — a JSON blob flattened onto one
 * line — so the cells offer them rather than showing them.
 */
const detail = ref<{ title: string; value: any } | null>(null)

function openDetail(title: string, value: any) {
  detail.value = { title, value }
}

// New-test modal
const showNew = ref(false)
const creating = ref(false)
const newTest = ref<Record<string, any>>({})

/*
 * Device and software are typed, not scrolled: with a hundred devices and fifty
 * software a dropdown is a haystack. Each field autocompletes over what exists and
 * confirms underneath what it resolved to, so a mistyped name is visible before
 * the form is submitted rather than after.
 */

/** How the server matches names: trimmed and case-folded. */
const key = (v: any) => String(v ?? '').trim().toLowerCase()

/*
 * Devices by their folded unique_id, built once per load rather than scanned
 * per keystroke. `matchedDevice` recomputes on every character typed into the
 * device field, and a linear find over a few thousand rows on each of those is
 * work that does not need doing twice.
 */
const devicesByUniqueId = computed(() => {
  const index = new Map<string, any>()
  for (const device of devices.value) index.set(key(device.unique_id), device)
  return index
})

const matchedDevice = computed(() => {
  const k = key(newTest.value.device_unique_id)
  return k ? devicesByUniqueId.value.get(k) || null : null
})

/*
 * Software grouped by folded name, newest version first. Same reasoning as the
 * device index: this is read on every keystroke in the software field, and the
 * grouping does not change between them.
 */
const softwareByName = computed(() => {
  const index = new Map<string, any[]>()
  for (const item of software.value) {
    const k = key(item.name)
    const group = index.get(k)
    if (group) group.push(item)
    else index.set(k, [item])
  }
  for (const group of index.values()) {
    group.sort((a: any, b: any) => (a.created_at < b.created_at ? 1 : -1))
  }
  return index
})

/** Every version of the software whose name was typed, newest first. */
const matchedSoftwareVersions = computed(() => {
  const k = key(newTest.value.software_name)
  return k ? softwareByName.value.get(k) || [] : []
})

/**
 * The exact software row this test will point at.
 *
 * A test records one specific version, so a typed version that matches a real
 * one binds to it. A version that matches nothing still records — you may be
 * testing a build that was never catalogued — and binds to the current version.
 */
const matchedSoftware = computed(() => {
  const versions = matchedSoftwareVersions.value
  if (!versions.length) return null
  const wanted = key(newTest.value.software_version)
  return (wanted && versions.find((t: any) => key(t.version) === wanted)) || versions[0]
})

// The suggestion is the device's identity and nothing else — the make and
// model are shown in the hint once it resolves, where they cannot be mistaken
// for the value.
const deviceOptions = computed(() =>
  devices.value.map((d: any) => ({ value: d.unique_id, label: d.unique_id })),
)

// One entry per software name: the version is picked in its own field.
const softwareOptions = computed(() => {
  const seen = new Map<string, string>()
  for (const t of software.value) if (!seen.has(key(t.name))) seen.set(key(t.name), t.name)
  return [...seen.values()].sort().map((n) => ({ value: n, label: n }))
})

function deviceHint(): string {
  if (!newTest.value.device_unique_id) return 'Type to search the inventory.'
  const d = matchedDevice.value
  if (!d) return `No device matches "${newTest.value.device_unique_id}".`
  return `${[d.make, d.model].filter(Boolean).join(' ') || 'no make/model'} — ${d.status}`
}

function softwareHint(): string {
  if (!newTest.value.software_name) return 'Type to search. Names are case-insensitive.'
  const versions = matchedSoftwareVersions.value
  if (!versions.length) return `No software matches "${newTest.value.software_name}".`
  return `${versions.length} version${versions.length === 1 ? '' : 's'}, currently ${versions[0].version || 'unversioned'}.`
}

function versionHint(): string {
  const versions = matchedSoftwareVersions.value
  if (!versions.length) return 'Pick software first.'
  const current = versions[0].version || 'the unversioned build'
  const typed = key(newTest.value.software_version)
  if (!typed) return `The build that was run. Blank records against ${current}, the current version.`
  if (!versions.some((t: any) => key(t.version) === typed)) {
    return `Not a catalogued version — the run is recorded against ${current}.`
  }
  return 'The build that was run.'
}

const matchedComponents = computed(() => matchedSoftware.value?.bundle_components || [])
const componentNames = computed(() => [...new Set(matchedComponents.value.map((item: any) => item.name))])
const matchedComponentVersions = computed(() => matchedComponents.value.filter(
  (item: any) => key(item.name) === key(newTest.value.component_name),
))

const BASE_NEW_TEST_FIELDS = computed<FormField[]>(() => [
  {
    key: 'device_unique_id',
    label: 'Device',
    type: 'datalist',
    required: true,
    placeholder: 'dev-0021',
    options: deviceOptions.value,
    hint: deviceHint(),
  },
  {
    key: 'software_name',
    label: 'Software',
    type: 'datalist',
    required: true,
    options: softwareOptions.value,
    hint: softwareHint(),
  },
  {
    key: 'software_version',
    label: 'Software Version',
    type: 'datalist',
    // Unversioned rows have nothing to suggest; they are reached by leaving
    // this blank, which records against the current version.
    options: matchedSoftwareVersions.value
      .filter((t: any) => t.version)
      .map((t: any) => ({ value: t.version, label: t.version })),
    hint: versionHint(),
  },
  {
    key: 'component_name',
    label: 'Component',
    type: 'select',
    options: componentNames.value.map((name: any) => ({ value: name, label: name })),
    hint: 'Optional. Select a component defined beneath this software suite.',
  },
  {
    key: 'component_version',
    label: 'Component Version',
    type: 'select',
    options: matchedComponentVersions.value
      .filter((item: any) => item.version)
      .map((item: any) => ({ value: item.version, label: item.version })),
    hint: 'Optional version of the suite component tested.',
  },
  {
    key: 'outcome',
    label: 'Outcome',
    type: 'select',
    required: true,
    options: ['pass', 'fail', 'warn'].map((v) => ({ value: v, label: v })),
  },
  {
    key: 'tag',
    label: 'Tag',
    type: 'select',
    required: true,
    options: ['adhoc', 'acceptance', 'end-to-end', 'automated'].map((v) => ({ value: v, label: v })),
  },
  { key: 'notes', label: 'Notes', type: 'textarea' },
  { key: 'misc_data', label: 'Misc data (JSON)', type: 'json', placeholder: '{}' },
])

const NEW_TEST_FIELDS = computed<FormField[]>(() => {
  const fields = testFields.value
    .filter((field) => field.writable && !['component_name', 'component_version'].includes(field.key))
    .map((field) => {
      const builtIn = BASE_NEW_TEST_FIELDS.value.find((item) => item.key === field.key)
      return builtIn ? { ...builtIn, label: field.label } : customFormField(field)
    })
  if (!matchedComponents.value.length) return fields

  const componentFields = BASE_NEW_TEST_FIELDS.value.filter(
    (field) => field.key === 'component_name' || field.key === 'component_version',
  )
  const softwareVersionIndex = fields.findIndex((field) => field.key === 'software_version')
  const softwareIndex = fields.findIndex((field) => field.key === 'software_name')
  fields.splice(Math.max(softwareVersionIndex, softwareIndex) + 1, 0, ...componentFields)
  return fields
})

/*
 * Opening the dialog has to fetch the fleet first, which is not instant and
 * used to happen with nothing on screen to say so — the button simply sat
 * there, and if the fetch failed it sat there forever, the rejection going
 * nowhere. It now says it is working and reports a failure to the toast.
 */
const openingNew = ref(false)

async function openNew() {
  if (openingNew.value) return
  if (!referencesLoaded.value) {
    openingNew.value = true
    try {
      await loadReferences()
    } catch (e: any) {
      showToast(e?.message || 'Devices and software could not be loaded.', true)
      return
    } finally {
      openingNew.value = false
    }
  }
  newTest.value = {
    device_unique_id: '',
    software_name: '',
    software_version: '',
    component_name: '',
    component_version: '',
    outcome: 'pass',
    tag: 'adhoc',
    notes: '',
    misc_data: '{}',
  }
  showNew.value = true
}

/** Naming software fills in its current version, which stays editable. */
function onNewTestChange(key_: string) {
  if (key_ === 'software_name') {
    newTest.value.software_version = matchedSoftwareVersions.value[0]?.version || ''
    newTest.value.component_name = ''
    newTest.value.component_version = ''
  } else if (key_ === 'software_version') {
    newTest.value.component_name = ''
    newTest.value.component_version = ''
  } else if (key_ === 'component_name') {
    newTest.value.component_version = matchedComponentVersions.value[0]?.version || ''
  }
}

// Rows with unsaved changes: row id -> set of changed fields
const dirty = ref<Map<string, Set<string>>>(new Map())
const selected = ref<any[]>([])
const bulkEditing = ref(false)
const savingBulkEdit = ref(false)
const bulkEditValues = ref<Record<string, any>>({})

const EDITABLE_FIELDS = new Set(['software_version', 'outcome', 'tag', 'run_at', 'notes', 'misc_data'])

function linkCell(field: string, url: (row: any) => string): any {
  return {
    field,
    editable: false,
    cellRenderer: (p: any) => {
      const el = document.createElement('div')
      if (p.value) {
        const a = document.createElement('a')
        a.className = 'grid-link'
        a.textContent = p.value
        a.href = url(p.data)
        a.onclick = (e) => {
          e.stopPropagation()
          if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
            e.preventDefault()
            router.push(url(p.data))
          }
        }
        el.appendChild(a)
      }
      return el
    },
  }
}

const baseColumns = [
  { ...linkCell('device_unique_id', (row: any) => `/devices/${encodeURIComponent(row.device_unique_id || row.device_id)}`), headerName: 'Device', minWidth: 150 },
  // A test points at one specific software version, so link by id — a bare
  // name would resolve to whichever version is current instead.
  { ...linkCell('software_name', (row: any) => `/software/${row.software_id}`), headerName: 'Software', minWidth: 150 },
  {
    field: 'software_version',
    headerName: 'Software Version',
    // Recorded per test: the software's version at the time of the run, not now.
    headerTooltip: 'The software build this run exercised',
  },
  { field: 'component_name', headerName: 'Component', editable: false },
  { field: 'component_version', headerName: 'Component Version', editable: false },
  {
    field: 'outcome',
    headerName: 'Outcome',
    cellEditor: 'agSelectCellEditor',
    cellEditorParams: { values: ['pass', 'fail', 'warn'] },
    cellStyle: (p: any) => ({
      color: p.value === 'pass' ? 'var(--green)' : p.value === 'fail' ? 'var(--red)' : 'var(--yellow)',
    }),
  },
  {
    field: 'tag',
    headerName: 'Tag',
    cellEditor: 'agSelectCellEditor',
    cellEditorParams: { values: ['adhoc', 'acceptance', 'end-to-end', 'automated'] },
  },
  dateColumn({ field: 'run_at', headerName: 'Run At' }),
  {
    field: 'notes',
    headerName: 'Notes',
    cellRenderer: detailCellRenderer('Notes', openDetail),
  },
  {
    field: 'misc_data',
    headerName: 'Misc Data',
    // AG Grid v32 infers a cell data type per column. For an object column it
    // infers "object" and then refuses the edit without a valueParser ("Cell
    // data type is 'object' but no Value Parser has been provided"), so the
    // editor's result was silently dropped. The editor returns a parsed object
    // already; there is nothing to parse.
    cellDataType: false,
    cellEditor: JsonCellEditor,
    cellEditorParams: { onInvalid: (msg: string) => showToast(msg, true) },
    cellRenderer: detailCellRenderer('Misc Data', openDetail),
    // Still needed: the CSV export and the grid's own quick filter read the
    // formatted value, and the renderer only covers what is painted.
    valueFormatter: (p: any) => (p.value && Object.keys(p.value).length ? JSON.stringify(p.value) : ''),
  },
  { field: 'created_by_username', headerName: 'Created By', editable: false },
]

const columns = computed(() => testFields.value.filter((field) => field.list_visible).map((field) => {
  const builtIn = baseColumns.find((column: any) => column.field === field.key)
  return builtIn ? { ...builtIn, headerName: field.label } : customColumn(field, 'misc_data')
}))

function showToast(msg: string, isError = false) {
  toast.value = msg
  toastError.value = isError
  setTimeout(() => (toast.value = ''), 4000)
}

// Exports read the whole table before the browser is handed anything, so the
// button has to say it is working — and a failure has to reach the toast
// rather than becoming an unhandled rejection nobody sees.
const { downloading, download } = useDownload(showToast)

// A computed (not a function called from the template): a fresh Set on every
// render would look like a change to the grid and trigger needless refreshes.
const dirtyIds = computed(() => new Set(dirty.value.keys()))

function isRowDirty(row: any): boolean {
  return !!row.id && dirty.value.has(row.id)
}

/*
 * Every device and every software version, which the New Test dialog needs in
 * full: it resolves a typed unique_id to a device, groups software by name to
 * offer its versions, and matches bundle components within the chosen version.
 * A suggestions endpoint could fill the two pickers but not the cascade.
 *
 * `referencesLoaded` rather than testing the arrays: an installation with no
 * software yet has an empty list, which is an answer, and re-fetching the
 * whole fleet every time the dialog opens because of it is not.
 */
const referencesLoaded = ref(false)

/*
 * Only the fields the dialog reads.
 *
 * It resolves a typed unique_id to a device and shows the make, model and
 * status in the hint; nothing below touches anything else. Asking for the
 * whole row meant a fleet of two thousand arrived as 2.3MB of documents —
 * every custom field and misc_data blob — to be thrown away after four values
 * were taken from each. `?fields=` is answered from the document by name, so
 * an installation that renames or adds a field keeps working; a field that
 * does not exist comes back null rather than failing the request.
 */
const DEVICE_REFERENCE_FIELDS = ['unique_id', 'make', 'model', 'status'].join(',')
/*
 * `created_at` orders the versions and `id` is what a test binds to.
 *
 * `bundle_components` is here because a test against a suite records *which
 * component* was run, and the two component fields only appear once the chosen
 * version turns out to have some. Leaving it out of the projection silently
 * removed them from the dialog — the field list asks `matchedComponents`, and
 * a version whose components were never fetched has none.
 *
 * It is the one field here the server cannot read straight off the row, so
 * asking for it costs the bundle lookup. That is the price of the feature, not
 * an oversight: the alternative is fetching components when a version is
 * chosen, which is a request per selection instead of one for the batch.
 */
const SOFTWARE_REFERENCE_FIELDS = [
  'id', 'name', 'version', 'created_at', 'bundle_components',
].join(',')

async function loadReferences() {
  const [devPage, softwarePage] = await Promise.all([
    loadAllPages<any>((page) =>
      api(`/devices?page=${page}&page_size=1000&fields=${DEVICE_REFERENCE_FIELDS}`)),
    loadAllPages<any>((page) =>
      api(`/software?page=${page}&page_size=1000&fields=${SOFTWARE_REFERENCE_FIELDS}`)),
  ])
  devices.value = devPage.items
  software.value = softwarePage.items
  referencesLoaded.value = true
}

/** Re-run filter and sort over rows whose values changed underneath them. */
async function load() { table.value?.reapplyView() }

/**
 * Re-read the list from the server, row count and all.
 *
 * For a test created, deleted or imported: a server-paged grid keeps the row
 * count it was last given, so refreshing the blocks it holds cannot show a row
 * that did not exist when it was told how many there were.
 */
async function reloadRows() { table.value?.reload() }

/*
 * Values for the column filters' checklists. The grid is server-paged, so the
 * distinct values of a column are the server's to answer; the vocabularies
 * (outcome, tag) this page already has.
 */
const filterValues = makeFilterValues({
  entity: 'tests',
  local: (colId) => {
    const field = testFields.value.find((f: any) => f.key === colId)
    if (field?.type === 'select') return field.options
    if (field?.type === 'boolean') return [true, false]
    return undefined
  },
  skip: ['misc_data', 'created_at', 'updated_at'],
})

async function loadRemoteTests(request: RemoteTableRequest) {
  const params = remoteTableParams(request)
  const page = await api<any>(`/tests?${params}`)
  rows.value = page.items
  return { rows: page.items, total: page.total }
}

function onGridReady() {
  profiles.value?.applyDefault()
}

function onCellEdit(row: any, field: string, value: any) {
  if (!row.id) return
  row[field] = value
  if (testFields.value.some((item) => item.key === field && item.writable)) {
    if (!dirty.value.has(row.id)) dirty.value.set(row.id, new Set())
    dirty.value.get(row.id)!.add(field)
  }
}

// ---------- edit test dialog ----------
// The whole run in one dialog, for the rows the grid edits a cell at a time.
// Device and software are not here: a run records what was actually run
// against what, so re-pointing it is a different test, not an edit — the API
// does not accept a change to either. They are named in the dialog's title.

const editTarget = ref<any>(null)
const savingEdit = ref(false)
const editValues = ref<Record<string, any>>({})

/** Catalogued versions of the software this test points at, for the picker. */
const editVersions = computed(() => {
  const t = editTarget.value
  if (!t) return []
  return software.value
    .filter((s: any) => s.id === t.software_id || key(s.name) === key(t.software_name))
    .filter((s: any) => s.version)
    .map((s: any) => ({ value: s.version, label: s.version }))
})

const BASE_EDIT_TEST_FIELDS = computed<FormField[]>(() => [
  {
    key: 'software_version',
    label: 'Software Version',
    type: 'datalist',
    options: editVersions.value,
    // Free text: a run may have exercised a build that was never catalogued.
    hint: 'The build this run exercised.',
  },
  {
    key: 'outcome',
    label: 'Outcome',
    type: 'select',
    required: true,
    options: ['pass', 'fail', 'warn'].map((v) => ({ value: v, label: v })),
  },
  {
    key: 'tag',
    label: 'Tag',
    type: 'select',
    required: true,
    options: ['adhoc', 'acceptance', 'end-to-end', 'automated'].map((v) => ({ value: v, label: v })),
  },
  {
    key: 'run_at',
    label: 'Run At',
    type: 'date',
    hint: 'The day the run happened. Blank leaves it unrecorded.',
  },
  { key: 'notes', label: 'Notes', type: 'textarea' },
  { key: 'misc_data', label: 'Misc data (JSON)', type: 'json', placeholder: '{}' },
])

const EDIT_TEST_FIELDS = computed<FormField[]>(() => testFields.value
  .filter((field) => field.writable && !['device_unique_id', 'software_name'].includes(field.key))
  .map((field) => {
    const builtIn = BASE_EDIT_TEST_FIELDS.value.find((item) => item.key === field.key)
    return builtIn ? { ...builtIn, label: field.label } : customFormField(field)
  }))

function openEdit(row: any) {
  // Read from the row, so unsaved cell edits carry into the dialog rather
  // than being silently reverted by it.
  editValues.value = Object.fromEntries(EDIT_TEST_FIELDS.value.map((item) => {
    const field = testFields.value.find((candidate) => candidate.key === item.key)!
    const value = dataValue(row, field, 'misc_data')
    return [item.key, item.type === 'json' ? JSON.stringify(value || {}, null, 2) : value ?? '']
  }))
  editTarget.value = row
}

async function saveEdit(values: Record<string, any>) {
  const row = editTarget.value
  if (!row) return
  savingEdit.value = true
  try {
    const updated = await api<any>(`/tests/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify(mergeCustomValues(
        { misc_data: row.misc_data || {}, ...values }, testFields.value, 'misc_data',
      )),
    })
    Object.assign(row, updated)
    // The dialog wrote every field, so nothing is left pending on the row.
    dirty.value.delete(row.id)
    editTarget.value = null
    showToast('Test saved')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    savingEdit.value = false
  }
}

async function saveRow(row: any) {
  if (!row.id) return
  const fields = dirty.value.get(row.id)
  if (!fields || fields.size === 0) return
  const payload: Record<string, any> = { misc_data: row.misc_data || {} }
  for (const f of fields) payload[f] = row[f]
  const mergedPayload = mergeCustomValues(payload, testFields.value, 'misc_data')
  try {
    const updated = await api<any>(`/tests/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify(mergedPayload),
    })
    Object.assign(row, updated)
    dirty.value.delete(row.id)
    showToast('Test saved')
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteRow(row: any) {
  if (!row.id) return
  if (!confirm('Delete this test?')) return
  try {
    await api(`/tests/${row.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter((r) => toRaw(r) !== toRaw(row))
    dirty.value.delete(row.id)
    // The selection lives in the grid, and a deleted row stays ticked in it
    // until told otherwise.
    table.value?.clearSelection()
    await reloadRows()
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteSelected() {
  const ids = selected.value.filter((r) => r.id).map((r) => r.id)
  if (!ids.length) return
  if (!confirm(`Delete ${ids.length} selected test${ids.length > 1 ? 's' : ''}?`)) return
  try {
    await api('/tests/delete', { method: 'POST', body: JSON.stringify({ ids }) })
    rows.value = rows.value.filter((r) => !ids.includes(r.id))
    for (const id of ids) dirty.value.delete(id)
    // Emptying this page's copy leaves the rows ticked in the grid, which then
    // adds them to whatever is ticked next — deleting 100 and selecting 100
    // more read as 200.
    table.value?.clearSelection()
    selected.value = []
    await reloadRows()
    showToast(`Deleted ${ids.length} test${ids.length > 1 ? 's' : ''}`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

const BULK_TEST_FIELDS = computed<FormField[]>(() => testFields.value
  .filter((field) => field.visible && field.writable
    && !['device_unique_id', 'software_name'].includes(field.key))
  .map((field) => {
    const builtIn = BASE_EDIT_TEST_FIELDS.value.find((item) => item.key === field.key)
    const form = builtIn ? { ...builtIn, label: field.label } : customFormField(field)
    form.required = false
    return form
  }))

function sharedTestValue(fieldKey: string, type?: FormField['type']): any {
  const field = testFields.value.find((candidate) => candidate.key === fieldKey)!
  const values = selected.value.map((row) => dataValue(row, field, 'misc_data'))
  const encoded = values.map((value) => JSON.stringify(value ?? null))
  const shared = encoded.every((value) => value === encoded[0]) ? values[0] : undefined
  return type === 'json' ? JSON.stringify(shared || {}, null, 2) : shared ?? ''
}

function openBulkEdit() {
  bulkEditValues.value = Object.fromEntries(BULK_TEST_FIELDS.value.map((field) => [
    field.key, sharedTestValue(field.key, field.type),
  ]))
  bulkEditing.value = true
}

async function saveBulkEdit(values: Record<string, any>) {
  savingBulkEdit.value = true
  let updated = 0
  const errors: string[] = []
  for (const row of selected.value) {
    try {
      const result = await api<any>(`/tests/${row.id}`, {
        method: 'PATCH',
        body: JSON.stringify(mergeCustomValues(
          { misc_data: row.misc_data || {}, ...values }, testFields.value, 'misc_data',
        )),
      })
      Object.assign(row, result)
      dirty.value.delete(row.id)
      updated++
    } catch (e: any) {
      errors.push(`${row.device_unique_id} / ${row.software_name}: ${e.message}`)
    }
  }
  table.value?.refreshRows(selected.value.map((row) => row.id))
  savingBulkEdit.value = false
  if (errors.length) {
    showToast(`Updated ${updated}; ${errors.length} failed — ${errors.slice(0, 3).join('; ')}`, true)
  } else {
    bulkEditing.value = false
    showToast(`Updated ${updated} selected test${updated === 1 ? '' : 's'}`)
  }
}

async function saveNewTest(values: Record<string, any>) {
  // The fields hold what was typed; the API wants ids.
  const device = matchedDevice.value
  const software = matchedSoftware.value
  if (!device) {
    showToast(`No device matches "${values.device_unique_id}"`, true)
    return
  }
  if (!software) {
    showToast(`No software matches "${values.software_name}"`, true)
    return
  }
  const merged = mergeCustomValues(values, testFields.value, 'misc_data')
  const payload = {
    device_id: device.id,
    software_id: software.id,
    software_version: merged.software_version || software.version,
    component_name: merged.component_name || null,
    component_version: merged.component_version || null,
    outcome: merged.outcome,
    tag: merged.tag,
    notes: merged.notes,
    run_at: merged.run_at || null,
    misc_data: merged.misc_data,
  }
  creating.value = true
  try {
    const created = await api<any>('/tests', { method: 'POST', body: JSON.stringify(payload) })
    // A row that did not exist a moment ago: re-read the list rather than
    // refresh the window the grid is already holding.
    await reloadRows()
    showNew.value = false
    showToast('Test recorded')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creating.value = false
  }
}

function exportAs(format: string) {
  download(`/tests/export?format=${format}`, `tests.${format}`, 'export')
}

/** A blank CSV with base fields; users may append misc-data columns. */
function downloadTemplate() {
  download('/tests/template', 'tests-template.csv', 'template')
}

async function onImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const result = await runImport('/tests/import', file)
    // An import adds rows, so the row count has moved.
    if (result) await reloadRows()
  } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}

onMounted(loadFields)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2>Tests</h2>
      <div class="toolbar">
        <button
          v-if="auth.can(PERMISSION.testsEdit)"
          class="btn btn-primary"
          :disabled="openingNew"
          @click="openNew"
        >
          {{ openingNew ? 'Loading…' : '+ New test' }}
        </button>
        <!-- Outside the menu: the panel closes on click, and a file input
             unmounted mid-picker never fires `change`. -->
        <input ref="fileInput" type="file" accept=".json,.csv" style="display: none" @change="onImportFile" />
        <OverflowMenu>
          <template v-if="auth.can(PERMISSION.testsEdit)">
            <button class="btn" @click="fileInput?.click()">Import</button>
            <button
              class="btn"
              title="Download a blank CSV; extra columns are saved as misc data"
              @click="downloadTemplate"
            >
              Template
            </button>
          </template>
          <button class="btn" :disabled="downloading" @click="exportAs('json')">
            {{ downloading ? 'Preparing…' : 'Export JSON' }}
          </button>
          <button class="btn" :disabled="downloading" @click="exportAs('csv')">
            {{ downloading ? 'Preparing…' : 'Export CSV' }}
          </button>
        </OverflowMenu>
      </div>
    </div>
    <!-- The other half of the sentence Vendor Claims starts. That page says
         its rows are claims carrying no evidence; this one says these are the
         evidence. Neither is much use without the other, and a label alone
         cannot say who did the testing. -->
    <p class="muted page-intro">
      Results this team recorded, each one a run of a software version against a
      device in this inventory. These are outcomes, not claims — what a vendor
      says its software supports is under
      <router-link to="/vendor-devices">Vendor Claims</router-link>, and nothing
      there has been verified here.
    </p>
    <DataTable
      ref="table"
      :columns="columns"
      :rows="rows"
      :remote-loader="loadRemoteTests"
      :filter-values="filterValues"
      :editable="auth.can(PERMISSION.testsEdit)"
      :selectable="auth.can(PERMISSION.testsEdit)"
      :dirty-ids="dirtyIds"
      :is-row-dirty="isRowDirty"
      :row-editable="auth.can(PERMISSION.testsEdit)"
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
          entity="tests"
          :get-state="() => table?.getState()"
          :apply-state="(s) => table?.applyState(s)"
        />
      </template>
      <template #selection-actions>
        <button
          v-if="auth.can(PERMISSION.testsEdit) && selected.length"
          class="btn"
          title="Edit common values on the selected tests"
          @click="openBulkEdit"
        >
          Edit
        </button>
        <button
          v-if="auth.can(PERMISSION.testsEdit) && selected.length"
          class="btn btn-danger"
          title="Delete the selected tests"
          @click="deleteSelected"
        >
          Delete
        </button>
      </template>
    </DataTable>

    <FormModal
      v-if="bulkEditing"
      :title="`Edit ${selected.length} selected tests`"
      :fields="BULK_TEST_FIELDS"
      :values="bulkEditValues"
      :busy="savingBulkEdit"
      selective
      submit-label="Apply changes"
      @submit="saveBulkEdit"
      @cancel="bulkEditing = false"
    />

    <FormModal
      v-if="editTarget"
      :title="`Edit Test — ${editTarget.device_unique_id} / ${editTarget.software_name}`"
      :fields="EDIT_TEST_FIELDS"
      :values="editValues"
      :busy="savingEdit"
      submit-label="Save changes"
      @submit="saveEdit"
      @cancel="editTarget = null"
    />
    <FormModal
      v-if="showNew"
      title="New Test"
      :fields="NEW_TEST_FIELDS"
      :values="newTest"
      :busy="creating"
      submit-label="Record test"
      @submit="saveNewTest"
      @change="onNewTestChange"
      @cancel="showNew = false"
    />

    <DetailModal
      v-if="detail"
      :title="detail.title"
      :value="detail.value"
      @close="detail = null"
    />
    <ImportProgressModal :state="importState" @close="closeImport" />

    <div v-if="toast" class="toast" :class="{ 'toast-error': toastError }">{{ toast }}</div>
  </div>
</template>

<style scoped>
/* Matches the Vendor Claims page, which carries the other half of the
   contrast: the two intros are meant to be read against each other. */
.page-intro { margin: 0 0 12px; max-width: 70ch; }
</style>
