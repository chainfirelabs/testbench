<script setup lang="ts">
import { computed, onMounted, ref, toRaw } from 'vue'
import DataTable, { type RemoteTableRequest } from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import FormModal, { type FormField } from '../components/FormModal.vue'
import OverflowMenu from '../components/OverflowMenu.vue'
import JsonCellEditor from '../components/JsonCellEditor.vue'
import DetailModal from '../components/DetailModal.vue'
import TestArtifactsModal from '../components/TestArtifactsModal.vue'
import TestOutputModal, { type TestOutputMeta } from '../components/TestOutputModal.vue'
import { NO_STORAGE, TEST_ARTIFACTS_FIELD, TEST_OUTPUT_FIELD, newTestFileFields, testArtifactsColumn, testOutputColumn, type TestFileStorage } from '../testArtifacts'
import { TEST_RUN_FIELD, TEST_RUN_FILES_FIELD, matchRun, newTestRunFields } from '../testRuns'
import ImportProgressModal from '../components/ImportProgressModal.vue'
import { detailCellRenderer } from '../detail'
import { dateColumn } from '../dates'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { tableExportRequest } from '../tableExport'
import { useImportProgress } from '../importProgress'
import { PERMISSION, useAuthStore } from '../stores/auth'
import { router } from '../router'
import { customColumn, customFormField, dataValue, mergeCustomValues, useEntityFields } from '../entityFields'
import { loadAllPages } from '../pagination'
import { remoteTableApi } from '../remoteTableApi'
import { remoteTableParams } from '../remoteTable'
import { makeFilterValues } from '../suggestions'

/*
 * Set when this list is the one on a test run's page: it shows only that run's
 * tests, and a test recorded from it joins the run.
 */
const props = defineProps<{ testRunId?: string }>()
const emit = defineEmits<{ (e: 'changed'): void }>()
const runScope = () => (props.testRunId ? { test_run_id: props.testRunId } : {})

const artifactTest = ref<any | null>(null)
function updateArtifactCount(count: number) {
  if (!artifactTest.value) return
  artifactTest.value.artifact_count = count
  table.value?.gridApi?.refreshCells({ columns: ['artifact_count'], force: true })
}
const outputTest = ref<any | null>(null)
function updateOutput(output: TestOutputMeta | null) {
  if (!outputTest.value) return
  outputTest.value.output_filename = output?.filename ?? null
  outputTest.value.output_size_bytes = output?.size_bytes ?? null
  table.value?.gridApi?.refreshCells({ columns: ['output_filename'], force: true })
}
const auth = useAuthStore()
const rows = ref<any[]>([])
const devices = ref<any[]>([])
const software = ref<any[]>([])
const toast = ref('')
const toastError = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const profiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const table = ref<InstanceType<typeof DataTable> | null>(null)
const { fields: testFields, ready: columnsReady, loadFields } = useEntityFields('tests')
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
 * The test runs a new test can join, newest first, read each time the dialog
 * opens; and on a run's page, that run, which the test always joins.
 */
const runChoices = ref<any[]>([])
const fixedRun = ref<any | null>(null)

/** The schema's fields, then the test run and its files, then the test's files. */
const NEW_TEST_FORM = computed<FormField[]>(() => [
  ...NEW_TEST_FIELDS.value,
  ...newTestRunFields(runChoices.value, newTest.value, {
    fixedRun: fixedRun.value,
    storageEnabled: !!fileStorage.value?.storage_enabled,
    maxBytes: fileStorage.value?.artifact_max_bytes,
    noStorage: NO_STORAGE,
  }),
  ...newTestFileFields(fileStorage.value, testFields.value),
])

/** Read the runs on offer; a failure leaves the field free text, still able to start a run. */
async function loadRunChoices() {
  try {
    runChoices.value = (await api<{ items: any[] }>('/test-runs?page_size=1000&sort=created_at&order=desc')).items
  } catch {
    runChoices.value = []
  }
  if (props.testRunId) {
    fixedRun.value = runChoices.value.find((run) => run.id === props.testRunId)
      ?? await api<any>(`/test-runs/${encodeURIComponent(props.testRunId)}`).catch(() => ({ id: props.testRunId, name: '' }))
  }
}

