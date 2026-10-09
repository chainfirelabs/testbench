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

function componentSupportRenderer(params: any, onOpen: (panel: ComponentPanelState) => void): HTMLElement {
  const entries = claims(params.data)
  if (!entries.length) {
    const empty = document.createElement('span')
    empty.className = 'muted'
    empty.textContent = 'No explicit component claims'
    return empty
  }

  const trigger = document.createElement('button')
  trigger.type = 'button'
  trigger.className = 'component-cell-button'
  const matching = !!params.data?.matching_components?.length
  const label = document.createElement('strong')
  label.textContent = componentSummaryLabel(entries.map((entry) => entry.component_name))
  trigger.append(label)
  trigger.title = `View ${entries.length} component version${entries.length === 1 ? '' : 's'}`
  trigger.addEventListener('click', (event) => {
    event.stopPropagation()
    onOpen({
      title: 'Component support',
      subtitle: [params.data?.software_name, params.data?.software_version,
        [params.data?.make, params.data?.model].filter(Boolean).join(' ')].filter(Boolean).join(' · '),
      matching,
      items: entries.map((entry) => ({
        name: entry.component_name,
        version: entry.component_version,
        status: entry.support_status,
        inherited: entry.inherited,
      })),
    })
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
