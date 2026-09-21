<script setup lang="ts">
/**
 * Every vendor compatibility claim, across every software version.
 *
 * The same rows the Vendor Claims tab of a software page edits, asked the
 * other way round. That tab answers "what does this software support?"; the
 * question people actually arrive with is "does anything support this box?",
 * and that one has no software to start from — so it had no page, and the only
 * way to answer it was to open every software in turn.
 *
 * Editable, like every other list. A claim belongs to one software version, but
 * each row carries its own `software_id`, so an edit here is a PATCH against
 * that row's software rather than against a page-wide one — and creating asks
 * which version is making the claim, since the page itself cannot say.
 *
 * The two software columns are the exception and stay read-only: retyping them
 * would mean moving a claim from one version to another, which is not an edit
 * to a value but a different claim, and is not something a cell edit should do
 * silently. Every row links back to the version that makes it.
 *
 * Customize Fields edits the vendor-device catalog, which is global and has
 * never belonged to one version at all.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import DataTable, { type RemoteTableRequest } from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import OverflowMenu from '../components/OverflowMenu.vue'
import DetailModal from '../components/DetailModal.vue'
import FormModal, { type FormField } from '../components/FormModal.vue'
import ImportProgressModal from '../components/ImportProgressModal.vue'
import JsonCellEditor from '../components/JsonCellEditor.vue'
import { detailCellRenderer } from '../detail'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { router } from '../router'
import { remoteTableParams } from '../remoteTable'
import { useImportProgress } from '../importProgress'
import { invalidateSuggestions, makeFilterValues } from '../suggestions'
import { customColumn, customFormField, mergeCustomValues, useEntityFields } from '../entityFields'
import SuggestCellEditor from '../components/SuggestCellEditor.vue'
import { PERMISSION, useAuthStore } from '../stores/auth'
import { SUPPORT_LABELS, SUPPORT_VALUES } from '../constants'

const route = useRoute()
const auth = useAuthStore()
const { importState, runImport, closeImport } = useImportProgress()
const fileInput = ref<HTMLInputElement | null>(null)
const rows = ref<any[]>([])
const total = ref<number | null>(null)
const table = ref<InstanceType<typeof DataTable> | null>(null)
const profiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const { fields: vendorFields, loadFields: loadVendorFields } = useEntityFields('vendor_devices')

/** Notes run to a paragraph, which is not a cell's worth of text. */
const detail = ref<{ title: string; value: any } | null>(null)

function openDetail(title: string, value: any) {
  detail.value = { title, value }
}

/** The vendor's claim, as a word rather than a bare value. */
function supportLabel(value: string): string {
  return SUPPORT_LABELS[value] || value || ''
}

/*
 * A claim's spellings have to agree with the ones already in use — a claim
 * only matches a device if they do — so the editor offers what is there.
 */
function suggesting(col: Record<string, any>) {
  return {
    ...col,
    cellEditor: SuggestCellEditor,
    cellEditorParams: { entity: 'vendor-devices', field: col.field },
  }
}

