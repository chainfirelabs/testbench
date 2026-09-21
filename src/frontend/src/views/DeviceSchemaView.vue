<script setup lang="ts">
/**
 * The Schema admin area for devices, software, and tests.
 *
 * Five screens, in the order somebody actually works through them:
 *
 *  1. Layouts — the fields on a page, and their order. The global set ("All
 *     device types") and each type's own page are scopes in one list, because
 *     they are the same job: deciding what a page shows. A type's page starts
 *     as the global one and diverges only where somebody overrides something.
 *  2. Fields — the catalog. Every definition that exists, its type, choices,
 *     rules and the semantic role plugins ask for, and where it is in use.
 *     Reference and housekeeping; placing a field happens on Layouts.
 *  3. Plugins — which installed plugins may act on which device type. Deny by
 *     default; the API enforces it again on every invocation.
 *  4/5. Software and Tests — one layout each, defined and placed in one table.
 *
 * Layouts lists only the fields a page actually has. Adding one is a picker
 * that can also create the definition, so a field is defined and placed in a
 * single action rather than across two screens with nothing to connect them.
 *
 * Saving a scope publishes a revision, and the backend refuses to publish a
 * configuration the stored devices contradict — a newly required field that
 * two hundred devices leave empty is reported here, before it becomes the rule
 * every save is measured against.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, downloadFile, uploadFile } from '../api/client'
import { optionLabel } from '../deviceColumns'
import {
  invalidateDeviceSchemas,
  type DeviceSchema,
  type FieldAssignment,
  type FieldDefinition,
  type SchemaField,
} from '../deviceSchema'
import { deviceTypes, loadDeviceTypes, type DeviceType } from '../deviceTypes'
import { invalidateEntityFields, type EntityField } from '../entityFields'

const route = useRoute()
const router = useRouter()

const TABS = [
  { id: 'layouts', label: 'Device Layouts' },
  { id: 'fields', label: 'Device Fields' },
  { id: 'plugins', label: 'Plugins' },
  { id: 'software', label: 'Software' },
  { id: 'tests', label: 'Tests' },
  { id: 'vendor_devices', label: 'Vendor Claims' },
] as const

type TabId = (typeof TABS)[number]['id']

/*
 * `global` and `types` were two tabs before the layout screens merged. Both
 * still arrive as bookmarks and as the redirect from the retired Device Types
 * page, so they keep resolving — `types` to the first device type, `global` to
 * the inherited set.
 */
const TAB_ALIASES: Record<string, TabId> = { global: 'layouts', types: 'layouts' }

const tab = ref<TabId>('layouts')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const report = ref<{ errors: any[]; warnings: any[] } | null>(null)

type AdditiveImportResult = {
  generation: string
  counts: Record<string, number>
  total: number
  additions: Record<string, string[]>
  skipped: Record<string, string[]>
  revision?: number | null
}
const importInput = ref<HTMLInputElement | null>(null)
const importFile = ref<File | null>(null)
const importPreview = ref<AdditiveImportResult | null>(null)
const importing = ref(false)

const definitions = ref<FieldDefinition[]>([])
const overview = ref<{
  revision: number
  reconciliation: string
  yaml_configured: boolean
  allow_global_exclusions: boolean
} | null>(null)

const FIELD_TYPES = ['text', 'textarea', 'password', 'number', 'boolean', 'date', 'select', 'json']

/*
 * Roles are how a plugin asks for meaning rather than for one installation's
 * field names: an address a scan can reach is `scan_address_wan`, whatever the
 * field is called. The list is the set the shipped plugins understand.
 */
const ROLES = [
  '', 'identifier', 'status', 'scan_address_wan', 'scan_address_lan', 'scan_state',
  'last_seen', 'device_username', 'device_password', 'discovery_firmware',
  'discovery_hardware', 'discovery_lan_mac', 'discovery_wan_mac',
  'checkout_started', 'checkout_due', 'checkout_purpose',
]

function flash(message: string) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 4000)
}

/** Server errors from this area carry a validation report; show all of it. */
function fail(e: any) {
  const detail = e?.detail || e
  if (detail && typeof detail === 'object' && (detail.errors || detail.warnings)) {
    report.value = { errors: detail.errors || [], warnings: detail.warnings || [] }
    error.value = detail.message || 'The schema cannot be published'
    return
  }
  try {
    const parsed = JSON.parse(e.message)
    if (parsed?.errors) {
      report.value = { errors: parsed.errors, warnings: parsed.warnings || [] }
      error.value = parsed.message || 'The schema cannot be published'
      return
    }
  } catch {
    /* not a report; fall through to the plain message */
  }
  error.value = e.message || String(e)
}

async function run(action: () => Promise<void>, done?: string) {
  busy.value = true
  error.value = ''
  report.value = null
  try {
    await action()
    invalidateDeviceSchemas()
    if (done) flash(done)
  } catch (e: any) {
    fail(e)
  } finally {
    busy.value = false
  }
}

const yamlOwned = (source?: string | null) => source === 'yaml'
const ownershipLabel = (source?: string | null) =>
  source === 'yaml' ? 'YAML' : source === 'system' ? 'Built in' : 'GUI'

/* ---------------------------------------------------------------- fields */

const editingField = ref<Partial<FieldDefinition> | null>(null)
const creatingField = ref(false)
/* Set when the field editor was opened from the layout picker: the definition
   it creates is placed on the open page rather than left in the catalog. */
const placeAfterCreate = ref(false)
const entityFields = ref<EntityField[]>([])
const editingEntityField = ref<any>(null)
const creatingEntityField = ref(false)
const entityLayoutDirty = ref(false)
const entityDragId = ref('')

const activeEntity = computed(() => ['software', 'tests', 'vendor_devices'].includes(tab.value) ? tab.value as 'software' | 'tests' | 'vendor_devices' : null)
const entityOptionsText = computed({
  get: () => (editingEntityField.value?.options || []).join('\n'),
  set: (value: string) => {
    if (editingEntityField.value) editingEntityField.value.options = value.split('\n').map((v) => v.trim()).filter(Boolean)
  },
})

async function loadEntitySchema() {
  if (!activeEntity.value) return
  entityFields.value = await api<EntityField[]>(`/entity-fields/${activeEntity.value}`)
  entityLayoutDirty.value = false
}

function toggleEntityField(field: EntityField, listVisible: boolean) {
  field.list_visible = listVisible
  entityLayoutDirty.value = true
}

function dropEntityField(target: EntityField) {
  const from = entityFields.value.findIndex((field) => field.id === entityDragId.value)
  const to = entityFields.value.findIndex((field) => field.id === target.id)
  entityDragId.value = ''
  if (from < 0 || to < 0 || from === to) return
  const next = [...entityFields.value]
  next.splice(to, 0, ...next.splice(from, 1))
  entityFields.value = next
  entityLayoutDirty.value = true
}

function nudgeEntityField(field: EntityField, delta: number) {
  const index = entityFields.value.indexOf(field)
  const target = entityFields.value[index + delta]
  if (!target) return
  entityDragId.value = field.id
  dropEntityField(target)
}

async function saveEntityLayout() {
  const entity = activeEntity.value
  if (!entity) return
  await run(async () => {
    entityFields.value = await api<EntityField[]>(`/entity-fields/${entity}`, {
      method: 'PUT',
      body: JSON.stringify({ fields: entityFields.value.map(({ id, list_visible }) => ({ id, list_visible })) }),
    })
    invalidateEntityFields()
    entityLayoutDirty.value = false
  }, 'Layout saved')
}

function newEntityField() {
  creatingEntityField.value = true
  editingEntityField.value = { key: '', label: '', field_type: 'text', description: '', options: [], required: false, sensitive: false, indexed: false, unique_value: false }
}

function editEntityField(field: EntityField) {
  creatingEntityField.value = false
  editingEntityField.value = { ...field, field_type: field.type, unique_value: field.unique }
}

async function saveEntityField() {
  const draft = editingEntityField.value
  const entity = activeEntity.value
  if (!draft || !entity) return
  await run(async () => {
    const body = {
      label: draft.label, field_type: draft.field_type, description: draft.description || null,
      options: draft.options || [], required: !!draft.required, sensitive: !!draft.sensitive,
      indexed: !!draft.indexed, unique_value: !!draft.unique_value,
    }
    if (draft.protected) {
      await api(`/entity-fields/${entity}/${draft.id}`, { method: 'PATCH', body: JSON.stringify({ label: draft.label, description: draft.description || null }) })
    } else if (creatingEntityField.value) {
      await api(`/entity-fields/${entity}`, { method: 'POST', body: JSON.stringify({ ...body, key: draft.key }) })
    } else {
      await api(`/entity-fields/${entity}/${draft.id}`, { method: 'PATCH', body: JSON.stringify(body) })
    }
    editingEntityField.value = null
    invalidateEntityFields()
    await loadEntitySchema()
  }, 'Schema field saved')
}

async function deleteEntityField(field: EntityField) {
  const entity = activeEntity.value
  if (!entity || !confirm(`Delete the additional field “${field.label}”? Stored JSON values are retained.`)) return
  await run(async () => {
    await api(`/entity-fields/${entity}/${field.id}`, { method: 'DELETE' })
    invalidateEntityFields()
    await loadEntitySchema()
  }, 'Schema field deleted')
}

async function loadDefinitions() {
  definitions.value = await api<FieldDefinition[]>('/device-fields')
}

/**
 * Whether a new definition also gets a global assignment.
 *
 * The API defaults this on, and a field created from the catalog keeps that:
 * "define a field" has meant "every device type has it" since the feature
 * shipped. Created from a layout's picker the answer is already known — it is
 * which page the picker was opened on — so the dialog does not ask.
 */
const newFieldGlobal = ref(true)

function newField(place = false) {
  creatingField.value = true
  placeAfterCreate.value = place
  newFieldGlobal.value = place ? isGlobalScope.value : true
  editingField.value = {
    key: '', label: '', field_type: 'text', description: '', options: [], validation: {},
    sensitive: false, indexed: false, unique_value: false, opens_web_page: false,
    link_scheme: 'http', link_port: null,
    plugin_role: null, enabled: true,
  }
}

function editField(definition: FieldDefinition) {
  creatingField.value = false
  placeAfterCreate.value = false
  editingField.value = JSON.parse(JSON.stringify(definition))
}

const optionsText = computed({
  get: () => (editingField.value?.options || []).join('\n'),
  set: (value: string) => {
    if (editingField.value) {
      editingField.value.options = value.split('\n').map((line) => line.trim()).filter(Boolean)
    }
  },
})

async function saveField() {
  const draft = editingField.value
  if (!draft) return
  await run(async () => {
    const body: Record<string, any> = {
      label: draft.label,
      field_type: draft.field_type,
      description: draft.description || null,
      options: draft.options || [],
      validation: draft.validation || {},
      sensitive: !!draft.sensitive,
      opens_web_page: !!draft.opens_web_page,
      link_scheme: draft.link_scheme === 'https' ? 'https' : 'http',
      // The number input hands back a string, and an empty one means the
      // scheme's own port rather than port zero.
      link_port:
        draft.link_port == null || String(draft.link_port).trim() === ''
          ? null
          : Number(draft.link_port),
      indexed: !!draft.indexed,
      unique_value: !!draft.unique_value,
      plugin_role: draft.plugin_role || null,
    }
    const created = creatingField.value
    const place = placeAfterCreate.value
    if (created) {
      await api('/device-fields', {
        method: 'POST',
        body: JSON.stringify({ ...body, key: draft.key, add_to_global: newFieldGlobal.value }),
      })
    } else {
      // A protected system field's meaning is the application's; only its
      // presentation is on offer, so those keys are simply not sent.
      if (draft.protected_system_field) {
        delete body.field_type
        delete body.plugin_role
      } else {
        body.enabled = draft.enabled
      }
      await api(`/device-fields/${draft.id}`, { method: 'PATCH', body: JSON.stringify(body) })
    }
    const key = draft.key
    editingField.value = null
    placeAfterCreate.value = false
    await refreshAll()
    // Defining a field and putting it somewhere is one intention. Creating one
    // from the picker finishes that intention rather than leaving it in the
    // catalog for the administrator to go and find.
    if (created && place && key) {
      await loadScope()
      // On the inherited page the global assignment the API just made has
      // already placed it; anywhere else it is this page's to add.
      if (addFieldToScope(key)) flash('Field created and added — save the page to publish it')
      else flash('Field created and added')
    }
  }, placeAfterCreate.value ? undefined : 'Field saved')
}

