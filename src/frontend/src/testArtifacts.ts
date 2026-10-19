/**
 * A test's files as list columns.
 *
 * Output and artifacts live in object storage rather than in the test's row,
 * but each is a read-only test schema field (`output`, `artifacts`), so every
 * list of tests shows, hides and orders them from Schema → Tests.
 */
export const TEST_OUTPUT_FIELD = 'output'
export const TEST_ARTIFACTS_FIELD = 'artifacts'

/** Shared read-only action column opening a test's artifacts. */
export function testArtifactsColumn(open: (row: any) => void) {
  return {
    field: 'artifact_count', headerName: 'Artifacts', editable: false,
    sortable: false, filter: false, minWidth: 135,
    cellRenderer: (p: any) => {
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'btn btn-mini'
      button.textContent = `Artifacts (${p.data?.artifact_count || 0})`
      button.addEventListener('click', (event) => {
        event.stopPropagation()
        if (p.data?.id) open(p.data)
      })
      return button
    },
  }
}

/**
 * Shared read-only column opening a test's output viewer.
 *
 * A test with no output shows the placeholder, except to someone who can add
 * one: for them the cell is the way to upload it.
 */
export function testOutputColumn(open: (row: any) => void, canEdit: () => boolean) {
  return {
    field: 'output_filename', headerName: 'Output', editable: false,
    sortable: false, filter: false, minWidth: 135,
    cellRenderer: (p: any) => {
      if (!p.data?.output_filename && !canEdit()) return null
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'btn btn-mini'
      button.textContent = p.data?.output_filename ? 'View output' : 'Add output'
      if (p.data?.output_filename) button.title = p.data.output_filename
      button.addEventListener('click', (event) => {
        event.stopPropagation()
        if (p.data?.id) open(p.data)
      })
      return button
    },
  }
}