const baseColumns = [
  {
    field: 'software_name',
    headerName: 'Software',
    minWidth: 180,
    // Read-only: see the note at the top of this file. Moving a claim between
    // versions is a different claim, not a cell edit.
    editable: false,
    cellRenderer: (p: any) => {
      const el = document.createElement('div')
      if (!p.value) return el
      const link = document.createElement('a')
      link.className = 'grid-link'
      link.textContent = p.value
      // The claim's own version, not whichever is current: a claim made by
      // 1.0 is not a claim made by 2.0, and the link has to land where the
      // row can actually be edited.
      const href = p.data?.software_is_latest || !p.data?.software_version
        ? `/software/${encodeURIComponent(p.value)}`
        : `/software/${encodeURIComponent(p.value)}/${encodeURIComponent(p.data.software_version)}`
      link.href = href
      link.onclick = (e: MouseEvent) => {
        e.stopPropagation()
        if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
          e.preventDefault()
          router.push(href)
        }
      }
      el.appendChild(link)
      return el
    },
  },
  {
    field: 'software_version',
    headerName: 'Version',
    maxWidth: 150,
    editable: false,
    cellRenderer: (p: any) => {
      const el = document.createElement('span')
      el.textContent = p.value || '(unversioned)'
      // A claim on a superseded version is still a claim, but it is not
      // current guidance, and a list that does not say so reads as if it is.
      if (p.data && !p.data.software_is_latest) {
        el.className = 'muted'
        el.title = 'An older version of this software'
      }
      return el
    },
  },
  suggesting({ field: 'make', headerName: 'Make', minWidth: 140 }),
  suggesting({ field: 'model', headerName: 'Model', minWidth: 140 }),
  suggesting({ field: 'firmware_version', headerName: 'Firmware' }),
  suggesting({ field: 'hardware_version', headerName: 'Hardware' }),
  { field: 'architecture', headerName: 'Architecture' },
  {
    field: 'support_status',
    headerName: 'Support',
    minWidth: 130,
    cellEditor: 'agSelectCellEditor',
    cellEditorParams: { values: SUPPORT_VALUES },
    valueFormatter: (p: any) => supportLabel(p.value),
    cellRenderer: (p: any) => {
      const el = document.createElement('span')
      // Global class: cell renderers build detached DOM, which scoped styles
      // never reach.
      el.className = `support-pill ${p.value || 'supported'}`
      el.textContent = supportLabel(p.value)
      return el
    },
  },
  suggesting({ field: 'source', headerName: 'Source', minWidth: 160 }),
  {
    field: 'notes',
    headerName: 'Notes',
    minWidth: 150,
    cellRenderer: detailCellRenderer('Notes', openDetail),
  },
  {
    // A catalog builtin, so the generated columns below would otherwise reach
    // it — and the generic one reads a key *inside* misc_data, which for
    // misc_data itself is nothing. It needs the treatment the software page
    // gives it, or it needs to not be generated.
    field: 'misc_data',
    headerName: 'Misc Data',
    // AG Grid infers "object" for this column and then refuses the edit
    // without a valueParser. The editor returns a parsed object already.
    cellDataType: false,
    cellEditor: JsonCellEditor,
    cellEditorParams: { onInvalid: (message: string) => showToast(message, true) },
    cellRenderer: detailCellRenderer('Misc Data', openDetail),
    // Export and the quick filter read the formatted value; the renderer only
    // covers what is painted.
    valueFormatter: (p: any) =>
      p.value && Object.keys(p.value).length ? JSON.stringify(p.value) : '',
  },
]

/*
 * The grid's columns: the two software ones, then the field catalog.
 *
 * Built from the catalog rather than listed here, so a vendor-device field an
 * installation added is a column like any other — visible, filterable and
 * editable in place. The built-in entries above supply the renderers and
 * editors the catalog cannot describe (the support pill, the notes modal, the
 * suggestion editors); anything the catalog knows about and this file does not
 * gets the generic one, reading and writing through `misc_data`.
 *
 * The global catalog, not one software's layout: this grid holds claims from
 * every software at once, so there is no one version whose overrides could say
 * which columns it should have.
 */
const columns = computed(() => {
  const software = baseColumns.filter((column: any) => column.field.startsWith('software_'))
  const claim = vendorFields.value
    .filter((field) => field.visible && field.list_visible)
    .map((field) => {
      const builtIn = baseColumns.find((column: any) => column.field === field.key)
      return builtIn ? { ...builtIn, headerName: field.label } : customColumn(field, 'misc_data')
    })
  // Before the catalog has loaded there is nothing to render but the software
  // columns, which would look like a broken grid — show the built-ins until it
  // arrives, exactly as they were before.
  return claim.length ? [...software, ...claim] : baseColumns
})

