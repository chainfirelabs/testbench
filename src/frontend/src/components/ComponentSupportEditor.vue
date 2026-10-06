<script setup lang="ts">
import { SUPPORT_LABELS, SUPPORT_VALUES } from '../constants'

type Component = { id: string; name: string; version?: string | null }
type Support = { component_id: string; support_status: string }

const props = defineProps<{ components: Component[]; modelValue: Support[] }>()
const emit = defineEmits<{ (event: 'update:modelValue', value: Support[]): void }>()

function selected(id: string): Support | undefined {
  return (props.modelValue || []).find((item) => item.component_id === id)
}

function toggle(id: string, checked: boolean) {
  const rest = (props.modelValue || []).filter((item) => item.component_id !== id)
  emit('update:modelValue', checked ? [...rest, { component_id: id, support_status: 'supported' }] : rest)
}

function setStatus(id: string, status: string) {
  emit('update:modelValue', (props.modelValue || []).map((item) =>
    item.component_id === id ? { ...item, support_status: status } : item))
}
</script>

<template>
  <section class="component-support-editor">
    <strong>Component support (optional)</strong>
    <p class="muted">Choose a component version only when its support differs from the general claim.</p>
    <p v-if="!components.length" class="muted">This software version has no components.</p>
    <div v-for="component in components" :key="component.id" class="component-support-row">
      <label>
        <input type="checkbox" :checked="!!selected(component.id)"
          @change="toggle(component.id, ($event.target as HTMLInputElement).checked)" />
        {{ component.name }} {{ component.version || '(unversioned)' }}
      </label>
      <select :disabled="!selected(component.id)" :value="selected(component.id)?.support_status || 'supported'"
        :aria-label="`Support for ${component.name} ${component.version || '(unversioned)'}`"
        @change="setStatus(component.id, ($event.target as HTMLSelectElement).value)">
        <option v-for="status in SUPPORT_VALUES" :key="status" :value="status">
          {{ SUPPORT_LABELS[status] }}
        </option>
      </select>
    </div>
  </section>
</template>

<style scoped>
.component-support-editor { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 14px; }
.component-support-editor p { margin: 5px 0 12px; }
.component-support-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 8px 0; }
.component-support-row label { display: flex; align-items: center; gap: 8px; }
.component-support-row select { max-width: 180px; }
@media (max-width: 600px) { .component-support-row { align-items: stretch; flex-direction: column; } }
</style>
