export interface VendorDeviceRow {
  id: string
  make?: string | null
  model?: string | null
  hardware_version?: string | null
  firmware_version?: string | null
  [key: string]: any
}

export interface CollapsedVendorDeviceRow extends VendorDeviceRow {
  _groupKey: string
  _firmwareMembers: VendorDeviceRow[]
}

const versionCollator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: 'base',
})

function normalized(value: unknown): string {
  return String(value ?? '').trim().toLocaleLowerCase()
}

export function vendorDeviceGroupKey(row: VendorDeviceRow): string {
  return [row.make, row.model, row.hardware_version].map(normalized).join('\u0000')
}

/** Newest first, using natural numeric ordering (10.2 after 9.12). */
export function compareFirmwareNewest(a: VendorDeviceRow, b: VendorDeviceRow): number {
  const aVersion = String(a.firmware_version ?? '').trim()
  const bVersion = String(b.firmware_version ?? '').trim()
  if (!aVersion && bVersion) return 1
  if (aVersion && !bVersion) return -1
  const byVersion = versionCollator.compare(bVersion, aVersion)
  if (byVersion) return byVersion
  return String(b.updated_at || b.created_at || '').localeCompare(
    String(a.updated_at || a.created_at || ''),
  )
}

/**
 * Presentation-only collapse. Every source row remains present in
 * `_firmwareMembers`; `selectedByGroup` chooses which record represents it.
 */
export function collapseVendorDevices(
  rows: VendorDeviceRow[],
  selectedByGroup: Readonly<Record<string, string>> = {},
): CollapsedVendorDeviceRow[] {
  const groups = new Map<string, VendorDeviceRow[]>()
  for (const row of rows) {
    const key = vendorDeviceGroupKey(row)
    const members = groups.get(key) || []
    members.push(row)
    groups.set(key, members)
  }

  return [...groups.entries()].map(([key, unsorted]) => {
    const members = [...unsorted].sort(compareFirmwareNewest)
    const selected = members.find((row) => row.id === selectedByGroup[key]) || members[0]
    return { ...selected, _groupKey: key, _firmwareMembers: members }
  })
}