const filterValues = makeFilterValues({
  entity: 'vendor-devices',
  local: (colId) => {
    if (colId === 'support_status') return SUPPORT_VALUES
    const field = vendorFields.value.find((item: any) => item.key === colId)
    if (field?.type === 'select') return field.options
    return undefined
  },
  // The software columns come off the join, not from the vendor-device
  // catalogue, so `/suggestions/vendor-devices` has no answer for them; the
  // filter falls back to the values the loaded rows carry.
  skip: ['software_name', 'software_version', 'notes', 'misc_data'],
})

/*
 * Filters the page arrives with.
 *
 * A vendor-claim hit in the global search links here rather than to a software
 * page — the same hardware often appears in several software's lists, and the
 * point is to see all of them — so the link carries what was searched for and
 * this applies it. Only the columns the API filters on are honoured; anything
 * else in the URL is ignored rather than sent on.
 */
const URL_FILTERS = ['make', 'model', 'firmware_version', 'hardware_version',
  'architecture', 'support_status', 'software'] as const

const urlFilters = computed(() => {
  const out: Record<string, string> = {}
  for (const key of URL_FILTERS) {
    const value = route.query[key]
    if (typeof value === 'string' && value) out[key] = value
  }
  return out
})

const activeFilters = computed(() => Object.entries(urlFilters.value))

function clearFilters() {
  router.replace('/vendor-devices')
}

async function loadRemoteVendorDevices(request: RemoteTableRequest) {
  const params = remoteTableParams(request, urlFilters.value)
  const page = await api<any>(`/vendor-devices?${params}`)
  rows.value = page.items
  total.value = page.total
  return { rows: page.items, total: page.total }
}

// Arriving from the search with a different make and model is a different row
// set, so the count has to be re-read with the rows.
watch(urlFilters, () => table.value?.reload())

function onGridReady() {
  profiles.value?.applyDefault()
}

/** Whatever the grid is currently showing, as a file. */
function exportAs(format: string) {
  const state = table.value?.getState()
  const params = remoteTableParams(
    {
      startRow: 0,
      endRow: 1,
      search: state?.quick_filter || '',
      sortModel: state?.sort || [],
      filterModel: state?.filter || {},
    },
    { format, ...urlFilters.value },
  )
  // Paging parameters mean nothing to an export, which returns the whole
  // filtered set.
  params.delete('page')
  params.delete('page_size')
  download(`/vendor-devices/export?${params}`, `vendor-devices.${format}`, 'export')
}

const countLabel = computed(() =>
  total.value === null ? '' : `${total.value} claim${total.value === 1 ? '' : 's'}`,
)

/* ---------- creating, importing and the field catalog ---------- */

const toast = ref('')
const toastError = ref(false)
const creating = ref(false)
const newClaim = ref<Record<string, any> | null>(null)
const editTarget = ref<any>(null)
const editValues = ref<Record<string, any>>({})
const savingEdit = ref(false)
/** Every software version, so a new claim can name the one making it. */
const softwareVersions = ref<{ name: string; version: string }[]>([])

function showToast(message: string, isError = false) {
  toast.value = message
  toastError.value = isError
  setTimeout(() => (toast.value = ''), isError ? 5000 : 3000)
}

// A catalogue export spans every software, so it is one of the slower ones.
const { downloading, download } = useDownload(showToast)

/*
 * Every software version there is, newest name first.
 *
 * Deliberately not `latest_only`: a claim is often added to the version that
 * was tested rather than the one that shipped this morning, and a picker that
 * hid the older ones would make that impossible from this page.
 */
async function loadSoftwareVersions() {
  try {
    // Two fields, not the whole row: this fills a picker, and a software
    // list carries its bundle components and version counts with it.
    const page = await api<any>(
      '/software?page_size=1000&sort=name&order=asc&fields=name,version',
    )
    softwareVersions.value = (page.items || []).map((item: any) => ({
      name: item.name,
      version: item.version || '',
    }))
  } catch {
    // A picker that cannot be filled is reported when the dialog is opened,
    // not as a page-load error over a grid that is working perfectly well.
    softwareVersions.value = []
  }
}

onMounted(() => {
  loadVendorFields()
  if (auth.can(PERMISSION.softwareEdit)) loadSoftwareVersions()
})

