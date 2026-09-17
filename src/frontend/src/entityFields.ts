import { ref, type Ref } from 'vue'
import { api } from './api/client'
import type { FormField } from './components/FormModal.vue'

export interface EntityField {
  id: string
  entity: 'software' | 'tests' | 'vendor_devices'
  key: string
  label: string
  type: 'text' | 'password' | 'textarea' | 'number' | 'boolean' | 'date' | 'select' | 'json'
  required: boolean
  visible: boolean
  list_visible: boolean
  sensitive: boolean
  writable: boolean
  storage: 'column' | 'data'
  options: string[]
  description?: string | null
  role?: string | null
  indexed: boolean
  unique: boolean
  database_storage: 'json' | 'column' | 'relationship' | 'derived'
  protected: boolean
  list_visibility_locked: boolean
  position: number
}

let catalogPromise: Promise<Record<string, EntityField[]>> | null = null

function catalog() {
  if (!catalogPromise) catalogPromise = api<Record<string, EntityField[]>>('/entity-fields')
  return catalogPromise
}

export function invalidateEntityFields() { catalogPromise = null }

export function useEntityFields(entity: 'devices' | 'software' | 'tests' | 'vendor_devices'): {
  fields: Ref<EntityField[]>
  loadFields: () => Promise<void>
} {
  const fields = ref<EntityField[]>([])
  return {
    fields,
    loadFields: async () => {
      fields.value = (await catalog())[entity] || []
    },
  }
}

export function dataValue(row: any, field: EntityField, bucket: 'misc_data' | 'data'): any {
  return field.storage === 'data' ? row?.[bucket]?.[field.key] : row?.[field.key]
}

export function setDataValue(
  row: any,
  field: EntityField,
  bucket: 'misc_data' | 'data',
  value: any,
) {
  if (field.storage === 'data') {
    row[bucket] = { ...(row[bucket] || {}), [field.key]: value }
  } else {
    row[field.key] = value
  }
}

export function customColumn(field: EntityField, bucket: 'misc_data' | 'data'): any {
  const column: any = {
    field: field.key,
    headerName: field.label,
    editable: field.writable,
    valueGetter: (p: any) => dataValue(p.data, field, bucket),
    valueSetter: (p: any) => {
      setDataValue(p.data, field, bucket, p.newValue)
      return true
    },
  }
  if (field.type === 'select') {
    column.cellEditor = 'agSelectCellEditor'
    column.cellEditorParams = { values: field.options }
  }
  if (field.type === 'boolean') column.cellDataType = 'boolean'
  return column
}

export function mergeCustomValues(
  values: Record<string, any>,
  fields: EntityField[],
  bucket: 'misc_data' | 'data',
): Record<string, any> {
  const payload = { ...values }
  const stored = { ...(payload[bucket] || {}) }
  for (const field of fields) {
    if (field.storage !== 'data' || !(field.key in payload)) continue
    const value = payload[field.key]
    // An explicitly blank sensitive value is meaningful: many appliances use
    // a username with no password. Presence means configured; absence means
    // the operator has not supplied a credential.
    if ((field.sensitive && value === '') || (value !== '' && value != null)) stored[field.key] = value
    else delete stored[field.key]
    delete payload[field.key]
  }
  payload[bucket] = stored
  return payload
}

export function customFormField(field: EntityField): FormField {
  return {
    key: field.key,
    label: field.label,
    type: field.type,
    required: field.required,
    options: field.options.map((value) => ({ value, label: value })),
    hint: field.description || undefined,
  }
}
