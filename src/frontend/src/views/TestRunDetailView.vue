<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import FormModal from '../components/FormModal.vue'
import TestsView from './TestsView.vue'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { router } from '../router'
import { PERMISSION, useAuthStore } from '../stores/auth'
import { fileSizeLabel } from '../testArtifacts'
import { RUN_FORM_FIELDS, runDuration, runPayload, runTimeLabel } from '../testRuns'

interface Artifact { id: string; filename: string; size_bytes: number; content_type: string; created_at: string }

const route = useRoute()
const auth = useAuthStore()
const canEdit = computed(() => auth.can(PERMISSION.testsEdit))
const runId = computed(() => String(route.params.id))
const run = ref<any | null>(null)
const loadError = ref('')
const editing = ref(false)
const saving = ref(false)
const toast = ref('')
const toastError = ref(false)

const files = ref<Artifact[]>([])
const storageEnabled = ref(false)
const maxBytes = ref(0)
const busy = ref(false)
const fileStatus = ref('')

function showToast(msg: string, isError = false) {
  toast.value = msg
  toastError.value = isError
  setTimeout(() => (toast.value = ''), 4000)
}
const { downloading, download } = useDownload(showToast)
const filesBase = computed(() => `/test-runs/${encodeURIComponent(runId.value)}/artifacts`)

async function loadRun() {
  try {
    run.value = await api<any>(`/test-runs/${encodeURIComponent(runId.value)}`)
    loadError.value = ''
  } catch (e: any) {
    loadError.value = e.message
  }
}

async function loadFiles() {
  try {
    const result = await api<{ items: Artifact[]; storage_enabled: boolean; max_bytes: number }>(filesBase.value)
    files.value = result.items
    storageEnabled.value = result.storage_enabled
    maxBytes.value = result.max_bytes
  } catch (e: any) {
    showToast(e.message, true)
  }
}

watch(runId, () => { run.value = null; loadRun(); loadFiles() }, { immediate: true })

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const chosen = Array.from(input.files || [])
  if (!chosen.length) return
  busy.value = true
  try {
    for (const file of chosen) {
      if (file.size > maxBytes.value) throw new Error(`${file.name} exceeds the ${fileSizeLabel(maxBytes.value)} file limit.`)
      fileStatus.value = `Uploading ${file.name}…`
      const body = new FormData()
      body.append('file', file)
      await api(filesBase.value, { method: 'POST', body })
    }
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    await Promise.all([loadFiles(), loadRun()])
    busy.value = false
    fileStatus.value = ''
    input.value = ''
  }
}

