<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ApiError, api } from '../api/client'
import DashboardWidgetContent from '../components/DashboardWidgetContent.vue'
import { WIDGET_CATALOG, copyDashboardWidgets, newDashboardWidget, reorderDashboardWidgets, resizedWidgetSize, type DashboardWidget, type ResizeDirection, type WidgetType } from '../dashboardLayout'
import { deviceTypes, loadDeviceTypes } from '../deviceTypes'
import { useAuthStore } from '../stores/auth'

interface DashboardLayout {
  revision: number
  widgets: DashboardWidget[]
}

const auth = useAuthStore()
const canEdit = computed(() => auth.user?.role === 'admin')
const layout = ref<DashboardLayout | null>(null)
const draft = ref<DashboardWidget[]>([])
const editing = ref(false)
const saving = ref(false)
const layoutLoading = ref(true)
const layoutError = ref('')
const saveError = ref('')
const conflict = ref(false)
const visibleWidgets = computed(() => editing.value ? draft.value : layout.value?.widgets || [])
const canSave = computed(() => draft.value.every((widget) => widget.title.trim().length > 0))
const availableWidgets = computed(() => WIDGET_CATALOG.filter((entry) => !draft.value.some((widget) => widget.type === entry.type)))
const selectedWidget = ref<WidgetType | ''>('')
const widgetData = ref<Record<string, any>>({})
const asOf = ref('')
const loading = ref(false)
const error = ref('')
let dataRequest = 0
const resizeHandles: { direction: ResizeDirection, label: string, icon: string }[] = [
  { direction: 'top-left', label: 'Resize widget from top left', icon: '⤡' },
  { direction: 'top', label: 'Resize widget from top', icon: '↕' },
  { direction: 'top-right', label: 'Resize widget from top right', icon: '⤢' },
  { direction: 'right', label: 'Resize widget from right', icon: '↔' },
  { direction: 'bottom-right', label: 'Resize widget from bottom right', icon: '⤡' },
  { direction: 'bottom', label: 'Resize widget from bottom', icon: '↕' },
  { direction: 'bottom-left', label: 'Resize widget from bottom left', icon: '⤢' },
  { direction: 'left', label: 'Resize widget from left', icon: '↔' },
]
let activeResize: {
  pointerId: number
  widget: DashboardWidget
  direction: ResizeDirection
  startX: number
  startY: number
  startWidth: number
  startHeight: number
  containerWidth: number
} | null = null
const draggingId = ref<string | null>(null)
const dropTargetId = ref<string | null>(null)
const selectedWidgetId = ref<string | null>(null)
let activeDrag: { pointerId: number, sourceId: string, startX: number, startY: number } | null = null

function selectWidget(id: string) {
  if (editing.value) selectedWidgetId.value = id
}

function clearDrag() {
  activeDrag = null
  draggingId.value = null
  dropTargetId.value = null
}

function dragTargetAt(x: number, y: number): string | null {
  const card = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-dashboard-widget]')
  const id = card?.dataset.dashboardWidget || null
  return id !== activeDrag?.sourceId ? id : null
}

function startDrag(id: string, event: PointerEvent) {
  if (!editing.value || event.button !== 0 || draft.value.length < 2) return
  const target = event.target
  if (target instanceof Element && target.closest('a, button, input, select, textarea, label, [contenteditable], [role="button"], [role="link"]')) return
  const card = event.currentTarget as HTMLElement
  activeDrag = { pointerId: event.pointerId, sourceId: id, startX: event.clientX, startY: event.clientY }
  card.setPointerCapture(event.pointerId)
  event.preventDefault()
}

function moveDrag(event: PointerEvent) {
  const drag = activeDrag
  if (!drag || drag.pointerId !== event.pointerId) return
  if (!draggingId.value && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 6) return
  draggingId.value = drag.sourceId
  dropTargetId.value = dragTargetAt(event.clientX, event.clientY)
}

