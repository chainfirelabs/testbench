<script setup lang="ts">
import { computed, onMounted, ref, toRaw } from 'vue'
import DataTable from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import FormModal, { type FormField } from '../components/FormModal.vue'
import BundleComponentsEditor from '../components/BundleComponentsEditor.vue'
import OverflowMenu from '../components/OverflowMenu.vue'
import JsonCellEditor from '../components/JsonCellEditor.vue'
import DetailModal from '../components/DetailModal.vue'
import ImportProgressModal from '../components/ImportProgressModal.vue'
import { detailCellRenderer } from '../detail'
import { api, downloadFile } from '../api/client'
import { useImportProgress } from '../importProgress'
import { useAuthStore } from '../stores/auth'
import { router } from '../router'
import { customColumn, customFormField, dataValue, mergeCustomValues, useEntityFields } from '../entityFields'

const auth = useAuthStore()
const rows = ref<any[]>([])
const toast = ref('')
const toastError = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const profiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const table = ref<InstanceType<typeof DataTable> | null>(null)
const { importState, runImport, closeImport } = useImportProgress()
const { fields: softwareFields, loadFields } = useEntityFields('software')

/*
 * The field a "View …" cell is currently showing: these columns hold more than
 * a row can display, so the cell offers the value rather than flattening it.
 */
const detail = ref<{ title: string; value: any } | null>(null)

function openDetail(title: string, value: any) {
  detail.value = { title, value }
}

// Rows with unsaved changes: row id -> set of changed fields
const dirty = ref<Map<string, Set<string>>>(new Map())
const selected = ref<any[]>([])
const bulkEditing = ref(false)
const savingBulkEdit = ref(false)
const bulkEditValues = ref<Record<string, any>>({})

const EDITABLE_FIELDS = new Set(['version', 'misc_data'])

/**
 * A row's URL. The current version lives at /software/:name; older ones carry
 * their version. An unversioned row has nothing to put in the segment, so it
 * uses the bare name and resolves to whatever is current.
 */
function softwareHref(row: any): string {
  const name = encodeURIComponent(row.name)
  return row.is_latest === false && row.version
    ? `/software/${name}/${encodeURIComponent(row.version)}`
    : `/software/${name}`
}

const baseColumns = [
  {
    field: 'name',
    headerName: 'Name',
    minWidth: 180,
    editable: false,
    cellRenderer: (p: any) => {
      const el = document.createElement('div')
      if (p.value) {
        const a = document.createElement('a')
        a.className = 'grid-link'
        a.textContent = p.value
        // Link to this row's own version, not to whichever is current.
        const href = softwareHref(p.data)
        a.href = href
        a.onclick = (e) => {
          e.stopPropagation()
          if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
            e.preventDefault()
            router.push(href)
          }
        }
        el.appendChild(a)
      }
      return el
    },
  },
  {
    field: 'version',
    headerName: 'Version',
    // Editing this renames the version in place. Adding a *new* version is the
    // "+ New version" action on the software page, which copies the vendor devices.
    valueFormatter: (p: any) => p.value || '(unversioned)',
  },
  {
    field: 'version_count',
    headerName: 'Versions',
    editable: false,
    maxWidth: 120,
    cellRenderer: (p: any) => {
      const el = document.createElement('span')
      const n = p.value ?? 1
      el.textContent = String(n)
      if (n > 1 && !p.data?.is_latest) {
        // Only meaningful when all versions are on screen.
        el.className = 'muted'
        el.title = 'An older version of this software'
      }
      return el
    },
  },
  { field: 'vendor_device_count', headerName: 'Vendor Devices', editable: false },
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
    // Still needed: export and the quick filter read the formatted value, and
    // the renderer only covers what is painted.
    valueFormatter: (p: any) =>
      p.value && Object.keys(p.value).length ? JSON.stringify(p.value) : '',
  },
]

