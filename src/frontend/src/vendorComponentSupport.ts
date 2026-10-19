import { SUPPORT_LABELS } from './constants'
import { componentSummaryLabel, type ComponentPanelState } from './componentPanel'

type ComponentClaim = {
  component_name: string
  component_version?: string | null
  support_status: string
  inherited?: boolean
}

function claims(row: any): ComponentClaim[] {
  const source = row?.matching_components?.length ? row.matching_components : row?.component_support || []
  return [...source].sort((a, b) =>
    a.component_name.localeCompare(b.component_name, undefined, { numeric: true, sensitivity: 'base' })
    || (a.component_version || '').localeCompare(b.component_version || '', undefined, { numeric: true }),
  )
}

export function componentSupportText(row: any): string {
  return claims(row)
    .map((item) => `${item.component_name} ${item.component_version || '(unversioned)'}: ${SUPPORT_LABELS[item.support_status] || item.support_status}`)
    .join(', ')
}

export const NO_COMPONENT_CLAIMS = 'No explicit component claims'

/** "3 components" (or the one component's name); empty when the claim names none. */
export function componentSupportSummary(row: any): string {
  const entries = claims(row)
  return entries.length ? componentSummaryLabel(entries.map((entry) => entry.component_name)) : ''
}

export function componentSupportTitle(row: any): string {
  const count = claims(row).length
  return `View ${count} component version${count === 1 ? '' : 's'}`
}

/**
 * The dialog listing a claim's components. `software` names the version when
 * the row does not, as on a device page where claims are grouped under it.
 */
export function componentSupportPanel(row: any, software?: { name?: string; version?: string | null }): ComponentPanelState {
  return {
    title: 'Component support',
    subtitle: [row?.software_name ?? software?.name, row?.software_version ?? software?.version,
      [row?.make, row?.model].filter(Boolean).join(' ')].filter(Boolean).join(' · '),
    matching: !!row?.matching_components?.length,
    items: claims(row).map((entry) => ({
      name: entry.component_name,
      version: entry.component_version,
      status: entry.support_status,
      inherited: entry.inherited,
    })),
  }
}

function componentSupportRenderer(params: any, onOpen: (panel: ComponentPanelState) => void): HTMLElement {
  const summary = componentSupportSummary(params.data)
  if (!summary) {
    const empty = document.createElement('span')
    empty.className = 'muted'
    empty.textContent = NO_COMPONENT_CLAIMS
    return empty
  }

  const trigger = document.createElement('button')
  trigger.type = 'button'
  trigger.className = 'component-cell-button'
  const label = document.createElement('strong')
  label.textContent = summary
  trigger.append(label)
  trigger.title = componentSupportTitle(params.data)
  trigger.addEventListener('click', (event) => {
    event.stopPropagation()
    onOpen(componentSupportPanel(params.data))
  })
  return trigger
}

export function componentSupportColumn(onOpen: (panel: ComponentPanelState) => void) {
  return {
    field: 'component_support',
    colId: 'component_name',
    headerName: 'Components',
    editable: false,
    filter: 'agTextColumnFilter',
    filterParams: { filterOptions: ['contains'], maxNumConditions: 1, debounceMs: 300 },
    sortable: false,
    minWidth: 230,
    valueGetter: (params: any) => componentSupportText(params.data),
    cellRenderer: (params: any) => componentSupportRenderer(params, onOpen),
  }
}
