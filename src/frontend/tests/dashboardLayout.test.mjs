import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { WIDGET_CATALOG, copyDashboardWidgets, newDashboardWidget, reorderDashboardWidgets, resizedWidgetSize } from '../src/dashboardLayout.ts'

test('every dashboard library entry has a distinct valid widget identity', () => {
  const widgets = WIDGET_CATALOG.map((entry) => newDashboardWidget(entry.type))
  assert.equal(widgets.length, 15)
  assert.equal(new Set(widgets.map((widget) => widget.id)).size, widgets.length)
  assert.ok(widgets.every((widget) => widget.id === widget.type.split('_').join('-')))
  assert.ok(widgets.every((widget) => widget.title && widget.top_n <= 20))
})

test('dashboard drafts copy Vue reactive widgets without mutating the saved layout', () => {
  const layout = ref({ widgets: [{
    id: 'fleet-summary', type: 'fleet_summary', title: 'Fleet summary',
    description: 'Device counts', width_percent: 100, height_px: null, show_breakdown: true,
  }] })

  const draft = copyDashboardWidgets(layout.value.widgets)
  draft[0].title = 'My fleet'
  draft[0].show_breakdown = false

  assert.equal(layout.value.widgets[0].title, 'Fleet summary')
  assert.equal(layout.value.widgets[0].show_breakdown, true)
  assert.deepEqual(copyDashboardWidgets(layout.value.widgets), [{
    id: 'fleet-summary', type: 'fleet_summary', title: 'Fleet summary',
    description: 'Device counts', width_percent: 100, height_px: null, show_breakdown: true,
  }])
})

test('reordering moves a widget onto its target without changing the saved order', () => {
  const widgets = ['fleet_summary', 'devices_by_make', 'online_by_type', 'inventory_status']
    .map((type) => newDashboardWidget(type))
  const forward = reorderDashboardWidgets(widgets, 'fleet-summary', 'online-by-type')
  assert.deepEqual(forward.map((widget) => widget.id), [
    'devices-by-make', 'online-by-type', 'fleet-summary', 'inventory-status',
  ])
  const backward = reorderDashboardWidgets(widgets, 'inventory-status', 'devices-by-make')
  assert.deepEqual(backward.map((widget) => widget.id), [
    'fleet-summary', 'inventory-status', 'devices-by-make', 'online-by-type',
  ])
  assert.deepEqual(widgets.map((widget) => widget.id), [
    'fleet-summary', 'devices-by-make', 'online-by-type', 'inventory-status',
  ])
  assert.deepEqual(reorderDashboardWidgets(widgets, 'missing', 'fleet-summary'), widgets)
})

test('resize directions update the requested dimensions and stay within bounds', () => {
  assert.deepEqual(resizedWidgetSize(800, 400, -200, 100, 1000, 'right'), {
    width_percent: 60, height_px: 400,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, -200, 100, 1000, 'left'), {
    width_percent: 100, height_px: 400,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, -200, 100, 1000, 'bottom'), {
    width_percent: 80, height_px: 500,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, -200, 100, 1000, 'top'), {
    width_percent: 80, height_px: 300,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, -200, 100, 1000, 'top-left'), {
    width_percent: 100, height_px: 300,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, -200, 100, 1000, 'top-right'), {
    width_percent: 60, height_px: 300,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, -200, 100, 1000, 'bottom-left'), {
    width_percent: 100, height_px: 500,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, -200, 100, 1000, 'bottom-right'), {
    width_percent: 60, height_px: 500,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, -1000, -1000, 1000, 'bottom-right'), {
    width_percent: 32, height_px: 260,
  })
  assert.deepEqual(resizedWidgetSize(800, 400, 1000, 1000, 1000, 'bottom-right'), {
    width_percent: 100, height_px: 1200,
  })
  assert.deepEqual(resizedWidgetSize(500, 400, -1000, 0, 500, 'right'), {
    width_percent: 64, height_px: 400,
  })
})
