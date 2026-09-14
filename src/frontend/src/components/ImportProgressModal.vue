<script setup lang="ts">
export interface ImportFailure {
  row: number | null
  error: string
}

export interface ImportProgressState {
  open: boolean
  filename: string
  phase: 'uploading' | 'processing' | 'complete' | 'failed'
  progress: number
  created: number
  updated: number
  errors: ImportFailure[]
  requestError: string
}

defineProps<{ state: ImportProgressState }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <div v-if="state.open" class="modal-backdrop" @click.self="state.phase === 'complete' || state.phase === 'failed' ? emit('close') : null">
    <div class="modal-card import-modal" role="dialog" aria-modal="true" aria-labelledby="import-title">
      <h3 id="import-title" class="modal-title">
        {{ state.phase === 'complete' ? 'Import complete' : state.phase === 'failed' ? 'Import failed' : 'Importing data' }}
      </h3>
      <p class="import-file">{{ state.filename }}</p>

      <div v-if="state.phase === 'uploading' || state.phase === 'processing'" class="import-working">
        <div class="import-status">
          <span>{{ state.phase === 'uploading' ? 'Uploading…' : 'Processing rows…' }}</span>
          <span v-if="state.phase === 'uploading'">{{ state.progress }}%</span>
        </div>
        <progress v-if="state.phase === 'uploading'" :value="state.progress" max="100" />
        <progress v-else />
      </div>

      <template v-else-if="state.phase === 'complete'">
        <div class="import-summary">
          <span><strong>{{ state.created }}</strong> created</span>
          <span><strong>{{ state.updated }}</strong> updated</span>
          <span :class="{ 'has-errors': state.errors.length }">
            <strong>{{ state.errors.length }}</strong> failed
          </span>
        </div>
        <div v-if="state.errors.length" class="import-errors">
          <h4>Rows that were not imported</h4>
          <table>
            <thead><tr><th>Row</th><th>Error</th></tr></thead>
            <tbody>
              <tr v-for="(failure, index) in state.errors" :key="`${failure.row}-${index}`">
                <td>{{ failure.row == null ? '—' : failure.row + 1 }}</td>
                <td>{{ failure.error }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="import-success">Every row imported successfully.</p>
      </template>

      <p v-else class="import-request-error">{{ state.requestError }}</p>

      <div v-if="state.phase === 'complete' || state.phase === 'failed'" class="modal-actions">
        <button class="btn btn-primary" type="button" @click="emit('close')">Close</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.import-modal { width: min(620px, calc(100vw - 32px)); }
.import-file { margin: -6px 0 18px; color: var(--text-muted); font-size: 12.5px; overflow-wrap: anywhere; }
.import-working { display: grid; gap: 8px; }
.import-status { display: flex; justify-content: space-between; color: var(--text-muted); font-size: 13px; }
progress { width: 100%; height: 10px; accent-color: var(--accent); }
.import-summary { display: flex; gap: 10px; flex-wrap: wrap; }
.import-summary span { padding: 8px 12px; border-radius: var(--r-md); background: var(--surface-2); }
.import-summary .has-errors, .import-request-error { color: var(--red); }
.import-errors { margin-top: 18px; max-height: min(45vh, 360px); overflow: auto; }
.import-errors h4 { margin: 0 0 8px; font-size: 13px; }
.import-errors table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.import-errors th, .import-errors td { padding: 8px; border-bottom: 1px solid var(--border-soft); text-align: left; vertical-align: top; }
.import-errors th:first-child, .import-errors td:first-child { width: 58px; }
.import-success { margin: 18px 0 0; color: var(--text-muted); }
.import-request-error { margin: 0; white-space: pre-wrap; }
</style>
