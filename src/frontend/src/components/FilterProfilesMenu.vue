<script setup lang="ts">
// Saved view profiles for a data table. A profile captures the grid's
// column state, filters, sort, quick filter and page size. The default
// profile (if any) is applied automatically when the view loads.
//
// Views belong to the shape of screen they were saved on. A desktop view names
// fourteen columns and a hundred rows a page; on a phone those same columns are
// the fields stacked on every card, so the two want different answers. The menu
// therefore lists — and saves, and defaults — only the current platform's
// views, and switching between them needs no new UI: set the list up the way
// you want it and save it as the default, on whichever device you are holding.
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { useIsMobile } from '../breakpoints'

const props = defineProps<{
  /**
   * What the saved views belong to. Device inventory pages append their type
   * key ("devices:router"), because those pages show different columns from
   * the same table and a layout saved for one is meaningless on another.
   */
  entity: string
  getState: () => any
  applyState: (s: any) => void
}>()

const open = ref(false)
const profiles = ref<any[]>([])
const busy = ref(false)
let loaded = false

const isMobile = useIsMobile()
const platform = computed(() => (isMobile.value ? 'mobile' : 'desktop'))

async function load() {
  try {
    profiles.value = await api<any[]>(
      `/saved_filters?entity=${props.entity}&platform=${platform.value}`,
    )
    loaded = true
  } catch {
    profiles.value = []
  }
}

onMounted(load)

// Crossing the breakpoint changes which views exist. Reload rather than filter
// what is already held: the two sets are disjoint, so there is nothing to keep.
watch(platform, () => {
  loaded = false
  profiles.value = []
  load()
})

function defaultProfile() {
  return profiles.value.find((p: any) => p.is_default) || null
}

/** Apply the default view (loads profiles first if needed). */
async function applyDefault() {
  if (!loaded) await load()
  const d = defaultProfile()
  if (d) applyProfile(d)
}

function applyProfile(p: any) {
  props.applyState({
    filter: p.filter_state,
    sort: p.sort_state,
    column: p.column_state,
    quick_filter: p.quick_filter || '',
    page_size: p.page_size || 100,
  })
  open.value = false
}

async function saveCurrent(isDefault: boolean) {
  let state: any
  try {
    state = props.getState()
  } catch (e: any) {
    alert(`Could not capture the current view: ${e?.message || e}`)
    return
  }
  if (!state) {
    alert('The table is not ready yet — try again in a moment.')
    return
  }
  const name = prompt(isDefault ? 'Save as default view — enter a name:' : 'Save current view — enter a name:')
  if (!name || !name.trim()) return
  busy.value = true
  try {
    await api('/saved_filters', {
      method: 'POST',
      body: JSON.stringify({
        name: name.trim(),
        entity: props.entity,
        platform: platform.value,
        filter_state: state.filter || {},
        sort_state: state.sort || [],
        column_state: state.column || [],
        quick_filter: state.quick_filter || '',
        page_size: state.page_size || 100,
        is_default: isDefault,
      }),
    })
    await load()
  } catch (e: any) {
    alert(e.message || 'Failed to save view')
  } finally {
    busy.value = false
  }
}

async function setDefault(p: any) {
  try {
    await api(`/saved_filters/${p.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ is_default: true }),
    })
    await load()
  } catch (e: any) {
    alert(e.message || 'Failed to set default view')
  }
}

async function remove(p: any) {
  if (!confirm(`Delete view "${p.name}"?`)) return
  try {
    await api(`/saved_filters/${p.id}`, { method: 'DELETE' })
    await load()
  } catch (e: any) {
    alert(e.message || 'Failed to delete view')
  }
}

defineExpose({ load, applyDefault })
</script>

<template>
  <div class="profile-menu">
    <button class="btn" :disabled="busy" @click="open = !open">
      Views ▾
    </button>
    <div v-if="open" class="profile-dropdown">
      <div v-if="profiles.length === 0" class="profile-empty">
        No saved views yet.
      </div>
      <div v-for="p in profiles" :key="p.id" class="profile-item">
        <button class="profile-name" :title="`Apply view: ${p.name}`" @click="applyProfile(p)">
          {{ p.name }}
          <span v-if="p.is_default" class="profile-default" title="Default view">★</span>
        </button>
        <span class="profile-tools">
          <button
            v-if="!p.is_default"
            class="profile-tool"
            title="Set as default view"
            @click="setDefault(p)"
          >
            ☆
          </button>
          <button class="profile-tool" title="Delete view" @click="remove(p)">✕</button>
        </span>
      </div>
      <div class="profile-sep" />
      <div class="profile-actions">
        <button class="btn btn-mini" :disabled="busy" @click="saveCurrent(false)">
          Save current view…
        </button>
        <button class="btn btn-mini" :disabled="busy" @click="saveCurrent(true)">
          Save as default view…
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.profile-menu {
  position: relative;
  display: inline-block;
}
.profile-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 100;
  min-width: 260px;
  max-height: 400px;
  overflow-y: auto;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  padding: 6px;
}
.profile-empty {
  color: var(--text-dim);
  font-size: 13px;
  padding: 8px;
}
.profile-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px;
  border-radius: 6px;
}
@media (hover: hover) {
  .profile-item:hover {
    background: var(--panel-2);
  }
}
.profile-name {
  flex: 1;
  text-align: left;
  background: none;
  border: none;
  color: var(--text);
  font-size: 13px;
  padding: 6px 8px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
@media (hover: hover) {
  .profile-name:hover {
    color: var(--accent);
  }
}
.profile-default {
  color: var(--yellow);
  margin-left: 4px;
}
.profile-tools {
  display: flex;
  gap: 2px;
}
.profile-tool {
  background: none;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  font-size: 13px;
  padding: 4px 6px;
  border-radius: 4px;
}
@media (hover: hover) {
  .profile-tool:hover {
    color: var(--text);
    background: var(--panel-2);
  }
}
.profile-sep {
  height: 1px;
  background: var(--border);
  margin: 6px 4px;
}
.profile-actions {
  display: flex;
  gap: 6px;
  padding: 2px 4px 4px;
}

/*
 * As a sheet on a phone. Anchored right at a 260px minimum, this panel hangs
 * off the screen edge once the toolbar wraps — and its rows and its two save
 * buttons are all well under a finger's width.
 *
 * Mirrors MOBILE in src/breakpoints.ts.
 */
@media (max-width: 899px) {
  .profile-dropdown {
    position: fixed;
    inset: auto 0 0 0;
    min-width: 0;
    max-height: 70dvh;
    border-radius: var(--r-lg) var(--r-lg) 0 0;
    padding: 10px 12px;
    padding-bottom: max(10px, env(safe-area-inset-bottom));
    overscroll-behavior: contain;
    z-index: 200;
    animation: sheet-up 180ms var(--ease);
  }

  .profile-name {
    min-height: 44px;
    font-size: 15px;
  }

  .profile-tool {
    min-width: 44px;
    min-height: 44px;
    font-size: 16px;
  }

  /* Two full-width rows rather than two half-width buttons. */
  .profile-actions {
    flex-direction: column;
    gap: 8px;
    padding: 4px 0 0;
  }

  .profile-actions .btn {
    width: 100%;
    min-height: 44px;
  }
}
</style>
