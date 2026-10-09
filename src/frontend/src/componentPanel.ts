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

export function componentSummaryLabel(names: string[]): string {
  const uniqueNames = [...new Map(names.map((name) => [name.toLocaleLowerCase(), name])).values()]
  return uniqueNames.length === 1 ? uniqueNames[0] : `${uniqueNames.length} components`
}