async function deleteField(definition: FieldDefinition) {
  if (!confirm(`Delete the field “${definition.label}”? Devices keep any values they already hold.`)) return
  await run(async () => {
    await api(`/device-fields/${definition.id}`, { method: 'DELETE' })
    await refreshAll()
  }, 'Field deleted')
}

/* --------------------------------------------------------------- layouts */

/**
 * One field as it appears on the open page.
 *
 * `own` is the difference that matters: a row the scope has its own assignment
 * for is sent when the page is saved, and one without is left inherited, so a
 * type keeps following the global set for everything nobody has overridden.
 */
interface Row {
  key: string
  /** The label this page resolves to, overrides applied. */
  label: string
  field_type: string
  protected: boolean
  storage: string
  /** Present in the global set, so removing it here is an exclusion. */
  fromGlobal: boolean
  /** This scope has an assignment row of its own for the field. */
  own: boolean
  excluded: boolean
  visible: boolean | null
  list_visible: boolean | null
  required: boolean | null
  writable: boolean | null
  position: number
  label_override: string | null
  description_override: string | null
  validation_override: Record<string, any> | null
  configuration_source?: string
  /** What the field resolves to before this scope overrides anything. */
  base: { visible: boolean; list_visible: boolean; required: boolean; writable: boolean; label: string }
}

/** Built-in defaults for a field no wider scope has anything to say about. */
const BASE_DEFAULTS = { visible: true, list_visible: true, required: false, writable: true }

const rows = ref<Row[]>([])
const activeTypeId = ref('')
const dirty = ref(false)
const activeType = computed(() => deviceTypes.value.find((item) => item.id === activeTypeId.value) || null)
/** The inherited set, edited directly rather than through a type. */
const isGlobalScope = computed(() => !activeTypeId.value)
const scopeLabel = computed(() => (isGlobalScope.value ? 'All device types' : activeType.value?.label || ''))

function definitionFor(key: string): FieldDefinition | undefined {
  return definitions.value.find((item) => item.key === key)
}

function rowFor(field: SchemaField, own: FieldAssignment | undefined, inherited: SchemaField | undefined): Row {
  const definition = definitionFor(field.key)
  return {
    key: field.key,
    label: field.label,
    field_type: field.type,
    protected: field.protected,
    storage: field.storage,
    fromGlobal: !!inherited,
    own: !!own,
    excluded: own?.excluded ?? false,
    visible: own?.visible ?? null,
    list_visible: own?.list_visible ?? null,
    required: own?.required ?? null,
    writable: own?.writable ?? null,
    position: field.position,
    label_override: own?.label_override ?? null,
    description_override: own?.description_override ?? null,
    validation_override: own?.validation_override ?? null,
    configuration_source: own?.configuration_source,
    base: inherited
      ? { visible: inherited.visible, list_visible: inherited.list_visible, required: inherited.required, writable: inherited.writable, label: inherited.label }
      : { ...BASE_DEFAULTS, label: definition?.label ?? field.label },
  }
}

/**
 * The open page's fields, in the order the page will show them.
 *
 * `schema.fields` is the merge every reader resolves through, so what this
 * lists is what the grid and the forms will list. Excluded fields are not in
 * it — they have been taken off the page — so they are appended, visibly
 * removed and restorable, rather than vanishing with no way back.
 */
function buildRows(schema: DeviceSchema, globalSchema: DeviceSchema): Row[] {
  const own = new Map((schema.assignments || []).map((item) => [item.field_key, item]))
  const globalByKey = isGlobalScope.value
    ? new Map<string, SchemaField>()
    : new Map(globalSchema.fields.map((field) => [field.key, field]))

  const built = schema.fields.map((field) => rowFor(field, own.get(field.key), globalByKey.get(field.key)))

  const shown = new Set(built.map((row) => row.key))
  for (const [key, assignment] of own) {
    if (shown.has(key) || !assignment.excluded) continue
    const inherited = globalByKey.get(key)
    const definition = definitionFor(key)
    if (!inherited && !definition) continue
    // An excluded field is not in the merge, so there is no effective field to
    // describe it. What it was called and what it would resolve to both come
    // from the scope it was inherited from.
    const base = inherited
      ? { visible: inherited.visible, list_visible: inherited.list_visible, required: inherited.required, writable: inherited.writable, label: inherited.label }
      : { ...BASE_DEFAULTS, label: definition!.label }
    built.push({
      key,
      label: assignment.label_override || base.label,
      field_type: inherited?.type ?? definition!.field_type,
      protected: inherited?.protected ?? definition!.protected_system_field,
      storage: inherited?.storage ?? definition!.storage,
      fromGlobal: !!inherited,
      own: true,
      excluded: true,
      visible: assignment.visible ?? null,
      list_visible: assignment.list_visible ?? null,
      required: assignment.required ?? null,
      writable: assignment.writable ?? null,
      position: assignment.position ?? Number.MAX_SAFE_INTEGER,
      label_override: assignment.label_override ?? null,
      description_override: assignment.description_override ?? null,
      validation_override: assignment.validation_override ?? null,
      configuration_source: assignment.configuration_source,
      base,
    })
  }
  return built
}

/*
 * Loads are token-guarded. The rail is a list of buttons, and two clicks in
 * quick succession leave two requests in flight — without this the slower one
 * wins and the page shows a scope nobody has selected.
 */
let scopeToken = 0

async function loadScope() {
  const token = ++scopeToken
  const typeId = activeTypeId.value
  const path = typeId
    ? `/device-schema/types/${encodeURIComponent(typeId)}`
    : '/device-schema/global'
  const schema = await api<DeviceSchema>(path)
  const globalSchema = typeId ? await api<DeviceSchema>('/device-schema/global') : schema
  if (token !== scopeToken) return
  rows.value = buildRows(schema, globalSchema)
  dirty.value = false
}

/** Rows on the page, as against the ones taken off it. */
const placedRows = computed(() => rows.value.filter((row) => !row.excluded))
const excludedRows = computed(() => rows.value.filter((row) => row.excluded))

/* ------------------------------------------------ one field on this page */

type Flag = 'visible' | 'list_visible' | 'required' | 'writable'

/** What the field resolves to for this flag, override or inherited value. */
function flagValue(row: Row, flag: Flag): boolean {
  const own = row[flag]
  return own === null || own === undefined ? row.base[flag] : own
}

function setFlag(row: Row, flag: Flag, value: boolean) {
  row[flag] = value
  row.own = true
  dirty.value = true
}

/** Whether the table can change a display flag without opening the row editor. */
function canQuickToggle(row: Row, flag: 'visible' | 'list_visible'): boolean {
  if (!isGlobalScope.value && yamlOwned(activeType.value?.configuration_source)) return false
  return flagEditable(row, flag)
}

function visibilityToggleTitle(row: Row, flag: 'visible' | 'list_visible'): string {
  if (!isGlobalScope.value && yamlOwned(activeType.value?.configuration_source)) {
    return 'This device type is owned by a YAML document'
  }
  if (yamlOwned(row.configuration_source)) return 'This assignment is owned by a YAML document'
  if (!canHide(row)) return flag === 'visible'
    ? 'The identity field is always shown on device pages'
    : 'The identity column is always shown in device lists'
  if (flag === 'list_visible' && !flagValue(row, 'visible')) {
    return 'Show this field on device pages before adding it to device lists'
  }
  if (flag === 'visible') {
    return flagValue(row, flag) ? 'Hide from individual device pages' : 'Show on individual device pages'
  }
  return flagValue(row, flag) ? 'Hide from device lists' : 'Show in device lists'
}

/** Drop a type's override and go back to following the global set. */
function clearFlag(row: Row, flag: Flag) {
  row[flag] = null
  dirty.value = true
}

const overridden = (row: Row, flag: Flag) => row[flag] !== null && row[flag] !== undefined

/** A field's state on this page, as the one line the table needs. */
function summaryFor(row: Row): string {
  const parts: string[] = []
  if (!flagValue(row, 'visible')) parts.push('Hidden from device pages')
  else if (!flagValue(row, 'list_visible')) parts.push('Hidden from list')
  if (flagValue(row, 'required')) parts.push('Required')
  if (!flagValue(row, 'writable')) parts.push('Read-only')
  return parts.join(' · ') || 'Shown · optional'
}

/**
 * The three decisions a page makes about a field, as the editor renders them.
 *
 * `words` is indexed by the boolean, so "inherit (hidden)" and "inherit
 * (shown)" come from one place rather than three conditional expressions in
 * the template.
 */
const FLAGS = [
  { key: 'visible', name: 'Device page', on: 'Shown on individual device pages',
    words: ['hidden', 'shown'], locked: 'Every layout needs an identity column.' },
  { key: 'list_visible', name: 'Device list', on: 'Shown as a column in device lists',
    words: ['hidden from list', 'shown in list'], locked: 'The identity column is always shown in lists.' },
  { key: 'required', name: 'Required', on: 'Must have a value',
    words: ['optional', 'required'], locked: 'The application maintains this value.' },
  { key: 'writable', name: 'Editable', on: 'People can change it',
    words: ['read-only', 'editable'], locked: 'The application maintains this value.' },
] as const satisfies readonly { key: Flag; name: string; on: string; words: readonly [string, string]; locked: string }[]

/** Required only means something for a value the document actually stores. */
const canRequire = (row: Row) => row.storage === 'data'

function flagEditable(row: Row, flag: Flag): boolean {
  if (yamlOwned(row.configuration_source)) return false
  if (flag === 'visible') return canHide(row)
  if (flag === 'list_visible') return canHide(row) && flagValue(row, 'visible')
  if (flag === 'required') return canRequire(row)
  // A derived or virtual value has nowhere to write back to.
  return row.storage !== 'derived' && row.storage !== 'virtual'
}

/*
 * `unique_id` is the identity anchor. The backend forces it visible and sorts
 * it first whatever its position says, so the page does not offer to hide it
 * or move it — an offer the save would silently undo.
 */
const ANCHOR = 'unique_id'
const canHide = (row: Row) => row.key !== ANCHOR
const canReorder = (row: Row) => row.key !== ANCHOR

function canRemove(row: Row): boolean {
  if (yamlOwned(row.configuration_source)) return false
  if (row.protected) return false
  // Taking an inherited field off one type is an exclusion, and an
  // installation has to have opted into those for them to mean anything.
  if (!isGlobalScope.value && row.fromGlobal) return !!overview.value?.allow_global_exclusions
  return true
}

function removeReason(row: Row): string {
  if (yamlOwned(row.configuration_source)) return 'Owned by a YAML document'
  if (row.protected) return 'The application reads this field by name'
  if (!isGlobalScope.value && row.fromGlobal && !overview.value?.allow_global_exclusions) {
    return 'This installation does not allow a type to drop an inherited field'
  }
  return ''
}