function stopDrag(event: PointerEvent) {
  const drag = activeDrag
  if (!drag || drag.pointerId !== event.pointerId) return
  const targetId = draggingId.value ? dragTargetAt(event.clientX, event.clientY) : null
  if (targetId) draft.value = reorderDashboardWidgets(draft.value, drag.sourceId, targetId)
  clearDrag()
}

function cancelDrag(event: PointerEvent) {
  if (activeDrag?.pointerId === event.pointerId) clearDrag()
}

function applySize(widget: DashboardWidget, direction: ResizeDirection, width: number, height: number, dx: number, dy: number, containerWidth: number) {
  const size = resizedWidgetSize(width, height, dx, dy, containerWidth, direction)
  if (direction.includes('left') || direction.includes('right')) widget.width_percent = size.width_percent
  if (direction.includes('top') || direction.includes('bottom')) widget.height_px = size.height_px
}

function startResize(widget: DashboardWidget, direction: ResizeDirection, event: PointerEvent) {
  if (!editing.value || event.button !== 0) return
  const handle = event.currentTarget as HTMLElement
  const section = handle.closest<HTMLElement>('.dashboard-widget')
  if (!section) return
  const rect = section.getBoundingClientRect()
  activeResize = {
    pointerId: event.pointerId, widget, direction,
    startX: event.clientX, startY: event.clientY,
    startWidth: rect.width, startHeight: rect.height,
    containerWidth: section.parentElement?.getBoundingClientRect().width || rect.width,
  }
  handle.setPointerCapture(event.pointerId)
  event.preventDefault()
}

function moveResize(event: PointerEvent) {
  const resize = activeResize
  if (!resize || resize.pointerId !== event.pointerId) return
  applySize(resize.widget, resize.direction, resize.startWidth, resize.startHeight,
    event.clientX - resize.startX, event.clientY - resize.startY, resize.containerWidth)
}

function stopResize(event: PointerEvent) {
  if (activeResize?.pointerId === event.pointerId) activeResize = null
}

function keyboardResize(widget: DashboardWidget, direction: ResizeDirection, event: KeyboardEvent) {
  const dx = event.key === 'ArrowLeft' ? -20 : event.key === 'ArrowRight' ? 20 : 0
  const dy = event.key === 'ArrowUp' ? -20 : event.key === 'ArrowDown' ? 20 : 0
  if ((!dx || (!direction.includes('left') && !direction.includes('right')))
    && (!dy || (!direction.includes('top') && !direction.includes('bottom')))) return
  const section = (event.currentTarget as HTMLElement).closest<HTMLElement>('.dashboard-widget')
  if (!section) return
  const rect = section.getBoundingClientRect()
  applySize(widget, direction, rect.width, rect.height, dx, dy,
    section.parentElement?.getBoundingClientRect().width || rect.width)
  event.preventDefault()
}

async function loadLayout() {
  clearDrag()
  selectedWidgetId.value = null
  layoutLoading.value = true
  layoutError.value = ''
  try {
    layout.value = await api<DashboardLayout>('/dashboard/layout')
    draft.value = copyDashboardWidgets(layout.value.widgets)
    editing.value = false
    saveError.value = ''
    conflict.value = false
  } catch (cause) {
    layoutError.value = cause instanceof Error ? cause.message : 'Could not load the dashboard.'
  } finally {
    layoutLoading.value = false
  }
}

function beginEdit() {
  if (!canEdit.value || !layout.value) return
  draft.value = copyDashboardWidgets(layout.value.widgets)
  selectedWidgetId.value = null
  editing.value = true
  saveError.value = ''
}

function addWidget() {
  if (!editing.value || !selectedWidget.value || !availableWidgets.value.some((entry) => entry.type === selectedWidget.value)) return
  draft.value.push(newDashboardWidget(selectedWidget.value))
  selectedWidget.value = ''
}