const componentsColumn = {
  field: 'bundle_components',
  headerName: 'Components',
  editable: false,
  minWidth: 190,
  cellDataType: false,
  valueGetter: (p: any) => (p.data?.bundle_components || [])
    .map((item: any) => `${item.name}${item.version ? ` ${item.version}` : ''}`)
    .join(', '),
  cellRenderer: (p: any) => {
    const components = p.data?.bundle_components || []
    if (!components.length) {
      const empty = document.createElement('span')
      empty.className = 'muted'
      empty.textContent = 'None'
      return empty
    }

    const select = document.createElement('select')
    select.className = 'component-list'
    select.title = components
      .map((item: any) => `${item.name}${item.version ? ` ${item.version}` : ''}`)
      .join('\n')
    select.setAttribute('aria-label', `Components for ${p.data.name}`)
    const summary = document.createElement('option')
    summary.textContent = `${components.length} component${components.length === 1 ? '' : 's'}`
    summary.value = ''
    select.appendChild(summary)
    for (const component of components) {
      const option = document.createElement('option')
      option.textContent = `${component.name}${component.version ? ` — ${component.version}` : ''}`
      option.value = component.id
      select.appendChild(option)
    }
    // This is a compact list, not an editor. Always return to the count after
    // somebody inspects an item so the cell cannot imply a selected component.
    select.onchange = () => { select.selectedIndex = 0 }
    select.onclick = (event) => event.stopPropagation()
    return select
  },
}

const columns = computed(() => {
  const configured = softwareFields.value.filter((field) => field.list_visible).map((field) => {
    const builtIn = baseColumns.find((column: any) => column.field === field.key)
    return builtIn ? { ...builtIn, headerName: field.label } : customColumn(field, 'misc_data')
  })
  const versionIndex = configured.findIndex((column: any) => column.field === 'version')
  const nameIndex = configured.findIndex((column: any) => column.field === 'name')
  configured.splice(Math.max(versionIndex, nameIndex) + 1, 0, componentsColumn)
  return configured
})

function showToast(msg: string, isError = false) {
  toast.value = msg
  toastError.value = isError
  setTimeout(() => (toast.value = ''), 4000)
}

// A computed (not a function called from the template): a fresh Set on every
// render would look like a change to the grid and trigger needless refreshes.
const dirtyIds = computed(() => new Set(dirty.value.keys()))

function isRowDirty(row: any): boolean {
  return !!row.id && dirty.value.has(row.id)
}

// Rows sharing a name are versions of the same software. The grid shows only the
// current version of each by default, so it reads as a list of software rather
// than a changelog.
const showAllVersions = ref(false)

async function load() {
  const page = await api<any>(
    `/software?page_size=500${showAllVersions.value ? '' : '&latest_only=true'}`,
  )
  rows.value = page.items
}

function toggleAllVersions() {
  showAllVersions.value = !showAllVersions.value
  load()
}

function onGridReady() {
  profiles.value?.applyDefault()
}

function onCellEdit(row: any, field: string, value: any) {
  row[field] = value
  if (row.id && softwareFields.value.some((item) => item.key === field && item.writable)) {
    if (!dirty.value.has(row.id)) dirty.value.set(row.id, new Set())
    dirty.value.get(row.id)!.add(field)
  }
}

// ---------- new software dialog ----------
/*
 * The software is filled in here and only reaches the API once it validates, so the
 * grid never shows a half-made row.
 *
 * Typing a name that already exists turns this into the dialog for
 * adding a *version* of it, because that is what it would have to be: names
 * identify software, and a second row under the same name is another version.
 * Submitting then goes through the versions endpoint, which copies the vendor
 * device list forward — creating the row directly would leave that list empty
 * with nothing to say so.
 */

const showNew = ref(false)
const creating = ref(false)
const newSoftware = ref<Record<string, any>>({})
const newBundleComponents = ref<any[]>([])
// Versions of the matched software, for the "inherit from" picker.
const matchVersions = ref<any[]>([])

// Sentinel for "create the version with an empty vendor device list".
const NO_INHERIT = '__none__'

function bundlePayload(items: any[]): any[] {
  return items.map((item, position) => {
    const name = String(item.name || '').trim()
    if (!name) throw new Error(`Bundle component ${position + 1} needs a software name`)
    return {
      id: item.id, name, version: String(item.version || '').trim(),
      required: item.required !== false, position,
    }
  })
}

