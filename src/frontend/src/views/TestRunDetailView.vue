<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import FormModal from '../components/FormModal.vue'
import PluginRunModal from '../components/PluginRunModal.vue'
import TestsView from './TestsView.vue'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { invokePluginAction, usePluginActions, type PluginAction } from '../pluginActions'
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

/*
 * Plugin actions on test runs (AI Test Summary's "Summarize with AI"). Not tied
 * to device types: a deployed plugin's action is offered on every run.
 */
const { actions: runActions, loadPluginActions } = usePluginActions('test_runs')
type RunView = { title: string; action: PluginAction; status: Record<string, any> }
const activeRun = ref<RunView | null>(null)
const showRun = ref(false)
const TERMINAL = ['completed', 'failed', 'cancelled', 'interrupted']
const SUMMARY_NOTE = "Written by an AI model from this run's tests, devices, outputs and files. Text that looked like a credential was removed before sending. Check important details against the results."
// Bumped when the page moves to another run or closes, so an old poll stops.
let watchGeneration = 0

const running = (action: PluginAction) =>
  activeRun.value?.action.id === action.id && activeRun.value?.action.plugin_id === action.plugin_id
  && !TERMINAL.includes(activeRun.value.status.state)

async function followRun(view: RunView, generation: number) {
  const { action } = view
  const deadline = Date.now() + 30 * 60 * 1000
  try {
    while (Date.now() < deadline && generation === watchGeneration) {
      await new Promise((resolve) => setTimeout(resolve, 2000))
      if (generation !== watchGeneration) return
      const status = await api<any>(`/plugins/${encodeURIComponent(action.plugin_id)}/runs/${encodeURIComponent(view.status.run_id)}`)
      if (generation !== watchGeneration) return
      activeRun.value = { ...view, status }
      if (status.state === 'completed') {
        await loadFiles()
        showToast(status.result?.file ? `${action.label}: report saved as ${status.result.file.filename}` : `${action.label} finished`)
        return
      }
      if (status.state === 'failed') throw new Error(status.error || `${action.label} failed`)
      if (status.state === 'interrupted') throw new Error(`${action.label} was interrupted when its plugin restarted`)
      if (status.state === 'cancelled') return
    }
  } catch (e: any) {
    if (generation === watchGeneration) showToast(e.message, true)
  }
}

async function runAction(action: PluginAction) {
  if (running(action)) {
    showRun.value = true
    return
  }
  try {
    const result: Record<string, any> = await invokePluginAction(action, { entity_id: runId.value })
    const view: RunView = { title: `${action.label} — ${run.value?.name || 'test run'}`, action,
                            status: { ...result, state: result.state || 'starting', output: result.output || '' } }
    activeRun.value = view
    showRun.value = true
    void followRun(view, watchGeneration)
  } catch (e: any) {
    showToast(e.message, true)
  }
}

async function cancelRun() {
  const view = activeRun.value
  if (!view || !confirm(`Stop ${view.title}? Nothing will be saved.`)) return
  try {
    const status = await api<any>(`/plugins/${encodeURIComponent(view.action.plugin_id)}/runs/${encodeURIComponent(view.status.run_id)}`, { method: 'DELETE' })
    activeRun.value = { ...view, status }
  } catch (e: any) {
    showToast(e.message, true)
  }
}

/** Pick up a summary still being written for this run, after a reload or a visit elsewhere. */
async function restoreActiveRun(generation: number) {
  try {
    const runs = await api<any[]>('/plugins/runs/active')
    if (generation !== watchGeneration) return
    for (const item of runs) {
      const action = runActions.value.find((candidate) =>
        candidate.plugin_id === item.plugin_id && candidate.id === item.action_id)
      if (!action || !(item.entity_ids || []).map(String).includes(runId.value)) continue
      const view: RunView = { title: `${action.label} — ${run.value?.name || 'test run'}`, action, status: item }
      activeRun.value = view
      void followRun(view, generation)
      return
    }
  } catch {
    // A plugin that is briefly unavailable just means nothing to reattach.
  }
}

watch(runId, async () => {
  watchGeneration += 1
  const generation = watchGeneration
  run.value = null
  activeRun.value = null
  showRun.value = false
  loadRun()
  loadFiles()
  await loadPluginActions()
  if (generation === watchGeneration && runActions.value.length) void restoreActiveRun(generation)
}, { immediate: true })
onBeforeUnmount(() => { watchGeneration += 1 })

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
          <button v-for="action in runActions" :key="`${action.plugin_id}:${action.id}`" class="btn btn-primary"
            :disabled="!!action.unavailable_reason" :title="action.unavailable_reason || action.title || action.label"
            @click="runAction(action)">
            {{ running(action) ? `${action.label}…` : action.label }}
          </button>
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

      <PluginRunModal
        v-if="activeRun && showRun"
        :title="activeRun.title"
        :run="activeRun.status"
        :note="SUMMARY_NOTE"
        @close="showRun = false"
        @cancel="cancelRun"
      />

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