function removeRow(row: Row) {
  if (!canRemove(row)) return
  if (!isGlobalScope.value && row.fromGlobal) {
    // Inherited: mark it excluded rather than dropping the row, because the
    // exclusion itself is what has to be stored.
    row.excluded = true
    row.own = true
  } else {
    rows.value = rows.value.filter((item) => item !== row)
  }
  if (editingRow.value === row) editingRow.value = null
  dirty.value = true
}

/**
 * The position a field joining the end of the page should take.
 *
 * Read before the row is put back on the page, or the row being placed is the
 * one the end is measured from.
 */
function endPosition(): number {
  const last = placedRows.value[placedRows.value.length - 1]
  return (last?.position ?? 0) + 10
}

/** Nothing is overridden here, so the scope has no reason to hold a row. */
function untouched(row: Row): boolean {
  return !overridden(row, 'visible') && !overridden(row, 'list_visible')
    && !overridden(row, 'required') && !overridden(row, 'writable')
    && !row.label_override && !row.description_override
}

function restoreRow(row: Row) {
  const position = endPosition()
  row.excluded = false
  if (untouched(row)) {
    // Drop the assignment entirely and let the field go back to plain
    // inheritance, including the order it inherits.
    row.own = false
  } else {
    // An excluded row carries no meaningful position — it had been taken off
    // the page. Coming back, it joins the end of it.
    row.position = position
  }
  dirty.value = true
}

/* ------------------------------------------------------ adding and order */

const picking = ref(false)
const pickerQuery = ref('')

/** Definitions this page does not already carry. */
const pickerCandidates = computed(() => {
  const present = new Set(rows.value.filter((row) => !row.excluded).map((row) => row.key))
  const query = pickerQuery.value.trim().toLowerCase()
  return definitions.value
    .filter((definition) => !present.has(definition.key))
    .filter((definition) =>
      !query || definition.key.includes(query) || definition.label.toLowerCase().includes(query))
})

function openPicker() {
  pickerQuery.value = ''
  picking.value = true
}

/** Returns whether the page changed — a field already on it is left alone. */
function addFieldToScope(key: string): boolean {
  const definition = definitionFor(key)
  if (!definition) return false
  const position = endPosition()
  const existing = rows.value.find((row) => row.key === key)
  if (existing) {
    picking.value = false
    // It was taken off this page rather than never added — putting it back is
    // the same action from the administrator's side.
    if (!existing.excluded) return false
    restoreRow(existing)
  } else {
    rows.value.push({
      key: definition.key,
      label: definition.label,
      field_type: definition.field_type,
      protected: definition.protected_system_field,
      storage: definition.storage,
      fromGlobal: false,
      own: true,
      excluded: false,
      // Adding a field means "show it here"; a new row that arrived hidden
      // would look like the button had done nothing.
      visible: true,
      list_visible: true,
      required: null,
      writable: null,
      position,
      label_override: null,
      description_override: null,
      validation_override: null,
      base: { ...BASE_DEFAULTS, label: definition.label },
    })
  }
  picking.value = false
  dirty.value = true
  return true
}

const dragKey = ref('')

function onDragStart(row: Row) {
  dragKey.value = canReorder(row) ? row.key : ''
}

function onDrop(target: Row) {
  if (!dragKey.value || !canReorder(target)) return
  const from = rows.value.findIndex((row) => row.key === dragKey.value)
  const to = rows.value.findIndex((row) => row.key === target.key)
  dragKey.value = ''
  if (from < 0 || to < 0 || from === to) return
  const next = [...rows.value]
  next.splice(to, 0, ...next.splice(from, 1))
  rows.value = next
  renumber()
  dirty.value = true
}

/** Move a row without a pointer; the grip is focusable so this has a keyboard. */
function nudge(row: Row, delta: number) {
  const placed = placedRows.value
  const index = placed.indexOf(row)
  const target = placed[index + delta]
  if (!target) return
  onDragStart(row)
  onDrop(target)
}

/**
 * Renumber the page from its current order.
 *
 * A row whose position is already right is left alone, so on a device type a
 * move materialises an assignment for the rows that actually shifted and no
 * others. Those rows then carry a pinned position and stop following the
 * inherited order — but only `position` is set, so visibility, requirement and
 * editability go on being inherited.
 */
function renumber() {
  placedRows.value.forEach((row, index) => {
    const position = index * 10
    if (row.position !== position) {
      row.position = position
      row.own = true
    }
  })
}

async function saveScope() {
  await run(async () => {
    // A type sends only what it has something of its own to say about;
    // everything else stays inherited, including fields added globally later.
    const payload = rows.value
      .filter((row) => isGlobalScope.value || row.own)
      .map((row) => ({
        field_key: row.key,
        visible: row.visible,
        list_visible: row.list_visible,
        required: row.required,
        writable: row.writable,
        position: row.position,
        label_override: row.label_override || null,
        description_override: row.description_override || null,
        validation_override: row.validation_override || null,
        excluded: row.excluded,
      }))
    const path = activeTypeId.value
      ? `/device-schema/types/${encodeURIComponent(activeTypeId.value)}`
      : '/device-schema/global'
    const saved = await api<any>(path, { method: 'PUT', body: JSON.stringify({ assignments: payload }) })
    if (saved.warnings?.length) report.value = { errors: [], warnings: saved.warnings }
    await refreshAll()
    await loadScope()
  }, 'Saved')
}

/* --------------------------------------------------------- the row editor */

const editingRow = ref<Row | null>(null)
let rowSnapshot: Row | null = null

function editRow(row: Row) {
  rowSnapshot = JSON.parse(JSON.stringify(row))
  editingRow.value = row
}

function cancelRowEdit() {
  if (editingRow.value && rowSnapshot) Object.assign(editingRow.value, rowSnapshot)
  editingRow.value = null
  rowSnapshot = null
}

function closeRowEdit() {
  editingRow.value = null
  rowSnapshot = null
}

/** Switching pages with edits in hand would drop them silently. */
function confirmDiscard(): boolean {
  if (!dirty.value) return true
  return confirm('This page has unsaved changes. Discard them?')
}

function selectScope(typeId: string) {
  if (typeId === activeTypeId.value) return
  if (!confirmDiscard()) return
  dirty.value = false
  activeTypeId.value = typeId
}

function selectTab(id: TabId) {
  if (id === tab.value) return
  if (tab.value === 'layouts' && !confirmDiscard()) return
  dirty.value = false
  tab.value = id
}

/* ----------------------------------------------------------- device types */

const editingType = ref<Partial<DeviceType> | null>(null)
const creatingType = ref(false)

function newType() {
  creatingType.value = true
  editingType.value = { key: '', label: '', description: '', enabled: true, position: deviceTypes.value.length * 10 }
}

function editType(item: DeviceType) {
  creatingType.value = false
  editingType.value = { ...item }
}

async function saveType() {
  const draft = editingType.value
  if (!draft) return
  await run(async () => {
    const body: Record<string, any> = {
      label: draft.label, description: draft.description || null,
      enabled: draft.enabled, position: draft.position,
    }
    if (creatingType.value) {
      const created = await api<DeviceType>('/device-types', { method: 'POST', body: JSON.stringify({ ...body, key: draft.key }) })
      editingType.value = null
      await refreshAll()
      // A new type's page is what the administrator wanted to get to. The
      // watcher on the scope loads it.
      activeTypeId.value = created.id
      return
    }
    await api(`/device-types/${draft.id}`, { method: 'PATCH', body: JSON.stringify(body) })
    editingType.value = null
    await refreshAll()
  }, 'Device type saved')
}

async function deleteType(item: DeviceType) {
  if (!confirm(`Delete the device type “${item.label}”?`)) return
  await run(async () => {
    await api(`/device-types/${item.id}`, { method: 'DELETE' })
    await refreshAll()
    // Back to the inherited set; the page just deleted has nothing to show.
    if (activeTypeId.value === item.id) activeTypeId.value = ''
    else await loadScope()
  }, 'Device type deleted')
}

/* --------------------------------------------------------------- plugins */

interface PluginRow {
  plugin_id: string
  label: string
  version?: string
  installed: boolean
  enabled: boolean
  configuration_defaults?: Record<string, any>
  ai_configuration?: Record<string, any>
  configuration: Record<string, any>
  configuration_source?: string | null
  missing_roles: string[]
  actions: { id: string; label: string; risk: string; scope: string; allow_global_assignment: boolean }[]
}

interface PluginRule {
  id: string
  field_key: string
  operator: 'equals' | 'contains' | 'starts_with'
  value: string
  case_sensitive?: boolean
  configuration: {
    method?: 'ssh' | 'ai' | 'browser' | 'auto' | null
    ssh_command?: string | null
    ssh_port?: number | null
    http_port?: number | null
    https_port?: number | null
    ai_profile_id?: string | null
    ai_model?: string | null
    ai_repeat_model?: string | null
    discovery_roles?: string[] | null
    prompt_addendum?: string | null
    ssh_commands?: { command: string; yields: string[] }[] | null
    ssh_connect_timeout?: number | null
    ssh_command_timeout?: number | null
    ssh_host_key_policy?: 'accept-new' | 'strict' | null
  }
}

const pluginRows = ref<PluginRow[]>([])
const aiProfiles = ref<any[]>([])
const aiModels = (profileId?: string | null) => aiProfiles.value.find((item) => item.id === profileId)?.models || []
const pluginRuleFields = ref<{ key: string; label: string }[]>([])
const configDrafts = ref<Record<string, string>>({})
const pluginRules = (row: PluginRow): PluginRule[] => row.configuration._rules || (row.configuration._rules = [])
const DISCOVERY_ROLE_OPTIONS = [
  { value: 'discovery_hardware', label: 'Hardware version' },
  { value: 'discovery_firmware', label: 'Firmware version' },
  { value: 'discovery_lan_mac', label: 'LAN MAC' },
  { value: 'discovery_wan_mac', label: 'WAN MAC' },
]

function toggleDiscoveryOverride(configuration: Record<string, any>, enabled: boolean) {
  configuration.discovery_roles = enabled
    ? DISCOVERY_ROLE_OPTIONS.map((item) => item.value)
    : null
}

function addInfoSshCommand(configuration: Record<string, any>) {
  if (!Array.isArray(configuration.ssh_commands)) configuration.ssh_commands = []
  configuration.ssh_commands.push({ command: '', yields: DISCOVERY_ROLE_OPTIONS.map((item) => item.value) })
}

function toggleInfoSshCommands(configuration: Record<string, any>, enabled: boolean) {
  configuration.ssh_commands = enabled ? [] : null
  if (enabled) addInfoSshCommand(configuration)
}

function addPluginRule(row: PluginRow) {
  pluginRules(row).push({
    id: globalThis.crypto?.randomUUID?.() || `${Date.now()}`,
    field_key: pluginRuleFields.value.find((field) => field.key === 'make')?.key || pluginRuleFields.value[0]?.key || '',
    operator: 'equals', value: '', configuration: row.plugin_id === 'device-reboot'
      ? { method: 'ssh', ssh_command: null, ssh_port: null }
      : { method: null, http_port: null, https_port: null, ssh_port: null, ssh_commands: null,
          ssh_connect_timeout: null, ssh_command_timeout: null, ssh_host_key_policy: null,
          discovery_roles: null, prompt_addendum: null },
  })
}

function movePluginRule(row: PluginRow, index: number, delta: number) {
  const rules = pluginRules(row)
  const target = index + delta
  if (target < 0 || target >= rules.length) return
  rules.splice(target, 0, ...rules.splice(index, 1))
}

function removePluginRule(row: PluginRow, index: number) {
  pluginRules(row).splice(index, 1)
}

