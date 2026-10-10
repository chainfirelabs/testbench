export const WIDGET_CATALOG = [
  { type: 'fleet_summary', title: 'Fleet summary', description: 'Total devices and latest scan state.' },
  { type: 'devices_by_make', title: 'Devices by make', description: 'Most common device makes.' },
  { type: 'online_by_type', title: 'Online by device type', description: 'Scan state across device types.' },
  { type: 'inventory_status', title: 'Inventory status', description: 'Available, checked out, and other states.' },
  { type: 'checkouts_due', title: 'Checkouts due', description: 'Overdue and upcoming returns.' },
  { type: 'recent_devices', title: 'Recently changed devices', description: 'Latest inventory updates.' },
  { type: 'test_activity', title: 'Test activity', description: 'Recent test volume and outcomes.' },
  { type: 'most_tested_software', title: 'Most tested software', description: 'Software ranked by test records.' },
  { type: 'most_tested_devices', title: 'Most tested devices', description: 'Devices ranked by test records.' },
  { type: 'announcement', title: 'Announcement', description: 'A note shared with everyone.' },
  { type: 'untested_devices', title: 'Untested devices', description: 'Devices without test records.' },
  { type: 'recent_problem_tests', title: 'Recent failed or warning tests', description: 'Recent results needing attention.' },
  { type: 'scan_freshness', title: 'Scan freshness', description: 'Recent, stale, and missing scan results.' },
  { type: 'software_outcomes', title: 'Test outcomes by software', description: 'Pass, fail, and warning counts by software.' },
  { type: 'firmware_coverage', title: 'Firmware coverage', description: 'Firmware versions across a device type.' },
] as const

export type WidgetType = typeof WIDGET_CATALOG[number]['type']

export interface DashboardWidget {
  id: string
  type: WidgetType
  title: string
  description: string
  width_percent: number
  height_px: number | null
  show_breakdown: boolean
  top_n: number
  days: number | null
  threshold_days: number
  body: string
  device_type_key: string | null
}

export function newDashboardWidget(type: WidgetType): DashboardWidget {
  const catalog = WIDGET_CATALOG.find((entry) => entry.type === type)!
  return {
    id: type.split('_').join('-'), type, title: catalog.title,
    description: catalog.description, width_percent: 100, height_px: null,
    show_breakdown: true, top_n: 8,
    days: type === 'test_activity' || type === 'recent_problem_tests' ? 30 : null,
    threshold_days: 7,
    body: '', device_type_key: null,
  }
}

export type ResizeDirection = 'left' | 'right' | 'top' | 'bottom' | 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'

export function resizedWidgetSize(
  startWidth: number,
  startHeight: number,
  deltaX: number,
  deltaY: number,
  containerWidth: number,
  direction: ResizeDirection,
): Pick<DashboardWidget, 'width_percent' | 'height_px'> {
  const width = startWidth + (direction.includes('left') ? -deltaX : direction.includes('right') ? deltaX : 0)
  const height = startHeight + (direction.includes('top') ? -deltaY : direction.includes('bottom') ? deltaY : 0)
  const minimumWidthPercent = Math.max(25, Math.ceil(Math.min(320, containerWidth) / containerWidth * 100))
  return {
    width_percent: Math.max(minimumWidthPercent, Math.min(100, Math.round(width / containerWidth * 100))),
    height_px: Math.max(260, Math.min(1200, Math.round(height))),
  }
}

// Dashboard widgets currently contain only scalar fields. Copy each one so
// editing a draft cannot mutate the saved layout's Vue reactive objects.
export function copyDashboardWidgets(widgets: readonly DashboardWidget[]): DashboardWidget[] {
  return widgets.map((widget) => ({ ...widget }))
}

export function reorderDashboardWidgets(widgets: readonly DashboardWidget[], sourceId: string, targetId: string): DashboardWidget[] {
  const sourceIndex = widgets.findIndex((widget) => widget.id === sourceId)
  const targetIndex = widgets.findIndex((widget) => widget.id === targetId)
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return [...widgets]

  const reordered = [...widgets]
  const [moved] = reordered.splice(sourceIndex, 1)
  reordered.splice(targetIndex, 0, moved)
  return reordered
}
