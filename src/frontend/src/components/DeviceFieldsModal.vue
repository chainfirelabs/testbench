<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import { invalidateDeviceSchemas, type DeviceSchema, type FieldAssignment, type SchemaField } from '../deviceSchema'

const props = defineProps<{ typeKey?: string; title: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'saved'): void }>()

type Draft = SchemaField & { originalListVisible: boolean; originalRequired: boolean }
const fields = ref<Draft[]>([])
const assignments = ref<FieldAssignment[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')

const path = () => props.typeKey
  ? `/device-schema/types/${encodeURIComponent(props.typeKey)}`
  : '/device-schema/global'

async function load() {
  loading.value = true
  error.value = ''
  try {
    const schema = await api<DeviceSchema>(path())
    assignments.value = schema.assignments || []
    fields.value = schema.fields.map((field) => ({
      ...field,
      originalListVisible: field.list_visible,
      originalRequired: field.required,
    }))
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    const byKey = new Map(assignments.value.map((item) => [item.field_key, { ...item }]))
    for (const field of fields.value) {
      if (field.list_visible === field.originalListVisible && field.required === field.originalRequired) continue
      const assignment: any = byKey.get(field.key) || { field_key: field.key }
      if (field.list_visible !== field.originalListVisible) assignment.list_visible = field.list_visible
      if (field.required !== field.originalRequired) assignment.required = field.required
      byKey.set(field.key, assignment)
    }
    await api(path(), {
      method: 'PUT',
      body: JSON.stringify({ assignments: [...byKey.values()] }),
    })
    invalidateDeviceSchemas()
    emit('saved')
    emit('close')
  } catch (e: any) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <form class="modal-card modal-wide" @submit.prevent="save">
      <h3 class="modal-title">{{ title }}</h3>
      <p class="muted">Changes are saved to the published device schema used by lists, forms, imports, API, and MCP.</p>
      <p v-if="loading" class="muted">Loading…</p>
      <div v-else class="schema-field-list">
        <div v-for="field in fields" :key="field.key" class="schema-field-row">
          <div><strong>{{ field.label }}</strong><br /><code>{{ field.key }}</code></div>
          <label class="check"><input v-model="field.list_visible" type="checkbox" :disabled="field.role === 'identifier' || field.key === 'unique_id'" /> Shown</label>
          <label class="check"><input v-model="field.required" type="checkbox" :disabled="field.protected" /> Required</label>
        </div>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <div class="actions">
        <button type="button" class="btn" :disabled="saving" @click="emit('close')">Cancel</button>
        <button class="btn btn-primary" :disabled="loading || saving">{{ saving ? 'Saving…' : 'Save fields' }}</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.schema-field-list { display: grid; gap: 8px; margin: 16px 0; }
.schema-field-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
}
@media (max-width: 720px) { .schema-field-row { grid-template-columns: minmax(0, 1fr) auto; } }
</style>