async function loadPlugins() {
  if (!activeTypeId.value) {
    pluginRows.value = []
    return
  }
  const [result, profiles] = await Promise.all([
    api<{ plugins: PluginRow[]; rule_fields: { key: string; label: string }[] }>(
      `/device-schema/types/${encodeURIComponent(activeTypeId.value)}/plugins`,
    ),
    api<any[]>('/ai/providers'),
  ])
  aiProfiles.value = profiles.filter((item) => item.enabled)
  pluginRuleFields.value = result.rule_fields || []
  pluginRows.value = result.plugins.map(row => row.plugin_id === 'device-reboot'
    ? { ...row, configuration: { ...row.configuration, method: row.configuration.method ?? null } }
    : row.plugin_id === 'device-info-agent'
      ? { ...row, configuration: { ...row.configuration } }
      : row)
  configDrafts.value = Object.fromEntries(
    result.plugins.map((row) => [row.plugin_id, JSON.stringify(row.configuration || {}, null, 2)]),
  )
}

async function savePlugins() {
  await run(async () => {
    const plugins = pluginRows.value
      .filter((row) => row.enabled)
      .map((row) => {
        let configuration: Record<string, any> = {}
        try {
          configuration = ['device-reboot', 'device-info-agent'].includes(row.plugin_id)
            ? row.configuration : JSON.parse(configDrafts.value[row.plugin_id] || '{}')
        } catch {
          throw new Error(`${row.label} configuration must be valid JSON`)
        }
        return { plugin_id: row.plugin_id, enabled: true, configuration }
      })
    await api(`/device-schema/types/${encodeURIComponent(activeTypeId.value)}/plugins`, {
      method: 'PUT', body: JSON.stringify({ plugins }),
    })
    await loadPlugins()
  }, 'Plugin assignments saved')
}

const hasDisruptive = (row: PluginRow) => row.actions.some((action) => action.risk === 'disruptive')

function inputPort(event: Event): number | null {
  const value = (event.target as HTMLInputElement).value
  return value === '' ? null : Number(value)
}

/**
 * A field's flags as one line.
 *
 * Built and joined rather than rendered as a run of conditional spans, each
 * carrying its own separator: a protected field that is neither indexed nor
 * sensitive rendered as a leading "· system", a separator with nothing on the
 * other side of it.
 */
function flagsFor(definition: FieldDefinition): string {
  const flags: string[] = []
  // Unique implies indexed — the index is what enforces it — so saying both
  // tells the reader nothing.
  if (definition.unique_value) flags.push('unique')
  else if (definition.indexed) flags.push('indexed')
  if (definition.sensitive) flags.push('sensitive')
  // Spelled out only where it is not the default, so the common case stays a
  // single short word and an unusual one is visible without opening the field.
  if (definition.opens_web_page) {
    const scheme = definition.link_scheme === 'https' ? 'https' : 'http'
    flags.push(definition.link_port ? `links ${scheme}:${definition.link_port}`
      : scheme === 'https' ? 'links https' : 'links')
  }
  if (definition.protected_system_field) flags.push('system')
  if (!definition.enabled) flags.push('disabled')
  return flags.join(' · ')
}

/* ----------------------------------------------------------------- shell */

async function loadOverview() {
  overview.value = await api('/device-schema')
}

async function refreshAll() {
  invalidateDeviceSchemas()
  await Promise.all([loadDefinitions(), loadDeviceTypes(true), loadOverview()])
}

async function exportYaml() {
  await run(async () => {
    await downloadFile('/device-schema/export', 'testbench-device-schema.yaml')
  }, 'Bootstrap ConfigMap downloaded')
}

function chooseImport() {
  importInput.value?.click()
}

async function previewImport(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  importing.value = true
  error.value = ''
  try {
    importFile.value = file
    importPreview.value = await uploadFile('/device-schema/import?dry_run=true', file)
  } catch (e: any) {
    importFile.value = null
    importPreview.value = null
    fail(e)
  } finally {
    importing.value = false
  }
}

function cancelImport() {
  importFile.value = null
  importPreview.value = null
}

async function applyImport() {
  if (!importFile.value) return
  importing.value = true
  error.value = ''
  try {
    const result: AdditiveImportResult = await uploadFile('/device-schema/import', importFile.value)
    cancelImport()
    invalidateEntityFields()
    await refreshAll()
    flash(result.total ? `${result.total} schema addition${result.total === 1 ? '' : 's'} imported` : 'Nothing new to import')
  } catch (e: any) {
    fail(e)
  } finally {
    importing.value = false
  }
}

const importLabels: Record<string, string> = {
  fields: 'Fields',
  device_types: 'Device types',
  global_assignments: 'Global layout fields',
  type_assignments: 'Device-type layout fields',
  plugins: 'Plugin assignments',
  vendor_device_fields: 'Vendor-device fields',
}

watch(tab, (value) => {
  router.replace({ query: { ...route.query, tab: value } })
  if (value === 'layouts') void loadScope()
  if (value === 'plugins') {
    if (!activeTypeId.value) activeTypeId.value = deviceTypes.value[0]?.id || ''
    void loadPlugins()
  }
  if (['software', 'tests', 'vendor_devices'].includes(value)) void loadEntitySchema()
})

watch(activeTypeId, () => {
  if (tab.value === 'plugins') void loadPlugins()
  else if (tab.value === 'layouts') void loadScope()
})

onMounted(async () => {
  const requested = route.query.tab as string | undefined
  if (requested) {
    const resolved = TAB_ALIASES[requested] || (TABS.some((item) => item.id === requested) ? requested as TabId : null)
    if (resolved) tab.value = resolved
    // An alias resolves to a tab that no longer has that name; leave the URL
    // saying what the page is actually showing.
    if (resolved && resolved !== requested) {
      router.replace({ query: { ...route.query, tab: resolved } })
    }
  }
  await refreshAll()
  // The retired Device Types tab landed on a type, not on the inherited set.
  if (requested === 'types' || tab.value === 'plugins') {
    activeTypeId.value = deviceTypes.value[0]?.id || ''
  }
  if (tab.value === 'layouts') await loadScope()
  if (tab.value === 'plugins') await loadPlugins()
  if (tab.value === 'software' || tab.value === 'tests') await loadEntitySchema()
})
</script>

