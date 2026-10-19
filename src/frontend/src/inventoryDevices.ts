/**
 * The Inventory Devices column of a vendor claim grid.
 *
 * A claim describes hardware by make, model, firmware and the like; the
 * inventory devices are the ones in this fleet that fit that description. The
 * cell says how many ("4 devices"), like the Components column, and names the
 * first few in its tooltip from what the API sends; the full list opens in a
 * dialog. A claim nothing matches renders empty, which the grid shows as N/A.
 *
 * A schema field (`inventory_devices`), so it is shown, hidden and ordered
 * under Schema like the claim's other columns.
 */
export const INVENTORY_DEVICES_FIELD = 'inventory_devices'

/** "dev-a, dev-b, dev-c +4 more" — or empty when nothing matches. */
export function inventoryDevicesText(row: any): string {
  const ids: string[] = row?.inventory_devices || []
  const count: number = row?.inventory_match_count ?? ids.length
  if (!count || !ids.length) return ''
  const more = count - ids.length
  return more > 0 ? `${ids.join(', ')} +${more} more` : ids.join(', ')
}

/** "4 devices" — or empty when nothing matches. */
export function inventoryDevicesCount(row: any): string {
  const count: number = row?.inventory_match_count ?? row?.inventory_devices?.length ?? 0
  return count ? `${count} device${count === 1 ? '' : 's'}` : ''
}

export function inventoryDevicesColumn(open: (row: any) => void) {
  return {
    field: INVENTORY_DEVICES_FIELD,
    headerName: 'Inventory Devices',
    editable: false,
    sortable: false,
    filter: false,
    minWidth: 200,
    // Sorting is off, but the card list reads this.
    valueGetter: (p: any) => inventoryDevicesCount(p.data),
    tooltipValueGetter: (p: any) => inventoryDevicesText(p.data),
    cellRenderer: (p: any) => {
      const text = inventoryDevicesCount(p.data)
      if (!text) return null
      const button = document.createElement('button')
      button.type = 'button'
      // Global class: cell renderers build detached DOM, which scoped styles
      // never reach.
      button.className = 'cell-view-btn inventory-devices-btn'
      button.textContent = text
      button.addEventListener('click', (event) => {
        event.stopPropagation()
        open(p.data)
      })
      return button
    },
  }
}