/** The software names, each offered once however many versions it has. */
const softwareNameOptions = computed(() => {
  const names = [...new Set(softwareVersions.value.map((item) => item.name))].sort()
  return names.map((name) => ({ value: name, label: name }))
})

/**
 * The versions of whichever software the dialog currently names.
 *
 * Recomputed as the name changes, so the version list is never offering
 * versions of something else. A name with a single unversioned row still gets
 * an option, because "(unversioned)" is a real answer and an empty select
 * would read as a failure to load.
 */
function versionOptionsFor(name: string) {
  return softwareVersions.value
    .filter((item) => item.name === name)
    .map((item) => ({ value: item.version, label: item.version || '(unversioned)' }))
}

/** What the Software field says underneath itself as it is filled in. */
function softwareHint(): string {
  const typed = String(newClaim.value?.software_name || '').trim()
  if (!typed) return 'Type to search. The claim joins this software\'s compatibility list.'
  const versions = versionOptionsFor(typed)
  if (!versions.length) return `No software named "${typed}".`
  return versions.length === 1
    ? `One version: ${versions[0].label}.`
    : `${versions.length} versions — pick the one making the claim.`
}

/** Likewise the Version field, which has to name a version that exists. */
function versionHint(): string {
  const typed = String(newClaim.value?.software_name || '').trim()
  if (!typed) return 'Name a software first.'
  const versions = versionOptionsFor(typed)
  if (!versions.length) return ''
  const wanted = String(newClaim.value?.software_version || '').trim()
  if (wanted && !versions.some((option) => option.value === wanted)) {
    return `${typed} has no version "${wanted}" — it has ${versions.map((o) => o.label).join(', ')}.`
  }
  // A claim on 1.0 is not a claim on 2.0, so this is not a detail to gloss.
  return 'The version making the claim.'
}

const NEW_CLAIM_FIELDS = computed<FormField[]>(() => {
  const chosen = String(newClaim.value?.software_name || '')
  const versions = versionOptionsFor(chosen)
  const fields: FormField[] = [
    {
      key: 'software_name',
      label: 'Software',
      // Type to search, as New test and New software do. A select was fine
      // with a dozen; it is unusable with several hundred, and this page is
      // the one where every software in the installation is a candidate.
      type: 'datalist',
      required: true,
      placeholder: 'Backup-Restore',
      options: softwareNameOptions.value,
      hint: softwareHint(),
    },
    {
      key: 'software_version',
      label: 'Version',
      type: 'datalist',
      // Required once there is a choice to make: blank means the unversioned
      // row, which is a real answer but not one to arrive at by not looking.
      required: () => versions.length > 1,
      options: versions.filter((option) => option.value),
      disabled: () => !chosen,
      hint: versionHint(),
    },
  ]
  for (const field of vendorFields.value) {
    if (!field.visible || !field.writable) continue
    const form = customFormField(field)
    if (field.key === 'support_status') {
      form.type = 'select'
      form.options = SUPPORT_VALUES.map((value: string) => ({ value, label: supportLabel(value) }))
    }
    fields.push(form)
  }
  return fields
})

function openNewClaim() {
  if (!softwareVersions.value.length) {
    showToast('No software to claim against yet — add a software version first.', true)
    return
  }
  newClaim.value = Object.fromEntries(NEW_CLAIM_FIELDS.value.map((field) => [field.key, '']))
  newClaim.value.support_status = 'supported'
  newClaim.value.misc_data = '{}'
}

/*
 * A claim has to describe *some* device.
 *
 * The backend builds a row's dedupe key from the identity fields, so a row
 * naming none of them is not a claim about anything and would collide with
 * every other such row. Checked here so the message names the fields rather
 * than arriving as a duplicate-key conflict.
 */
const IDENTITY_KEYS = ['make', 'model', 'firmware_version', 'hardware_version', 'architecture']

function hasAnyIdentity(values: Record<string, any>): boolean {
  return IDENTITY_KEYS.some((key) => String(values[key] ?? '').trim() !== '')
}