<template>
  <!-- page-flow, not plain page: these tabs are long tables that grow, and the
       default panel is capped at one viewport for the DataTable pages. -->
  <div class="page page-flow">
    <div class="page-header">
      <div>
        <h2>Schema</h2>
        <p class="muted">
          Fields and rules for devices, software, and tests.
        </p>
      </div>
      <div v-if="tab !== 'software' && tab !== 'tests'" class="toolbar">
        <input ref="importInput" class="file-input" type="file" accept=".yaml,.yml,application/yaml,text/yaml" @change="previewImport" />
        <button class="btn" :disabled="busy || importing" @click="chooseImport">
          {{ importing ? 'Reading YAML…' : 'Import bootstrap YAML' }}
        </button>
        <button class="btn" :disabled="busy" @click="exportYaml">Download bootstrap YAML</button>
      </div>
    </div>

    <p v-if="overview?.yaml_configured" class="banner">
      <template v-if="overview.reconciliation === 'bootstrap'">
        This configuration was initialized from a DeviceSchema document. The admin GUI owns imported objects.
      </template>
      <template v-else>
        A DeviceSchema document owns part of this configuration
        (<strong>{{ overview.reconciliation }}</strong> mode). Objects marked <em>YAML</em> are read-only here.
      </template>
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <div v-if="report && (report.errors.length || report.warnings.length)" class="report">
      <p v-for="(item, index) in report.errors" :key="`e${index}`" class="error">{{ item.message }}</p>
      <p v-for="(item, index) in report.warnings" :key="`w${index}`" class="muted">{{ item.message }}</p>
    </div>

    <div class="tabs">
      <button v-for="t in TABS" :key="t.id" :class="{ active: tab === t.id }" @click="selectTab(t.id)">
        {{ t.label }}
      </button>
    </div>

    <!-- --------------------------------------------------------- layouts -->
    <section v-if="tab === 'layouts'" class="layouts">
      <!-- The inherited set is the first page in the list rather than a tab of
           its own: it is the same job, and putting it above the types makes
           what inherits from what something you can see. -->
      <nav class="scope-rail">
        <button :class="{ active: isGlobalScope }" @click="selectScope('')">
          <strong>All device types</strong>
          <span class="muted">Inherited by every type</span>
        </button>
        <button
          v-for="item in deviceTypes"
          :key="item.id"
          :class="{ active: activeTypeId === item.id }"
          @click="selectScope(item.id)"
        >
          <strong>{{ item.label }}{{ item.enabled ? '' : ' (disabled)' }}</strong>
          <span class="muted">{{ item.device_count }} device(s)</span>
        </button>
        <button class="rail-add" @click="newType">+ New device type</button>
      </nav>

      <div class="scope-body">
        <div class="section-head">
          <div class="scope-title">
            <h3>{{ scopeLabel }}</h3>
            <p v-if="isGlobalScope" class="muted">
              Fields every device type inherits — including types created later.
            </p>
            <p v-else-if="activeType" class="muted">
              <code>/devices/type/{{ activeType.key }}</code> ·
              {{ activeType.device_count }} device(s). Starts as the inherited page; anything you
              change here applies to this type only.
            </p>
            <!-- Deleting a type is not an edit to its layout and is not
                 something anyone does twice, so it stays out of the bar. Its
                 neighbour up there — renaming — is ordinary, and demoting the
                 two together left people unable to find it. -->
            <p v-if="!isGlobalScope && activeType" class="type-admin muted">
              <button
                class="linkish"
                :disabled="activeType.device_count > 0 || yamlOwned(activeType.configuration_source)"
                :title="activeType.device_count ? 'Assigned to devices — disable it instead' : ''"
                @click="deleteType(activeType)"
              >Delete this device type</button>
            </p>
          </div>
          <div class="scope-actions">
            <button
              v-if="!isGlobalScope && activeType"
              class="btn"
              :disabled="yamlOwned(activeType.configuration_source)"
              :title="yamlOwned(activeType.configuration_source) ? 'Owned by a YAML document' : 'Change this type\'s display name, description or order'"
              @click="editType(activeType)"
            >Edit type</button>
            <button class="btn" @click="openPicker">+ Add field</button>
            <button class="btn btn-primary" :disabled="busy || !dirty" @click="saveScope">
              {{ dirty ? 'Save changes' : 'Saved' }}
            </button>
          </div>
        </div>

        <table class="data-list layout-table">
          <thead>
            <tr>
              <th class="grip-col"></th><th>Field</th>
              <th class="visibility-col">Device page</th>
              <th class="visibility-col">Device list</th>
              <th>Behavior</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in placedRows"
              :key="row.key"
              :class="{ hidden: !flagValue(row, 'visible') }"
              @dragover.prevent
              @drop="onDrop(row)"
            >
              <td class="grip-col">
                <button
                  v-if="canReorder(row)"
                  class="grip"
                  draggable="true"
                  title="Drag to reorder, or Alt+↑ / Alt+↓"
                  @dragstart="onDragStart(row)"
                  @keydown.alt.up.prevent="nudge(row, -1)"
                  @keydown.alt.down.prevent="nudge(row, 1)"
                >⠿</button>
                <span v-else class="muted anchor" title="The identity column is always first">⚓</span>
              </td>
              <td data-label="Field">
                <strong>{{ row.label }}</strong>
                <code>{{ row.key }}</code>
                <span v-if="row.protected" class="tag">system</span>
                <span v-if="!isGlobalScope && !row.fromGlobal" class="tag">this type only</span>
                <span v-if="yamlOwned(row.configuration_source)" class="tag">YAML</span>
              </td>
              <td data-label="Device page" class="visibility-col">
                <input
                  type="checkbox"
                  :checked="flagValue(row, 'visible')"
                  :disabled="!canQuickToggle(row, 'visible')"
                  :aria-label="`Show ${row.label} on individual device pages`"
                  :title="visibilityToggleTitle(row, 'visible')"
                  @change="setFlag(row, 'visible', ($event.target as HTMLInputElement).checked)"
                />
              </td>
              <td data-label="Device list" class="visibility-col">
                <input
                  type="checkbox"
                  :checked="flagValue(row, 'list_visible')"
                  :disabled="!canQuickToggle(row, 'list_visible')"
                  :aria-label="`Show ${row.label} in device lists`"
                  :title="visibilityToggleTitle(row, 'list_visible')"
                  @change="setFlag(row, 'list_visible', ($event.target as HTMLInputElement).checked)"
                />
              </td>
              <td data-label="Behavior" class="muted">{{ summaryFor(row) }}</td>
              <td class="row-actions">
                <button class="btn" :disabled="yamlOwned(row.configuration_source)" @click="editRow(row)">Edit</button>
                <!-- Not a danger button: taking a field off a page changes what
                     is displayed and validated and never touches a stored
                     value, and a column of red down a 20-row table says
                     otherwise. Delete, on the catalog tab, is the destructive
                     one and keeps the weight. -->
                <button
                  class="btn"
                  :disabled="!canRemove(row)"
                  :title="removeReason(row)"
                  @click="removeRow(row)"
                >Remove</button>
              </td>
            </tr>
            <tr v-if="!placedRows.length">
              <td colspan="5" class="muted">No fields on this page yet.</td>
            </tr>
          </tbody>
        </table>

        <!-- Moving a field is the one edit whose consequence is not visible in
             the row it happened to, so it is worth saying before rather than
             after. -->
        <p v-if="!isGlobalScope && placedRows.length" class="muted foot-note">
          A field you move keeps this type's order rather than the inherited one.
          Everything else about it stays inherited.
        </p>

        <!-- Excluded fields are still inherited; they are just switched off
             here. Listing them is what makes that reversible. -->
        <section v-if="excludedRows.length" class="removed">
          <h4>Removed from this type</h4>
          <ul>
            <li v-for="row in excludedRows" :key="row.key">
              {{ row.label }} <code>{{ row.key }}</code>
              <button class="btn" @click="restoreRow(row)">Restore</button>
            </li>
          </ul>
        </section>
      </div>
    </section>

    <!-- ---------------------------------------------------------- fields -->
    <section v-else-if="tab === 'fields'">
      <div class="section-head">
        <p class="muted">
          The catalog of reusable field definitions. Put a field on a page from
          <button class="linkish" @click="tab = 'layouts'">Device Layouts</button>. Disabling a field
          takes it off every layout without deleting what devices already store.
        </p>
        <div class="scope-actions">
          <button class="btn btn-primary" @click="newField()">+ New field</button>
        </div>
      </div>
      <table class="data-list">
        <thead>
          <tr>
            <th>Key</th><th>Label</th><th>Type</th><th>Role</th><th>Flags</th><th>In use</th><th>Owner</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="definition in definitions" :key="definition.id" :class="{ disabled: !definition.enabled }">
            <td data-label="Key"><code>{{ definition.key }}</code></td>
            <td data-label="Label">{{ definition.label }}</td>
            <td data-label="Type">{{ definition.field_type }}</td>
            <td data-label="Role" class="muted">{{ definition.plugin_role || '—' }}</td>
            <td data-label="Flags" class="muted">{{ flagsFor(definition) || '—' }}</td>
            <td data-label="In use" class="muted">
              {{ definition.usage.global ? 'global' : definition.usage.device_types.join(', ') || 'unassigned' }}
              <template v-if="definition.usage.devices_with_values">
                · {{ definition.usage.devices_with_values }} device(s)
              </template>
            </td>
            <td data-label="Owner" class="muted">{{ ownershipLabel(definition.configuration_source) }}</td>
            <td class="row-actions">
              <button class="btn" :disabled="yamlOwned(definition.configuration_source)" @click="editField(definition)">
                Edit
              </button>
              <button
                class="btn btn-danger"
                :disabled="definition.protected_system_field || yamlOwned(definition.configuration_source)"
                @click="deleteField(definition)"
              >
                Delete
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- Software and tests have one layout each. Relationship and application
         fields stay visible here but only their presentation can be edited. -->
    <section v-else-if="tab === 'software' || tab === 'tests' || tab === 'vendor_devices'">
      <div class="section-head">
        <p class="muted">
          Protected fields preserve application identity and relationships. Additional fields are stored in JSON and may be added or removed.
        </p>
        <div class="scope-actions">
          <button class="btn" :disabled="busy || !entityLayoutDirty" @click="saveEntityLayout">{{ entityLayoutDirty ? 'Save changes' : 'Saved' }}</button>
          <button class="btn btn-primary" @click="newEntityField">+ New field</button>
        </div>
      </div>
      <table class="data-list">
        <thead><tr><th class="grip-col"></th><th>Key</th><th>Label</th><th>Type</th><th class="visibility-col">Shown</th><th>Required</th><th>Storage</th><th></th></tr></thead>
        <tbody>
          <tr v-for="field in entityFields" :key="field.id" :class="{ hidden: !field.list_visible }" @dragover.prevent @drop="dropEntityField(field)">
            <td class="grip-col"><button class="grip" draggable="true" title="Drag to reorder, or Alt+↑ / Alt+↓" @dragstart="entityDragId = field.id" @keydown.alt.up.prevent="nudgeEntityField(field, -1)" @keydown.alt.down.prevent="nudgeEntityField(field, 1)">⠿</button></td>
            <td data-label="Key"><code>{{ field.key }}</code> <span v-if="field.protected" class="tag">protected</span></td>
            <td data-label="Label">{{ field.label }}</td>
            <td data-label="Type">{{ field.type }}</td>
            <td data-label="Shown" class="visibility-col"><input type="checkbox" :checked="field.list_visible" :disabled="field.list_visibility_locked" :title="field.list_visibility_locked ? 'Application-required fields cannot be hidden from lists' : field.list_visible ? 'Hide from the main list' : 'Show in the main list'" @change="toggleEntityField(field, ($event.target as HTMLInputElement).checked)" /></td>
            <td data-label="Required">{{ field.required ? 'Yes' : 'No' }}</td>
            <td data-label="Storage" class="muted">{{ field.database_storage }}</td>
            <td class="row-actions">
              <button class="btn" @click="editEntityField(field)">Edit</button>
              <button class="btn btn-danger" :disabled="field.protected" :title="field.protected ? 'Required by the application' : ''" @click="deleteEntityField(field)">Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- --------------------------------------------------------- plugins -->
    <section v-else>
      <div class="section-head">
        <label class="type-picker">
          Device type
          <select :value="activeTypeId" @change="activeTypeId = ($event.target as HTMLSelectElement).value">
            <option v-for="item in deviceTypes" :key="item.id" :value="item.id">{{ item.label }}</option>
          </select>
        </label>
        <div class="scope-actions">
          <button class="btn btn-primary" :disabled="busy || !activeTypeId" @click="savePlugins">Save changes</button>
        </div>
      </div>
      <p class="muted">
        Nothing is available to a device type until it is enabled here, and the API repeats the check
        on every invocation — hiding a button is not what makes an action safe.
      </p>
      <p v-if="!pluginRows.length" class="muted">No plugins are installed in this deployment.</p>
      <div v-for="row in pluginRows" :key="row.plugin_id" class="card plugin-card">
        <div class="plugin-head">
          <label class="check">
            <input type="checkbox" v-model="row.enabled" :disabled="yamlOwned(row.configuration_source)" />
            <strong>{{ row.label }}</strong>
          </label>
          <span class="muted">{{ row.version || '' }}</span>
          <span v-if="!row.installed" class="tag warn">not installed</span>
          <span v-if="hasDisruptive(row)" class="tag danger">disruptive</span>
          <span v-if="yamlOwned(row.configuration_source)" class="tag">YAML</span>
        </div>
        <p v-if="row.missing_roles.length" class="error">
          This device type has no field for: {{ row.missing_roles.join(', ') }}. Add one on the
          Device Layouts tab, or the action will never be able to run.
        </p>
        <ul class="muted action-list">
          <li v-for="action in row.actions" :key="action.id">
            {{ action.label || action.id }} — {{ action.scope }}, {{ optionLabel(action.risk) }}
            <template v-if="!action.allow_global_assignment">· must be authorised per type</template>
          </li>
        </ul>
        <div v-if="row.enabled && row.plugin_id === 'device-reboot'" class="config">
          <template v-if="row.ai_configuration?.locked">
            <label>AI endpoint<input :value="row.ai_configuration.url" disabled /></label>
            <label>AI model<input :value="row.ai_configuration.model" disabled /></label>
            <label>AI repeat model<input :value="row.ai_configuration.repeat_model || row.ai_configuration.model" disabled /></label>
            <small class="muted">Managed by Helm; AI overrides are disabled.</small>
          </template>
          <template v-else>
            <label>AI provider override<select v-model="row.configuration.ai_profile_id" :disabled="yamlOwned(row.configuration_source)"><option :value="null">Inherit global</option><option v-for="p in aiProfiles" :key="p.id" :value="p.id">{{ p.name }}</option></select></label>
            <label>AI model override<input v-model="row.configuration.ai_model" :list="`ai-models-${row.plugin_id}`" placeholder="Inherit global" :disabled="yamlOwned(row.configuration_source)" /></label>
            <label>AI repeat model override<input v-model="row.configuration.ai_repeat_model" :list="`ai-models-${row.plugin_id}`" placeholder="Inherit model" :disabled="yamlOwned(row.configuration_source)" /></label>
            <datalist :id="`ai-models-${row.plugin_id}`"><option v-for="model in aiModels(row.configuration.ai_profile_id)" :key="model" :value="model" /></datalist>
          </template>
          <label>Reboot method
            <select v-model="row.configuration.method" :disabled="yamlOwned(row.configuration_source)">
              <option :value="null">Inherit global ({{ row.configuration_defaults?.method || 'ai' }})</option>
              <option value="ssh">SSH</option>
              <option value="ai">AI browser discovery</option>
            </select>
          </label>
          <label>SSH command override
            <input :value="row.configuration.ssh_command || ''"
              @input="row.configuration.ssh_command = ($event.target as HTMLInputElement).value || null"
              :placeholder="row.configuration_defaults?.ssh_command || 'reboot'"
              :disabled="yamlOwned(row.configuration_source)" />
          </label>
          <label>SSH port override
            <input type="number" min="1" max="65535" :value="row.configuration.ssh_port ?? ''"
              @input="row.configuration.ssh_port = inputPort($event)"
              :placeholder="String(row.configuration_defaults?.ssh_port || 22)"
              :disabled="yamlOwned(row.configuration_source)" />
          </label>
          <small class="muted">Leave empty to inherit the global command. Used only for SSH; commands must not require interactive prompts.</small>
          <div class="rule-head">
            <strong>Ordered device rules</strong>
            <button type="button" class="btn" :disabled="yamlOwned(row.configuration_source)" @click="addPluginRule(row)">+ Add rule</button>
          </div>
          <small class="muted">The first matching rule wins. A device-specific override wins over every rule.</small>
          <div v-for="(rule, index) in pluginRules(row)" :key="rule.id" class="plugin-rule">
            <span class="rule-order">{{ index + 1 }}</span>
            <select v-model="rule.field_key" :disabled="yamlOwned(row.configuration_source)">
              <option v-for="field in pluginRuleFields" :key="field.key" :value="field.key">{{ field.label }}</option>
            </select>
            <select v-model="rule.operator" :disabled="yamlOwned(row.configuration_source)">
              <option value="equals">equals</option>
              <option value="contains">contains</option>
              <option value="starts_with">starts with</option>
            </select>
            <input v-model="rule.value" placeholder="Value, e.g. MikroTik" :disabled="yamlOwned(row.configuration_source)" />
            <select v-model="rule.configuration.method" :disabled="yamlOwned(row.configuration_source)">
              <option value="ssh">SSH</option>
              <option value="ai">AI browser discovery</option>
              <option :value="null">Inherit type</option>
            </select>
            <input :value="rule.configuration.ssh_command || ''"
              @input="rule.configuration.ssh_command = ($event.target as HTMLInputElement).value || null"
              placeholder="SSH command (inherit when empty)" :disabled="yamlOwned(row.configuration_source)" />
            <input type="number" min="1" max="65535" :value="rule.configuration.ssh_port ?? ''"
              @input="rule.configuration.ssh_port = inputPort($event)"
              placeholder="SSH port (inherit)" :disabled="yamlOwned(row.configuration_source)" />
            <template v-if="!row.ai_configuration?.locked">
              <select v-model="rule.configuration.ai_profile_id" :disabled="yamlOwned(row.configuration_source)"><option :value="null">AI provider: inherit</option><option v-for="p in aiProfiles" :key="p.id" :value="p.id">{{ p.name }}</option></select>
              <input v-model="rule.configuration.ai_model" :list="`ai-models-${row.plugin_id}-${index}`" placeholder="AI model (inherit)" :disabled="yamlOwned(row.configuration_source)" />
              <input v-model="rule.configuration.ai_repeat_model" :list="`ai-models-${row.plugin_id}-${index}`" placeholder="AI repeat model (inherit)" :disabled="yamlOwned(row.configuration_source)" />
              <datalist :id="`ai-models-${row.plugin_id}-${index}`"><option v-for="model in aiModels(rule.configuration.ai_profile_id)" :key="model" :value="model" /></datalist>
            </template>
            <label class="check"><input type="checkbox" v-model="rule.case_sensitive" :disabled="yamlOwned(row.configuration_source)" />Case-sensitive</label>
            <div class="rule-actions">
              <button type="button" class="btn" :disabled="index === 0 || yamlOwned(row.configuration_source)" @click="movePluginRule(row, index, -1)">↑</button>
              <button type="button" class="btn" :disabled="index === pluginRules(row).length - 1 || yamlOwned(row.configuration_source)" @click="movePluginRule(row, index, 1)">↓</button>
              <button type="button" class="btn btn-danger" :disabled="yamlOwned(row.configuration_source)" @click="removePluginRule(row, index)">Remove</button>
            </div>
          </div>
        </div>
        <div v-else-if="row.enabled && row.plugin_id === 'device-info-agent'" class="config">
          <label>Collection method
            <select v-model="row.configuration.method" :disabled="yamlOwned(row.configuration_source)">
              <option :value="null">Inherit (browser)</option><option value="browser">Browser</option>
              <option value="ssh">SSH only</option><option value="auto">Auto (SSH, then browser)</option>
            </select>
          </label>
          <template v-if="row.ai_configuration?.locked">
            <label>AI endpoint<input :value="row.ai_configuration.url" disabled /></label>
            <label>AI model<input :value="row.ai_configuration.model" disabled /></label>
            <label>AI repeat model<input :value="row.ai_configuration.repeat_model || row.ai_configuration.model" disabled /></label>
            <small class="muted">Managed by Helm; AI overrides are disabled.</small>
          </template>
          <template v-else>
            <label>AI provider override<select v-model="row.configuration.ai_profile_id" :disabled="yamlOwned(row.configuration_source)"><option :value="null">Inherit global</option><option v-for="p in aiProfiles" :key="p.id" :value="p.id">{{ p.name }}</option></select></label>
            <label>AI model override<input v-model="row.configuration.ai_model" :list="`ai-models-${row.plugin_id}`" placeholder="Inherit global" :disabled="yamlOwned(row.configuration_source)" /></label>
            <label>AI repeat model override<input v-model="row.configuration.ai_repeat_model" :list="`ai-models-${row.plugin_id}`" placeholder="Inherit model" :disabled="yamlOwned(row.configuration_source)" /></label>
            <datalist :id="`ai-models-${row.plugin_id}`"><option v-for="model in aiModels(row.configuration.ai_profile_id)" :key="model" :value="model" /></datalist>
          </template>
          <label>HTTP port override
            <input type="number" min="1" max="65535" :value="row.configuration.http_port ?? ''"
              @input="row.configuration.http_port = inputPort($event)"
              :placeholder="String(row.configuration_defaults?.http_port || 80)"
              :disabled="yamlOwned(row.configuration_source)" />
          </label>
          <label>HTTPS port override
            <input type="number" min="1" max="65535" :value="row.configuration.https_port ?? ''"
              @input="row.configuration.https_port = inputPort($event)"
              :placeholder="String(row.configuration_defaults?.https_port || 443)"
              :disabled="yamlOwned(row.configuration_source)" />
          </label>
          <label>SSH port override
            <input type="number" min="1" max="65535" :value="row.configuration.ssh_port ?? ''"
              @input="row.configuration.ssh_port = inputPort($event)" placeholder="22"
              :disabled="yamlOwned(row.configuration_source)" />
          </label>
          <label>SSH host keys
            <select v-model="row.configuration.ssh_host_key_policy" :disabled="yamlOwned(row.configuration_source)">
              <option :value="null">Inherit (accept new)</option><option value="accept-new">Accept new</option><option value="strict">Strict</option>
            </select>
          </label>
          <label>SSH connect timeout (seconds)<input v-model.number="row.configuration.ssh_connect_timeout" type="number" min="1" max="300" placeholder="15" :disabled="yamlOwned(row.configuration_source)" /></label>
          <label>SSH command timeout (seconds)<input v-model.number="row.configuration.ssh_command_timeout" type="number" min="1" max="300" placeholder="30" :disabled="yamlOwned(row.configuration_source)" /></label>
          <fieldset class="config-fieldset info-command-editor">
            <legend>Ordered SSH commands</legend>
            <label class="check"><input type="checkbox" :checked="Array.isArray(row.configuration.ssh_commands)"
              @change="toggleInfoSshCommands(row.configuration, ($event.target as HTMLInputElement).checked)"
              :disabled="yamlOwned(row.configuration_source)" /> Override inherited commands</label>
            <div v-for="(command, commandIndex) in (row.configuration.ssh_commands || [])" :key="commandIndex" class="info-command-row">
              <input class="command-input" v-model="command.command" placeholder="show version" :disabled="yamlOwned(row.configuration_source)" />
              <div class="command-yields">
                <label v-for="item in DISCOVERY_ROLE_OPTIONS" :key="item.value" class="check"><input v-model="command.yields" type="checkbox" :value="item.value" :disabled="yamlOwned(row.configuration_source)" />{{ item.label }}</label>
              </div>
              <button type="button" class="btn btn-danger" @click="row.configuration.ssh_commands.splice(commandIndex, 1)" :disabled="yamlOwned(row.configuration_source)">Remove</button>
            </div>
            <button v-if="Array.isArray(row.configuration.ssh_commands)" type="button" class="btn" @click="addInfoSshCommand(row.configuration)" :disabled="yamlOwned(row.configuration_source)">+ Add command</button>
          </fieldset>
          <fieldset class="config-fieldset">
            <legend>Information to gather</legend>
            <label class="check"><input type="checkbox"
              :checked="Array.isArray(row.configuration.discovery_roles)"
              :disabled="yamlOwned(row.configuration_source)"
              @change="toggleDiscoveryOverride(row.configuration, ($event.target as HTMLInputElement).checked)" /> Override global fields</label>
            <label v-for="item in DISCOVERY_ROLE_OPTIONS" :key="item.value" class="check">
              <input v-model="row.configuration.discovery_roles" type="checkbox" :value="item.value"
                :disabled="!Array.isArray(row.configuration.discovery_roles) || yamlOwned(row.configuration_source)" /> {{ item.label }}
            </label>
          </fieldset>
          <label>Prompt guidance
            <textarea :value="row.configuration.prompt_addendum || ''" maxlength="8000"
              @input="row.configuration.prompt_addendum = ($event.target as HTMLTextAreaElement).value || null"
              placeholder="Inherit global guidance" :disabled="yamlOwned(row.configuration_source)"></textarea>
          </label>
          <small class="muted">Leave settings empty to inherit. Device-specific overrides take priority.</small>
          <div class="rule-head">
            <strong>Ordered device rules</strong>
            <button type="button" class="btn" :disabled="yamlOwned(row.configuration_source)" @click="addPluginRule(row)">+ Add rule</button>
          </div>
          <small class="muted">The first matching rule wins. A device-specific override wins over every rule.</small>
          <div v-for="(rule, index) in pluginRules(row)" :key="rule.id" class="plugin-rule">
            <span class="rule-order">{{ index + 1 }}</span>
            <select v-model="rule.field_key" :disabled="yamlOwned(row.configuration_source)">
              <option v-for="field in pluginRuleFields" :key="field.key" :value="field.key">{{ field.label }}</option>
            </select>
            <select v-model="rule.operator" :disabled="yamlOwned(row.configuration_source)">
              <option value="equals">equals</option>
              <option value="contains">contains</option>
              <option value="starts_with">starts with</option>
            </select>
            <input v-model="rule.value" placeholder="Value" :disabled="yamlOwned(row.configuration_source)" />
            <select v-model="rule.configuration.method" :disabled="yamlOwned(row.configuration_source)"><option :value="null">Method: inherit</option><option value="browser">Browser</option><option value="ssh">SSH only</option><option value="auto">Auto</option></select>
            <input type="number" min="1" max="65535" :value="rule.configuration.http_port ?? ''"
              @input="rule.configuration.http_port = inputPort($event)" placeholder="HTTP port (inherit)"
              :disabled="yamlOwned(row.configuration_source)" />
            <input type="number" min="1" max="65535" :value="rule.configuration.https_port ?? ''"
              @input="rule.configuration.https_port = inputPort($event)" placeholder="HTTPS port (inherit)"
              :disabled="yamlOwned(row.configuration_source)" />
            <input type="number" min="1" max="65535" :value="rule.configuration.ssh_port ?? ''" @input="rule.configuration.ssh_port = inputPort($event)" placeholder="SSH port (inherit)" :disabled="yamlOwned(row.configuration_source)" />
            <fieldset class="config-fieldset compact info-command-editor"><legend>SSH commands</legend>
              <label class="check"><input type="checkbox" :checked="Array.isArray(rule.configuration.ssh_commands)" @change="toggleInfoSshCommands(rule.configuration, ($event.target as HTMLInputElement).checked)" :disabled="yamlOwned(row.configuration_source)" />Override</label>
              <div v-for="(command, commandIndex) in (rule.configuration.ssh_commands || [])" :key="commandIndex" class="info-command-row">
                <input class="command-input" v-model="command.command" placeholder="show version" :disabled="yamlOwned(row.configuration_source)" />
                <div class="command-yields">
                  <label v-for="item in DISCOVERY_ROLE_OPTIONS" :key="item.value" class="check"><input v-model="command.yields" type="checkbox" :value="item.value" :disabled="yamlOwned(row.configuration_source)" />{{ item.label }}</label>
                </div>
                <button type="button" class="btn btn-danger" @click="rule.configuration.ssh_commands!.splice(commandIndex, 1)" :disabled="yamlOwned(row.configuration_source)">Remove</button>
              </div>
              <button v-if="Array.isArray(rule.configuration.ssh_commands)" type="button" class="btn" @click="addInfoSshCommand(rule.configuration)" :disabled="yamlOwned(row.configuration_source)">+ Add command</button>
            </fieldset>
            <fieldset class="config-fieldset compact">
              <legend>Discovery fields</legend>
              <label class="check"><input type="checkbox"
                :checked="Array.isArray(rule.configuration.discovery_roles)"
                :disabled="yamlOwned(row.configuration_source)"
                @change="toggleDiscoveryOverride(rule.configuration, ($event.target as HTMLInputElement).checked)" /> Override</label>
              <label v-for="item in DISCOVERY_ROLE_OPTIONS" :key="item.value" class="check">
                <input v-model="rule.configuration.discovery_roles" type="checkbox" :value="item.value"
                  :disabled="!Array.isArray(rule.configuration.discovery_roles) || yamlOwned(row.configuration_source)" /> {{ item.label }}
              </label>
            </fieldset>
            <textarea :value="rule.configuration.prompt_addendum || ''" maxlength="8000"
              @input="rule.configuration.prompt_addendum = ($event.target as HTMLTextAreaElement).value || null"
              placeholder="Prompt guidance (inherit when empty)" :disabled="yamlOwned(row.configuration_source)"></textarea>
            <template v-if="!row.ai_configuration?.locked">
              <select v-model="rule.configuration.ai_profile_id" :disabled="yamlOwned(row.configuration_source)"><option :value="null">AI provider: inherit</option><option v-for="p in aiProfiles" :key="p.id" :value="p.id">{{ p.name }}</option></select>
              <input v-model="rule.configuration.ai_model" :list="`ai-models-${row.plugin_id}-${index}`" placeholder="AI model (inherit)" :disabled="yamlOwned(row.configuration_source)" />
              <input v-model="rule.configuration.ai_repeat_model" :list="`ai-models-${row.plugin_id}-${index}`" placeholder="AI repeat model (inherit)" :disabled="yamlOwned(row.configuration_source)" />
              <datalist :id="`ai-models-${row.plugin_id}-${index}`"><option v-for="model in aiModels(rule.configuration.ai_profile_id)" :key="model" :value="model" /></datalist>
            </template>
            <label class="check"><input type="checkbox" v-model="rule.case_sensitive" :disabled="yamlOwned(row.configuration_source)" />Case-sensitive</label>
            <div class="rule-actions">
              <button type="button" class="btn" :disabled="index === 0 || yamlOwned(row.configuration_source)" @click="movePluginRule(row, index, -1)">↑</button>
              <button type="button" class="btn" :disabled="index === pluginRules(row).length - 1 || yamlOwned(row.configuration_source)" @click="movePluginRule(row, index, 1)">↓</button>
              <button type="button" class="btn btn-danger" :disabled="yamlOwned(row.configuration_source)" @click="removePluginRule(row, index)">Remove</button>
            </div>
          </div>
        </div>
        <label v-else-if="row.enabled" class="config">
          Configuration (JSON)
          <textarea v-model="configDrafts[row.plugin_id]" spellcheck="false" class="json-editor"></textarea>
        </label>
      </div>
    </section>

    <!-- ---------------------------------------------------------- dialogs -->

    <!-- Add a field: the definitions this page does not carry, and a way to
         create one without leaving the page you are building. -->
    <div v-if="picking" class="modal-backdrop" @click.self="picking = false">
      <div class="modal-card editor">
        <h2>Add a field to {{ scopeLabel }}</h2>
        <input v-model="pickerQuery" placeholder="Search fields…" autofocus />
        <ul class="picker-list">
          <li v-for="definition in pickerCandidates" :key="definition.id">
            <button class="picker-row" @click="addFieldToScope(definition.key)">
              <span>
                <strong>{{ definition.label }}</strong>
                <code>{{ definition.key }}</code>
                <span v-if="!definition.enabled" class="tag">disabled</span>
              </span>
              <span class="muted">{{ definition.field_type }}</span>
            </button>
          </li>
          <li v-if="!pickerCandidates.length" class="muted empty">
            Every defined field is already on this page.
          </li>
        </ul>
        <div class="actions">
          <button type="button" class="btn" @click="picking = false">Cancel</button>
          <button class="btn btn-primary" @click="picking = false; newField(true)">+ Create a new field</button>
        </div>
      </div>
    </div>

    <!-- One field on one page. Both what it is called here and how it behaves
         here, so an override is made where its effect is visible. -->
    <div v-if="editingRow" class="modal-backdrop" @click.self="cancelRowEdit">
      <div class="modal-card editor">
        <h2>{{ editingRow.label }} <code>{{ editingRow.key }}</code></h2>
        <p class="muted">
          On <strong>{{ scopeLabel }}</strong>.
          <template v-if="!isGlobalScope && editingRow.fromGlobal">
            Inherited from all device types — override only what this type needs to differ on.
          </template>
        </p>

        <div class="flags">
          <div v-for="flag in FLAGS" :key="flag.name" class="flag-row">
            <span class="flag-name">{{ flag.name }}</span>
            <label class="check">
              <input
                type="checkbox"
                :checked="flagValue(editingRow, flag.key)"
                :disabled="!flagEditable(editingRow, flag.key)"
                @change="setFlag(editingRow, flag.key, ($event.target as HTMLInputElement).checked)"
              />
              {{ flag.on }}
            </label>
            <span v-if="!flagEditable(editingRow, flag.key)" class="flag-state muted">{{ flag.locked }}</span>
            <!-- Inheritance is stated only where there is something to inherit
                 from, and the way back to it sits next to the statement. -->
            <span v-else-if="!isGlobalScope && editingRow.fromGlobal" class="flag-state muted">
              <template v-if="overridden(editingRow, flag.key)">
                Overridden here ·
                <button class="linkish" @click="clearFlag(editingRow, flag.key)">
                  go back to inherited ({{ flag.words[Number(editingRow.base[flag.key])] }})
                </button>
              </template>
              <template v-else>Inherited from all device types</template>
            </span>
          </div>
        </div>

        <label>
          Label on this page
          <input
            :value="editingRow.label_override"
            :placeholder="editingRow.base.label"
            @input="editingRow.label_override = ($event.target as HTMLInputElement).value; editingRow.own = true; dirty = true"
          />
          <small class="muted">Presentation only — the key never changes.</small>
        </label>
        <label>
          Help text on this page
          <textarea
            :value="editingRow.description_override"
            @input="editingRow.description_override = ($event.target as HTMLTextAreaElement).value; editingRow.own = true; dirty = true"
          ></textarea>
        </label>

        <div class="actions">
          <button type="button" class="btn" @click="cancelRowEdit">Cancel</button>
          <button type="button" class="btn btn-primary" @click="closeRowEdit">Done</button>
        </div>
      </div>
    </div>

    <div v-if="editingEntityField" class="modal-backdrop">
      <form class="modal-card editor" @submit.prevent="saveEntityField">
        <h2>{{ creatingEntityField ? `New ${activeEntity} field` : `Edit ${editingEntityField.label}` }}</h2>
        <label v-if="creatingEntityField">Key<input v-model="editingEntityField.key" required pattern="[a-z][a-z0-9_]*" /></label>
        <label>Label<input v-model="editingEntityField.label" required /></label>
        <label>Type
          <select v-model="editingEntityField.field_type" :disabled="editingEntityField.protected">
            <option v-for="type in FIELD_TYPES" :key="type" :value="type">{{ type }}</option>
          </select>
        </label>
        <label>Description<textarea v-model="editingEntityField.description"></textarea></label>
        <label v-if="editingEntityField.field_type === 'select'">Choices (one per line)<textarea v-model="entityOptionsText"></textarea></label>
        <template v-if="!editingEntityField.protected">
          <label class="check"><input v-model="editingEntityField.required" type="checkbox" /> Required</label>
          <label class="check"><input v-model="editingEntityField.sensitive" type="checkbox" /> Sensitive</label>
          <label class="check"><input v-model="editingEntityField.indexed" type="checkbox" /> Indexed</label>
          <label class="check"><input v-model="editingEntityField.unique_value" type="checkbox" /> Unique</label>
        </template>
        <p v-else class="muted">This field is required by the application. Its key, type, requirement, and storage cannot be changed.</p>
        <p v-if="error" class="error">{{ error }}</p>
        <div class="actions">
          <button type="button" class="btn" @click="editingEntityField = null">Cancel</button>
          <button class="btn btn-primary" :disabled="busy">{{ busy ? 'Saving…' : 'Save' }}</button>
        </div>
      </form>
    </div>

    <div v-if="editingField" class="modal-backdrop">
      <form class="modal-card editor" @submit.prevent="saveField">
        <h2>{{ creatingField ? 'New Field' : `Edit ${editingField.label}` }}</h2>
        <p v-if="placeAfterCreate" class="muted">It will be added to {{ scopeLabel }} once it exists.</p>
        <!-- Only from the catalog, where nothing else has said where the field
             goes. The picker already answered this by which page it was on. -->
        <label v-else-if="creatingField" class="check">
          <input type="checkbox" v-model="newFieldGlobal" />
          Add it to every device type
        </label>
        <label v-if="creatingField">
          Key
          <input v-model="editingField.key" required pattern="[a-z][a-z0-9_]*" placeholder="serial_number" />
          <small class="muted">Immutable. Used in URLs, imports, the API and plugin contracts.</small>
        </label>
        <label>Label<input v-model="editingField.label" required /></label>
        <label>
          Type
          <select v-model="editingField.field_type" :disabled="editingField.protected_system_field">
            <option v-for="type in FIELD_TYPES" :key="type" :value="type">{{ type }}</option>
          </select>
        </label>
        <label>Description<textarea v-model="editingField.description"></textarea></label>
        <label v-if="editingField.field_type === 'select'">
          Choices (one per line)
          <textarea v-model="optionsText" spellcheck="false"></textarea>
        </label>
        <label>
          Plugin role
          <select v-model="editingField.plugin_role" :disabled="editingField.protected_system_field">
            <option v-for="role in ROLES" :key="role" :value="role || null">{{ role || '— none —' }}</option>
          </select>
          <small class="muted">What plugins ask for by meaning, whatever this field is called.</small>
        </label>
        <label class="check"><input type="checkbox" v-model="editingField.sensitive" /> Sensitive (kept out of search, suggestions and plugin payloads)</label>
        <label class="check"><input type="checkbox" v-model="editingField.indexed" /> Indexed (filtered often)</label>
        <label class="check"><input type="checkbox" v-model="editingField.unique_value" /> Unique across the fleet</label>
        <label class="check">
          <input type="checkbox" v-model="editingField.opens_web_page" />
          Opens a web page (the value is linked in the grid and on the device page)
        </label>
        <!-- The installation's default for that link. A single device that
             answers somewhere else overrides both on its own page; this is
             what it overrides. Hidden until the box is ticked, because until
             then there is no link for them to describe. -->
        <template v-if="editingField.opens_web_page">
          <label>
            Link scheme
            <select v-model="editingField.link_scheme">
              <option value="http">http</option>
              <option value="https">https</option>
            </select>
            <small class="muted">
              http suits most devices: one serving only https usually redirects from port 80,
              while https against a self-signed certificate warns before it connects.
            </small>
          </label>
          <label>
            Link port
            <input v-model="editingField.link_port" type="number" min="1" max="65535" placeholder="Default for the scheme" />
            <small class="muted">Leave blank for 80 or 443. A device on another port overrides this on its own page.</small>
          </label>
        </template>
        <label v-if="!creatingField && !editingField.protected_system_field" class="check">
          <input type="checkbox" v-model="editingField.enabled" /> Enabled
        </label>
        <p v-if="error" class="error">{{ error }}</p>
        <div class="actions">
          <button type="button" class="btn" @click="editingField = null; placeAfterCreate = false">Cancel</button>
          <button class="btn btn-primary" :disabled="busy">{{ busy ? 'Saving…' : 'Save' }}</button>
        </div>
      </form>
    </div>

    <div v-if="editingType" class="modal-backdrop">
      <form class="modal-card editor" @submit.prevent="saveType">
        <h2>{{ creatingType ? 'New Device Type' : `Edit ${editingType.label}` }}</h2>
        <label v-if="creatingType">
          Key
          <input v-model="editingType.key" required pattern="[a-z][a-z0-9-]*" placeholder="mobile" />
          <small class="muted">Immutable, and the page's URL: /devices/type/&lt;key&gt;.</small>
        </label>
        <!-- Shown, not hidden, when editing: it is the thing the display name
             below is safe to change *because of*, and a reader cannot take that
             on trust from a field that is not on screen. -->
        <label v-else>
          Key
          <input :value="editingType.key" disabled />
          <small class="muted">Immutable. Used in URLs, imports, the API and plugin contracts.</small>
        </label>
        <label>
          Display name
          <input v-model="editingType.label" required placeholder="Mobile Phones" />
          <small class="muted">
            What people read, in the navigation and on the type's page. Safe to change at any
            time — the key above is what URLs, imports and plugins address.
          </small>
        </label>
        <label>Description<textarea v-model="editingType.description"></textarea></label>
        <label>
          Position
          <input v-model.number="editingType.position" type="number" />
          <small class="muted">Where it sits in the Devices menu and the layout list; lower is earlier.</small>
        </label>
        <label class="check"><input type="checkbox" v-model="editingType.enabled" /> Enabled</label>
        <p v-if="creatingType" class="muted">
          A new type inherits every global field immediately, including ones added later.
        </p>
        <p v-else class="muted">
          Nothing here changes this type's fields or the devices in it.
        </p>
        <p v-if="error" class="error">{{ error }}</p>
        <div class="actions">
          <button type="button" class="btn" @click="editingType = null">Cancel</button>
          <button class="btn btn-primary" :disabled="busy">{{ busy ? 'Saving…' : 'Save' }}</button>
        </div>
      </form>
    </div>

    <div v-if="importPreview" class="modal-backdrop" @click.self="cancelImport">
      <div class="modal-card import-preview" role="dialog" aria-modal="true" aria-labelledby="schema-import-title">
        <h3 id="schema-import-title">Import bootstrap YAML</h3>
        <p class="muted">
          Review the additions from <strong>{{ importFile?.name }}</strong>. Existing schema objects will be left unchanged,
          and nothing will be deleted or disabled.
        </p>
        <div class="import-counts">
          <div v-for="(label, key) in importLabels" :key="key">
            <strong>{{ importPreview.counts[key] || 0 }}</strong>
            <span>{{ label }}</span>
          </div>
        </div>
        <details v-if="Object.values(importPreview.skipped).some((items) => items.length)">
          <summary>Existing objects that will be skipped</summary>
          <div v-for="(items, key) in importPreview.skipped" :key="key">
            <p v-if="items.length" class="muted"><strong>{{ importLabels[key] }}:</strong> {{ items.join(', ') }}</p>
          </div>
        </details>
        <p v-if="error" class="error">{{ error }}</p>
        <div class="actions">
          <button class="btn" :disabled="importing" @click="cancelImport">Cancel</button>
          <button class="btn btn-primary" :disabled="importing || importPreview.total === 0" @click="applyImport">
            {{ importing ? 'Importing…' : `Apply ${importPreview.total} addition${importPreview.total === 1 ? '' : 's'}` }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.section-head { display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin: 14px 0; }
.file-input { display: none; }
.import-preview { width: min(650px, calc(100vw - 32px)); }
.import-counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; margin: 18px 0; }
.import-counts div { display: grid; gap: 3px; padding: 12px; border-radius: var(--r-md); background: var(--surface-2); }
.import-counts strong { font-size: 20px; }
.import-counts span { color: var(--text-muted); font-size: 12px; }
.type-picker { display: flex; align-items: center; gap: 8px; }
.tabs { display: flex; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }
.tabs button { padding: 8px 14px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--surface); color: inherit; cursor: pointer; }
.tabs button.active { background: var(--accent-a16); border-color: var(--accent-a24); }