/**
 * The run a dialog's Test Run field names: `keep` when it still names the run
 * the test is in (two runs can share a name), an existing run, or a new one
 * started under that name. Null when blank. `started` is set to a run this
 * call created, for the caller to remove if what it was for is refused.
 */
async function resolveRunId(typed: any, keep?: { id: string; name: string } | null): Promise<{ id: string | null; started: string | null }> {
  const name = String(typed ?? '').trim()
  if (!name) return { id: null, started: null }
  if (keep?.id && keep.name.trim().toLowerCase() === name.toLowerCase()) return { id: keep.id, started: null }
  const match = matchRun(name, runChoices.value)
  if (match) return { id: match.id, started: null }
  const run = await api<any>('/test-runs', { method: 'POST', body: JSON.stringify({ name }) })
  return { id: run.id, started: run.id }
}

/** The edit dialog: the schema's fields, then the test run and files to share with it. */
const EDIT_TEST_FORM = computed<FormField[]>(() => [
  ...EDIT_TEST_FIELDS.value,
  ...newTestRunFields(runChoices.value, editValues.value, {
    currentName: editTarget.value?.test_run_name,
    storageEnabled: !!fileStorage.value?.storage_enabled,
    maxBytes: fileStorage.value?.artifact_max_bytes,
    noStorage: NO_STORAGE,
  }),
])

/*
 * Opening the dialog has to fetch the fleet first, which is not instant and
 * used to happen with nothing on screen to say so — the button simply sat
 * there, and if the fetch failed it sat there forever, the rejection going
 * nowhere. It now says it is working and reports a failure to the toast.
 */
const openingNew = ref(false)
// Whether new tests can carry output and artifacts. Read once, with the fleet.
const fileStorage = ref<TestFileStorage | null>(null)

async function openNew() {
  if (openingNew.value) return
  await loadRunChoices()
  if (!referencesLoaded.value) {
    openingNew.value = true
    try {
      const [, storage] = await Promise.all([
        loadReferences(),
        // Not worth failing the dialog over: without it the file fields say
        // storage is unavailable, and the test itself can still be recorded.
        api<TestFileStorage>('/tests/file-storage').catch(() => null),
      ])
      fileStorage.value = storage
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
    [TEST_OUTPUT_FIELD]: [],
    [TEST_ARTIFACTS_FIELD]: [],
    [TEST_RUN_FIELD]: fixedRun.value?.name || '',
    [TEST_RUN_FILES_FIELD]: [],
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
  { ...linkCell('test_run_name', (row: any) => `/test-runs/${encodeURIComponent(row.test_run_id)}`), headerName: 'Test Run', minWidth: 150 },
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
  const builtIn: any = field.key === TEST_OUTPUT_FIELD
    ? testOutputColumn((row) => { outputTest.value = row }, () => auth.can(PERMISSION.testsEdit))
    : field.key === TEST_ARTIFACTS_FIELD
      ? testArtifactsColumn((row) => { artifactTest.value = row })
      : baseColumns.find((column: any) => column.field === field.key)
  const column = builtIn ? { ...builtIn, headerName: field.label } : customColumn(field, 'misc_data')
    if (field.type === 'json' || field.sensitive || ['misc_data', 'created_at', 'updated_at'].includes(field.key)) column.filter = false
    return column
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
 * status in the hint, and `id` is what the new test binds to. A projection
 * returns only what it names: without `id` the device was found, the hint
 * looked right, and the test went to the API with no device at all. Asking for the
 * whole row meant a fleet of two thousand arrived as 2.3MB of documents —
 * every custom field and misc_data blob — to be thrown away after four values
 * were taken from each. `?fields=` is answered from the document by name, so
 * an installation that renames or adds a field keeps working; a field that
 * does not exist comes back null rather than failing the request.
 */
const DEVICE_REFERENCE_FIELDS = ['id', 'unique_id', 'make', 'model', 'status'].join(',')
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
    if (field?.type === 'select') return [null, ...field.options]
    if (field?.type === 'boolean') return [null, true, false]
    return undefined
  },
  skip: ['misc_data', 'created_at', 'updated_at'],
  base: runScope,
})

