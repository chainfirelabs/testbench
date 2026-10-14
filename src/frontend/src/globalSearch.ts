export function softwareSearchUrl(item: { id?: string; name: string; version?: string | null; is_latest?: boolean }): string {
  const name = encodeURIComponent(item.name)
  if (item.is_latest === false && !item.version && item.id) return `/software/${encodeURIComponent(item.id)}`
  return item.is_latest || !item.version ? `/software/${name}` : `/software/${name}/${encodeURIComponent(item.version)}`
}

export function testSearchUrl(test: { device_unique_id: string; id: string }): string {
  return `/devices/${encodeURIComponent(test.device_unique_id)}?tab=tests&test=${encodeURIComponent(test.id)}`
}

export function deviceSearchUrl(device: { unique_id: string; tested_components?: unknown[] }): string {
  const path = `/devices/${encodeURIComponent(device.unique_id)}`
  return device.tested_components?.length ? `${path}?tab=tests` : path
}

export function componentSearchLabel(component: { name: string; version?: string | null }): string {
  return `${component.name}${component.version ? ` ${component.version}` : ''}`
}