// Keeps the version honest while the dialog is open. Both fields are free
// text now, so naming a different software has to clear a version that does
// not belong to it, or the form submits a pairing that does not exist. A
// software with exactly one version fills it in: there is no choice to make.
// More than one is left blank deliberately — a claim on the wrong version is
// a wrong claim, and guessing the newest is not better than asking.
function onNewClaimChange(key: string, value: any) {
  if (!newClaim.value) return
  newClaim.value[key] = value
  if (key !== 'software_name') return
  const versions = versionOptionsFor(String(value || ''))
  if (!versions.some((option) => option.value === newClaim.value!.software_version)) {
    newClaim.value.software_version = versions.length === 1 ? versions[0].value : ''
  }
}

async function createClaim(values: Record<string, any>) {
  if (!hasAnyIdentity(values)) {
    showToast('A vendor device needs at least a make, model, firmware, hardware version, or architecture', true)
    return
  }
  creating.value = true
  try {
    const payload = mergeCustomValues(values, vendorFields.value, 'misc_data')
    await api<any>('/vendor-devices', { method: 'POST', body: JSON.stringify(payload) })
    invalidateSuggestions('vendor-devices')
    newClaim.value = null
    await table.value?.reload()
    showToast('Vendor device added')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creating.value = false
  }
}

/* ---------- editing and removing rows ---------- */

const selected = ref<any[]>([])
const dirty = ref<Map<string, Set<string>>>(new Map())

// A computed rather than a function called from the template: a fresh Set on
// every render would look like a change to the grid and churn its refreshes.
const dirtyIds = computed(() => new Set(dirty.value.keys()))

function isRowDirty(row: any): boolean {
  return !!row.id && dirty.value.has(row.id)
}

/*
 * The row dialog, for the whole claim at once.
 *
 * The same fields the create dialog offers, with the two software ones shown
 * but disabled: which version makes a claim is what the claim *is*, and a
 * dialog that let it be retyped would be offering to move the row rather than
 * edit it. Seeing it is still worth the two rows — on this page the version is
 * the only thing distinguishing two otherwise identical claims.
 */
const EDIT_CLAIM_FIELDS = computed<FormField[]>(() => NEW_CLAIM_FIELDS.value.map((field) =>
  field.key === 'software_name' || field.key === 'software_version'
    ? { ...field, disabled: true, required: false, hint: 'Set when the claim was created.' }
    : field))

function openEdit(row: any) {
  editValues.value = Object.fromEntries(EDIT_CLAIM_FIELDS.value.map((field) => {
    if (field.key === 'misc_data') return [field.key, JSON.stringify(row.misc_data || {}, null, 2)]
    const definition = vendorFields.value.find((item) => item.key === field.key)
    return [field.key, definition?.storage === 'data'
      ? row.misc_data?.[field.key] ?? ''
      : row[field.key] ?? '']
  }))
  editTarget.value = row
}

async function saveEdit(values: Record<string, any>) {
  const row = editTarget.value
  if (!row) return
  if (!hasAnyIdentity(values)) {
    showToast('A vendor device needs at least a make, model, firmware, hardware version, or architecture', true)
    return
  }
  savingEdit.value = true
  try {
    // The software fields are disabled, so FormModal submits them as null;
    // they are not this dialog's to send in either case.
    const { software_name, software_version, ...claim } = values
    const payload = mergeCustomValues(claim, vendorFields.value, 'misc_data')
    const updated = await api<any>(
      `/software/${row.software_id}/vendor-devices/${row.id}`,
      { method: 'PATCH', body: JSON.stringify(payload) },
    )
    Object.assign(row, updated, {
      software_name: row.software_name,
      software_version: row.software_version,
      software_is_latest: row.software_is_latest,
    })
    dirty.value.delete(row.id)
    invalidateSuggestions('vendor-devices')
    editTarget.value = null
    table.value?.refreshRows([row.id])
    showToast('Vendor device saved')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    savingEdit.value = false
  }
}

