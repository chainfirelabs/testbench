<script setup lang="ts">
import { onMounted, ref } from 'vue'
import DataTable, { type RemoteTableRequest } from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import DetailModal from '../components/DetailModal.vue'
import { detailCellRenderer } from '../detail'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { remoteTableParams } from '../remoteTable'
import { makeFilterValues } from '../suggestions'

const rows = ref<any[]>([])
const toast = ref('')
const toastError = ref(false)
const profiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const table = ref<InstanceType<typeof DataTable> | null>(null)

const fmt = (p: any) => (p.value ? new Date(p.value).toLocaleString() : '')

/*
 * The entry a "View Detail" cell is currently showing. An audit detail is
 * usually a field diff — `{"status": {"old": "available", "new": "checked_out"}}`
 * — which flattened onto one grid line is the least readable form of the one
 * thing the row exists to say.
 */
const detailEntry = ref<{ title: string; value: any } | null>(null)

function openDetail(title: string, value: any) {
  detailEntry.value = { title, value }
}

const columns = [
  { field: 'timestamp', headerName: 'When', valueFormatter: fmt, minWidth: 170 },
  { field: 'username', headerName: 'User' },
  { field: 'action', headerName: 'Action', minWidth: 150 },
  { field: 'entity_type', headerName: 'Entity' },
  { field: 'entity_id', headerName: 'Entity ID', editable: false },
  { field: 'ip_address', headerName: 'IP' },
  {
    field: 'detail',
    headerName: 'Detail',
    editable: false,
    minWidth: 200,
    cellRenderer: detailCellRenderer('Detail', openDetail),
    // Still needed: the export and the grid's quick filter read the formatted
    // value, and the renderer only covers what is painted.
    valueFormatter: (p: any) => (p.value && Object.keys(p.value).length ? JSON.stringify(p.value) : ''),
  },
]

async function load() {
  table.value?.reapplyView()
}

/*
 * Values for the column filters' checklists.
 *
 * The audit log is the list where picking values off a list matters most —
 * "everything this user did", "every login failure" — and it is also the
 * longest, so the page on screen is the least representative sample of it
 * there is. The distinct values come from the server.
 */
const filterValues = makeFilterValues({
  entity: 'audit_logs',
  // Timestamps, opaque ids and the JSON detail blob: nothing anyone filters by
  // ticking a value.
  skip: ['timestamp', 'entity_id', 'detail'],
})

async function loadRemoteAudit(request: RemoteTableRequest) {
  const params = remoteTableParams(request)
  const page = await api<any>(`/audit_logs?${params}`)
  rows.value = page.items
  return { rows: page.items, total: page.total }
}

function onGridReady() {
  profiles.value?.applyDefault()
}

/*
 * The toast on this page was declared and rendered but never set by anything,
 * so an export that failed said nothing at all. It says something now.
 */
function showToast(msg: string, isError = false) {
  toast.value = msg
  toastError.value = isError
  setTimeout(() => (toast.value = ''), 4000)
}

// The audit log is the largest table in the application and the one most
// likely to keep somebody waiting.
const { downloading, download } = useDownload(showToast)

function exportAs(format: string) {
  download(`/audit_logs/export?format=${format}`, `audit_logs.${format}`, 'export')
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2>Audit Log</h2>
      <div class="toolbar">
        <button class="btn" :disabled="downloading" @click="exportAs('json')">
            {{ downloading ? 'Preparing…' : 'Export JSON' }}
          </button>
        <button class="btn" :disabled="downloading" @click="exportAs('csv')">
            {{ downloading ? 'Preparing…' : 'Export CSV' }}
          </button>
      </div>
    </div>
    <DataTable
      ref="table"
      :columns="columns"
      :rows="rows"
      :remote-loader="loadRemoteAudit"
      :filter-values="filterValues"
      @grid-ready="onGridReady"
    >
      <template #table-actions>
        <FilterProfilesMenu
          ref="profiles"
          entity="audit"
          :get-state="() => table?.getState()"
          :apply-state="(s) => table?.applyState(s)"
        />
      </template>
    </DataTable>
    <DetailModal
      v-if="detailEntry"
      :title="detailEntry.title"
      :value="detailEntry.value"
      @close="detailEntry = null"
    />

    <div v-if="toast" class="toast" :class="{ 'toast-error': toastError }">{{ toast }}</div>
  </div>
</template>
