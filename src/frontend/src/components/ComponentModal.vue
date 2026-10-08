<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { groupBundleComponents } from '../bundleComponentGroups'
import { SUPPORT_LABELS } from '../constants'
import type { ComponentPanelState } from '../componentPanel'

const props = defineProps<{ panel: ComponentPanelState }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const dialog = ref<HTMLDialogElement | null>(null)
const query = ref('')

const filtered = computed(() => {
  const search = query.value.trim().toLocaleLowerCase()
  return search
    ? props.panel.items.filter((item) => `${item.name} ${item.version || ''} ${item.status || ''}`.toLocaleLowerCase().includes(search))
    : props.panel.items
})
const groups = computed(() => groupBundleComponents(filtered.value)
  .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })))

onMounted(() => dialog.value?.showModal())

function close() {
  dialog.value?.close()
}

function onDialogClick(event: MouseEvent) {
  if (event.target === dialog.value) close()
}
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="component-modal" aria-labelledby="component-panel-title"
      @close="emit('close')" @click="onDialogClick">
      <div class="component-panel-content">
        <header class="component-panel-header">
          <div>
            <h2 id="component-panel-title">{{ panel.title }}</h2>
            <p v-if="panel.subtitle">{{ panel.subtitle }}</p>
          </div>
          <button class="btn btn-mini" type="button" aria-label="Close components" @click="close">Close</button>
        </header>
        <div class="component-panel-toolbar">
          <span>
            {{ groups.length }} {{ panel.matching ? 'matching ' : '' }}component{{ groups.length === 1 ? '' : 's' }}
            <template v-if="filtered.length !== groups.length"> · {{ filtered.length }} versions</template>
          </span>
          <input v-if="panel.items.length > 8" v-model="query" type="search" aria-label="Search components" placeholder="Search components" />
        </div>
        <div class="component-panel-list">
          <p v-if="!groups.length" class="muted">No components match your search.</p>
          <section v-for="group in groups" :key="group.name" class="component-panel-group">
            <h3>{{ group.name }}</h3>
            <div v-for="(item, index) in group.versions" :key="`${item.version || ''}-${index}`" class="component-panel-version">
              <span>{{ item.version || '(unversioned)' }}</span>
              <span v-if="item.status" class="support-pill" :class="item.status">
                {{ SUPPORT_LABELS[item.status] || item.status }}{{ item.inherited ? ' (inherited)' : '' }}
              </span>
            </div>
          </section>
        </div>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.component-modal {
  position: fixed;
  inset: 0;
  width: min(620px, calc(100vw - 32px));
  max-width: none;
  max-height: min(720px, calc(100dvh - 32px));
  margin: auto;
  padding: 0;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.component-modal::backdrop { background: rgba(0, 0, 0, 0.55); }
.component-panel-content {
  display: flex;
  flex-direction: column;
  max-height: min(720px, calc(100dvh - 32px));
}
.component-panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: max(18px, env(safe-area-inset-top)) 20px 16px;
  border-bottom: 1px solid var(--border);
}
.component-panel-header h2 { margin: 0; font-size: 18px; }
.component-panel-header p { margin: 4px 0 0; color: var(--text-muted); }
.component-panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 20px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border-soft);
}
.component-panel-toolbar input { min-width: 0; width: min(220px, 60%); }
.component-panel-list {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 4px 20px max(20px, env(safe-area-inset-bottom));
}
.component-panel-group { padding: 12px 0; border-bottom: 1px solid var(--border-soft); }
.component-panel-group h3 { margin: 0 0 6px; font-size: 14px; overflow-wrap: anywhere; }
.component-panel-version {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 32px;
  padding: 3px 0 3px 12px;
  color: var(--text-muted);
}
.component-panel-version .support-pill { flex: none; }
</style>