async function removeFile(item: Artifact) {
  if (!confirm(`Delete "${item.filename}"? Every test in this run loses it.`)) return
  busy.value = true
  try {
    await api(`${filesBase.value}/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
    await Promise.all([loadFiles(), loadRun()])
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    busy.value = false
  }
}

async function saveEdit(values: Record<string, any>) {
  saving.value = true
  try {
    run.value = await api<any>(`/test-runs/${encodeURIComponent(runId.value)}`, {
      method: 'PATCH', body: JSON.stringify(runPayload(values)),
    })
    editing.value = false
    showToast('Test run saved')
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    saving.value = false
  }
}

async function setFinished(finished: boolean) {
  try {
    run.value = await api<any>(`/test-runs/${encodeURIComponent(runId.value)}`, {
      method: 'PATCH', body: JSON.stringify({ finished_at: finished ? new Date().toISOString() : null }),
    })
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function deleteRun() {
  if (!confirm(`Delete test run "${run.value?.name}"? Its ${run.value?.test_count || 0} tests are kept, no longer in a run.`)) return
  try {
    await api(`/test-runs/${encodeURIComponent(runId.value)}`, { method: 'DELETE' })
    router.push('/tests')
  } catch (e: any) {
    showToast(e.message, true)
  }
}
</script>

<template>
  <div class="page page-flow">
    <p v-if="loadError" role="alert" class="login-error">{{ loadError }}</p>
    <template v-if="run">
      <div class="page-header">
        <h2>{{ run.name }}</h2>
        <div class="toolbar">
          <a v-if="run.url" class="btn" :href="run.url" target="_blank" rel="noopener noreferrer">Pipeline ↗</a>
          <template v-if="canEdit">
            <button class="btn" @click="editing = true">Edit</button>
            <button class="btn" @click="setFinished(!run.finished_at)">
              {{ run.finished_at ? 'Mark unfinished' : 'Mark finished' }}
            </button>
            <button class="btn btn-danger" @click="deleteRun">Delete</button>
          </template>
          <button class="btn" @click="router.push('/tests')">Back to tests</button>
        </div>
      </div>

      <p class="run-summary">
        <span v-if="run.external_id">ID {{ run.external_id }} · </span>
        <span>Started {{ runTimeLabel(run.started_at || run.created_at) }}</span>
        <span v-if="run.finished_at"> · Finished {{ runTimeLabel(run.finished_at) }}
          <template v-if="run.started_at">({{ runDuration(run.started_at, run.finished_at) }})</template></span>
        <span v-else class="muted"> · Not finished</span>
        <span> · {{ run.test_count }} test{{ run.test_count === 1 ? '' : 's' }}:
          <span class="outcome-pass">{{ run.pass_count }} pass</span>,
          <span class="outcome-fail">{{ run.fail_count }} fail</span>,
          <span class="outcome-warn">{{ run.warn_count }} warn</span></span>
        <span v-if="run.created_by_username" class="muted"> · Created by {{ run.created_by_username }}</span>
      </p>
      <p v-if="run.notes" class="run-notes">{{ run.notes }}</p>

      <section class="run-files">
        <h3>Shared files</h3>
        <p class="muted">Stored once, and listed in the Artifacts of every test in this run.</p>
        <p v-if="!storageEnabled" class="muted">Uploads and downloads require object storage to be enabled by an administrator.</p>
        <label v-if="canEdit && storageEnabled" class="upload-label">
          Upload files (up to {{ fileSizeLabel(maxBytes) }} each)
          <input type="file" multiple :disabled="busy || downloading" @change="upload" />
        </label>
        <p v-if="!files.length" class="muted">No shared files yet.</p>
        <ul v-else class="file-list">
          <li v-for="item in files" :key="item.id">
            <div class="file-info"><strong>{{ item.filename }}</strong><span class="muted">{{ fileSizeLabel(item.size_bytes) }} · {{ item.content_type }}</span></div>
            <div class="file-actions">
              <button class="btn btn-mini" :disabled="busy || downloading || !storageEnabled"
                @click="download(`${filesBase}/${encodeURIComponent(item.id)}/download`, item.filename)">Download</button>
              <button v-if="canEdit" class="btn btn-mini btn-danger" :disabled="busy || downloading || !storageEnabled"
                @click="removeFile(item)">Delete</button>
            </div>
          </li>
        </ul>
        <p v-if="fileStatus || downloading" role="status">{{ fileStatus || 'Downloading…' }}</p>
      </section>

      <TestsView :key="run.id" :test-run-id="run.id" @changed="loadRun(); loadFiles()" />

      <FormModal
        v-if="editing"
        title="Edit Test Run"
        :fields="RUN_FORM_FIELDS"
        :values="{ name: run.name, external_id: run.external_id || '', url: run.url || '', notes: run.notes || '' }"
        :busy="saving"
        submit-label="Save changes"
        @submit="saveEdit"
        @cancel="editing = false"
      />
    </template>
    <p v-else-if="!loadError" role="status">Loading test run…</p>

    <div v-if="toast" class="toast" :class="{ 'toast-error': toastError }">{{ toast }}</div>
  </div>
</template>

<style scoped>
.run-summary { margin: 0 0 8px; }
.run-notes { margin: 0 0 12px; white-space: pre-wrap; max-width: 80ch; }
.outcome-pass { color: var(--green); }
.outcome-fail { color: var(--red); }
.outcome-warn { color: var(--yellow); }
.run-files { margin: 12px 0 20px; padding: 16px; border: 1px solid var(--border); border-radius: 8px; }
.run-files h3 { margin: 0 0 4px; }
.upload-label, .file-info { display: flex; flex-direction: column; gap: 8px; }
.file-list { list-style: none; padding: 0; margin: 0; }
.file-list li { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); }
.file-actions { display: flex; align-items: center; gap: 12px; }
.file-info { min-width: 0; overflow-wrap: anywhere; }
</style>
