<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DetailModal from '../components/DetailModal.vue'
import DetailValue from '../components/DetailValue.vue'
import { api } from '../api/client'
import { daysFromToday, daysUntil, formatDate } from '../dates'
import { describeScan } from '../scan'
import { useAuthStore } from '../stores/auth'
import { optionLabel } from '../deviceColumns'
import {
  UNCATEGORIZED,
  fieldByRole,
  fieldValue,
  loadDeviceSchema,
  type SchemaField,
} from '../deviceSchema'
import { loadDeviceActions, type PluginAction } from '../pluginActions'
import { deviceTypes, loadDeviceTypes } from '../deviceTypes'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const detail = ref<any>(null)
const tab = ref('details')
const error = ref('')
const toast = ref('')
/*
 * One page for every device type. Which fields it shows, in what order, with
 * what rules, is this device's type's effective schema — so a router page and
 * a phone page differ without either of them existing as its own component.
 */
const displayFields = ref<SchemaField[]>([])
const configuredType = computed(() => deviceTypes.value.find((item) => item.id === detail.value?.device_type_id))
const visibleFields = computed(() => displayFields.value.filter((field) => field.visible))
const writableFields = computed(() =>
  visibleFields.value.filter((field) => field.writable && field.storage !== 'derived'),
)
const statusField = computed(() => displayFields.value.find((field) => field.key === 'status'))
const hasStatus = computed(() => !!statusField.value?.visible)
const addressRoleFor = (field: SchemaField) =>
  field.role === 'scan_address_wan' ? 'wan' : field.role === 'scan_address_lan' ? 'lan' : null

/**
 * The plugin actions this device can run, and why the others cannot.
 *
 * Read from the backend rather than worked out here: whether Reboot applies to
 * this device is a question about the installation's device-type policy, and
 * only one side of the wire knows the answer.
 */
const deviceActions = ref<PluginAction[]>([])
const scanEnabled = computed(() => deviceActions.value.some((action) =>
  action.plugin_id === 'network-scan'
  && action.id === 'network-scan.scan-device'
  && action.available === true,
))

async function loadSchema() {
  const key = configuredType.value?.key
  displayFields.value = (await loadDeviceSchema(key === UNCATEGORIZED ? undefined : key)).fields
}

// "Details" shows the device info; "Vendor Claims" what vendors claim runs on it;
// "Tests" what we have actually run.
const tabs = [
  { id: 'details', label: 'Details' },
  { id: 'plugin-steps', label: 'Plugin Steps' },
  { id: 'software', label: 'Vendor Claims' },
  { id: 'tests', label: 'Tests' },
]

/** For the DATE columns, which have no time to render and no zone to shift. */
const fmtDay = (v: any) => formatDate(v) || '—'

/**
 * Days past the return date, or null. The same rule the API derives `overdue`
 * from: checked out, has a due date, and the date has gone.
 */
const daysOverdue = computed(() => {
  const d = detail.value?.data
  if (!d || d.status !== 'checked_out' || !d.checkout_due) return null
  const days = daysUntil(d.checkout_due)
  return days !== null && days < 0 ? -days : null
})

const statusColors: Record<string, string> = {
  available: '#16a34a',
  checked_out: '#d97706',
  inventory: '#2563eb',
  missing: '#dc2626',
  broken: '#7c3aed',
}

const scanning = ref(false)
// Result of the most recent scan started from this page, shown under the IPs.
const lastScan = ref<any>(null)

// ---------- compatible software ----------
// Software whose vendor device lists describe this device. Loaded on first
// visit to the tab rather than with the page: it is a scan of every vendor
// claim, and most visits never open the tab.
const compat = ref<any>(null)
const compatLoading = ref(false)
const compatError = ref('')
const vendorClaimCount = ref<number | null>(null)
const testCount = ref<number | null>(null)
// Vendors also publish "this will not work" rows. They match the device the
// same way, but they are not an answer to "what can I run", so they are folded
// away behind a count.
const showUnsupported = ref(false)
const expanded = ref<string[]>([])

// ---------- plugin successful steps ----------
const pluginArtifacts = ref<any[]>([])
const pluginArtifactsLoading = ref(false)
const pluginArtifactsError = ref('')
const artifactDrafts = ref<Record<string, string>>({})
const artifactSaving = ref('')
const artifactDefinitions = [
  { plugin_id: 'device-info-agent', artifact_type: 'discovery_recipe', label: 'Device Info' },
  { plugin_id: 'device-reboot', artifact_type: 'reboot_recipe', label: 'Reboot' },
]
const rebootOverride = ref<{ method: 'ssh' | 'ai' | null; ssh_command: string | null; ssh_port: number | null; ai_profile_id: string | null; ai_model: string | null; ai_repeat_model: string | null }>({ method: null, ssh_command: null, ssh_port: null, ai_profile_id: null, ai_model: null, ai_repeat_model: null })
const rebootEffective = ref<Record<string, any>>({})
const rebootAiConfiguration = ref<Record<string, any>>({})
const rebootConfigSource = ref('')
const rebootConfigAvailable = ref(false)

async function loadRebootOverride() {
  if (!detail.value || !auth.isAdmin) return
  try {
    const result = await api<any>(`/plugins/device-reboot/devices/${detail.value.id}/configuration`)
    rebootOverride.value = {
      method: result.configuration?.method ?? null,
      ssh_command: result.configuration?.ssh_command ?? null,
      ssh_port: result.configuration?.ssh_port ?? null,
      ai_profile_id: result.configuration?.ai_profile_id ?? null,
      ai_model: result.configuration?.ai_model ?? null,
      ai_repeat_model: result.configuration?.ai_repeat_model ?? null,
    }
    rebootEffective.value = result.effective_configuration || {}
    rebootConfigSource.value = result.source || ''
    rebootAiConfiguration.value = result.ai_configuration || {}
    rebootConfigAvailable.value = true
  } catch {
    rebootConfigAvailable.value = false
  }
}

async function saveRebootOverride() {
  try {
    const configuration = Object.fromEntries(Object.entries(rebootOverride.value).filter(([, value]) => value != null && value !== ''))
    const result = await api<any>(`/plugins/device-reboot/devices/${detail.value.id}/configuration`, {
      method: 'PUT', body: JSON.stringify({ configuration }),
    })
    rebootEffective.value = result.effective_configuration || {}
    rebootConfigSource.value = result.source || ''
    toast.value = 'Device reboot override saved'
  } catch (e: any) {
    toast.value = e.message
  }
  setTimeout(() => (toast.value = ''), 3000)
}

