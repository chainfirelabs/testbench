<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import { invalidateEntityFields, type EntityField } from '../entityFields'

const props = defineProps<{
  entity: 'software' | 'tests'
  title: string
}>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'saved'): void }>()

const fields = ref<EntityField[]>([])
const originalRequired = new Map<string, boolean>()
const loading = ref(true)
const saving = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    fields.value = await api<EntityField[]>(`/entity-fields/${props.entity}`)
    originalRequired.clear()
    for (const field of fields.value) originalRequired.set(field.id, field.required)
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function move(index: number, by: number) {
  const target = index + by
  if (target < 0 || target >= fields.value.length) return
  const next = [...fields.value]
  next.splice(target, 0, ...next.splice(index, 1))
  fields.value = next
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    for (const field of fields.value) {
      if (field.protected || originalRequired.get(field.id) === field.required) continue
      await api(`/entity-fields/${props.entity}/${field.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ required: field.required }),
      })
    }
    await api(`/entity-fields/${props.entity}`, {
      method: 'PUT',
      body: JSON.stringify({
        fields: fields.value.map(({ id, list_visible }) => ({ id, list_visible })),
      }),
    })
    invalidateEntityFields()
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
      <p class="muted">Changes are saved to the same field schema used by Schema settings, forms, imports, API, and MCP.</p>
      <p v-if="loading" class="muted">Loading…</p>
      <div v-else class="schema-field-list">
        <div v-for="(field, index) in fields" :key="field.id" class="schema-field-row entity-field-row">
          <div>
            <strong>{{ field.label }}</strong><br />
            <code>{{ field.key }}</code>
          </div>
          <div class="field-order">
            <button type="button" class="btn btn-mini" :disabled="index === 0" title="Move up" @click="move(index, -1)">↑</button>
            <button type="button" class="btn btn-mini" :disabled="index === fields.length - 1" title="Move down" @click="move(index, 1)">↓</button>
          </div>
          <label class="check">
            <input v-model="field.list_visible" type="checkbox" :disabled="field.list_visibility_locked" /> Shown
          </label>
          <label class="check" :title="field.protected ? 'Application fields are managed by TestBench' : ''">
            <input v-model="field.required" type="checkbox" :disabled="field.protected" /> Required
          </label>
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
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
}
.entity-field-row { grid-template-columns: minmax(180px, 1fr) auto auto auto; }
.field-order { display: flex; gap: 5px; }
@media (max-width: 720px) {
  .entity-field-row { grid-template-columns: minmax(0, 1fr) auto; }
}
</style>