function removeWidget(id: string) {
  if (activeDrag?.sourceId === id || dropTargetId.value === id) clearDrag()
  if (selectedWidgetId.value === id) selectedWidgetId.value = null
  draft.value = draft.value.filter((widget) => widget.id !== id)
}

function moveWidget(index: number, offset: number) {
  const target = index + offset
  if (target < 0 || target >= draft.value.length) return
  draft.value = reorderDashboardWidgets(draft.value, draft.value[index].id, draft.value[target].id)
}

function cancelEdit() {
  clearDrag()
  selectedWidgetId.value = null
  draft.value = copyDashboardWidgets(layout.value?.widgets || [])
  editing.value = false
  saveError.value = ''
  conflict.value = false
}

async function saveLayout() {
  if (!canEdit.value || !layout.value || saving.value || !canSave.value) return
  clearDrag()
  saving.value = true
  saveError.value = ''
  conflict.value = false
  try {
    layout.value = await api<DashboardLayout>('/dashboard/layout', {
      method: 'PUT',
      body: JSON.stringify({
        revision: layout.value.revision,
        widgets: draft.value.map((widget) => ({
          ...widget, title: widget.title.trim(), description: widget.description.trim(),
        })),
      }),
    })
    draft.value = copyDashboardWidgets(layout.value.widgets)
    editing.value = false
    selectedWidgetId.value = null
  } catch (cause) {
    conflict.value = cause instanceof ApiError && cause.status === 409
    saveError.value = cause instanceof Error ? cause.message : 'Could not save the dashboard.'
  } finally {
    saving.value = false
  }
}

async function refresh() {
  const request = ++dataRequest
  if (!visibleWidgets.value.length) {
    widgetData.value = {}
    asOf.value = ''
    loading.value = false
    error.value = ''
    return
  }
  loading.value = true
  error.value = ''
  try {
    const response = await api<{ as_of: string, widgets: Record<string, any> }>('/dashboard/data', {
      method: 'POST', body: JSON.stringify({ widgets: visibleWidgets.value }),
    })
    if (request === dataRequest) {
      widgetData.value = response.widgets
      asOf.value = response.as_of
    }
  } catch (cause) {
    if (request === dataRequest) error.value = cause instanceof Error ? cause.message : 'Could not load dashboard data.'
  } finally {
    if (request === dataRequest) loading.value = false
  }
}

watch(() => visibleWidgets.value.map((widget) => [widget.id, widget.top_n, widget.days, widget.threshold_days, widget.device_type_key].join(':')).join('|'), () => {
  if (layout.value) refresh()
})

onMounted(async () => {
  await loadLayout()
  loadDeviceTypes().catch(() => {})
})
</script>