async function clearRebootOverride() {
  try {
    const result = await api<any>(`/plugins/device-reboot/devices/${detail.value.id}/configuration`, { method: 'DELETE' })
    rebootOverride.value = { method: null, ssh_command: null, ssh_port: null, ai_profile_id: null, ai_model: null, ai_repeat_model: null }
    rebootEffective.value = result.effective_configuration || {}
    rebootConfigSource.value = result.source || ''
    toast.value = 'Device reboot override cleared'
  } catch (e: any) {
    toast.value = e.message
  }
  setTimeout(() => (toast.value = ''), 3000)
}

const DISCOVERY_ROLE_OPTIONS = [
  { value: 'discovery_hardware', label: 'Hardware version' },
  { value: 'discovery_firmware', label: 'Firmware version' },
  { value: 'discovery_lan_mac', label: 'LAN MAC' },
  { value: 'discovery_wan_mac', label: 'WAN MAC' },
]
type InfoOverride = {
  method: 'browser' | 'ssh' | 'auto' | null
  http_port: number | null; https_port: number | null
  ssh_port: number | null; ssh_connect_timeout: number | null; ssh_command_timeout: number | null
  ssh_host_key_policy: 'accept-new' | 'strict' | null
  ssh_commands: { command: string; yields: string[] }[] | null
  ai_profile_id: string | null; ai_model: string | null; ai_repeat_model: string | null
  discovery_roles: string[] | null; prompt_addendum: string | null
}
const emptyInfoOverride = (): InfoOverride => ({
  method: null, http_port: null, https_port: null, ssh_port: null, ssh_connect_timeout: null,
  ssh_command_timeout: null, ssh_host_key_policy: null, ssh_commands: null,
  ai_profile_id: null, ai_model: null,
  ai_repeat_model: null, discovery_roles: null, prompt_addendum: null,
})
const infoOverride = ref<InfoOverride>(emptyInfoOverride())
const infoEffective = ref<Record<string, any>>({})
const infoAiConfiguration = ref<Record<string, any>>({})
const aiProfiles = ref<any[]>([])
const aiModels = (profileId: string | null) => aiProfiles.value.find((item) => item.id === profileId)?.models || []
async function loadAiProfiles() {
  if (!auth.isAdmin || aiProfiles.value.length) return
  aiProfiles.value = (await api<any[]>('/ai/providers')).filter((item) => item.enabled)
}
const infoConfigSource = ref('')
const infoConfigAvailable = ref(false)

async function loadInfoOverride() {
  if (!detail.value || !auth.isAdmin) return
  try {
    const result = await api<any>(`/plugins/device-info-agent/devices/${detail.value.id}/configuration`)
    infoOverride.value = {
      method: result.configuration?.method ?? null,
      http_port: result.configuration?.http_port ?? null,
      https_port: result.configuration?.https_port ?? null,
      ssh_port: result.configuration?.ssh_port ?? null,
      ssh_connect_timeout: result.configuration?.ssh_connect_timeout ?? null,
      ssh_command_timeout: result.configuration?.ssh_command_timeout ?? null,
      ssh_host_key_policy: result.configuration?.ssh_host_key_policy ?? null,
      ssh_commands: result.configuration?.ssh_commands ?? null,
      ai_profile_id: result.configuration?.ai_profile_id ?? null,
      ai_model: result.configuration?.ai_model ?? null,
      ai_repeat_model: result.configuration?.ai_repeat_model ?? null,
      discovery_roles: result.configuration?.discovery_roles ?? null,
      prompt_addendum: result.configuration?.prompt_addendum ?? null,
    }
    infoEffective.value = result.effective_configuration || {}
    infoConfigSource.value = result.source || ''
    infoAiConfiguration.value = result.ai_configuration || {}
    infoConfigAvailable.value = true
  } catch {
    infoConfigAvailable.value = false
  }
}

async function saveInfoOverride() {
  try {
    const configuration = Object.fromEntries(Object.entries(infoOverride.value).filter(
      ([, value]) => value != null && value !== '',
    ))
    const result = await api<any>(`/plugins/device-info-agent/devices/${detail.value.id}/configuration`, {
      method: 'PUT', body: JSON.stringify({ configuration }),
    })
    infoEffective.value = result.effective_configuration || {}
    infoConfigSource.value = result.source || ''
    toast.value = 'Device Info override saved'
  } catch (e: any) {
    toast.value = e.message
  }
  setTimeout(() => (toast.value = ''), 3000)
}

function toggleInfoDiscoveryOverride(enabled: boolean) {
  infoOverride.value.discovery_roles = enabled
    ? [...(infoEffective.value.discovery_roles || DISCOVERY_ROLE_OPTIONS.map((item) => item.value))]
    : null
}

function addDeviceInfoSshCommand() {
  if (!Array.isArray(infoOverride.value.ssh_commands)) infoOverride.value.ssh_commands = []
  infoOverride.value.ssh_commands.push({ command: '', yields: DISCOVERY_ROLE_OPTIONS.map((item) => item.value) })
}

function toggleDeviceInfoSshCommands(enabled: boolean) {
  infoOverride.value.ssh_commands = enabled ? [] : null
  if (enabled) addDeviceInfoSshCommand()
}

async function clearInfoOverride() {
  try {
    const result = await api<any>(`/plugins/device-info-agent/devices/${detail.value.id}/configuration`, { method: 'DELETE' })
    infoOverride.value = emptyInfoOverride()
    infoEffective.value = result.effective_configuration || {}
    infoConfigSource.value = result.source || ''
    toast.value = 'Device Info override cleared'
  } catch (e: any) {
    toast.value = e.message
  }
  setTimeout(() => (toast.value = ''), 3000)
}

const artifactKey = (pluginId: string, artifactType: string) => `${pluginId}:${artifactType}`
const artifactFor = (definition: any) => pluginArtifacts.value.find((item) =>
  item.plugin_id === definition.plugin_id && item.artifact_type === definition.artifact_type)