/* The Advanced menu. A <details> rather than a scripted popover: the browser
   already owns the open state and the keyboard behaviour, and there is nothing
   here that needs to survive a click elsewhere on the page. */
.menu { position: relative; }
.menu > summary { list-style: none; cursor: pointer; display: inline-flex; align-items: center; }
.menu > summary::-webkit-details-marker { display: none; }
.menu-panel {
  position: absolute;
  right: 0;
  z-index: 30;
  display: grid;
  gap: 6px;
  min-width: 260px;
  margin-top: 6px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  box-shadow: var(--shadow-md);
}
.menu-panel .btn { justify-content: flex-start; text-align: left; }
.menu-note { margin: 2px 0 0; font-size: 12px; }

/* Two panes: the pages on the left, the open one on the right. */
.layouts { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 18px; align-items: start; }
.scope-rail { display: grid; gap: 4px; align-content: start; }
.scope-rail button {
  display: grid;
  gap: 2px;
  padding: 9px 11px;
  text-align: left;
  border: 1px solid transparent;
  border-radius: var(--r-md);
  background: none;
  color: inherit;
  cursor: pointer;
  font: inherit;
}
.scope-rail button:hover { background: var(--surface-2); }
.scope-rail button.active { background: var(--accent-a16); border-color: var(--accent-a24); }
.scope-rail .muted { font-size: 12px; }
.scope-rail .rail-add { margin-top: 6px; border-color: var(--border); color: var(--text-muted); }
.scope-body { min-width: 0; }
.scope-title h3 { margin: 0 0 3px; }
.scope-title p { margin: 0; }
.scope-actions { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }

