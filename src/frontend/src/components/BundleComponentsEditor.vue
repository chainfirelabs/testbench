<script setup lang="ts">
type BundleComponent = {
  id?: string
  name: string
  version: string
  required: boolean
  position?: number
}

const props = defineProps<{ modelValue: BundleComponent[] }>()
const emit = defineEmits<{ (event: 'update:modelValue', value: BundleComponent[]): void }>()

function addComponent() {
  emit('update:modelValue', [
    ...(props.modelValue || []),
    { name: '', version: '', required: true, position: props.modelValue?.length || 0 },
  ])
}

function updateComponent(index: number, key: 'name' | 'version', value: string) {
  const next = (props.modelValue || []).map((item, position) => ({ ...item, position }))
  next[index] = { ...next[index], [key]: value }
  // Once the identifying text changes, let the API resolve the replacement by
  // name/version instead of retaining the old suite component id.
  if (key === 'name' || key === 'version') delete next[index].id
  emit('update:modelValue', next)
}

function removeComponent(index: number) {
  emit('update:modelValue', (props.modelValue || [])
    .filter((_item, position) => position !== index)
    .map((item, position) => ({ ...item, position })))
}
</script>

<template>
  <section class="bundle-editor">
    <div class="bundle-editor-head">
      <div>
        <strong>Bundle components</strong>
        <p class="muted">Components belong only to this software suite and do not appear as separate software.</p>
      </div>
      <button type="button" class="btn" @click="addComponent">+ Add component</button>
    </div>
    <p v-if="!modelValue?.length" class="muted bundle-empty">No components. This is standalone software.</p>
    <div v-for="(component, index) in modelValue" :key="index" class="bundle-component">
      <label>Component name
        <input :value="component.name" placeholder="Microsoft Outlook"
          @input="updateComponent(index, 'name', ($event.target as HTMLInputElement).value)" />
      </label>
      <label>Version
        <input :value="component.version" placeholder="Optional"
          @input="updateComponent(index, 'version', ($event.target as HTMLInputElement).value)" />
      </label>
      <button type="button" class="btn btn-danger" @click="removeComponent(index)">Remove</button>
    </div>
  </section>
</template>

<style scoped>
.bundle-editor{margin-top:18px;border-top:1px solid var(--border);padding-top:16px}
.bundle-editor-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.bundle-editor-head p{margin:4px 0 0}.bundle-empty{margin:12px 0 0}
.bundle-component{display:grid;grid-template-columns:minmax(180px,2fr) minmax(120px,1fr) auto;gap:10px;align-items:end;margin-top:12px;padding:12px;border:1px solid var(--border);border-radius:8px}
.bundle-component label{display:flex;flex-direction:column;gap:5px}
@media(max-width:720px){.bundle-component{grid-template-columns:1fr}}
</style>