async function loadPluginArtifacts() {
  if (!detail.value) return
  pluginArtifactsLoading.value = true
  pluginArtifactsError.value = ''
  try {
    pluginArtifacts.value = await api<any[]>(`/plugins/device-artifacts/${detail.value.id}`)
    artifactDrafts.value = Object.fromEntries(pluginArtifacts.value.map((artifact) => [
      artifactKey(artifact.plugin_id, artifact.artifact_type),
      JSON.stringify(artifact.payload, null, 2),
    ]))
  } catch (e: any) {
    pluginArtifactsError.value = e.message
  } finally {
    pluginArtifactsLoading.value = false
  }
}

async function savePluginArtifact(definition: any) {
  const artifact = artifactFor(definition)
  if (!artifact) return
  const key = artifactKey(definition.plugin_id, definition.artifact_type)
  let payload: any
  try {
    payload = JSON.parse(artifactDrafts.value[key] || '{}')
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error()
  } catch {
    toast.value = `${definition.label} steps must be a JSON object`
    setTimeout(() => (toast.value = ''), 4000)
    return
  }
  artifactSaving.value = key
  try {
    await api(`/plugins/device-artifacts/${detail.value.id}/${definition.plugin_id}/${definition.artifact_type}`, {
      method: 'PUT',
      body: JSON.stringify({
        schema_version: artifact.schema_version || 1,
        payload,
        metadata: artifact.metadata || {},
      }),
    })
    await loadPluginArtifacts()
    toast.value = `${definition.label} steps saved`
  } catch (e: any) {
    toast.value = e.message
  } finally {
    artifactSaving.value = ''
    setTimeout(() => (toast.value = ''), 3000)
  }
}

async function deletePluginArtifact(definition: any) {
  if (!confirm(`Delete all saved ${definition.label} successful steps for this device?`)) return
  const key = artifactKey(definition.plugin_id, definition.artifact_type)
  artifactSaving.value = key
  try {
    await api(`/plugins/device-artifacts/${detail.value.id}/${definition.plugin_id}/${definition.artifact_type}`, {
      method: 'DELETE',
    })
    await loadPluginArtifacts()
    toast.value = `${definition.label} steps deleted; the next run will start fresh`
  } catch (e: any) {
    toast.value = e.message
  } finally {
    artifactSaving.value = ''
    setTimeout(() => (toast.value = ''), 4000)
  }
}

const SUPPORT_LABELS: Record<string, string> = {
  supported: 'Supported',
  partial: 'Partial',
  unsupported: 'Unsupported',
  planned: 'Planned',
}

const compatItems = computed(() => {
  const items = compat.value?.items || []
  return showUnsupported.value
    ? items
    : items.filter((i: any) => i.support_status !== 'unsupported')
})

const unsupportedCount = computed(
  () => (compat.value?.items || []).filter((i: any) => i.support_status === 'unsupported').length,
)

async function loadCompat() {
  compatLoading.value = true
  compatError.value = ''
  try {
    compat.value = await api<any>(`/devices/${detail.value.id}/compatible-software`)
    vendorClaimCount.value = (compat.value.items || []).reduce(
      (total: number, item: any) => total + (item.vendor_devices?.length || 0),
      0,
    )
  } catch (e: any) {
    compatError.value = e.message
  } finally {
    compatLoading.value = false
  }
}

async function loadRelatedCounts() {
  if (!detail.value?.id) return
  const deviceId = detail.value.id
  vendorClaimCount.value = null
  testCount.value = null
  try {
    const counts = await api<any>(`/devices/${deviceId}/related-counts`)
    // Do not land a slow response on a different device after navigation.
    if (detail.value?.id !== deviceId) return
    vendorClaimCount.value = counts.vendor_claims
    testCount.value = counts.tests
  } catch {
    // The tabs remain usable and load their own data; an unavailable count is
    // represented honestly as pending rather than as a false zero.
  }
}

// The canonical software URL: a bare name for the current version, name +
// version for an older one. Matches what the software page normalises to.
function softwareLink(sw: any): string {
  const name = encodeURIComponent(sw.name)
  return sw.is_latest || !sw.version
    ? `/software/${name}`
    : `/software/${name}/${encodeURIComponent(sw.version)}`
}

function toggleExpanded(id: string) {
  const at = expanded.value.indexOf(id)
  if (at === -1) expanded.value.push(id)
  else expanded.value.splice(at, 1)
}

// The match runs off make/model/firmware/hardware/architecture, so an edit to
// any of them changes the answer. Drop what we have and refetch if shown.
function invalidateCompat() {
  compat.value = null
  vendorClaimCount.value = null
  expanded.value = []
  void loadRelatedCounts()
  if (tab.value === 'software') loadCompat()
}

watch(tab, (t) => {
  if (t === 'software' && !compat.value && !compatLoading.value) loadCompat()
  if (t === 'plugin-steps') {
    loadAiProfiles()
    loadPluginArtifacts()
    loadRebootOverride()
    loadInfoOverride()
  }
})

// ---------- edit mode ----------
const editing = ref(false)
const saving = ref(false)
const form = ref<any>(null)
const editFields = ref<SchemaField[]>([])
const editSchemaLoading = ref(false)
const editWritableFields = computed(() =>
  editFields.value.filter((field) => field.visible && field.writable && field.storage !== 'derived'),
)
let editSchemaRequest = 0

function startEdit() {
  editFields.value = displayFields.value
  form.value = Object.fromEntries(editWritableFields.value.map((field) => {
    const value = fieldValue(detail.value, field)
    return [field.key, field.type === 'json' ? JSON.stringify(value || {}, null, 2) : value ?? '']
  }))
  form.value.device_type_id = detail.value.device_type_id || ''
  editing.value = true
  tab.value = 'details'
}

watch(
  () => editing.value ? form.value?.device_type_id : undefined,
  async (typeId) => {
    if (typeId === undefined) return
    const request = ++editSchemaRequest
    const type = deviceTypes.value.find((item) => item.id === typeId)
    editSchemaLoading.value = true
    try {
      const fields = (await loadDeviceSchema(type?.key)).fields
      if (request !== editSchemaRequest || !editing.value || form.value?.device_type_id !== typeId) return
      editFields.value = fields
      // Values already typed survive a type switch. Newly visible fields start
      // with the device's stored value when it has one, otherwise blank.
      for (const field of editWritableFields.value) {
        if (Object.prototype.hasOwnProperty.call(form.value, field.key)) continue
        const value = fieldValue(detail.value, field)
        form.value[field.key] = field.type === 'json' ? JSON.stringify(value || {}, null, 2) : value ?? ''
      }
    } catch (e: any) {
      toast.value = e.message
    } finally {
      if (request === editSchemaRequest) editSchemaLoading.value = false
    }
  },
)