let latestTestsRequest = 0
async function loadRemoteTests(request: RemoteTableRequest) {
  const requestId = ++latestTestsRequest
  const params = remoteTableParams(request, runScope())
  const page = await remoteTableApi(`/tests`, params)
  if (requestId === latestTestsRequest) rows.value = page.items
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

/*
 * The component is picked, not typed: the API takes a component of the test's
 * software by id, so the name and version fields the grid shows are replaced
 * by one choice over that software's components. Software without components
 * has nothing to pick, and offers no field.
 */
const COMPONENT_KEYS = ['component_name', 'component_version']
const editComponents = ref<any[]>([])

const EDIT_TEST_FIELDS = computed<FormField[]>(() => {
  const fields = testFields.value
    .filter((field) => field.writable && !['device_unique_id', 'software_name', ...COMPONENT_KEYS].includes(field.key))
    .map((field) => {
      const builtIn = BASE_EDIT_TEST_FIELDS.value.find((item) => item.key === field.key)
      return builtIn ? { ...builtIn, label: field.label } : customFormField(field)
    })
  const offered = testFields.value.some((field) => field.key === 'component_name' && field.writable)
  if (offered && editComponents.value.length) {
    const label = testFields.value.find((field) => field.key === 'component_name')?.label || 'Component'
    const at = fields.findIndex((field) => field.key === 'software_version') + 1
    fields.splice(at, 0, {
      key: 'component_id', label, type: 'select',
      options: editComponents.value.map((item: any) => ({ value: item.id, label: item.version ? `${item.name} ${item.version}` : item.name })),
      hint: 'Optional. The component of this software suite the run tested.',
    })
  }
  return fields
})

async function openEdit(row: any) {
  editComponents.value = []
  await Promise.all([
    loadRunChoices(),
    api<any>(`/software/${encodeURIComponent(row.software_id)}`)
      .then((item) => (editComponents.value = item.bundle_components || []))
      .catch(() => null),
    fileStorage.value ? null : api<TestFileStorage>('/tests/file-storage').then((s) => (fileStorage.value = s)).catch(() => null),
  ])
  // Read from the row, so unsaved cell edits carry into the dialog rather
  // than being silently reverted by it.
  editValues.value = {
    ...Object.fromEntries(EDIT_TEST_FIELDS.value.filter((item) => item.key !== 'component_id').map((item) => {
      const field = testFields.value.find((candidate) => candidate.key === item.key)!
      const value = dataValue(row, field, 'misc_data')
      return [item.key, item.type === 'json' ? JSON.stringify(value || {}, null, 2) : value ?? '']
    })),
    ...(editComponents.value.length ? { component_id: row.component_id || '' } : {}),
    [TEST_RUN_FIELD]: row.test_run_name || '',
    [TEST_RUN_FILES_FIELD]: [],
  }
  editTarget.value = row
}

async function saveEdit(values: Record<string, any>) {
  const row = editTarget.value
  if (!row) return
  const { [TEST_RUN_FIELD]: typedRun, [TEST_RUN_FILES_FIELD]: runFiles = [], component_id: componentId, ...fields } = values
  // Sent only when it changed; '' is "(none)".
  const component = componentId === undefined || (componentId || null) === (row.component_id || null)
    ? {} : { component_id: componentId || null }
  savingEdit.value = true
  let startedRun: string | null = null
  try {
    const current = row.test_run_id ? { id: row.test_run_id, name: row.test_run_name || '' } : null
    const resolved = await resolveRunId(typedRun, current)
    startedRun = resolved.started
    const runId = resolved.id
    const moved = runId !== (row.test_run_id ?? null)
    let updated: any
    try {
      updated = await api<any>(`/tests/${row.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ...mergeCustomValues({ misc_data: row.misc_data || {}, ...fields }, testFields.value, 'misc_data'),
          ...component,
          ...(moved ? { test_run_id: runId } : {}),
        }),
      })
    } catch (e) {
      if (startedRun) await api(`/test-runs/${encodeURIComponent(startedRun)}`, { method: 'DELETE' }).catch(() => {})
      throw e
    }
    const failed = runId ? await uploadRunFiles(runId, runFiles) : []
    Object.assign(row, updated)
    // The dialog wrote every field, so nothing is left pending on the row.
    dirty.value.delete(row.id)
    editTarget.value = null
    if (moved || runFiles.length) {
      // On a run's page a test moved out leaves the list, and the run's
      // counts and files change; elsewhere the shared-file count does.
      await reloadRows()
      emit('changed')
    }
    if (failed.length) showToast(`Test saved, but ${failed.join('; ')}`, true)
    else showToast('Test saved')
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
    emit('changed')
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
    emit('changed')
    showToast(`Deleted ${ids.length} test${ids.length > 1 ? 's' : ''}`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

/*
 * No component: the selected tests can be of different software, each with
 * its own components. The test run is offered instead, to move the selection
 * into one run together, or (blank) out of their runs.
 */
const BULK_TEST_FIELDS = computed<FormField[]>(() => [
  ...testFields.value
    .filter((field) => field.visible && field.writable
      && !['device_unique_id', 'software_name', ...COMPONENT_KEYS].includes(field.key))
    .map((field) => {
      const builtIn = BASE_EDIT_TEST_FIELDS.value.find((item) => item.key === field.key)
      const form = builtIn ? { ...builtIn, label: field.label } : customFormField(field)
      form.required = false
      return form
    }),
  bulkRunField(),
])

function bulkRunField(): FormField {
  const [field] = newTestRunFields(runChoices.value, bulkEditValues.value, { storageEnabled: false, noStorage: '' })
  const typed = String(bulkEditValues.value[TEST_RUN_FIELD] ?? '').trim()
  return typed ? field : { ...field, hint: 'Blank takes the selected tests out of their test runs.' }
}

function sharedTestValue(fieldKey: string, type?: FormField['type']): any {
  const field = testFields.value.find((candidate) => candidate.key === fieldKey)!
  const values = selected.value.map((row) => dataValue(row, field, 'misc_data'))
  const encoded = values.map((value) => JSON.stringify(value ?? null))
  const shared = encoded.every((value) => value === encoded[0]) ? values[0] : undefined
  return type === 'json' ? JSON.stringify(shared || {}, null, 2) : shared ?? ''
}

async function openBulkEdit() {
  await loadRunChoices()
  bulkEditValues.value = Object.fromEntries(BULK_TEST_FIELDS.value.map((field) => [
    field.key, sharedTestValue(field.key, field.type),
  ]))
  bulkEditing.value = true
}

async function saveBulkEdit(values: Record<string, any>) {
  savingBulkEdit.value = true
  let updated = 0
  const errors: string[] = []
  // Checked fields only arrive; the run, when checked, is resolved once for all.
  const { [TEST_RUN_FIELD]: typedRun, ...fields } = values
  let run: Record<string, any> = {}
  if (TEST_RUN_FIELD in values) {
    try {
      run = { test_run_id: (await resolveRunId(typedRun)).id }
    } catch (e: any) {
      savingBulkEdit.value = false
      showToast(e.message, true)
      return
    }
  }
  for (const row of selected.value) {
    try {
      const result = await api<any>(`/tests/${row.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ...mergeCustomValues({ misc_data: row.misc_data || {}, ...fields }, testFields.value, 'misc_data'),
          ...run,
        }),
      })
      Object.assign(row, result)
      dirty.value.delete(row.id)
      updated++
    } catch (e: any) {
      errors.push(`${row.device_unique_id} / ${row.software_name}: ${e.message}`)
    }
  }
  table.value?.refreshRows(selected.value.map((row) => row.id))
  if (TEST_RUN_FIELD in values) {
    await reloadRows()
    emit('changed')
  }
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
  const outputFile: File | undefined = (values[TEST_OUTPUT_FIELD] || [])[0]
  const artifactFiles: File[] = values[TEST_ARTIFACTS_FIELD] || []
  const runFiles: File[] = values[TEST_RUN_FILES_FIELD] || []
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
  // A run started for this test, removed again if the test is refused.
  let startedRun: string | null = null
  try {
    let runId: string | null = props.testRunId || null
    if (!runId) ({ id: runId, started: startedRun } = await resolveRunId(values[TEST_RUN_FIELD]))
    let created: any
    try {
      created = await api<any>('/tests', { method: 'POST', body: JSON.stringify({ ...payload, test_run_id: runId }) })
    } catch (e) {
      if (startedRun) await api(`/test-runs/${encodeURIComponent(startedRun)}`, { method: 'DELETE' }).catch(() => {})
      throw e
    }
    const failed = await uploadNewTestFiles(created.id, outputFile, artifactFiles)
    if (runId) failed.push(...await uploadRunFiles(runId, runFiles))
    // A row that did not exist a moment ago: re-read the list rather than
    // refresh the window the grid is already holding.
    await reloadRows()
    emit('changed')
    showNew.value = false
    // The test exists either way; a file that did not make it can be added
    // from the row's Output or Artifacts button.
    if (failed.length) showToast(`Test recorded, but ${failed.join('; ')}`, true)
    else showToast('Test recorded')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creating.value = false
  }
}

