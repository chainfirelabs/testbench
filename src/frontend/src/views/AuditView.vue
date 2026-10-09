<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import DataTable, { type RemoteTableRequest } from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import DetailModal from '../components/DetailModal.vue'
import { detailCellRenderer } from '../detail'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { remoteTableApi } from '../remoteTableApi'
import { remoteTableParams } from '../remoteTable'
import { makeFilterValues } from '../suggestions'
import { PERMISSION, useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const canManage = () => auth.can(PERMISSION.settingsManage)
const retentionDays = ref(0)
const deleteDeviceChangelogs = ref(false)
const cleanupDate = ref('')
const cleanupPreview = ref<{
  date: string; eligible: number; protected: number; delete_device_changelogs: boolean
} | null>(null)
type CleanupJob = {
  id: string; before: string; state: string; total: number; deleted: number
  error: string | null; requested_by: string; delete_device_changelogs: boolean
}
const cleanupJob = ref<CleanupJob | null>(null)
let cleanupPollTimer: ReturnType<typeof setInterval> | null = null
const managing = ref(false)
const manageError = ref('')

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
    filter: false,
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
  // The JSON detail blob has no server-side checklist predicate.
  skip: ['detail'],
})

let latestAuditRequest = 0
async function loadRemoteAudit(request: RemoteTableRequest) {
  const requestId = ++latestAuditRequest
  const params = remoteTableParams(request)
  const page = await remoteTableApi(`/audit_logs`, params)
  if (requestId === latestAuditRequest) rows.value = page.items
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

async function loadRetention() {
  if (!canManage()) return
  const policy = await api<{ days: number; delete_device_changelogs: boolean }>('/audit_logs/retention')
  retentionDays.value = policy.days
  deleteDeviceChangelogs.value = policy.delete_device_changelogs
}

async function saveRetention() {
  managing.value = true; manageError.value = ''
  try {
    const result = await api<{ days: number }>('/audit_logs/retention', {
      method: 'PUT', body: JSON.stringify({ days: retentionDays.value }),
    })
    retentionDays.value = result.days
    showToast(result.days === 0 ? 'Automatic audit cleanup disabled' : `Eligible audit logs older than ${result.days} day(s) will be deleted daily`)
  } catch (e: any) { manageError.value = e.message } finally { managing.value = false }
}

async function previewCleanup() {
  manageError.value = ''
  cleanupPreview.value = null
  if (!cleanupDate.value) {
    manageError.value = 'Choose a date.'
    return
  }
  const date = cleanupDate.value
  managing.value = true
  try {
    const result = await api<{ eligible: number; protected: number; delete_device_changelogs: boolean }>('/audit_logs/cleanup/preview', {
      method: 'POST', body: JSON.stringify({ before: `${date}T00:00:00Z` }),
    })
    cleanupPreview.value = { date, ...result }
  } catch (e: any) { manageError.value = e.message } finally { managing.value = false }
}

async function deleteOldLogs() {
  const preview = cleanupPreview.value
  if (!preview || preview.date !== cleanupDate.value || !preview.eligible) return
  const warning = preview.delete_device_changelogs ? ' Device changelog entries may also be deleted.' : ''
  if (!confirm(`Delete all eligible audit logs before ${preview.date} (UTC)? The preview found ${preview.eligible.toLocaleString()} logs.${warning}`)) return
  managing.value = true; manageError.value = ''
  try {
    cleanupJob.value = await api<CleanupJob>('/audit_logs/cleanup', {
      method: 'POST', body: JSON.stringify({ before: `${preview.date}T00:00:00Z` }),
    })
    cleanupPreview.value = null
    startCleanupPolling()
  } catch (e: any) { manageError.value = e.message } finally { managing.value = false }
}

async function refreshCleanupJob() {
  const current = cleanupJob.value
  if (!current) return
  try {
    const updated = await api<CleanupJob>(`/audit_logs/cleanup/jobs/${current.id}`)
    cleanupJob.value = updated
    if (updated.state === 'completed' || updated.state === 'failed') {
      if (cleanupPollTimer) clearInterval(cleanupPollTimer)
      cleanupPollTimer = null
      table.value?.reapplyView()
      if (updated.state === 'completed') showToast(`Deleted ${updated.deleted.toLocaleString()} audit logs`)
    }
  } catch (e: any) { manageError.value = e.message }
}

function startCleanupPolling() {
  if (cleanupPollTimer) clearInterval(cleanupPollTimer)
  cleanupPollTimer = setInterval(() => { void refreshCleanupJob() }, 2000)
  void refreshCleanupJob()
}

async function loadRecentCleanupJob() {
  const jobs = await api<CleanupJob[]>('/audit_logs/cleanup/jobs')
  if (!jobs.length) return
  cleanupJob.value = jobs[0]
  if (['queued', 'running'].includes(jobs[0].state)) startCleanupPolling()
}

async function retryCleanupJob() {
  if (!cleanupJob.value || cleanupJob.value.state !== 'failed') return
  managing.value = true; manageError.value = ''
  try {
    cleanupJob.value = await api<CleanupJob>(`/audit_logs/cleanup/jobs/${cleanupJob.value.id}/retry`, { method: 'POST' })
    startCleanupPolling()
  } catch (e: any) { manageError.value = e.message } finally { managing.value = false }
}

onMounted(() => {
  load()
  loadRetention().catch((e) => { manageError.value = e.message })
  if (canManage()) loadRecentCleanupJob().catch((e) => { manageError.value = e.message })
})
onBeforeUnmount(() => { if (cleanupPollTimer) clearInterval(cleanupPollTimer) })
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
    <section v-if="canManage()" class="audit-management card">
      <h3>Audit log cleanup</h3>
      <p>Cleanup never removes the last 15 minutes of logs used for sign-in protection.
        <span v-if="deleteDeviceChangelogs">Device changelog entries may also be deleted. This policy is controlled by Helm.</span>
        <span v-else>Device changelog entries are protected from cleanup. Only Helm can change this policy.</span>
      </p>
      <div class="audit-management-row">
        <label>Keep logs for (days)
          <input v-model.number="retentionDays" type="number" min="0" max="36500" step="1" />
        </label>
        <button class="btn btn-primary" :disabled="managing" @click="saveRetention">Save retention</button>
        <span>0 disables automatic deletion. Cleanup runs daily at 02:17 UTC.</span>
      </div>
      <div class="audit-management-row">
        <label>Delete logs before (UTC date)
          <input v-model="cleanupDate" type="date" @input="cleanupPreview = null" />
        </label>
        <button class="btn" :disabled="managing || !cleanupDate" @click="previewCleanup">Preview</button>
      </div>
      <div v-if="cleanupPreview" class="cleanup-preview" aria-live="polite">
        <strong>Preview for dates before {{ cleanupPreview.date }} (00:00 UTC)</strong>
        <p>{{ cleanupPreview.eligible.toLocaleString() }} eligible logs would be deleted.
          {{ cleanupPreview.protected.toLocaleString() }} logs are protected by the current policy.</p>
        <button v-if="cleanupPreview.eligible" class="btn btn-danger" :disabled="managing" @click="deleteOldLogs">
          {{ managing ? 'Deleting…' : 'Delete all eligible logs' }}
        </button>
      </div>
      <div v-if="cleanupJob" class="cleanup-preview" aria-live="polite">
        <strong>Latest cleanup: {{ cleanupJob.state }}</strong>
        <p>Deleted {{ cleanupJob.deleted.toLocaleString() }} of {{ cleanupJob.total.toLocaleString() }} logs counted when the job started.
          The count can change if logs are added while it runs.</p>
        <p v-if="cleanupJob.error" class="login-error">{{ cleanupJob.error }}</p>
        <button v-if="cleanupJob.state === 'failed'" class="btn" :disabled="managing" @click="retryCleanupJob">Retry cleanup</button>
      </div>
      <p v-if="manageError" class="login-error">{{ manageError }}</p>
    </section>
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

<style scoped>
.audit-management { padding: 18px; margin-bottom: 18px; }
.audit-management-row { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin-top: 12px; }
.audit-management-row label { display: flex; flex-direction: column; gap: 5px; }
.audit-management-row input { max-width: 210px; }
.cleanup-preview { margin-top: 14px; padding: 12px; border: 1px solid var(--border); border-radius: 6px; }
</style>