<template>
  <div class="page page-flow dashboard">
    <div class="dashboard-heading">
      <div>
        <p class="dashboard-eyebrow">Overview</p>
        <h1>Home</h1>
        <p class="dashboard-intro">A quick look at the devices in your fleet.</p>
      </div>
      <div class="dashboard-actions">
        <router-link to="/devices" class="btn dashboard-inventory-link">View all devices <span aria-hidden="true">↗</span></router-link>
        <button v-if="layout && visibleWidgets.length" class="btn" type="button" :disabled="loading" @click="refresh">{{ loading ? 'Refreshing…' : 'Refresh dashboard' }}</button>
        <button v-if="canEdit && layout && !editing" class="btn" type="button" @click="beginEdit">Edit dashboard</button>
        <template v-if="canEdit && editing">
          <button class="btn" type="button" :disabled="saving" @click="cancelEdit">Cancel</button>
          <button class="btn btn-primary" type="button" :disabled="saving || !canSave" @click="saveLayout">{{ saving ? 'Saving…' : 'Save layout' }}</button>
        </template>
      </div>
    </div>

    <p v-if="layoutError" role="alert" class="widget-error">{{ layoutError }} <button type="button" @click="loadLayout">Try again</button></p>
    <p v-if="layoutLoading && !layout" role="status" class="widget-message">Loading dashboard…</p>
    <p v-if="saveError" role="alert" class="widget-error">
      {{ saveError }} <button v-if="conflict" type="button" @click="loadLayout">Reload layout</button>
    </p>

    <section v-if="editing" class="dashboard-editor" aria-label="Available widgets">
      <div>
        <strong>Widget library</strong>
        <p>Drag a widget onto another to reorder it. Click a widget to reveal resize grips on every edge and corner. Use Remove widget to take one off the dashboard, then Save layout to publish your changes.</p>
      </div>
      <div class="add-widget">
        <label for="widget-choice">Widget</label>
        <select id="widget-choice" v-model="selectedWidget" :disabled="!availableWidgets.length">
          <option value="">Choose a widget</option>
          <option v-for="entry in availableWidgets" :key="entry.type" :value="entry.type">{{ entry.title }}</option>
        </select>
        <button class="btn" type="button" :disabled="!selectedWidget" @click="addWidget">Add widget</button>
      </div>
    </section>

    <div class="dashboard-grid">
      <section v-for="(item, index) in visibleWidgets" :key="item.id" class="dashboard-widget" :class="{ 'widget-resizing': editing, 'widget-selected': editing && selectedWidgetId === item.id, 'widget-fixed-height': item.height_px !== null, 'widget-dragging': draggingId === item.id, 'widget-drop-target': dropTargetId === item.id }" :data-dashboard-widget="item.id" :style="{ width: `calc(${item.width_percent}% - ${18 * (1 - item.width_percent / 100)}px)`, height: item.height_px ? `${item.height_px}px` : undefined }" :aria-labelledby="`widget-title-${item.id}`" @pointerdown="selectWidget(item.id); startDrag(item.id, $event)" @pointermove="moveDrag" @pointerup="stopDrag" @pointercancel="cancelDrag" @lostpointercapture="cancelDrag" @focusin="selectWidget(item.id)">
        <div v-if="editing" class="widget-toolbar">
          <span v-if="visibleWidgets.length > 1" class="widget-drag-cue" title="Drag anywhere on the widget to reorder it" aria-hidden="true">⠿ Drag widget</span>
          <button class="btn btn-danger widget-remove" type="button" :aria-label="`Remove ${item.title} from dashboard`" @click="removeWidget(item.id)">Remove widget</button>
        </div>
        <div class="widget-body">
          <div v-if="editing" class="widget-settings">
            <label>Title <input v-model="item.title" type="text" maxlength="80" required /></label>
            <label>Description <input v-model="item.description" type="text" maxlength="160" /></label>
            <label v-if="['most_tested_software', 'most_tested_devices', 'recent_devices', 'checkouts_due', 'recent_problem_tests', 'untested_devices', 'software_outcomes', 'devices_by_make', 'firmware_coverage'].includes(item.type)">Top items <select v-model.number="item.top_n"><option v-for="limit in [3, 5, 8, 10, 15, 20]" :key="limit" :value="limit">{{ limit }}</option></select></label>
            <label v-if="['test_activity', 'most_tested_software', 'most_tested_devices', 'recent_problem_tests', 'software_outcomes'].includes(item.type)">Period <select v-model.number="item.days"><option v-if="!['test_activity', 'recent_problem_tests'].includes(item.type)" :value="null">All time</option><option v-for="days in [7, 30, 90, 365]" :key="days" :value="days">Last {{ days }} days</option></select></label>
            <label v-if="item.type === 'scan_freshness'">Stale after <select v-model.number="item.threshold_days"><option v-for="days in [1, 3, 7, 14, 30, 90]" :key="days" :value="days">{{ days }} days</option></select></label>
            <label v-if="item.type === 'firmware_coverage'">Device type <select v-model="item.device_type_key"><option :value="null">All types</option><option v-for="deviceType in deviceTypes" :key="deviceType.key" :value="deviceType.key">{{ deviceType.label }}</option></select></label>
            <label v-if="item.type === 'fleet_summary'" class="setting-checkbox"><input v-model="item.show_breakdown" type="checkbox" /> Show breakdown bar</label>
            <label v-if="item.type === 'announcement'" class="announcement-setting">Message <textarea v-model="item.body" maxlength="1000" rows="3"></textarea></label>
            <div class="widget-order"><button class="btn" type="button" :disabled="index === 0" :aria-label="`Move ${item.title} up`" @click="moveWidget(index, -1)">↑</button><button class="btn" type="button" :disabled="index === visibleWidgets.length - 1" :aria-label="`Move ${item.title} down`" @click="moveWidget(index, 1)">↓</button></div>
          </div>
          <div class="widget-heading">
            <div>
              <p class="widget-kicker">{{ WIDGET_CATALOG.find((entry) => entry.type === item.type)?.title }}</p>
              <h2 :id="`widget-title-${item.id}`">{{ item.title }}</h2>
              <p v-if="item.description" class="widget-description">{{ item.description }}</p>
            </div>
          </div>
          <p v-if="error" role="alert" class="widget-error">{{ error }} <button type="button" @click="refresh">Try again</button></p>
          <p v-else-if="!widgetData[item.id] && loading" role="status" class="widget-message">Loading {{ item.title }}…</p>
          <DashboardWidgetContent v-else-if="widgetData[item.id]" :widget="item" :data="widgetData[item.id]" />
          <p v-if="asOf && widgetData[item.id]" class="widget-footnote">Updated {{ new Date(asOf).toLocaleString() }}</p>
        </div>
        <template v-if="editing && selectedWidgetId === item.id">
          <button v-for="handle in resizeHandles" :key="handle.direction" type="button" class="resize-handle" :class="`resize-${handle.direction}`" :aria-label="handle.label" :title="handle.label" @pointerdown="startResize(item, handle.direction, $event)" @pointermove="moveResize" @pointerup="stopResize" @pointercancel="stopResize" @keydown="keyboardResize(item, handle.direction, $event)">{{ handle.icon }}</button>
        </template>
      </section>
    </div>
    <section v-if="!layoutLoading && !layoutError && !visibleWidgets.length" class="dashboard-empty">
      <h2>No widgets selected</h2>
      <p>An administrator can add a widget to this shared dashboard.</p>
    </section>
  </div>
