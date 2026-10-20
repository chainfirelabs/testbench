/**
 * A test's files as list columns.
 *
 * Output and artifacts live in object storage rather than in the test's row,
 * but each is a read-only test schema field (`output`, `artifacts`), so every
 * list of tests shows, hides and orders them from Schema → Tests.
 */
import type { FormField } from './components/FormModal.vue'

export const TEST_OUTPUT_FIELD = 'output'
export const TEST_ARTIFACTS_FIELD = 'artifacts'

/** A byte count as people read it: 512 B, 3.4 KiB, 100.0 MiB. */
export function fileSizeLabel(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MiB`
    : bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${bytes} B`
}

/** What `GET /tests/file-storage` reports. */
export interface TestFileStorage {
  storage_enabled: boolean
  output_max_bytes: number
  artifact_max_bytes: number
}

const NO_STORAGE = 'File storage is not configured on this installation (backend.objectStorage).'

/**
 * Output and Artifacts on the New Test dialog. Both are optional.
 *
 * They follow the schema like the rest of the dialog — a field turned off for
 * this installation is not offered — and stay on show, disabled, when there is
 * nowhere to store them, so the form says why rather than leaving them out.
 * The files are uploaded once the test exists; the dialog only collects them.
 */
export function newTestFileFields(
  storage: TestFileStorage | null,
  fields: { key: string; label: string; visible?: boolean }[],
): FormField[] {
  const enabled = !!storage?.storage_enabled
  const label = (key: string, fallback: string) => fields.find((field) => field.key === key)?.label || fallback
  const offered = (key: string) => fields.some((field) => field.key === key && field.visible !== false)
  const out: FormField[] = []
  if (offered(TEST_OUTPUT_FIELD)) {
    out.push({
      key: TEST_OUTPUT_FIELD, label: label(TEST_OUTPUT_FIELD, 'Output'), type: 'file',
      accept: '.txt,.log,text/plain', maxBytes: storage?.output_max_bytes, disabled: !enabled,
      hint: enabled
        ? `Optional. One plain-text file, such as a log, up to ${fileSizeLabel(storage!.output_max_bytes)}.`
        : NO_STORAGE,
    })
  }
  if (offered(TEST_ARTIFACTS_FIELD)) {
    out.push({
      key: TEST_ARTIFACTS_FIELD, label: label(TEST_ARTIFACTS_FIELD, 'Artifacts'), type: 'file',
      multiple: true, maxBytes: storage?.artifact_max_bytes, disabled: !enabled,
      hint: enabled
        ? `Optional. Any files, such as reports or screenshots, up to ${fileSizeLabel(storage!.artifact_max_bytes)} each.`
        : NO_STORAGE,
    })
  }
  return out
}

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