.layout-table strong { font-weight: 600; }
.layout-table code { margin-left: 7px; font-size: 12px; color: var(--text-muted); }
.visibility-col { width: 92px; min-width: 92px; text-align: center; }
.visibility-col input { cursor: pointer; }
.visibility-col input:disabled { cursor: not-allowed; }
.grip-col { width: 34px; }
.grip {
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: grab;
  font-size: 15px;
  line-height: 1;
  padding: 4px 2px;
}
.grip:focus-visible { box-shadow: var(--ring); border-radius: var(--r-sm); outline: none; }
.anchor { font-size: 13px; }
tr.hidden td { opacity: 0.55; }
.foot-note { margin: 8px 2px 0; font-size: 12px; }
.removed { margin-top: 18px; }
.removed h4 { margin: 0 0 6px; }
.removed ul { margin: 0; padding-left: 18px; display: grid; gap: 5px; }
.removed code { margin: 0 8px 0 6px; font-size: 12px; color: var(--text-muted); }

/* A button that reads as prose, for the one-line "inherit again" and
   cross-references that would be over-weighted as real buttons. */
.linkish {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  color: var(--accent);
  cursor: pointer;
  text-decoration: underline;
}
.linkish:disabled { color: var(--text-muted); cursor: default; text-decoration: none; }
.type-admin { margin: 6px 0 0; font-size: 12px; }