/** Software names match case-insensitively, so this is how the server matches too. */
const nameKey = (v: any) => String(v ?? '').trim().toLowerCase()

/** The existing software the typed name refers to, if any. */
const matchedSoftware = computed(() => {
  const key = nameKey(newSoftware.value.name)
  return key ? rows.value.find((t) => nameKey(t.name) === key) || null : null
})

// One entry per software name, for the name field's autocomplete. The grid may be
// showing every version, so the names are deduplicated.
const softwareNames = computed(() => {
  const seen = new Map<string, string>()
  for (const t of rows.value) if (!seen.has(nameKey(t.name))) seen.set(nameKey(t.name), t.name)
  return [...seen.values()].sort().map((n) => ({ value: n, label: n }))
})

const BASE_NEW_SOFTWARE_FIELDS = computed<FormField[]>(() => {
  const match = matchedSoftware.value
  const nameField: FormField = {
    key: 'name',
    label: 'Name',
    type: 'datalist',
    required: true,
    options: softwareNames.value,
    hint: match
      ? `${match.name} already exists — ${match.version_count} version${match.version_count === 1 ? '' : 's'}. This adds another.`
      : 'Case-insensitive: Backup-Restore and backup-restore are the same software.',
  }
  if (!match) {
    return [
      nameField,
      {
        key: 'version',
        label: 'Version',
        placeholder: '2.4.1',
        hint: 'Later versions inherit this one\'s vendor devices.',
      },
      { key: 'misc_data', label: 'Misc data (JSON)', type: 'json', placeholder: '{}' },
    ]
  }
  return [
    nameField,
    {
      key: 'version',
      label: 'New version',
      required: true,
      placeholder: '2.4.1',
      hint: `Must not be one ${match.name} already has.`,
    },
    {
      key: 'inherit_from',
      label: 'Inherit from',
      type: 'select',
      required: true,
      options: [
        ...matchVersions.value.map((v: any) => ({
          value: v.id,
          label: `${v.version || '(unversioned)'}${v.is_latest ? ' — current' : ''}`,
        })),
        { value: NO_INHERIT, label: 'Nothing — start with an empty list' },
      ],
      hint: 'Vendor devices are copied, so editing them here leaves the older version alone.',
    },
    { key: 'misc_data', label: 'Misc data (JSON)', type: 'json', placeholder: '{}' },
  ]
})

const NEW_SOFTWARE_FIELDS = computed<FormField[]>(() => {
  const base = BASE_NEW_SOFTWARE_FIELDS.value
  const configured = softwareFields.value
    .filter((field) => field.writable)
    .map((field) => {
      const builtIn = base.find((item) => item.key === field.key)
      return builtIn ? { ...builtIn, label: field.label } : customFormField(field)
    })
  const inherit = base.find((item) => item.key === 'inherit_from')
  if (inherit) configured.splice(2, 0, inherit)
  return configured
})

const newSoftwareTitle = computed(() =>
  matchedSoftware.value ? `New Version of ${matchedSoftware.value.name}` : 'New Software',
)

function openNew() {
  newSoftware.value = { name: '', version: '', inherit_from: '', misc_data: '{}' }
  newBundleComponents.value = []
  matchVersions.value = []
  showNew.value = true
}

/** Load the matched software's versions so "inherit from" can list them. */
async function onNewSoftwareChange(key: string) {
  if (key !== 'name') return
  const match = matchedSoftware.value
  if (!match) {
    matchVersions.value = []
    return
  }
  if (matchVersions.value[0]?.name && nameKey(matchVersions.value[0].name) === nameKey(match.name)) {
    return // already loaded for this software
  }
  try {
    matchVersions.value = await api<any[]>(`/software/${match.id}/versions`)
    // Default to inheriting from the current version.
    newSoftware.value.inherit_from = matchVersions.value.find((v: any) => v.is_latest)?.id || ''
  } catch (e: any) {
    matchVersions.value = []
    showToast(e.message, true)
  }
}

