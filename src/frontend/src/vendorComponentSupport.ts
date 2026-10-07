import { SUPPORT_LABELS } from './constants'

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

/** Native details also works in the mobile card renderer, which reuses this column. */
function componentSupportRenderer(params: any): HTMLElement {
  const entries = claims(params.data)
  if (!entries.length) {
    const empty = document.createElement('span')
    empty.className = 'muted'
    empty.textContent = 'No explicit component claims'
    return empty
  }

  const details = document.createElement('details')
  details.className = 'vendor-component-details'
  details.onclick = (event) => event.stopPropagation()

  const summary = document.createElement('summary')
  summary.textContent = `${entries.length}${params.data?.matching_components?.length ? ' matching' : ''} component${entries.length === 1 ? '' : 's'}`
  details.appendChild(summary)

  const list = document.createElement('ul')
  for (const entry of entries) {
    const item = document.createElement('li')
    const name = document.createElement('span')
    name.textContent = `${entry.component_name} ${entry.component_version || '(unversioned)'}`
    const status = document.createElement('span')
    status.className = `support-pill ${entry.support_status}`
    status.textContent = `${SUPPORT_LABELS[entry.support_status] || entry.support_status}${entry.inherited ? ' (inherited)' : ''}`
    item.append(name, status)
    list.appendChild(item)
  }
  details.appendChild(list)
  return details
}

export const componentSupportColumn = {
  field: 'component_support',
  headerName: 'Components',
  editable: false,
  filter: false,
  minWidth: 230,
  autoHeight: true,
  wrapText: true,
  valueGetter: (params: any) => componentSupportText(params.data),
  cellRenderer: componentSupportRenderer,
}