.picker-list { list-style: none; margin: 12px 0; padding: 0; max-height: 45vh; overflow: auto; display: grid; gap: 3px; }
.picker-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 11px;
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  background: var(--surface-2);
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-align: left;
}
.picker-row:hover { border-color: var(--accent-a24); background: var(--row-hover); }
.picker-row code { margin-left: 7px; font-size: 12px; color: var(--text-muted); }
.picker-list .empty { padding: 10px 2px; }

.flags { display: grid; gap: 12px; margin: 16px 0; }
/* A grid rather than a wrapping flex row: the state line ("Inherited from all
   device types") belongs under the control it describes, and a flex row drops
   it to the left margin where it reads as a separate paragraph. */
.flag-row {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 3px 10px;
  align-items: center;
}
.flag-name { font-weight: 600; }
.flag-state { grid-column: 2; font-size: 12px; }

.banner { padding: 9px 12px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--surface-2); }
.error { color: var(--red, #dc2626); }
.notice { color: var(--green, #16a34a); }
.report { padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--r-md); }
.report p { margin: 3px 0; }
.row-actions { display: flex; gap: 6px; }
/* The Field column holds a label, a key and up to three tags, so it wraps.
   Leading here rather than on the pills keeps the wrapped lines even. */
.data-list td { line-height: 1.7; }
tr.disabled td { opacity: 0.55; }
/* Pills are inline-flex, not plain inline: vertical padding and a border on an
   inline box do not grow the line box, so a rounded background paints over the
   line above and below it. Same treatment as .support-pill and .status-pill in
   style.css. */
.tag {
  display: inline-flex;
  align-items: center;
  /* A little vertical margin so tags that wrap onto their own line clear the
     text above them rather than sitting flush against it. */
  margin: 1px 0 1px 6px;
  padding: 1px 7px;
  border-radius: var(--r-pill);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  font-size: 11px;
  line-height: 1.6;
  /* "read-only", "this type only", "not installed" are one label each; breaking
     one across lines would split its background in half. */
  white-space: nowrap;
}
.tag.danger { background: var(--red, #dc2626); color: #fff; border-color: transparent; }
.tag.warn { background: #d97706; color: #fff; border-color: transparent; }
.plugin-card { padding: 14px; margin-bottom: 12px; }
.plugin-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.action-list { margin: 6px 0; padding-left: 20px; }
.check { display: flex; align-items: center; gap: 7px; }
.config { display: grid; gap: 10px; margin-top: 10px; }
.config > label:not(.check) { display: grid; gap: 5px; }
.config textarea { min-height: 80px; }
.config-fieldset { min-width: 0; display: grid; gap: 12px; margin: 4px 0; padding: 14px; border: 1px solid var(--border-soft); border-radius: var(--r-md); }
.config-fieldset legend { padding: 0 6px; font-weight: 600; }
.info-command-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 12px; border: 1px solid var(--border-soft); border-radius: var(--r-md); background: var(--surface-2); }
.info-command-row .command-input { grid-column: 1 / -1; width: 100%; }
.command-yields { display: flex; flex-wrap: wrap; gap: 8px 16px; min-width: 0; }
.info-command-row > .btn { justify-self: end; min-width: 88px; }
.rule-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 14px; }
.plugin-rule { display: grid; grid-template-columns: 28px minmax(120px, 1fr) 110px minmax(130px, 1fr) minmax(150px, 1fr); gap: 7px; align-items: center; padding: 10px; border: 1px solid var(--border-soft); border-radius: var(--r-md); }
.rule-order { font-weight: 700; text-align: center; }
.plugin-rule > .check, .plugin-rule > .rule-actions { grid-column: 2 / -1; }
.rule-actions { display: flex; gap: 6px; justify-content: flex-end; }
.modal-backdrop { position: fixed; inset: 0; z-index: 200; display: grid; place-items: center; padding: 20px; background: rgba(0,0,0,.65); }
.editor { width: min(680px, 100%); max-height: 90vh; overflow: auto; padding: 22px; }
.editor > label:not(.check) { display: grid; gap: 5px; margin: 12px 0; }
.editor input:not([type=checkbox]), .editor textarea, .editor select { padding: 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: inherit; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }

/* Mirrors MOBILE in breakpoints.ts. The rail becomes a strip of pages above
   the one that is open, because 240px of chrome beside a table is most of a
   phone. */
@media (max-width: 899px) {
  .layouts { grid-template-columns: minmax(0, 1fr); }
  .scope-rail {
    grid-auto-flow: column;
    grid-auto-columns: max-content;
    overflow-x: auto;
    padding-bottom: 4px;
  }
  .scope-rail button { border-color: var(--border); }
  .scope-actions { margin-left: 0; }
  .info-command-row { grid-template-columns: minmax(0, 1fr); }
  .info-command-row > .btn { justify-self: start; }
}
</style>