async function createSoftware(values: Record<string, any>) {
  creating.value = true
  try {
    const merged = mergeCustomValues(values, softwareFields.value, 'misc_data')
    if (matchedSoftware.value) {
      const inherit = merged.inherit_from
      const source = inherit === NO_INHERIT ? matchedSoftware.value.id : inherit
      const created = await api<any>(`/software/${source}/versions`, {
        method: 'POST',
        body: JSON.stringify({
          version: merged.version,
          copy_vendor_devices: inherit !== NO_INHERIT,
          misc_data: merged.misc_data,
        }),
      })
      showNew.value = false
      showToast(
        `${created.name} ${created.version} created with ${created.vendor_device_count} vendor device${created.vendor_device_count === 1 ? '' : 's'}`,
      )
      // A new version changes which row represents the software, so reload.
      await load()
      return
    }
    delete merged.inherit_from
    merged.bundle_components = bundlePayload(newBundleComponents.value)
    const created = await api<any>('/software', { method: 'POST', body: JSON.stringify(merged) })
    // Newest first, so the row you just made is where you are looking.
    rows.value.unshift(created)
    showNew.value = false
    showToast(`Software ${created.name} created`)
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creating.value = false
  }
}

// ---------- edit software dialog ----------
// The same fields as the software page's Edit, so a row can be edited in full
// from the list. Grid cell editing still handles a one-field change.

const editTarget = ref<any>(null)
const savingEdit = ref(false)
const editValues = ref<Record<string, any>>({})
const editBundleComponents = ref<any[]>([])

const BASE_EDIT_SOFTWARE_FIELDS: FormField[] = [
  {
    key: 'name',
    label: 'Name',
    required: true,
    // The API moves every version with the name, because rows sharing a name
    // are one software — renaming a single row would split it in two.
    hint: 'Renaming here renames every version of this software.',
  },
  {
    key: 'version',
    label: 'Version',
    placeholder: '2.4.1',
    hint: 'Renames this version in place. Use “+ New version” to add one.',
  },
  { key: 'misc_data', label: 'Misc data (JSON)', type: 'json', placeholder: '{}' },
]

const EDIT_SOFTWARE_FIELDS = computed<FormField[]>(() => softwareFields.value
  .filter((field) => field.writable)
  .map((field) => {
    const builtIn = BASE_EDIT_SOFTWARE_FIELDS.find((item) => item.key === field.key)
    return builtIn ? { ...builtIn, label: field.label } : customFormField(field)
  }))

function openEdit(row: any) {
  // Read from the row, so unsaved cell edits carry into the dialog rather
  // than being silently reverted by it.
  editValues.value = Object.fromEntries(EDIT_SOFTWARE_FIELDS.value.map((item) => {
    const field = softwareFields.value.find((candidate) => candidate.key === item.key)!
    const value = dataValue(row, field, 'misc_data')
    return [item.key, item.type === 'json' ? JSON.stringify(value || {}, null, 2) : value ?? '']
  }))
  editBundleComponents.value = (row.bundle_components || []).map((item: any, position: number) => ({
    id: item.id, name: item.name, version: item.version || '',
    required: item.required !== false, position,
  }))
  editTarget.value = row
}

