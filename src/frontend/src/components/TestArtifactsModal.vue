<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import { useDownload } from '../downloads'
import { PERMISSION, useAuthStore } from '../stores/auth'

interface Artifact { id: string; filename: string; size_bytes: number; content_type: string; created_at: string }
const props = defineProps<{ testId: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'updated', count: number): void }>()
const auth = useAuthStore()
const dialog = ref<HTMLDialogElement | null>(null)
const items = ref<Artifact[]>([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const status = ref('')
const storageEnabled = ref(false)
const maxBytes = ref(0)
const base = `/tests/${encodeURIComponent(props.testId)}/artifacts`
const { downloading, download } = useDownload((message) => { error.value = message })
function sizeLabel(bytes: number) {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MiB`
    : bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${bytes} B`
}
async function load() {
  loading.value = true
  try {
    const result = await api<{ items: Artifact[]; storage_enabled: boolean; max_bytes: number }>(base)
    items.value = result.items
    storageEnabled.value = result.storage_enabled
    maxBytes.value = result.max_bytes
    emit('updated', items.value.length)
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (!files.length) return
  busy.value = true
  error.value = ''
  try {
    for (const file of files) {
      if (file.size > maxBytes.value) throw new Error(`${file.name} exceeds the ${sizeLabel(maxBytes.value)} file limit.`)
      status.value = `Uploading ${file.name}…`
      const body = new FormData()
      body.append('file', file)
      await api(base, { method: 'POST', body })
    }
  } catch (e: any) {
    error.value = e.message
  } finally {
    await load()
    busy.value = false
    status.value = ''
    input.value = ''
  }
}
async function remove(item: Artifact) {
  if (!window.confirm(`Delete artifact "${item.filename}"?`)) return
  busy.value = true
  error.value = ''
  try {
    await api(`${base}/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
    await load()
  } catch (e: any) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
onMounted(() => { dialog.value?.showModal(); load() })
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="artifacts-modal" aria-labelledby="test-artifacts-title"
      @close="emit('close')" @cancel="(event) => { if (busy || downloading) event.preventDefault() }">
      <header>
        <h2 id="test-artifacts-title">Test artifacts</h2>
        <button class="btn" :disabled="busy || downloading" @click="dialog?.close()">Close</button>
      </header>
      <p class="muted">Reports, logs, screenshots, and other files associated with this test.</p>
      <p v-if="error" role="alert" class="login-error">{{ error }} <button class="btn btn-mini" :disabled="busy" @click="error = ''; load()">Retry list</button></p>
      <p v-if="loading" role="status">Loading artifacts…</p>
      <template v-else>
        <p v-if="!storageEnabled" class="muted">Artifact uploads and downloads require object storage to be enabled by an administrator.</p>
        <label v-if="auth.can(PERMISSION.testsEdit) && storageEnabled" class="upload-label">
          Upload files (up to {{ sizeLabel(maxBytes) }} each)
          <input type="file" multiple :disabled="busy || downloading" @change="upload" />
        </label>
        <p v-if="!items.length" class="muted">No artifacts attached to this test.</p>
        <ul v-else class="artifact-list">
          <li v-for="item in items" :key="item.id">
            <div class="artifact-info"><strong>{{ item.filename }}</strong><span class="muted">{{ sizeLabel(item.size_bytes) }} · {{ item.content_type }}</span></div>
            <div class="artifact-actions">
              <button class="btn btn-mini" :disabled="busy || downloading || !storageEnabled"
                @click="error = ''; download(`${base}/${encodeURIComponent(item.id)}/download`, item.filename)">Download</button>
              <button v-if="auth.can(PERMISSION.testsEdit)" class="btn btn-mini btn-danger"
                :disabled="busy || downloading || !storageEnabled" @click="remove(item)">Delete</button>
            </div>
          </li>
        </ul>
      </template>
      <p v-if="status || downloading" role="status">{{ status || 'Downloading artifact…' }}</p>
    </dialog>
  </Teleport>
</template>

<style scoped>
.artifacts-modal { width: min(720px, 92vw); max-height: 85vh; overflow: auto; padding: 24px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text); }
.artifacts-modal::backdrop { background: rgb(0 0 0 / 50%); }
header, .artifact-actions { display: flex; align-items: center; gap: 12px; }
header { justify-content: space-between; }
h2 { margin: 0; }
.upload-label, .artifact-info { display: flex; flex-direction: column; gap: 8px; }
.artifact-list { list-style: none; padding: 0; }
.artifact-list li { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 14px 0; border-bottom: 1px solid var(--border); }
.artifact-info { min-width: 0; overflow-wrap: anywhere; }
</style>