function onCellEdit(row: any, field: string, value: any) {
  const definition = vendorFields.value.find((item) => item.key === field)
  if (definition?.storage !== 'data') row[field] = value
  if (row.id && definition?.writable !== false) {
    if (!dirty.value.has(row.id)) dirty.value.set(row.id, new Set())
    dirty.value.get(row.id)!.add(field)
  }
}

/** What to call a row in a confirmation or a toast. */
function describeRow(row: any): string {
  const name = [row.make, row.model].filter(Boolean).join(' ') || '(blank)'
  const version = row.software_version ? ` ${row.software_version}` : ''
  return `${name} from ${row.software_name}${version}`
}

/*
 * Save one edited row against its own software.
 *
 * The URL is built from the row rather than from the page: this grid holds
 * claims from every software at once, and the version a row belongs to is the
 * one that has to validate and dedupe it.
 */
async function saveRow(row: any) {
  if (!row.id || !row.software_id) return
  const fields = dirty.value.get(row.id)
  if (!fields || fields.size === 0) return
  const payload: Record<string, any> = {}
  for (const key of fields) {
    const field = vendorFields.value.find((item) => item.key === key)
    if (field?.storage === 'data') payload.misc_data = row.misc_data || {}
    else payload[key] = row[key]
  }
  try {
    const updated = await api<any>(
      `/software/${row.software_id}/vendor-devices/${row.id}`,
      { method: 'PATCH', body: JSON.stringify(payload) },
    )
    // The PATCH answers with the claim, which does not carry the software
    // columns this page joined on — assigning it wholesale would blank them.
    const { software_name, software_version, software_is_latest, ...claim } = row
    Object.assign(row, claim, updated)
    dirty.value.delete(row.id)
    invalidateSuggestions('vendor-devices')
    showToast('Vendor device saved')
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteRow(row: any) {
  if (!row.id || !row.software_id) return
  if (!confirm(`Remove ${describeRow(row)}?`)) return
  try {
    await api(`/software/${row.software_id}/vendor-devices/${row.id}`, { method: 'DELETE' })
    dirty.value.delete(row.id)
    table.value?.clearSelection()
    await table.value?.reload()
    showToast('Vendor device removed')
  } catch (e: any) {
    showToast(e.message, true)
  }
}

/*
 * Remove every selected claim, wherever each one lives.
 *
 * One call rather than one per software: a selection here spans versions by
 * design, and `/vendor-devices/delete` resolves each id to its own software.
 */
async function deleteSelected() {
  const ids = selected.value.map((row) => row.id).filter(Boolean)
  if (!ids.length) return
  if (!confirm(`Remove ${ids.length} vendor device${ids.length === 1 ? '' : 's'}?`)) return
  try {
    await api('/vendor-devices/delete', { method: 'POST', body: JSON.stringify({ ids }) })
    for (const id of ids) dirty.value.delete(id)
    table.value?.clearSelection()
    await table.value?.reload()
    showToast(`Removed ${ids.length} vendor device${ids.length === 1 ? '' : 's'}`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

/** A blank CSV whose first two columns say which version each row belongs to. */
function downloadTemplate() {
  download('/vendor-devices/template', 'vendor-devices-template.csv', 'template')
}

async function onImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const result = await runImport('/vendor-devices/import', file)
    if (result) {
      invalidateSuggestions('vendor-devices')
      await table.value?.reload()
    }
  } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2>Vendor Claims <span v-if="countLabel" class="muted count">{{ countLabel }}</span></h2>
      <div class="toolbar">
        <button
          v-if="auth.can(PERMISSION.softwareEdit)"
          class="btn btn-primary"
          @click="openNewClaim"
        >
          + New vendor device
        </button>
        <!-- Outside the menu: the panel closes on click, and a file input
             unmounted mid-picker never fires `change`. -->
        <input ref="fileInput" type="file" accept=".json,.csv" style="display: none" @change="onImportFile" />
        <OverflowMenu>
          <template v-if="auth.can(PERMISSION.softwareEdit)">
            <button
              class="btn"
              title="Claims for any number of software versions, each row naming its own"
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
          <button class="btn" :disabled="downloading" @click="exportAs('json')">
            {{ downloading ? 'Preparing…' : 'Export JSON' }}
          </button>
          <button class="btn" :disabled="downloading" @click="exportAs('csv')">
            {{ downloading ? 'Preparing…' : 'Export CSV' }}
          </button>
        </OverflowMenu>
      </div>
    </div>
    <p class="muted page-intro">
      Hardware vendors claim their software works against, across every software
      version at once. These are claims, not results — they are not necessarily
      devices this fleet owns, and none of them carries test evidence. Each one
      belongs to the software version that makes it, so adding one here asks
      which; editing an existing one happens on that version's own page.
    </p>
    <p v-if="activeFilters.length" class="active-filters">
      <span class="muted">Filtered to</span>
      <span v-for="[key, value] in activeFilters" :key="key" class="filter-chip">
        {{ key.split('_').join(' ') }}: {{ value }}
      </span>
      <button class="btn btn-mini" type="button" @click="clearFilters">Clear</button>
    </p>
    <DataTable
      ref="table"
      :columns="columns"
      :rows="rows"
      :remote-loader="loadRemoteVendorDevices"
      :filter-values="filterValues"
      :editable="auth.can(PERMISSION.softwareEdit)"
      :row-editable="auth.can(PERMISSION.softwareEdit)"
      :selectable="auth.can(PERMISSION.softwareEdit)"
      :dirty-ids="dirtyIds"
      :is-row-dirty="isRowDirty"
      @cell-edit="onCellEdit"
      @save-row="saveRow"
      @edit-row="openEdit"
      @delete-row="deleteRow"
      @selection-change="(r: any[]) => (selected = r)"
      @grid-ready="onGridReady"
    >
      <template #table-actions>
        <FilterProfilesMenu
          ref="profiles"
          entity="vendor_device_catalog"
          :get-state="() => table?.getState()"
          :apply-state="(s) => table?.applyState(s)"
        />
      </template>
      <template #selection-actions>
        <button
          v-if="auth.can(PERMISSION.softwareEdit) && selected.length"
          class="btn btn-danger"
          title="Remove the selected vendor devices, whichever software each belongs to"
          @click="deleteSelected"
        >
          Delete
        </button>
      </template>
    </DataTable>
    <DetailModal
      v-if="detail"
      :title="detail.title"
      :value="detail.value"
      @close="detail = null"
    />
    <FormModal
      v-if="newClaim"
      title="New vendor device"
      :fields="NEW_CLAIM_FIELDS"
      :values="newClaim"
      :busy="creating"
      submit-label="Add claim"
      @change="onNewClaimChange"
      @submit="createClaim"
      @cancel="newClaim = null"
    />
    <FormModal
      v-if="editTarget"
      :title="`Edit vendor device — ${editTarget.software_name} ${editTarget.software_version || ''}`.trim()"
      :fields="EDIT_CLAIM_FIELDS"
      :values="editValues"
      :busy="savingEdit"
      submit-label="Save changes"
      @submit="saveEdit"
      @cancel="editTarget = null"
    />
    <ImportProgressModal :state="importState" @close="closeImport" />
    <div v-if="toast" class="toast" :class="{ 'toast-error': toastError }">{{ toast }}</div>
  </div>
</template>

<style scoped>
.count { font-size: 13px; font-weight: 400; margin-left: 8px; }
.page-intro { margin: 0 0 12px; max-width: 70ch; }

/* What the URL narrowed the list to, said out loud: arriving pre-filtered from
   a search result otherwise looks like a catalogue with four rows in it. */
.active-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
}

.filter-chip {
  padding: 2px 10px;
  border-radius: var(--r-pill);
  background: var(--surface-2);
  box-shadow: inset 0 0 0 1px var(--border-soft);
  font-size: 12px;
}
</style>