async function saveEdit(values: Record<string, any>) {
  const row = editTarget.value
  if (!row) return
  savingEdit.value = true
  try {
    const payload = mergeCustomValues(
      { misc_data: row.misc_data || {}, ...values }, softwareFields.value, 'misc_data',
    )
    payload.bundle_components = bundlePayload(editBundleComponents.value)
    const updated = await api<any>(`/software/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    Object.assign(row, updated)
    // The dialog wrote every field, so nothing is left pending on the row.
    dirty.value.delete(row.id)
    editTarget.value = null
    showToast(`Software ${updated.name} saved`)
    // A rename moves the software's other versions too, and they may be on
    // screen; only a reload shows them under the new name.
    if (updated.name !== row.name || showAllVersions.value) await load()
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    savingEdit.value = false
  }
}

async function saveRow(row: any) {
  // Every row in the grid is a saved software: new ones are created in the dialog.
  if (!row.id) return
  const fields = dirty.value.get(row.id)
  if (!fields || fields.size === 0) return
  const payload: Record<string, any> = { misc_data: row.misc_data || {} }
  for (const f of fields) payload[f] = row[f]
  const mergedPayload = mergeCustomValues(payload, softwareFields.value, 'misc_data')
  try {
    const updated = await api<any>(`/software/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify(mergedPayload),
    })
    Object.assign(row, updated)
    dirty.value.delete(row.id)
    showToast(`Software ${row.name} saved`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteRow(row: any) {
  if (!row.id) return
  if (!confirm(`Delete software "${row.name}"?`)) return
  try {
    await api(`/software/${row.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter((r) => toRaw(r) !== toRaw(row))
    dirty.value.delete(row.id)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteSelected() {
  const ids = selected.value.filter((r) => r.id).map((r) => r.id)
  if (!ids.length) return
  if (!confirm(`Delete ${ids.length} selected software record${ids.length > 1 ? 's' : ''}?`)) return
  try {
    await api('/software/delete', { method: 'POST', body: JSON.stringify({ ids }) })
    rows.value = rows.value.filter((r) => !ids.includes(r.id))
    for (const id of ids) dirty.value.delete(id)
    selected.value = []
    showToast(`Deleted ${ids.length} software record${ids.length > 1 ? 's' : ''}`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

const BULK_SOFTWARE_FIELDS = computed<FormField[]>(() => softwareFields.value
  .filter((field) => field.visible && field.writable && field.key !== 'name')
  .map((field) => {
    const builtIn = BASE_EDIT_SOFTWARE_FIELDS.find((item) => item.key === field.key)
    const form = builtIn ? { ...builtIn, label: field.label } : customFormField(field)
    form.required = false
    return form
  }))

function sharedSoftwareValue(fieldKey: string, type?: FormField['type']): any {
  const field = softwareFields.value.find((candidate) => candidate.key === fieldKey)!
  const values = selected.value.map((row) => dataValue(row, field, 'misc_data'))
  const encoded = values.map((value) => JSON.stringify(value ?? null))
  const shared = encoded.every((value) => value === encoded[0]) ? values[0] : undefined
  return type === 'json' ? JSON.stringify(shared || {}, null, 2) : shared ?? ''
}

function openBulkEdit() {
  bulkEditValues.value = Object.fromEntries(BULK_SOFTWARE_FIELDS.value.map((field) => [
    field.key, sharedSoftwareValue(field.key, field.type),
  ]))
  bulkEditing.value = true
}

async function saveBulkEdit(values: Record<string, any>) {
  savingBulkEdit.value = true
  let updated = 0
  const errors: string[] = []
  for (const row of selected.value) {
    try {
      await api(`/software/${row.id}`, {
        method: 'PATCH',
        body: JSON.stringify(mergeCustomValues(
          { misc_data: row.misc_data || {}, ...values }, softwareFields.value, 'misc_data',
        )),
      })
      dirty.value.delete(row.id)
      updated++
    } catch (e: any) {
      errors.push(`${row.name}${row.version ? ` ${row.version}` : ''}: ${e.message}`)
    }
  }
  await load()
  savingBulkEdit.value = false
  if (errors.length) {
    showToast(`Updated ${updated}; ${errors.length} failed — ${errors.slice(0, 3).join('; ')}`, true)
  } else {
    bulkEditing.value = false
    selected.value = []
    showToast(`Updated ${updated} selected software record${updated === 1 ? '' : 's'}`)
  }
}

function exportAs(format: string) {
  downloadFile(`/software/export?format=${format}`, `software.${format}`)
}

/** A blank CSV carrying exactly the columns /software/import accepts —
 * `vendor_devices` included, as a JSON array in one cell. */
function downloadTemplate() {
  downloadFile('/software/template', 'software-template.csv')
}

async function onImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const result = await runImport('/software/import', file)
    // Vendor device counts change with the import too; the grid shows them.
    if (result) await load()
  } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}

onMounted(() => Promise.all([load(), loadFields()]))
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2>Software</h2>
      <div class="toolbar">
        <button v-if="auth.canWrite" class="btn btn-primary" @click="openNew">+ New software</button>
        <!-- Outside the menu: the panel closes on click, and a file input
             unmounted mid-picker never fires `change`. -->
        <input ref="fileInput" type="file" accept=".json,.csv" style="display: none" @change="onImportFile" />
        <!-- Stays visible even on a phone: its label is the only thing saying
             which of the two sets you are currently looking at. -->
        <button
          class="btn"
          :class="{ 'btn-on': showAllVersions }"
          :title="showAllVersions ? 'Show only the current version of each' : 'Show every version of every name'"
          @click="toggleAllVersions"
        >
          {{ showAllVersions ? 'All versions' : 'Latest only' }}
        </button>
        <OverflowMenu>
          <template v-if="auth.canWrite">
            <button
              class="btn"
              title="Software and, where a row carries one, its vendor device list"
              @click="fileInput?.click()"
            >
              Import
            </button>
            <button
              class="btn"
              title="Download a blank CSV with the columns an import accepts"
              @click="downloadTemplate"
            >
              Template
            </button>
          </template>
          <button
            class="btn"
            title="Every software version, each with its vendor device list"
            @click="exportAs('json')"
          >
            Export JSON
          </button>
          <button
            class="btn"
            title="Every software version, each with its vendor device list (one JSON cell per row)"
            @click="exportAs('csv')"
          >
            Export CSV
          </button>
        </OverflowMenu>
        <FilterProfilesMenu
          ref="profiles"
          entity="software"
          :get-state="() => table?.getState()"
          :apply-state="(s) => table?.applyState(s)"
        />
      </div>
    </div>
    <DataTable
      ref="table"
      :columns="columns"
      :rows="rows"
      :editable="auth.canWrite"
      :selectable="auth.canWrite"
      :dirty-ids="dirtyIds"
      :is-row-dirty="isRowDirty"
      :row-editable="auth.canWrite"
      @cell-edit="onCellEdit"
      @edit-row="openEdit"
      @save-row="saveRow"
      @delete-row="deleteRow"
      @selection-change="(r: any[]) => (selected = r)"
      @grid-ready="onGridReady"
    >
      <template #selection-actions>
        <button
          v-if="auth.canWrite && selected.length"
          class="btn"
          title="Edit common values on the selected software"
          @click="openBulkEdit"
        >
          Edit
        </button>
        <button
          v-if="auth.canWrite && selected.length"
          class="btn btn-danger"
          title="Delete the selected software"
          @click="deleteSelected"
        >
          Delete
        </button>
      </template>
    </DataTable>
    <FormModal
      v-if="bulkEditing"
      :title="`Edit ${selected.length} selected software records`"
      :fields="BULK_SOFTWARE_FIELDS"
      :values="bulkEditValues"
      :busy="savingBulkEdit"
      selective
      submit-label="Apply changes"
      @submit="saveBulkEdit"
      @cancel="bulkEditing = false"
    />
    <FormModal
      v-if="editTarget"
      :title="`Edit Software — ${editTarget.name}`"
      :fields="EDIT_SOFTWARE_FIELDS"
      :values="editValues"
      :busy="savingEdit"
      submit-label="Save changes"
      @submit="saveEdit"
      @cancel="editTarget = null"
    >
      <BundleComponentsEditor v-model="editBundleComponents" />
    </FormModal>
    <FormModal
      v-if="showNew"
      :title="newSoftwareTitle"
      :fields="NEW_SOFTWARE_FIELDS"
      :values="newSoftware"
      :busy="creating"
      :submit-label="matchedSoftware ? 'Create version' : 'Create'"
      @submit="createSoftware"
      @change="onNewSoftwareChange"
      @cancel="showNew = false"
    >
      <BundleComponentsEditor v-if="!matchedSoftware" v-model="newBundleComponents" />
    </FormModal>

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
