<script setup lang="ts">
import { computed, onMounted, ref, toRaw, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DataTable from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import FormModal, { type FormField } from '../components/FormModal.vue'
import BundleComponentsEditor from '../components/BundleComponentsEditor.vue'
import OverflowMenu from '../components/OverflowMenu.vue'
import JsonCellEditor from '../components/JsonCellEditor.vue'
import DetailModal from '../components/DetailModal.vue'
import ImportProgressModal from '../components/ImportProgressModal.vue'
import DetailValue from '../components/DetailValue.vue'
import SuggestCellEditor from '../components/SuggestCellEditor.vue'
import { detailCellRenderer } from '../detail'
import { invalidateSuggestions, useSuggestions } from '../suggestions'
import { api, downloadFile } from '../api/client'
import { useImportProgress } from '../importProgress'
import { useAuthStore } from '../stores/auth'
import {
  customColumn, customFormField, dataValue, mergeCustomValues,
  type EntityField, useEntityFields,
} from '../entityFields'
import { loadAllPages } from '../pagination'
import { collapseVendorDevices } from '../vendorDeviceGroups'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { fields: softwareFields, loadFields } = useEntityFields('software')

const software = ref<any>(null)
const error = ref('')
const toast = ref('')
const { importState, runImport, closeImport } = useImportProgress()

/*
 * The field a "View …" cell is currently showing: these columns hold more than
 * a row can display, so the cell offers the value rather than flattening it.
 */
const detail = ref<{ title: string; value: any } | null>(null)

function openDetail(title: string, value: any) {
  detail.value = { title, value }
}
const toastError = ref(false)
const vendorTable = ref<InstanceType<typeof DataTable> | null>(null)
const testedTable = ref<InstanceType<typeof DataTable> | null>(null)
const vendorProfiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const testedProfiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const vendorViewEntity = computed(() => `vendor_devices:${software.value?.id || ''}`)
const testedViewEntity = computed(() => `tested_devices:${software.value?.id || ''}`)

function onVendorGridReady() {
  vendorProfiles.value?.applyDefault()
}

function onTestedGridReady() {
  testedProfiles.value?.applyDefault()
}

/* ---------------- Versions ---------------- */
/*
 * Rows sharing a name are the versions of the same software, each owning its own vendor
 * device list. A new version starts as a copy of the one it branches from and
 * diverges from there, so a device added to 2.0 never shows up on 1.0.
 */

const versions = ref<any[]>([])
const showNewVersion = ref(false)
const creatingVersion = ref(false)
const newVersion = ref<Record<string, any>>({})
const showNewComponent = ref(false)
const creatingComponent = ref(false)
const newComponent = ref<Record<string, any>>({})

const NEW_VERSION_FIELDS: FormField[] = [
  {
    key: 'version',
    label: 'New version',
    required: true,
    placeholder: '2.0.0',
    hint: 'Must not already exist for this software.',
  },
  {
    key: 'copy_vendor_devices',
    label: 'Vendor devices',
    type: 'select',
    required: true,
    options: [
      { value: 'yes', label: 'Inherit a copy from this version' },
      { value: 'no', label: 'Start with an empty list' },
    ],
    hint: 'Inherited devices are copies — editing them here leaves the older version alone.',
  },
]

const NEW_COMPONENT_FIELDS: FormField[] = [
  {
    key: 'name',
    label: 'Component name',
    required: true,
    placeholder: 'Microsoft Outlook',
    hint: 'The component remains beneath this software and is not created as standalone software.',
  },
  {
    key: 'version',
    label: 'Component version',
    placeholder: '16.2',
    hint: 'Optional.',
  },
]

const versionLabel = (v: any) => v.version || '(unversioned)'

function openNewVersion() {
  newVersion.value = { version: '', copy_vendor_devices: 'yes' }
  showNewVersion.value = true
}

async function createVersion(values: Record<string, any>) {
  creatingVersion.value = true
  try {
    const created = await api<any>(`/software/${software.value.id}/versions`, {
      method: 'POST',
      body: JSON.stringify({
        version: values.version,
        copy_vendor_devices: values.copy_vendor_devices === 'yes',
      }),
    })
    showNewVersion.value = false
    showToast(
      `Version ${created.version} created with ${created.vendor_device_count} vendor device${created.vendor_device_count === 1 ? '' : 's'}`,
    )
    // Land on the version that was just made.
    goToVersion(created)
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creatingVersion.value = false
  }
}

function openNewComponent() {
  newComponent.value = { name: '', version: '' }
  showNewComponent.value = true
}

async function createComponent(values: Record<string, any>) {
  const name = String(values.name || '').trim()
  const version = String(values.version || '').trim()
  if (!name) {
    showToast('Component needs a name', true)
    return
  }
  const components = software.value.bundle_components || []
  if (components.some((item: any) =>
    String(item.name || '').trim().toLowerCase() === name.toLowerCase()
    && String(item.version || '').trim() === version
  )) {
    showToast(`${name}${version ? ` ${version}` : ''} is already a component`, true)
    return
  }

  creatingComponent.value = true
  try {
    const updated = await api<any>(`/software/${software.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        bundle_components: bundlePayload([
          ...components,
          { name, version, required: true },
        ]),
      }),
    })
    Object.assign(software.value, updated)
    showNewComponent.value = false
    showToast(`${name}${version ? ` ${version}` : ''} added`)
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creatingComponent.value = false
  }
}

/**
 * Switch versions.
 *
 * The newest version lives at the bare /software/:name; older ones carry their
 * version in the URL. An unversioned row has nothing to put there, so it uses
 * the bare name too — a cold load of that link resolves to whichever version
 * is current, which is the one case the URL cannot express.
 */
function goToVersion(v: any) {
  const name = encodeURIComponent(v.name)
  router.push(
    v.is_latest || !v.version ? `/software/${name}` : `/software/${name}/${encodeURIComponent(v.version)}`,
  )
}

const tabs = [
  { id: 'details', label: 'Details' },
  { id: 'vendor', label: 'Vendor Devices' },
  { id: 'tested', label: 'Tested Devices' },
]
const tab = ref('details')

const fmt = (v: any) => (v ? new Date(v).toLocaleString() : '—')

function showToast(msg: string, isError = false) {
  toast.value = msg
  toastError.value = isError
  setTimeout(() => (toast.value = ''), 4000)
}

/* ---------------- Details tab ---------------- */

const editing = ref(false)
const saving = ref(false)
const form = ref<any>(null)

function bundlePayload(items: any[]): any[] {
  return items.map((item, position) => {
    const name = String(item.name || '').trim()
    if (!name) throw new Error(`Bundle component ${position + 1} needs a name`)
    return {
      id: item.id, name, version: String(item.version || '').trim(),
      required: item.required !== false, position,
    }
  })
}

function startEdit() {
  form.value = Object.fromEntries(
    softwareFields.value.filter((field) => field.writable).map((field) => {
      const value = dataValue(software.value, field, 'misc_data')
      return [field.key, field.type === 'json' ? JSON.stringify(value || {}, null, 2) : value ?? '']
    }),
  )
  form.value.bundle_components = (software.value.bundle_components || []).map((item: any, position: number) => ({
    id: item.id, name: item.name, version: item.version || '',
    required: item.required !== false, position,
  }))
  editing.value = true
  tab.value = 'details'
}

async function saveEdit() {
  if ('name' in form.value && !String(form.value.name || '').trim()) {
    showToast('Software needs a name', true)
    return
  }
  const payload: Record<string, any> = {}
  let misc: Record<string, any> = { ...(software.value.misc_data || {}) }
  for (const field of softwareFields.value.filter((item) => item.writable)) {
    let value = form.value[field.key]
    if (field.type === 'json') {
      try {
        value = JSON.parse(String(value || '{}'))
        if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error()
      } catch {
        showToast(`${field.label} must be a JSON object`, true)
        return
      }
    } else if (field.type === 'number' && value !== '') {
      value = Number(value)
      if (!Number.isFinite(value)) {
        showToast(`${field.label} must be a number`, true)
        return
      }
    }
    if (field.storage === 'data') {
      if (value === '' || value == null) delete misc[field.key]
      else misc[field.key] = value
    } else if (field.key === 'misc_data') {
      misc = value
    } else {
      payload[field.key] = typeof value === 'string' ? value.trim() : value
    }
  }
  payload.misc_data = misc
  try {
    payload.bundle_components = bundlePayload(form.value.bundle_components || [])
  } catch (error: any) {
    showToast(error.message, true)
    return
  }
  saving.value = true
  try {
    const updated = await api<any>(`/software/${software.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    Object.assign(software.value, updated)
    editing.value = false
    showToast('Software saved')
    // A rename moves every version of the software, so refresh the switcher.
    versions.value = await api<any[]>(`/software/${updated.id}/versions`)
    // Name and version are both part of the URL identity.
    if (route.params.id !== updated.name || route.params.version !== updated.version) {
      goToVersion(updated)
    }
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    saving.value = false
  }
}

/* ---------------- Vendor Devices tab ---------------- */
/*
 * Devices the vendor claims this software works against — imported from a
 * compatibility list, not picked from the inventory. They deliberately have no
 * link to a row in `devices`: we may not own any of them.
 */

const vendorRows = ref<any[]>([])
const vendorFields = ref<EntityField[]>([])
const selectedFirmwareByGroup = ref<Record<string, string>>({})
const vendorDisplayRows = computed(() => collapseVendorDevices(
  vendorRows.value,
  selectedFirmwareByGroup.value,
))
const vendorSelected = ref<any[]>([])
// Rows with unsaved changes: row id -> set of changed fields
const vendorDirty = ref<Map<string, Set<string>>>(new Map())
const fileInput = ref<HTMLInputElement | null>(null)
const editingVendorSchema = ref(false)
const savingVendorSchema = ref(false)
const vendorSchemaDraft = ref<EntityField[]>([])

function openVendorSchema() {
  vendorSchemaDraft.value = vendorFields.value.map((field) => ({ ...field }))
  editingVendorSchema.value = true
}

async function saveVendorSchema() {
  savingVendorSchema.value = true
  try {
    vendorFields.value = await api<EntityField[]>(
      `/software/${software.value.id}/vendor-devices/schema`,
      {
        method: 'PUT',
        body: JSON.stringify({
          fields: vendorSchemaDraft.value.map((field) => ({
            id: field.id, visible: field.visible, required: field.required,
          })),
        }),
      },
    )
    editingVendorSchema.value = false
    showToast('Vendor device fields saved')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    savingVendorSchema.value = false
  }
}

const SUPPORT_VALUES = ['supported', 'partial', 'unsupported', 'planned']
const SUPPORT_LABELS: Record<string, string> = {
  supported: 'Supported',
  partial: 'Partial',
  unsupported: 'Unsupported',
  planned: 'Planned',
}

/** A text column that autocompletes over the values it already holds. */
function suggesting(col: Record<string, any>) {
  return {
    ...col,
    cellEditor: SuggestCellEditor,
    cellEditorParams: { entity: 'vendor-devices', field: col.field },
  }
}

const baseVendorColumns = [
  // A vendor's compatibility list repeats the same makes and models down the
  // page — and a claim only matches a device if the
  // spellings agree, so offering the spelling already in use is not cosmetic.
  suggesting({ field: 'make', headerName: 'Make', minWidth: 140 }),
  suggesting({ field: 'model', headerName: 'Model', minWidth: 140 }),
  {
    field: 'firmware_version',
    headerName: 'Firmware',
    editable: false,
    minWidth: 150,
    cellRenderer: (p: any) => {
      const members = p.data?._firmwareMembers || []
      if (members.length <= 1) {
        const span = document.createElement('span')
        span.textContent = p.value || '—'
        return span
      }
      const select = document.createElement('select')
      select.className = 'firmware-picker'
      select.title = `${members.length} firmware records; newest shown first`
      for (const member of members) {
        const option = document.createElement('option')
        option.value = member.id
        option.textContent = member.firmware_version || '(unspecified)'
        option.selected = member.id === p.data.id
        select.appendChild(option)
      }
      select.addEventListener('click', (event) => event.stopPropagation())
      select.addEventListener('change', () => {
        selectedFirmwareByGroup.value = {
          ...selectedFirmwareByGroup.value,
          [p.data._groupKey]: select.value,
        }
      })
      return select
    },
  },
  suggesting({ field: 'hardware_version', headerName: 'Hardware' }),
  { field: 'architecture', headerName: 'Architecture' },
  {
    field: 'support_status',
    headerName: 'Support',
    cellEditor: 'agSelectCellEditor',
    cellEditorParams: { values: SUPPORT_VALUES },
    cellRenderer: (p: any) => {
      const el = document.createElement('span')
      // Global class: cell renderers build detached DOM, which scoped styles
      // never reach.
      el.className = `support-pill ${p.value || 'supported'}`
      el.textContent = SUPPORT_LABELS[p.value] || p.value || ''
      return el
    },
  },
  suggesting({
    field: 'source',
    headerName: 'Source',
    minWidth: 160,
    tooltipValueGetter: (p: any) => p.value || '',
  }),
  {
    field: 'notes',
    headerName: 'Notes',
    minWidth: 160,
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
    // Still needed: export and the quick filter read the formatted value, and
    // the renderer only covers what is painted.
    valueFormatter: (p: any) =>
      p.value && Object.keys(p.value).length ? JSON.stringify(p.value) : '',
  },
]

const vendorColumns = computed(() => vendorFields.value
  .filter((field) => field.visible && field.list_visible)
  .map((field) => {
    const builtIn = baseVendorColumns.find((column: any) => column.field === field.key)
    return builtIn ? { ...builtIn, headerName: field.label } : customColumn(field, 'misc_data')
  }))

// A computed (not a function called from the template): a fresh Set on every
// render would look like a change to the grid and trigger needless refreshes.
const vendorDirtyIds = computed(() => new Set(vendorDirty.value.keys()))

function isVendorRowDirty(row: any): boolean {
  return !!row.id && vendorDirty.value.has(row.id)
}

// Mirrors the server's identity fields: a row saying nothing about which
// device it describes has nothing to dedupe on.
function hasAnyIdentity(row: any): boolean {
  return !!(
    row.make ||
    row.model ||
    row.firmware_version ||
    row.hardware_version ||
    row.architecture
  )
}

async function loadVendorDevices() {
  const result = await loadAllPages<any>((page) => api<any>(
    `/software/${software.value.id}/vendor-devices?page=${page}&page_size=1000`,
  ))
  vendorRows.value = result.items
  // The server total is unpaginated. Using the first page's length here made
  // a successful 1,030-row import appear capped at exactly 1,000.
  if (software.value) software.value.vendor_device_count = result.total
}

async function loadVendorSchema() {
  vendorFields.value = await api<EntityField[]>(
    `/software/${software.value.id}/vendor-devices/schema`,
  )
}

function onVendorCellEdit(row: any, field: string, value: any) {
  const definition = vendorFields.value.find((item) => item.key === field)
  if (definition?.storage !== 'data') row[field] = value
  if (row.id && definition?.writable) {
    if (!vendorDirty.value.has(row.id)) vendorDirty.value.set(row.id, new Set())
    vendorDirty.value.get(row.id)!.add(field)
  }
}

// ---------- new vendor device dialog ----------
// Filled in here and only sent once it validates, so the grid never shows a
// half-made row.

const showNewVendor = ref(false)
const creatingVendor = ref(false)
const newVendor = ref<Record<string, any>>({})
const vendorEditTarget = ref<any>(null)
const savingVendorEdit = ref(false)
const vendorEditValues = ref<Record<string, any>>({})

const vendorMakes = useSuggestions('vendor-devices', 'make')
const vendorModels = useSuggestions('vendor-devices', 'model')
const vendorFirmwares = useSuggestions('vendor-devices', 'firmware_version')
const vendorHardwares = useSuggestions('vendor-devices', 'hardware_version')
const vendorSources = useSuggestions('vendor-devices', 'source')

// A computed, not a constant: the suggestion lists arrive after the first
// render and again after a write, and the dialog has to see them.
const BASE_NEW_VENDOR_FIELDS = computed<FormField[]>(() => [
  { key: 'make', label: 'Make', type: 'datalist', options: vendorMakes.value },
  { key: 'model', label: 'Model', type: 'datalist', options: vendorModels.value },
  {
    key: 'firmware_version',
    label: 'Firmware Version',
    type: 'datalist',
    options: vendorFirmwares.value,
  },
  {
    key: 'hardware_version',
    label: 'Hardware Version',
    type: 'datalist',
    options: vendorHardwares.value,
  },
  { key: 'architecture', label: 'Architecture' },
  {
    key: 'support_status',
    label: 'Support',
    type: 'select',
    required: true,
    options: SUPPORT_VALUES.map((v) => ({ value: v, label: SUPPORT_LABELS[v] })),
  },
  {
    key: 'source',
    label: 'Source',
    type: 'datalist',
    options: vendorSources.value,
    hint: 'Where the claim came from — a datasheet, a release note, a URL.',
  },
  { key: 'notes', label: 'Notes', type: 'textarea' },
  { key: 'misc_data', label: 'Misc data (JSON)', type: 'json', placeholder: '{}' },
])

const NEW_VENDOR_FIELDS = computed<FormField[]>(() => vendorFields.value
  .filter((field) => field.visible && field.writable)
  .map((field) => {
    const builtIn = BASE_NEW_VENDOR_FIELDS.value.find((item) => item.key === field.key)
    return builtIn
      ? { ...builtIn, label: field.label, required: field.required }
      : customFormField(field)
  }))

function openNewVendor() {
  newVendor.value = Object.fromEntries(NEW_VENDOR_FIELDS.value.map((f) => [f.key, '']))
  newVendor.value.support_status = 'supported'
  newVendor.value.misc_data = '{}'
  showNewVendor.value = true
}

async function createVendorDevice(values: Record<string, any>) {
  if (!hasAnyIdentity(values)) {
    showToast('A vendor device needs at least a make, model, firmware, hardware version, or architecture', true)
    return
  }
  creatingVendor.value = true
  try {
    const payload = mergeCustomValues(values, vendorFields.value, 'misc_data')
    const created = await api<any>(`/software/${software.value.id}/vendor-devices`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    // Newest first, so the row you just made is where you are looking.
    vendorRows.value.unshift(created)
    // The spellings just written are the ones the next claim should be offered.
    invalidateSuggestions('vendor-devices')
    bumpVendorCount(1)
    showNewVendor.value = false
    showToast('Vendor device added')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creatingVendor.value = false
  }
}

function openVendorEdit(row: any) {
  vendorEditValues.value = Object.fromEntries(NEW_VENDOR_FIELDS.value.map((field) => {
    if (field.key === 'misc_data') return [field.key, JSON.stringify(row.misc_data || {}, null, 2)]
    const definition = vendorFields.value.find((item) => item.key === field.key)
    return [field.key, definition?.storage === 'data'
      ? row.misc_data?.[field.key] ?? ''
      : row[field.key] ?? '']
  }))
  vendorEditTarget.value = row
}

async function saveVendorEdit(values: Record<string, any>) {
  const row = vendorEditTarget.value
  if (!row) return
  if (!hasAnyIdentity(values)) {
    showToast('A vendor device needs at least a make, model, firmware, hardware version, or architecture', true)
    return
  }
  savingVendorEdit.value = true
  try {
    const payload = mergeCustomValues(values, vendorFields.value, 'misc_data')
    const updated = await api<any>(`/software/${software.value.id}/vendor-devices/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    Object.assign(row, updated)
    const source = vendorRows.value.find((item) => item.id === updated.id)
    if (source) Object.assign(source, updated)
    vendorDirty.value.delete(row.id)
    invalidateSuggestions('vendor-devices')
    vendorEditTarget.value = null
    showToast('Vendor device saved')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    savingVendorEdit.value = false
  }
}

async function saveVendorRow(row: any) {
  // Every row in the grid is a saved vendor device: new ones come from the dialog.
  if (!row.id) return
  const fields = vendorDirty.value.get(row.id)
  if (!fields || fields.size === 0) return
  const payload: Record<string, any> = {}
  for (const key of fields) {
    const field = vendorFields.value.find((item) => item.key === key)
    if (field?.storage === 'data') payload.misc_data = row.misc_data || {}
    else payload[key] = row[key]
  }
  try {
    const updated = await api<any>(`/software/${software.value.id}/vendor-devices/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    Object.assign(row, updated)
    const source = vendorRows.value.find((item) => item.id === updated.id)
    if (source) Object.assign(source, updated)
    invalidateSuggestions('vendor-devices')
    vendorDirty.value.delete(row.id)
    showToast('Vendor device saved')
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteVendorRow(row: any) {
  if (!row.id) return
  if (!confirm(`Remove ${describeVendorRow(row)} from ${software.value.name}'s vendor devices?`)) return
  try {
    await api(`/software/${software.value.id}/vendor-devices/${row.id}`, { method: 'DELETE' })
    vendorRows.value = vendorRows.value.filter((r) => toRaw(r) !== toRaw(row))
    vendorDirty.value.delete(row.id)
    bumpVendorCount(-1)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteSelectedVendorRows() {
  const ids = vendorSelected.value.filter((r) => r.id).map((r) => r.id)
  if (!ids.length) return
  if (!confirm(`Remove ${ids.length} vendor device${ids.length > 1 ? 's' : ''}?`)) return
  try {
    await api(`/software/${software.value.id}/vendor-devices/delete`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    })
    vendorRows.value = vendorRows.value.filter((r) => !ids.includes(r.id))
    for (const id of ids) vendorDirty.value.delete(id)
    vendorSelected.value = []
    bumpVendorCount(-ids.length)
    showToast(`Removed ${ids.length} vendor device${ids.length > 1 ? 's' : ''}`)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

function describeVendorRow(row: any): string {
  return [row.make, row.model].filter(Boolean).join(' ') || 'this vendor device'
}

/** Keep the Details tab's count in step without re-fetching the software. */
function bumpVendorCount(by: number) {
  if (software.value) software.value.vendor_device_count = Math.max(0, (software.value.vendor_device_count || 0) + by)
}

/** A blank CSV carrying exactly the columns a vendor-device import accepts. */
function downloadVendorTemplate() {
  const stem = software.value.name.replace(/\s+/g, '_')
  downloadFile(
    `/software/${software.value.id}/vendor-devices/template`,
    `${stem}-vendor-devices-template.csv`,
  )
}

function exportVendorAs(format: string) {
  const stem = software.value.name.replace(/\s+/g, '_')
  downloadFile(
    `/software/${software.value.id}/vendor-devices/export?format=${format}`,
    `${stem}-vendor-devices.${format}`,
  )
}

async function onImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const result = await runImport(`/software/${software.value.id}/vendor-devices/import`, file)
    if (result) {
      await loadVendorDevices()
    }
  } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}

/* ---------------- Tested Devices tab ---------------- */
/*
 * Same grid as Vendor Devices, but read-only and derived: every row is an
 * inventory device this software has actually been run against, summarised from
 * the tests table. Nothing here is editable — the source of truth is `tests`.
 */

const testedRows = ref<any[]>([])
const testedLoading = ref(false)

const testedColumns = [
  {
    field: 'component_name', headerName: 'Component', minWidth: 180,
    valueFormatter: (p: any) => p.value
      ? `${p.value}${p.data.component_version ? ` ${p.data.component_version}` : ''}` : '',
  },
  {
    field: 'unique_id',
    headerName: 'Device',
    minWidth: 150,
    cellRenderer: (p: any) => {
      const el = document.createElement('div')
      if (!p.value) return el
      const a = document.createElement('a')
      a.className = 'grid-link'
      a.textContent = p.value
      a.href = `/devices/${encodeURIComponent(p.value)}`
      a.onclick = (e) => {
        e.stopPropagation()
        // Plain left click navigates in-tab (SPA); middle click, Ctrl/Cmd+click
        // and right-click > "Open in new tab" keep the browser's native behaviour
        if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
          e.preventDefault()
          router.push(`/devices/${p.value}`)
        }
      }
      el.appendChild(a)
      return el
    },
  },
  { field: 'make', headerName: 'Make', minWidth: 140 },
  { field: 'model', headerName: 'Model', minWidth: 140 },
  { field: 'firmware_version', headerName: 'Firmware' },
  { field: 'hardware_version', headerName: 'Hardware' },
  { field: 'architecture', headerName: 'Architecture' },
  {
    field: 'outcomes',
    headerName: 'Outcomes',
    minWidth: 190,
    // Sorting and the quick filter work off the flattened text; the renderer
    // reads the original object off the row.
    valueGetter: (p: any) =>
      Object.entries(p.data?.outcomes || {})
        .map(([outcome, n]) => `${outcome}: ${n}`)
        .join('  '),
    cellRenderer: (p: any) => {
      const el = document.createElement('div')
      for (const [outcome, n] of Object.entries(p.data?.outcomes || {})) {
        const s = document.createElement('span')
        // Global class: cell renderers build detached DOM, which scoped styles
        // never reach.
        s.className = `outcome-badge ${outcome}`
        s.textContent = `${outcome}: ${n}`
        el.appendChild(s)
      }
      return el
    },
  },
  { field: 'test_count', headerName: 'Tests', maxWidth: 110 },
  {
    field: 'last_test_at',
    headerName: 'Last Test',
    minWidth: 170,
    valueFormatter: (p: any) => (p.value ? new Date(p.value).toLocaleString() : ''),
  },
]

async function loadTestedDevices() {
  testedLoading.value = true
  try {
    const res = await api<any>(`/software/${software.value.id}/tested-devices`)
    // Flattened so the grid can sort and filter on the device fields directly.
    testedRows.value = (res.devices || []).map((t: any) => ({
      id: `${t.component?.id || software.value.id}:${t.device.id}`,
      component_name: t.component?.name || '',
      component_version: t.component?.version || '',
      unique_id: t.device.unique_id,
      make: t.device.make,
      model: t.device.model,
      firmware_version: t.device.firmware_version,
      hardware_version: t.device.hardware_version,
      architecture: t.device.architecture,
      test_count: t.test_count,
      last_test_at: t.last_test_at,
      outcomes: t.outcomes,
    }))
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    testedLoading.value = false
  }
}

/* ---------------- Load ---------------- */

async function load() {
  try {
    await loadFields()
    // Resolves a UUID or a name; a bare name gives the current version.
    const resolved = await api<any>(`/software/${route.params.id}`)
    versions.value = await api<any[]>(`/software/${resolved.id}/versions`)

    // The URL may pin an older version; fall back to what the name resolved to.
    const wanted = route.params.version as string | undefined
    software.value = (wanted && versions.value.find((v) => v.version === wanted)) || resolved
    if (wanted && software.value.version !== wanted) {
      showToast(`${resolved.name} has no version ${wanted}; showing ${versionLabel(software.value)}`, true)
    }

    // Normalise the URL to the software's name (the stable identity).
    if (route.params.id !== software.value.name) {
      const name = encodeURIComponent(software.value.name)
      router.replace(
        software.value.is_latest || !software.value.version
          ? `/software/${name}`
          : `/software/${name}/${encodeURIComponent(software.value.version)}`,
      )
    }
    // Both tabs load up front so their counts are correct before they are opened.
    await Promise.all([loadVendorSchema(), loadVendorDevices(), loadTestedDevices()])
  } catch (e: any) {
    error.value = e.message
  }
}

// Switching versions changes the URL, not the component, so reload on the route.
watch(() => [route.params.id, route.params.version], load)

onMounted(load)
</script>

<template>
  <div class="page" v-if="software">
    <div class="page-header">
      <h2>{{ software.name }}</h2>
      <div class="version-picker">
        <select
          v-if="versions.length > 1"
          :value="software.id"
          title="Every version of this software"
          @change="goToVersion(versions.find((v) => v.id === ($event.target as HTMLSelectElement).value))"
        >
          <option v-for="v in versions" :key="v.id" :value="v.id">
            {{ versionLabel(v) }}{{ v.is_latest ? ' — current' : '' }}
          </option>
        </select>
        <small v-else class="muted">{{ versionLabel(software) }}</small>
        <span v-if="!software.is_latest" class="stale-flag" title="A newer version of this software exists">
          older version
        </span>
      </div>
      <div class="toolbar">
        <template v-if="auth.canWrite">
          <button v-if="!editing" class="btn" @click="startEdit">Edit</button>
          <button v-if="!editing" class="btn" title="Add a version, inheriting this one's vendor devices" @click="openNewVersion">
            + New version
          </button>
          <button v-if="!editing" class="btn" title="Add a component beneath this software version" @click="openNewComponent">
            + New component
          </button>
          <template v-else>
            <button class="btn btn-primary" :disabled="saving" @click="saveEdit">
              {{ saving ? 'Saving…' : 'Save changes' }}
            </button>
            <button class="btn" :disabled="saving" @click="editing = false">Cancel</button>
          </template>
        </template>
        <button class="btn" @click="router.push('/software')">Back to software</button>
      </div>
    </div>

    <div class="tabs">
      <button v-for="t in tabs" :key="t.id" :class="{ active: tab === t.id }" @click="tab = t.id">
        {{ t.label
        }}<span v-if="t.id === 'vendor'"> ({{ software.vendor_device_count ?? vendorRows.length }})</span
        ><span v-else-if="t.id === 'tested'"> ({{ testedRows.length }})</span>
      </button>
    </div>

    <!-- Details -->
    <div v-if="tab === 'details'" class="tab-panel">
      <form v-if="editing" class="form-grid" @submit.prevent>
        <template v-for="field in softwareFields.filter((item) => item.writable)" :key="field.key">
          <label>{{ field.label }}</label>
          <select v-if="field.type === 'select'" v-model="form[field.key]">
            <option v-if="!field.required" value="">—</option>
            <option v-for="option in field.options" :key="option" :value="option">{{ option }}</option>
          </select>
          <input v-else-if="field.type === 'boolean'" v-model="form[field.key]" type="checkbox" />
          <textarea
            v-else-if="field.type === 'json' || field.type === 'textarea'"
            v-model="form[field.key]"
            :class="{ 'json-editor': field.type === 'json' }"
            :style="field.type === 'json' ? 'min-height: 160px' : ''"
            spellcheck="false"
          ></textarea>
          <input v-else v-model="form[field.key]" :type="field.type === 'date' ? 'date' : field.type === 'number' ? 'number' : 'text'" />
        </template>
        <BundleComponentsEditor v-model="form.bundle_components" style="grid-column: 1 / -1" />
      </form>
      <template v-else>
        <dl class="kv">
          <template v-for="field in softwareFields" :key="field.key">
            <dt>{{ field.label }}</dt>
            <dd><DetailValue :value="dataValue(software, field, 'misc_data')" /></dd>
          </template>
          <dt>All Versions</dt>
          <dd>
            <span v-for="(v, i) in versions" :key="v.id">
              <a v-if="v.id !== software.id" href="#" class="grid-link" @click.prevent="goToVersion(v)">{{ versionLabel(v) }}</a>
              <strong v-else>{{ versionLabel(v) }}</strong><span v-if="i < versions.length - 1">, </span>
            </span>
          </dd>
          <dt>Bundle Components</dt>
          <dd v-if="software.bundle_components?.length">
            <span v-for="(component, i) in software.bundle_components" :key="component.id">
              {{ component.name }} {{ component.version }}<span v-if="i < software.bundle_components.length - 1">, </span>
            </span>
          </dd>
          <dd v-else>—</dd>
          <dt>Vendor Devices</dt>
          <dd>
            {{ software.vendor_device_count ?? vendorRows.length }}
            <span class="muted">— devices the vendor says this software works against</span>
          </dd>
          <dt>Tested Devices</dt>
          <dd>
            {{ testedRows.length }}
            <span class="muted">— inventory devices this software has been run against</span>
          </dd>
          <dt>Software ID</dt><dd>{{ software.id }}</dd>
          <dt>Created</dt><dd>{{ fmt(software.created_at) }}</dd>
          <dt>Updated</dt><dd>{{ fmt(software.updated_at) }}</dd>
        </dl>
      </template>
    </div>

    <!-- Vendor Devices -->
    <div v-else-if="tab === 'vendor'" class="tab-panel">
      <div class="panel-header">
        <p class="muted panel-intro">
          Hardware the vendor claims {{ software.name }} {{ versionLabel(software) }} works against. These
          are not inventory devices — import a vendor's compatibility list here. This list belongs
          to this version alone: a new version starts as a copy of it and the two diverge from there.
          Matching make, model, and hardware records are collapsed; use the Firmware dropdown to
          select an underlying record. All {{ vendorRows.length }} records remain available to API/MCP
          searches and exports.
        </p>
        <div class="toolbar">
          <button v-if="auth.canWrite" class="btn btn-primary" @click="openNewVendor">
            + New vendor device
          </button>
          <button v-if="auth.isAdmin" class="btn" @click="openVendorSchema">
            Customize fields
          </button>
          <!-- Outside the menu: the panel closes on click, and a file input
               unmounted mid-picker never fires `change`. -->
          <input
            ref="fileInput"
            type="file"
            accept=".json,.csv"
            style="display: none"
            @change="onImportFile"
          />
          <OverflowMenu>
            <template v-if="auth.canWrite">
              <button class="btn" @click="fileInput?.click()">Import</button>
              <button
                class="btn"
                title="Download a blank CSV with the columns an import accepts"
                @click="downloadVendorTemplate"
              >
                Template
              </button>
            </template>
            <button class="btn" @click="exportVendorAs('json')">Export JSON</button>
            <button class="btn" @click="exportVendorAs('csv')">Export CSV</button>
          </OverflowMenu>
          <FilterProfilesMenu
            ref="vendorProfiles"
            :entity="vendorViewEntity"
            :get-state="() => vendorTable?.getState()"
            :apply-state="(state) => vendorTable?.applyState(state)"
          />
        </div>
      </div>
      <DataTable
        ref="vendorTable"
        :columns="vendorColumns"
        :rows="vendorDisplayRows"
        :editable="auth.canWrite"
        :row-editable="auth.canWrite"
        :selectable="auth.canWrite"
        :dirty-ids="vendorDirtyIds"
        :is-row-dirty="isVendorRowDirty"
        @cell-edit="onVendorCellEdit"
        @save-row="saveVendorRow"
        @edit-row="openVendorEdit"
        @delete-row="deleteVendorRow"
        @selection-change="(r: any[]) => (vendorSelected = r)"
        @grid-ready="onVendorGridReady"
      >
        <template #selection-actions>
          <button
            v-if="auth.canWrite && vendorSelected.length"
            class="btn btn-danger"
            title="Remove the selected vendor devices"
            @click="deleteSelectedVendorRows"
          >
            Delete
          </button>
        </template>
      </DataTable>
      <div v-if="editingVendorSchema" class="modal-backdrop">
        <form class="modal-card modal-wide" @submit.prevent="saveVendorSchema">
          <h3 class="modal-title">Vendor Device Fields — {{ software.name }} {{ versionLabel(software) }}</h3>
          <p class="muted">These overrides apply to this software version. Field definitions are managed under Schema → Vendor Devices.</p>
          <div class="schema-field-list">
            <div v-for="field in vendorSchemaDraft" :key="field.id" class="schema-field-row">
              <div><strong>{{ field.label }}</strong><br /><code>{{ field.key }}</code></div>
              <label class="check"><input v-model="field.visible" type="checkbox" @change="!field.visible && (field.required = false)" /> Shown</label>
              <label class="check"><input v-model="field.required" type="checkbox" :disabled="!field.visible" /> Required</label>
            </div>
          </div>
          <div class="actions">
            <button type="button" class="btn" :disabled="savingVendorSchema" @click="editingVendorSchema = false">Cancel</button>
            <button class="btn btn-primary" :disabled="savingVendorSchema">{{ savingVendorSchema ? 'Saving…' : 'Save fields' }}</button>
          </div>
        </form>
      </div>
    </div>

    <!-- Tested Devices -->
    <div v-else-if="tab === 'tested'" class="tab-panel">
      <div class="panel-header">
        <p class="muted panel-intro">
          Inventory devices {{ software.name }} has actually been run against, summarised from
          recorded tests. Read-only — edit the underlying runs on the Tests page.
        </p>
        <div class="toolbar">
          <FilterProfilesMenu
            ref="testedProfiles"
            :entity="testedViewEntity"
            :get-state="() => testedTable?.getState()"
            :apply-state="(state) => testedTable?.applyState(state)"
          />
        </div>
      </div>
      <p v-if="testedLoading" class="muted">Loading…</p>
      <DataTable
        v-else
        ref="testedTable"
        :columns="testedColumns"
        :rows="testedRows"
        @grid-ready="onTestedGridReady"
      />
    </div>

    <FormModal
      v-if="showNewVersion"
      :title="`New Version of ${software.name}`"
      :fields="NEW_VERSION_FIELDS"
      :values="newVersion"
      :busy="creatingVersion"
      submit-label="Create version"
      @submit="createVersion"
      @cancel="showNewVersion = false"
    />

    <FormModal
      v-if="showNewComponent"
      :title="`New Component for ${software.name} ${versionLabel(software)}`"
      :fields="NEW_COMPONENT_FIELDS"
      :values="newComponent"
      :busy="creatingComponent"
      submit-label="Add component"
      @submit="createComponent"
      @cancel="showNewComponent = false"
    />

    <FormModal
      v-if="vendorEditTarget"
      :title="`Edit Vendor Device — ${describeVendorRow(vendorEditTarget)}`"
      :fields="NEW_VENDOR_FIELDS"
      :values="vendorEditValues"
      :busy="savingVendorEdit"
      submit-label="Save changes"
      @submit="saveVendorEdit"
      @cancel="vendorEditTarget = null"
    />

    <FormModal
      v-if="showNewVendor"
      title="New Vendor Device"
      :fields="NEW_VENDOR_FIELDS"
      :values="newVendor"
      :busy="creatingVendor"
      submit-label="Add"
      @submit="createVendorDevice"
      @cancel="showNewVendor = false"
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
  <div v-else-if="error" class="page">
    <p class="login-error">{{ error }}</p>
  </div>
</template>

<style scoped>
/* The page is a flex column; a tab holding a grid has to pass that height on. */
.tab-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

/* The version selector sits between the software name and the toolbar. */
.version-picker {
  display: flex;
  align-items: center;
  gap: 8px;
}

.version-picker select {
  height: 28px;
  padding: 0 8px;
  font-size: 12.5px;
}

/* Scoped styles outrank the base `select` rule in style.css, so the 16px that
   stops iOS zooming on focus has to be repeated here, and the height with it —
   28px is too small to tap. Mirrors MOBILE in src/breakpoints.ts. */
@media (max-width: 899px) {
  .version-picker select {
    height: var(--control-h);
    font-size: 16px;
  }
}

.stale-flag {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(217, 119, 6, 0.15);
  border: 1px solid rgba(217, 119, 6, 0.4);
  color: var(--yellow);
}

.panel-intro {
  margin: 0;
  max-width: 70ch;
  font-size: 12.5px;
}

.schema-field-list {
  display: grid;
  gap: 8px;
  margin: 16px 0;
  max-height: min(55vh, 560px);
  overflow: auto;
}

.schema-field-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto auto;
  align-items: center;
  gap: 20px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
}

@media (max-width: 599px) {
  .schema-field-row {
    grid-template-columns: 1fr auto;
  }

  .schema-field-row > div {
    grid-column: 1 / -1;
  }
}
</style>
