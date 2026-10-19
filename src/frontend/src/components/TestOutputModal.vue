<script setup lang="ts">
/**
 * One test's output, shown as text.
 *
 * The output is read here rather than downloaded: it is a log to look at, and a
 * file save dialog for every glance would be the wrong way round. Artifacts are
 * the place for files meant to leave the browser. Editors upload, replace and
 * remove the output from the same dialog.
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import { PERMISSION, useAuthStore } from '../stores/auth'

export interface TestOutputMeta { filename: string; size_bytes: number }

const props = defineProps<{ testId: string; title?: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'updated', output: TestOutputMeta | null): void }>()
const auth = useAuthStore()
const dialog = ref<HTMLDialogElement | null>(null)
const output = ref<TestOutputMeta | null>(null)
const text = ref<string | null>(null)
const readError = ref('')
const error = ref('')
const loading = ref(true)
const busy = ref(false)
const storageEnabled = ref(false)
const maxBytes = ref(0)
const base = `/tests/${encodeURIComponent(props.testId)}/output`
const canEdit = computed(() => auth.can(PERMISSION.testsEdit) && storageEnabled.value)

function sizeLabel(bytes: number) {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MiB`
    : bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${bytes} B`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await api<{
      output: TestOutputMeta | null; text: string | null; error: string | null
      storage_enabled: boolean; max_bytes: number
    }>(base)
    output.value = result.output
    text.value = result.text
    readError.value = result.error || ''
    storageEnabled.value = result.storage_enabled
    maxBytes.value = result.max_bytes
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  error.value = ''
  if (file.size > maxBytes.value) {
    error.value = `${file.name} exceeds the ${sizeLabel(maxBytes.value)} output limit.`
    input.value = ''
    return
  }
  busy.value = true
  try {
    const body = new FormData()
    body.append('file', file)
    const saved = await api<TestOutputMeta>(base, { method: 'PUT', body })
    emit('updated', saved)
    await load()
  } catch (e: any) {
    error.value = e.message
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function remove() {
  if (!output.value || !window.confirm(`Delete output "${output.value.filename}"?`)) return
  busy.value = true
  error.value = ''
  try {
    await api(base, { method: 'DELETE' })
    emit('updated', null)
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
    <dialog ref="dialog" class="output-modal" aria-labelledby="test-output-title"
      @close="emit('close')" @cancel="(event) => { if (busy) event.preventDefault() }">
      <header>
        <div>
          <h2 id="test-output-title">{{ title || 'Test output' }}</h2>
          <p v-if="output" class="muted">{{ output.filename }} · {{ sizeLabel(output.size_bytes) }}</p>
        </div>
        <button class="btn" :disabled="busy" @click="dialog?.close()">Close</button>
      </header>
      <p v-if="error" role="alert" class="login-error">{{ error }}</p>
      <p v-if="loading" role="status">Loading output…</p>
      <template v-else>
        <p v-if="!storageEnabled" class="muted">Test output requires object storage to be enabled by an administrator.</p>
        <p v-if="!output" class="muted">No output recorded for this test.</p>
        <p v-else-if="readError" role="alert" class="login-error">{{ readError }}</p>
        <pre v-else class="output-text">{{ text }}</pre>
        <div v-if="canEdit" class="output-actions">
          <label class="btn btn-mini upload-label" :class="{ disabled: busy }">
            {{ output ? 'Replace output' : 'Upload output' }}
            <input type="file" accept=".txt,.log,text/plain" :disabled="busy" @change="upload" />
          </label>
          <button v-if="output" class="btn btn-mini btn-danger" :disabled="busy" @click="remove">Delete output</button>
          <span class="muted">ASCII text, up to {{ sizeLabel(maxBytes) }}.</span>
        </div>
      </template>
      <p v-if="busy" role="status">Saving…</p>
    </dialog>
  </Teleport>
</template>

<style scoped>
.output-modal { width: min(960px, 94vw); max-height: 88vh; overflow: auto; padding: 24px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text); }
.output-modal::backdrop { background: rgb(0 0 0 / 50%); }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
h2 { margin: 0; overflow-wrap: anywhere; }
header p { margin: 4px 0 0; overflow-wrap: anywhere; }
.output-text { max-height: 62vh; overflow: auto; margin: 16px 0 0; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12.5px; }
.output-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 14px; }
.upload-label { position: relative; cursor: pointer; }
.upload-label input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.upload-label.disabled { opacity: 0.5; pointer-events: none; }
</style>