/*
 * Whether the form is editing a checkout at all. The two checkout fields are
 * open only while it is true, and clearing them as it goes false keeps the
 * boxes from showing text that the save is about to drop: the payload below
 * sends null for both, matching what the API would do anyway.
 */
const editingCheckout = computed(() => form.value?.status === 'checked_out')

watch(editingCheckout, (isCheckout) => {
  if (isCheckout || !form.value) return
  const purpose = fieldByRole(displayFields.value, 'checkout_purpose')
  const due = fieldByRole(displayFields.value, 'checkout_due')
  if (purpose) form.value[purpose.key] = ''
  if (due) form.value[due.key] = ''
})

async function saveEdit() {
  const dueField = fieldByRole(editFields.value, 'checkout_due')
  if (editingCheckout.value && dueField && form.value[dueField.key] > daysFromToday(7)) {
    toast.value = 'The return date cannot be more than 7 days away.'
    setTimeout(() => (toast.value = ''), 4000)
    return
  }
  const data: Record<string, any> = {}
  for (const field of editWritableFields.value) {
    if (field.key === 'misc_data') continue
    let value = form.value[field.key]
    if (field.type === 'json' && typeof value === 'string') {
      try {
        value = JSON.parse(value || '{}')
        if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error()
      } catch {
        toast.value = `${field.label} must be a JSON object`
        setTimeout(() => (toast.value = ''), 4000)
        return
      }
    } else if (field.type === 'number' && value !== '' && value != null) {
      value = Number(value)
      if (!Number.isFinite(value)) {
        toast.value = `${field.label} must be a number`
        setTimeout(() => (toast.value = ''), 4000)
        return
      }
    }
    // An explicitly blank secret is a configured credential — a username with
    // no password is an ordinary appliance — so it is sent as written.
    if (field.sensitive && value === '') data[field.key] = ''
    else data[field.key] = value === '' || value == null ? null : value
  }
  // The whole document is not sent: only the fields on this layout. A value
  // whose field this type does not show stays exactly where it is.
  if (typeof form.value.misc_data === 'string') {
    try {
      Object.assign(data, JSON.parse(form.value.misc_data || '{}'))
    } catch {
      toast.value = 'Misc data must be a JSON object'
      setTimeout(() => (toast.value = ''), 4000)
      return
    }
  }
  if (!editingCheckout.value) {
    const purpose = fieldByRole(editFields.value, 'checkout_purpose')
    const due = fieldByRole(editFields.value, 'checkout_due')
    if (purpose) data[purpose.key] = null
    if (due) data[due.key] = null
  }
  saving.value = true
  try {
    const updated = await api<any>(`/devices/${detail.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ device_type_id: form.value.device_type_id || null, data }),
    })
    Object.assign(detail.value, updated)
    editing.value = false
    invalidateCompat()
    toast.value = 'Device saved'
  } catch (e: any) {
    toast.value = e.message
  } finally {
    saving.value = false
    setTimeout(() => (toast.value = ''), 3000)
  }
}

function probeFor(role: string): any | null {
  return (lastScan.value?.probes || []).find((p: any) => p.role === role) || null
}

function probeTitle(role: string): string {
  const p = probeFor(role)
  if (!p) return ''
  return p.online ? `Last scan: answered on ${p.services.join(', ')}` : 'Last scan: no response'
}

async function scanNow() {
  scanning.value = true
  try {
    const result = await api<any>(`/devices/${detail.value.id}/scan`, { method: 'POST' })
    if (result.device) Object.assign(detail.value, result.device)
    lastScan.value = result
    // Names the address that answered and on which services, so a device that
    // is up on one of its two IPs does not read the same as one that is simply up.
    toast.value = `Scan complete — ${describeScan(result).detail}`
  } catch (e: any) {
    toast.value = e.message
  } finally {
    scanning.value = false
  }
  setTimeout(() => (toast.value = ''), 4000)
}

async function load() {
  try {
    detail.value = await api<any>(`/devices/${route.params.id}`)
    // Normalize the URL to the device's unique_id (e.g. /devices/dev-0001)
    if (detail.value.unique_id && route.params.id !== detail.value.unique_id) {
      router.replace(`/devices/${encodeURIComponent(detail.value.unique_id)}`)
    }
  } catch (e: any) {
    error.value = e.message
  }
}

// If the unique_id is renamed, follow it in the URL
watch(
  () => detail.value?.unique_id,
  (uid) => {
    if (uid && route.params.id !== uid && !editing.value) {
      router.replace(`/devices/${encodeURIComponent(uid)}`)
    }
  },
)

const testCols = ['software_name', 'software_version', 'outcome', 'tag', 'run_at', 'notes', 'misc_data', 'created_by_username']

/*
 * Notes and misc data are longer than a table cell: the rows offer them and
 * this dialog shows them, the same way the grids do.
 */
const detail_ = ref<{ title: string; value: any } | null>(null)

/** A field key as a person reads it, using the schema's own label. */
function fieldLabel(key: string): string {
  return displayFields.value.find((field) => field.key === key)?.label || optionLabel(key)
}

function openDetail(title: string, value: any) {
  detail_.value = { title, value }
}
const outcomeBadge = (o: string) => ({ pass: '#16a34a', fail: '#dc2626', warn: '#d97706' }[o] || '#9ca3af')

async function loadActions() {
  if (detail.value?.id) deviceActions.value = await loadDeviceActions(detail.value.id)
}

onMounted(async () => {
  await Promise.all([load(), loadDeviceTypes()])
  // The schema depends on the device's type, so it is fetched once the device
  // is in hand rather than alongside it.
  await Promise.all([loadSchema(), loadActions(), loadRelatedCounts()])
})

// Changing a device's type changes what this page is: different columns,
// different rules, a different plugin allowlist.
watch(() => detail.value?.device_type_id, () => {
  void loadSchema()
  void loadActions()
})
</script>

<template>
  <!-- page-flow: this page grows with its field list and test history rather
       than scrolling a grid inside a fixed-height panel. -->
  <div class="page page-flow" v-if="detail">
    <div class="page-header">
      <h2>{{ detail.unique_id }}</h2>
      <span v-if="detail.device_type_label" class="muted">{{ detail.device_type_label }}</span>
      <!-- Overdue outranks the status in the header: the device is still
           checked out, but that is no longer the useful thing to say. -->
      <span
        v-if="hasStatus"
        class="status-pill"
        :style="{ background: daysOverdue !== null ? '#dc2626' : statusColors[detail.data?.status] || '#6b7280' }"
      >
        {{ daysOverdue !== null ? 'Overdue' : optionLabel(detail.data?.status || '') }}
      </span>
      <div class="toolbar">
        <template v-if="auth.canWrite">
          <button v-if="!editing" class="btn" @click="startEdit">Edit</button>
          <template v-else>
            <button class="btn btn-primary" :disabled="saving || editSchemaLoading" @click="saveEdit">
              {{ saving ? 'Saving…' : editSchemaLoading ? 'Loading type…' : 'Save changes' }}
            </button>
            <button class="btn" :disabled="saving" @click="editing = false">Cancel</button>
          </template>
          <button v-if="scanEnabled" class="btn" :disabled="scanning" @click="scanNow">
            {{ scanning ? 'Scanning…' : 'Scan now' }}
          </button>
        </template>
      </div>
    </div>

    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.id"
        :class="{ active: tab === t.id }"
        @click="tab = t.id"
      >
        {{ t.label
        }}<span v-if="t.id === 'software'"> ({{ vendorClaimCount ?? '…' }})</span
        ><span v-else-if="t.id === 'tests'"> ({{ testCount ?? '…' }})</span>
      </button>
    </div>

    <div v-if="tab === 'details'">
      <form v-if="editing" class="form-grid" @submit.prevent>
        <label>Device Type</label>
        <select v-model="form.device_type_id">
          <option value="">Uncategorized</option>
          <option v-for="item in deviceTypes" :key="item.id" :value="item.id">{{ item.label }}</option>
        </select>
        <template v-for="field in editWritableFields" :key="field.key">
          <label>
            {{ field.label }}
            <span v-if="field.required" class="req" title="Required for this device type">*</span>
          </label>
          <select v-if="field.type === 'select'" v-model="form[field.key]">
            <option v-if="!field.required" value="">—</option>
            <option v-for="option in field.options" :key="option" :value="option">
              {{ optionLabel(option) }}
            </option>
          </select>
          <textarea
            v-else-if="field.type === 'textarea' || field.type === 'json'"
            v-model="form[field.key]"
            :class="{ 'json-editor': field.type === 'json' }"
            :disabled="field.role === 'checkout_purpose' && !editingCheckout"
            style="min-height: 90px"
            spellcheck="false"
          ></textarea>
          <input
            v-else-if="field.type === 'boolean'"
            v-model="form[field.key]"
            type="checkbox"
          />
          <input
            v-else
            v-model="form[field.key]"
            :type="field.type === 'date' ? 'date' : field.type === 'number' ? 'number' : 'text'"
            :disabled="field.role === 'checkout_due' && !editingCheckout"
            :max="field.role === 'checkout_due' ? daysFromToday(7) : undefined"
          />
        </template>
      </form>
      <template v-else>
        <dl class="kv">
          <template v-for="field in visibleFields" :key="field.key">
            <dt>{{ field.label }}</dt>
            <dd :class="{ 'due-overdue': field.role === 'checkout_due' && daysOverdue !== null }">
              <DetailValue
                v-if="field.type === 'json'"
                :value="fieldValue(detail, field)"
              />
              <template v-else-if="field.type === 'boolean'">
                {{ fieldValue(detail, field) ? 'Yes' : 'No' }}
              </template>
              <template v-else-if="field.type === 'date'">
                {{ fmtDay(fieldValue(detail, field)) }}
              </template>
              <template v-else-if="field.type === 'select'">
                {{ fieldValue(detail, field) ? optionLabel(fieldValue(detail, field)) : '—' }}
              </template>
              <template v-else>{{ fieldValue(detail, field) || '—' }}</template>
              <span v-if="field.role === 'checkout_due' && daysOverdue !== null">
                — {{ daysOverdue }} day{{ daysOverdue === 1 ? '' : 's' }} overdue
              </span>
              <span
                v-if="addressRoleFor(field) && probeFor(addressRoleFor(field)!)"
                class="status-dot"
                :class="probeFor(addressRoleFor(field)!).online ? 'online' : 'offline'"
                :title="probeTitle(addressRoleFor(field)!)"
              ></span>
            </dd>
          </template>
        </dl>
        <!-- What the installation's plugin policy makes of this device. Read
             from the backend, reasons and all, so "why can I not reboot this?"
             is answered here rather than guessed at. -->
        <section v-if="deviceActions.length" class="actions-pane">
          <h3>Automation</h3>
          <ul class="action-list">
            <li v-for="action in deviceActions" :key="`${action.plugin_id}:${action.id}`">
              <span class="action-name">
                {{ action.label || action.plugin_id }}
                <span v-if="action.risk === 'disruptive'" class="risk-pill">disruptive</span>
              </span>
              <span :class="action.available ? 'action-ok' : 'muted'">
                {{ action.available ? 'Available' : action.unavailable_reason }}
              </span>
            </li>
          </ul>
        </section>
      </template>
    </div>

    <div v-else-if="tab === 'plugin-steps'" class="plugin-steps-pane">
      <p class="muted plugin-steps-intro">
        Successful steps are reused by later AI runs. Editing creates a new version;
        deleting clears every saved version so the next run starts fresh.
      </p>
      <section v-if="auth.isAdmin && rebootConfigAvailable" class="plugin-step-card device-plugin-config">
        <h3>Reboot configuration for this device</h3>
        <p class="muted">
          This override has highest priority. Effective method:
          <strong>{{ rebootEffective.method || 'global default' }}</strong>
          <template v-if="rebootConfigSource"> (from {{ rebootConfigSource }})</template>.
        </p>
        <div class="device-config-fields">
          <template v-if="rebootAiConfiguration.locked">
            <label>AI endpoint<input :value="rebootAiConfiguration.url" disabled /></label>
            <label>AI model<input :value="rebootAiConfiguration.model" disabled /></label>
            <label>AI repeat model<input :value="rebootAiConfiguration.repeat_model || rebootAiConfiguration.model" disabled /></label>
          </template>
          <template v-else>
            <label>AI provider<select v-model="rebootOverride.ai_profile_id"><option :value="null">Inherit</option><option v-for="p in aiProfiles" :key="p.id" :value="p.id">{{ p.name }}</option></select></label>
            <label>AI model<input v-model="rebootOverride.ai_model" list="reboot-device-ai-models" placeholder="Inherit" /></label>
            <label>AI repeat model<input v-model="rebootOverride.ai_repeat_model" list="reboot-device-ai-models" placeholder="Inherit model" /></label>
            <datalist id="reboot-device-ai-models"><option v-for="model in aiModels(rebootOverride.ai_profile_id)" :key="model" :value="model" /></datalist>
          </template>
          <label>Method
            <select v-model="rebootOverride.method">
              <option :value="null">No device override</option>
              <option value="ssh">SSH</option>
              <option value="ai">AI browser discovery</option>
            </select>
          </label>
          <label>SSH command
            <input v-model="rebootOverride.ssh_command" placeholder="Inherit rule/type/global command" />
          </label>
          <label>SSH port
            <input v-model.number="rebootOverride.ssh_port" type="number" min="1" max="65535" placeholder="Inherit rule/type/global port" />
          </label>
        </div>
        <div class="plugin-step-actions">
          <button class="btn" @click="clearRebootOverride">Clear override</button>
          <button class="btn btn-primary" @click="saveRebootOverride">Save override</button>
        </div>
      </section>
      <section v-if="auth.isAdmin && infoConfigAvailable" class="plugin-step-card device-plugin-config">
        <h3>Device Info configuration for this device</h3>
        <p class="muted">
          This override has highest priority.
          <template v-if="infoConfigSource"> Effective settings are from {{ infoConfigSource }}.</template>
        </p>
        <div class="device-config-fields">
          <label>Collection method<select v-model="infoOverride.method"><option :value="null">Inherit</option><option value="browser">Browser</option><option value="ssh">SSH only</option><option value="auto">Auto (SSH, then browser)</option></select></label>
          <template v-if="infoAiConfiguration.locked">
            <label>AI endpoint<input :value="infoAiConfiguration.url" disabled /></label>
            <label>AI model<input :value="infoAiConfiguration.model" disabled /></label>
            <label>AI repeat model<input :value="infoAiConfiguration.repeat_model || infoAiConfiguration.model" disabled /></label>
          </template>
          <template v-else>
            <label>AI provider<select v-model="infoOverride.ai_profile_id"><option :value="null">Inherit</option><option v-for="p in aiProfiles" :key="p.id" :value="p.id">{{ p.name }}</option></select></label>
            <label>AI model<input v-model="infoOverride.ai_model" list="info-device-ai-models" placeholder="Inherit" /></label>
            <label>AI repeat model<input v-model="infoOverride.ai_repeat_model" list="info-device-ai-models" placeholder="Inherit model" /></label>
            <datalist id="info-device-ai-models"><option v-for="model in aiModels(infoOverride.ai_profile_id)" :key="model" :value="model" /></datalist>
          </template>
          <label>HTTP port
            <input v-model.number="infoOverride.http_port" type="number" min="1" max="65535"
              :placeholder="String(infoEffective.http_port || 80)" />
          </label>
          <label>HTTPS port
            <input v-model.number="infoOverride.https_port" type="number" min="1" max="65535"
              :placeholder="String(infoEffective.https_port || 443)" />
          </label>
          <label>SSH port<input v-model.number="infoOverride.ssh_port" type="number" min="1" max="65535" :placeholder="String(infoEffective.ssh_port || 22)" /></label>
          <label>SSH host keys<select v-model="infoOverride.ssh_host_key_policy"><option :value="null">Inherit</option><option value="accept-new">Accept new</option><option value="strict">Strict</option></select></label>
          <label>SSH connect timeout<input v-model.number="infoOverride.ssh_connect_timeout" type="number" min="1" max="300" placeholder="15" /></label>
          <label>SSH command timeout<input v-model.number="infoOverride.ssh_command_timeout" type="number" min="1" max="300" placeholder="30" /></label>
          <fieldset class="config-fieldset info-command-editor"><legend>Ordered SSH commands</legend>
            <label class="check"><input type="checkbox" :checked="infoOverride.ssh_commands !== null" @change="toggleDeviceInfoSshCommands(($event.target as HTMLInputElement).checked)" />Override commands for this device</label>
            <div v-for="(command, commandIndex) in (infoOverride.ssh_commands || [])" :key="commandIndex" class="info-command-row">
              <input class="command-input" v-model="command.command" placeholder="show version" />
              <div class="command-yields">
                <label v-for="item in DISCOVERY_ROLE_OPTIONS" :key="item.value" class="check"><input v-model="command.yields" type="checkbox" :value="item.value" />{{ item.label }}</label>
              </div>
              <button type="button" class="btn btn-danger" @click="infoOverride.ssh_commands!.splice(commandIndex, 1)">Remove</button>
            </div>
            <button v-if="infoOverride.ssh_commands !== null" type="button" class="btn" @click="addDeviceInfoSshCommand">+ Add command</button>
          </fieldset>
          <fieldset class="config-fieldset">
            <legend>Information to gather</legend>
            <label class="check">
              <input type="checkbox" :checked="infoOverride.discovery_roles !== null"
                @change="toggleInfoDiscoveryOverride(($event.target as HTMLInputElement).checked)" />
              Override requested fields for this device
            </label>
            <label v-for="item in DISCOVERY_ROLE_OPTIONS" :key="item.value" class="check">
              <input v-model="infoOverride.discovery_roles" type="checkbox" :value="item.value"
                :disabled="infoOverride.discovery_roles === null" /> {{ item.label }}
            </label>
            <small class="muted">Existing MAC addresses are still protected from replacement.</small>
          </fieldset>
          <label>Prompt guidance for this device
            <textarea v-model="infoOverride.prompt_addendum" maxlength="8000"
              placeholder="Inherit; e.g. Firmware is under Administration → Status."></textarea>
          </label>
        </div>
        <div class="plugin-step-actions">
          <button class="btn" @click="clearInfoOverride">Clear override</button>
          <button class="btn btn-primary" @click="saveInfoOverride">Save override</button>
        </div>
      </section>
      <p v-if="pluginArtifactsLoading" class="muted">Loading…</p>
      <p v-else-if="pluginArtifactsError" class="login-error">{{ pluginArtifactsError }}</p>
      <div v-else class="plugin-step-grid">
        <section v-for="definition in artifactDefinitions" :key="definition.plugin_id" class="plugin-step-card">
          <div class="plugin-step-head">
            <div>
              <h3>{{ definition.label }}</h3>
              <p v-if="artifactFor(definition)" class="muted">
                Version {{ artifactFor(definition).version }} · Updated {{ artifactFor(definition).updated_at }}
              </p>
            </div>
          </div>
          <template v-if="artifactFor(definition)">
            <textarea
              v-model="artifactDrafts[artifactKey(definition.plugin_id, definition.artifact_type)]"
              class="plugin-step-editor"
              spellcheck="false"
              :readonly="!auth.canWrite"
            ></textarea>
            <div v-if="auth.canWrite" class="plugin-step-actions">
              <button
                class="btn btn-primary"
                :disabled="artifactSaving === artifactKey(definition.plugin_id, definition.artifact_type)"
                @click="savePluginArtifact(definition)"
              >Save steps</button>
              <button
                class="btn btn-danger"
                :disabled="artifactSaving === artifactKey(definition.plugin_id, definition.artifact_type)"
                @click="deletePluginArtifact(definition)"
              >Delete steps</button>
            </div>
          </template>
          <p v-else class="muted">No successful steps have been saved for this plugin.</p>
        </section>
      </div>
    </div>

    <div v-else-if="tab === 'software'">
      <p class="muted compat-intro">
        Software whose vendor device list names this device — what the vendor claims will run
        on it, not what has been tested here. See the <strong>Tests</strong> tab for results.
      </p>

      <p v-if="compatLoading" class="muted">Loading…</p>
      <p v-else-if="compatError" class="login-error">{{ compatError }}</p>
      <template v-else-if="compat">
        <p v-if="!compat.items.length" class="muted">
          No vendor device list names this device. A claim has to agree on every field it
          specifies — make, model, firmware, hardware revision and architecture — so a device
          with an unusual firmware often matches nothing.
        </p>
        <template v-else>
          <label class="compat-toggle">
            <input type="checkbox" v-model="showUnsupported" />
            Show vendor-unsupported ({{ unsupportedCount }})
          </label>
          <p v-if="!compatItems.length" class="muted">
            Every match for this device is marked unsupported by its vendor.
          </p>
          <table v-else class="data-list">
            <thead>
              <tr>
                <th></th>
                <th>Software</th>
                <th>Version</th>
                <th>Vendor says</th>
                <th>Matched on</th>
                <th>Claims</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="item in compatItems" :key="item.software.id">
                <tr class="compat-row" @click="toggleExpanded(item.software.id)">
                  <td class="compat-caret">{{ expanded.includes(item.software.id) ? '▾' : '▸' }}</td>
                  <td class="row-heading" data-label="Software">
                    <router-link
                      :to="softwareLink(item.software)"
                      class="compat-link"
                      @click.stop
                    >
                      {{ item.software.name }}
                    </router-link>
                  </td>
                  <td data-label="Version">{{ item.software.version || '—' }}</td>
                  <td data-label="Vendor says">
                    <span class="support-pill" :class="item.support_status">
                      {{ SUPPORT_LABELS[item.support_status] || item.support_status }}
                    </span>
                  </td>
                  <td class="muted" data-label="Matched on">
                    {{ item.matched_on.map(fieldLabel).join(', ') }}
                  </td>
                  <td data-label="Claims">{{ item.vendor_devices.length }}</td>
                </tr>
                <tr v-if="expanded.includes(item.software.id)" :key="`${item.software.id}-claims`">
                  <td></td>
                  <td colspan="5">
                    <table class="data-list compat-claims">
                      <thead>
                        <tr>
                          <th>Make</th>
                          <th>Model</th>
                          <th>Firmware</th>
                          <th>Hardware</th>
                          <th>Architecture</th>
                          <th>Status</th>
                          <th>Source</th>
                          <th>Notes</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="vd in item.vendor_devices" :key="vd.id">
                          <td data-label="Make">{{ vd.make || 'any' }}</td>
                          <td data-label="Model">{{ vd.model || 'any' }}</td>
                          <td data-label="Firmware">{{ vd.firmware_version || 'any' }}</td>
                          <td data-label="Hardware">{{ vd.hardware_version || 'any' }}</td>
                          <td data-label="Architecture">{{ vd.architecture || 'any' }}</td>
                          <td data-label="Status">
                            <span class="support-pill" :class="vd.support_status">
                              {{ SUPPORT_LABELS[vd.support_status] || vd.support_status }}
                            </span>
                          </td>
                          <td data-label="Source">{{ vd.source || '—' }}</td>
                          <td data-label="Notes">
                            <button
                              v-if="vd.notes"
                              type="button"
                              class="cell-view-btn"
                              @click="openDetail('Notes', vd.notes)"
                            >
                              View Notes
                            </button>
                            <span v-else>—</span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </template>
      </template>
    </div>

    <div v-else-if="tab === 'tests'">
      <p v-if="!detail.all_tests.length" class="muted">
        No tests recorded for this device yet.
      </p>
      <div v-else class="table-scroll">
      <table class="data-list">
        <thead>
          <tr>
            <th v-for="c in testCols" :key="c">{{ c.split('_').join(' ') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in detail.all_tests" :key="t.id">
            <td class="row-heading" data-label="Software">{{ t.software_name }}</td>
            <td data-label="Version">{{ t.software_version || '—' }}</td>
            <td data-label="Outcome">
              <span class="status-pill" style="font-size: 11px" :style="{ background: outcomeBadge(t.outcome) }">
                {{ t.outcome }}
              </span>
            </td>
            <td data-label="Tag">{{ t.tag }}</td>
            <td data-label="Run at">{{ fmtDay(t.run_at) }}</td>
            <td data-label="Notes">
              <button
                v-if="t.notes"
                type="button"
                class="cell-view-btn"
                @click="openDetail('Notes', t.notes)"
              >
                View Notes
              </button>
              <span v-else>—</span>
            </td>
            <td data-label="Test data">
              <button
                v-if="t.misc_data && Object.keys(t.misc_data).length"
                type="button"
                class="cell-view-btn"
                @click="openDetail('Misc Data', t.misc_data)"
              >
                View Misc Data
              </button>
              <span v-else>—</span>
            </td>
            <td data-label="By">{{ t.created_by_username || '—' }}</td>
          </tr>
        </tbody>
      </table>
      </div>
    </div>

    <DetailModal
      v-if="detail_"
      :title="detail_.title"
      :value="detail_.value"
      @close="detail_ = null"
    />

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
  <div v-else-if="error" class="page">
    <p class="login-error">{{ error }}</p>
  </div>
</template>

<style scoped>
.actions-pane { margin-top: 18px; }
.actions-pane h3 { margin: 0 0 8px; font-size: 14px; }
.action-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.action-list li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 10px; border: 1px solid var(--border-soft); border-radius: var(--r-md); }
.action-name { display: flex; align-items: center; gap: 8px; font-weight: 500; }
.action-ok { color: var(--green, #16a34a); }
/* inline-flex for the same reason as the tags on the Device Schema page: an
   inline box does not grow for vertical padding, so the rounded background
   would paint over its neighbours. */
.risk-pill {
  display: inline-flex;
  align-items: center;
  padding: 1px 7px;
  border-radius: var(--r-pill);
  background: var(--red, #dc2626);
  color: #fff;
  font-size: 11px;
  line-height: 1.6;
  text-transform: uppercase;
  letter-spacing: .04em;
  white-space: nowrap;
}
.req { color: var(--red, #dc2626); }

/* The due date, once it has been missed. Matches the red the fleet view uses
   for the same state. */
.due-overdue {
  color: var(--red);
  font-weight: 600;
}

.plugin-steps-intro { max-width: 72ch; }
.plugin-step-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); }
.plugin-step-card { padding: 16px; border: 1px solid var(--border-soft); border-radius: var(--r-md); background: var(--surface-2); }
.plugin-step-head h3 { margin: 0; }
.plugin-step-head p { margin: 4px 0 10px; font-size: 12px; }
.plugin-step-editor { width: 100%; min-height: 280px; resize: vertical; font: 12px/1.5 ui-monospace, monospace; }
.plugin-step-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px; }
.device-plugin-config { margin-bottom: 18px; }
.device-plugin-config > h3 { margin: 0 0 6px; }
.device-plugin-config > p { margin: 0 0 16px; }
.device-config-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 16px; }
.device-config-fields label { display: grid; gap: 5px; }
.device-config-fields label.check { display: flex; align-items: center; gap: 7px; }
.device-config-fields select, .device-config-fields input, .device-config-fields textarea { padding: 8px; border: 1px solid var(--border); border-radius: var(--r-sm); background: var(--surface); color: inherit; }
.device-config-fields textarea { min-height: 90px; resize: vertical; }
.device-config-fields > .config-fieldset { grid-column: 1 / -1; }
.config-fieldset { min-width: 0; display: grid; gap: 12px; margin: 2px 0; padding: 14px; border: 1px solid var(--border-soft); border-radius: var(--r-md); }
.config-fieldset legend { padding: 0 6px; font-weight: 600; }
.info-command-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 12px; border: 1px solid var(--border-soft); border-radius: var(--r-md); background: var(--surface); }
.info-command-row .command-input { grid-column: 1 / -1; width: 100%; }
.command-yields { display: flex; flex-wrap: wrap; gap: 8px 16px; min-width: 0; }
.command-yields label.check { display: flex; }
.info-command-row > .btn { justify-self: end; min-width: 88px; }

.compat-intro {
  margin: 0 0 12px;
  max-width: 70ch;
}
.compat-toggle {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 10px;
  font-size: 12.5px;
  color: var(--text-dim);
  cursor: pointer;
}
.compat-row {
  cursor: pointer;
}
.compat-caret {
  width: 18px;
  color: var(--text-dim);
  user-select: none;
}
.compat-link {
  color: var(--accent);
  text-decoration: none;
}
@media (hover: hover) {
  .compat-link:hover {
    text-decoration: underline;
  }
}
/* The claims table is detail inside a row, not a peer of the outer list. */
.compat-claims {
  margin: 4px 0 10px;
  font-size: 12px;
}

/*
 * Both compatibility tables become stacked blocks on a narrow screen.
 *
 * The claims table is nine columns nested inside a six-column row, which is
 * past rescuing with a horizontal scroll — and scrolling the outer table would
 * drag the nested one out of reach with it. So the pair turn into label/value
 * blocks instead, using the `data-label` on each cell as the caption.
 *
 * Mirrors STACK in src/breakpoints.ts.
 */
@media (max-width: 639px) {
  .device-config-fields { grid-template-columns: minmax(0, 1fr); }
  .info-command-row { grid-template-columns: minmax(0, 1fr); }
  .info-command-row > .btn { justify-self: start; }
  .data-list thead {
    display: none;
  }

  .data-list,
  .data-list tbody,
  .data-list tr,
  .data-list td {
    display: block;
    width: 100%;
  }

  .data-list tbody tr {
    position: relative;
    border: 1px solid var(--border-soft);
    border-radius: var(--r-sm);
    padding: 9px 11px;
    margin-bottom: 8px;
    background: var(--surface-2);
  }

  .data-list td {
    border: none;
    padding: 3px 0;
    display: grid;
    grid-template-columns: minmax(84px, 33%) 1fr;
    gap: 10px;
    align-items: baseline;
  }

  .data-list td::before {
    content: attr(data-label);
    color: var(--text-dim);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  /* The first cell is the block's heading, not another labelled field. */
  .row-heading {
    display: block;
    font-size: 15px;
    font-weight: 600;
    padding: 0 0 6px;
  }

  .row-heading::before {
    content: none;
  }

  /* Only the expandable rows have a caret to leave room for. */
  .compat-row .row-heading {
    margin-right: 20px;
  }

  /* The caret marks the block as expandable from its corner rather than
     taking a line of its own. */
  .compat-caret {
    position: absolute;
    top: 9px;
    right: 10px;
    width: auto;
    display: block;
    padding: 0;
  }

  .compat-caret::before {
    content: none;
  }

  /* A claims block sits inside an outer block, so it needs to look nested
     rather than like another peer in the list. */
  .compat-claims tbody tr {
    background: var(--surface-3);
  }
}
</style>
