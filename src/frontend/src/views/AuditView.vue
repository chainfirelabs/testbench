<script setup lang="ts">
import { onMounted, ref } from 'vue'
import DataTable from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import DetailModal from '../components/DetailModal.vue'
import { detailCellRenderer } from '../detail'
import { api, downloadFile } from '../api/client'

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
  const page = await api<any>('/audit_logs?page_size=500')
  rows.value = page.items
}

function onGridReady() {
  profiles.value?.applyDefault()
}

function exportAs(format: string) {
  downloadFile(`/audit_logs/export?format=${format}`, `audit_logs.${format}`)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2>Audit Log</h2>
      <div class="toolbar">
        <button class="btn" @click="exportAs('json')">Export JSON</button>
        <button class="btn" @click="exportAs('csv')">Export CSV</button>
        <FilterProfilesMenu
          ref="profiles"
          entity="audit"
          :get-state="() => table?.getState()"
          :apply-state="(s) => table?.applyState(s)"
        />
      </div>
    </div>
    <DataTable
      ref="table"
      :columns="columns"
      :rows="rows"
      @grid-ready="onGridReady"
    />
    <DetailModal
      v-if="detailEntry"
      :title="detailEntry.title"
      :value="detailEntry.value"
      @close="detailEntry = null"
    />

    <div v-if="toast" class="toast" :class="{ 'toast-error': toastError }">{{ toast }}</div>
  </div>
</template>