/** Share files with every test in a run, returning what failed, in words. */
async function uploadRunFiles(runId: string, files: File[]): Promise<string[]> {
  const failed: string[] = []
  for (const file of files) {
    const body = new FormData()
    body.append('file', file)
    try {
      await api(`/test-runs/${encodeURIComponent(runId)}/artifacts`, { method: 'POST', body })
    } catch (e: any) {
      failed.push(`${file.name} was not shared with the test run: ${e?.message || 'request failed'}`)
    }
  }
  return failed
}

/** Upload a just-created test's files, returning what failed, in words. */
async function uploadNewTestFiles(testId: string, output: File | undefined, artifacts: File[]): Promise<string[]> {
  const base = `/tests/${encodeURIComponent(testId)}`
  const failed: string[] = []
  const send = async (file: File, path: string, method: string) => {
    const body = new FormData()
    body.append('file', file)
    try {
      await api(path, { method, body })
    } catch (e: any) {
      failed.push(`${file.name} was not uploaded: ${e?.message || 'request failed'}`)
    }
  }
  if (output) await send(output, `${base}/output`, 'PUT')
  for (const file of artifacts) await send(file, `${base}/artifacts`, 'POST')
  return failed
}

function exportAs(format: string) {
  const request = tableExportRequest('/tests', table.value?.getState(), { format, ...runScope() }, selected.value.map((row) => row.id).filter(Boolean))
  download(request.path, `tests.${format}`, 'export', request.options)
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
  <div :class="testRunId ? 'embedded-tests' : 'page'">
    <div class="page-header">
      <h3 v-if="testRunId">Tests in this run</h3>
      <h2 v-else>Tests</h2>
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
          <template v-if="auth.can(PERMISSION.testsEdit) && !testRunId">
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
            {{ downloading ? 'Preparing…' : selected.length ? 'Export selected JSON' : 'Export JSON' }}
          </button>
          <button class="btn" :disabled="downloading" @click="exportAs('csv')">
            {{ downloading ? 'Preparing…' : selected.length ? 'Export selected CSV' : 'Export CSV' }}
          </button>
        </OverflowMenu>
      </div>
    </div>
    <!-- The other half of the sentence Vendor Claims starts. That page says
         its rows are claims carrying no evidence; this one says these are the
         evidence. Neither is much use without the other, and a label alone
         cannot say who did the testing. -->
    <p v-if="!testRunId" class="muted page-intro">
      Results this team recorded, each one a run of a software version against a
      device in this inventory. These are outcomes, not claims — what a vendor
      says its software supports is under
      <router-link to="/vendor-devices">Vendor Claims</router-link>, and nothing
      there has been verified here.
    </p>
    <TestArtifactsModal v-if="artifactTest" :test-id="artifactTest.id"
      @close="artifactTest = null" @updated="updateArtifactCount" />
    <TestOutputModal v-if="outputTest" :test-id="outputTest.id"
      :title="`Output — ${outputTest.device_unique_id} / ${outputTest.software_name}`"
      @close="outputTest = null" @updated="updateOutput" />
    <DataTable
      ref="table"
      :columns="columns"
      :schema-visible-columns="['output_filename', 'artifact_count']"
      :columns-ready="columnsReady"
      :rows="rows"
      :remote-loader="loadRemoteTests"
      :filter-values="filterValues"
      :editable="auth.can(PERMISSION.testsEdit)"
      :selectable="true"
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
      :fields="EDIT_TEST_FORM"
      :values="editValues"
      :busy="savingEdit"
      submit-label="Save changes"
      @submit="saveEdit"
      @cancel="editTarget = null"
    />
    <FormModal
      v-if="showNew"
      title="New Test"
      :fields="NEW_TEST_FORM"
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
/* On a test run's page, below the run: the grid sizes itself to its parent,
   which here has to give it a height of its own. */
.embedded-tests { display: flex; flex-direction: column; height: 70vh; min-height: 420px; }
.embedded-tests .page-header h3 { margin: 0; }
</style>
