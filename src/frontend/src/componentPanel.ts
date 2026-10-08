export interface ComponentPanelItem {
  name: string
  version?: string | null
  status?: string
  inherited?: boolean
  [key: string]: unknown
}

export interface ComponentPanelState {
  title: string
  subtitle?: string
  items: ComponentPanelItem[]
  matching?: boolean
}

export function componentPreview(items: ComponentPanelItem[], limit = 2): string {
  const names = [...new Set(items.map((item) => item.name))]
  const shown = names.slice(0, limit).join(', ')
  return `${shown}${names.length > limit ? ` +${names.length - limit}` : ''}`
}