</template>

<style scoped>
.dashboard { max-width: 1320px; width: 100%; margin: 0 auto; }
.dashboard-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin: 10px 0 30px; flex-wrap: wrap; }
.dashboard-eyebrow, .widget-kicker { margin: 0 0 5px; color: var(--accent-soft); font-size: 11px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
.dashboard h1 { margin: 0; font-size: clamp(29px, 4vw, 39px); line-height: 1.18; }
.dashboard-intro { margin: 9px 0 0; color: var(--text-muted); font-size: 15px; }
.dashboard-inventory-link { text-decoration: none; white-space: nowrap; }
.dashboard-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.dashboard-editor { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 18px; padding: 17px 20px; border: 1px solid var(--accent-a24); border-radius: var(--r-md); background: var(--accent-a08); }
.dashboard-editor p { margin: 3px 0 0; color: var(--text-muted); }
.add-widget { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; }
.add-widget select { min-height: var(--control-h); padding: 0 9px; color: var(--text); background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--r-sm); }
.dashboard-grid { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 18px; width: 100%; }
.widget-toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin: 0 14px 16px; }
.widget-remove { margin-left: auto; white-space: nowrap; }
.widget-settings { display: flex; flex-wrap: wrap; align-items: end; gap: 10px; margin-bottom: 24px; padding-bottom: 18px; border-bottom: 1px solid var(--border); }
.widget-settings label { display: flex; flex-direction: column; gap: 5px; color: var(--text-muted); font-size: 12px; font-weight: 600; }
.widget-settings > label:first-of-type { min-width: 160px; }
.widget-settings > label:nth-of-type(2) { flex: 1 1 240px; }
.widget-settings input:not([type=checkbox]), .widget-settings select { height: var(--control-h); min-width: 0; padding: 0 9px; color: var(--text); background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--r-sm); font: inherit; }
.widget-settings textarea { min-width: 0; padding: 9px; color: var(--text); background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--r-sm); font: inherit; }
.widget-settings .announcement-setting { flex: 1 1 100%; }
.widget-order { display: flex; gap: 5px; }
.widget-drag-cue { border: 1px solid var(--accent-a24); border-radius: var(--r-sm); background: var(--accent-a08); color: var(--accent-soft); cursor: grab; font-size: 12px; font-weight: 700; padding: 6px 9px; touch-action: none; user-select: none; white-space: nowrap; }
.widget-settings .setting-checkbox { flex-direction: row; align-items: center; height: var(--control-h); gap: 6px; white-space: nowrap; }
.widget-settings .setting-checkbox input { accent-color: var(--accent); }
.dashboard-empty { padding: 42px 20px; border: 1px dashed var(--border); border-radius: var(--r-md); text-align: center; color: var(--text-muted); }
.dashboard-empty h2 { margin: 0 0 6px; color: var(--text); }
.dashboard-empty p { margin: 0; }
.dashboard-widget { position: relative; min-width: min(100%, 320px); min-height: 260px; max-width: 100%; border: 1px solid var(--border); border-radius: var(--r-lg); background: var(--surface); box-shadow: var(--shadow-sm); padding: clamp(20px, 3vw, 30px); }
.widget-fixed-height { display: flex; flex-direction: column; }
.widget-fixed-height .widget-body { flex: 1; min-height: 0; overflow: auto; }
.widget-resizing { outline: 1px dashed var(--accent-a24); outline-offset: 3px; cursor: grab; }
.widget-resizing .widget-settings { cursor: default; }
.widget-resizing a { cursor: pointer; }
.widget-selected { outline: 2px solid var(--accent); outline-offset: 3px; }
.widget-dragging { opacity: .55; }
.widget-drop-target { outline: 3px solid var(--accent); outline-offset: 3px; }
.resize-handle { position: absolute; z-index: 2; width: 30px; height: 30px; padding: 0; border: 1px solid var(--border); border-radius: var(--r-sm); background: var(--surface-3); color: var(--text-muted); font-size: 18px; line-height: 1; touch-action: none; }
.resize-handle:hover, .resize-handle:focus-visible { color: var(--text); border-color: var(--accent); background: var(--surface-2); }
.resize-left { left: 0; top: calc(50% - 15px); cursor: ew-resize; }
.resize-right { right: 0; top: calc(50% - 15px); cursor: ew-resize; }
.resize-top { top: 0; left: calc(50% - 15px); cursor: ns-resize; }
.resize-bottom { bottom: 0; left: calc(50% - 15px); cursor: ns-resize; }
.resize-top-left { top: 0; left: 0; cursor: nwse-resize; }
.resize-top-right { top: 0; right: 0; cursor: nesw-resize; }
.resize-bottom-left { bottom: 0; left: 0; cursor: nesw-resize; }
.resize-bottom-right { bottom: 0; right: 0; cursor: nwse-resize; }
.widget-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.widget-heading h2 { margin: 0; font-size: 21px; }
.widget-description { color: var(--text-muted); margin: 6px 0 0; }
.widget-footnote { margin: 28px 0 0; color: var(--text-muted); font-size: 12px; }
.widget-message { margin: 27px 0; color: var(--text-muted); }
.widget-error { margin: 18px 0 0; color: var(--danger); }
.widget-error button { color: var(--text); border: 0; background: none; text-decoration: underline; cursor: pointer; }
@media (max-width: 520px) { .dashboard-heading { margin-bottom: 22px; } .widget-heading { align-items: center; } .widget-settings > label { width: 100%; } }
</style>
