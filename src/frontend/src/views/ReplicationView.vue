<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api/client'

interface Run {
  run_id: string
  state: string
  trigger: string
  started_at?: string
  finished_at?: string
  error?: string
  audit_error?: string
  entities?: Record<string, { selected: number; remote: { applied: number }; local?: { applied: number } }>
}
interface Status {
  enabled: boolean
  mode?: string
  interval_minutes?: number
  peer_url?: string
  running?: boolean
  current_run?: Run | null
  last_run?: Run | null
}

const status = ref<Status | null>(null)
const error = ref('')
const busy = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  try {
    status.value = await api<Status>('/replication/status')
    error.value = ''
  } catch (exc: any) {
    error.value = exc.message || 'Could not read replication status'
  }
}

async function run() {
  busy.value = true
  error.value = ''
  try {
    await api('/replication/run', { method: 'POST' })
    await refresh()
  } catch (exc: any) {
    error.value = exc.message || 'Could not start replication'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 5000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="page page-flow">
    <div class="page-header">
      <h2>Replication</h2>
      <button v-if="status?.enabled && status.mode !== 'receive'" class="btn btn-primary" :disabled="busy || status.running" @click="run">
        {{ status.running ? 'Sync running…' : 'Sync now' }}
      </button>
    </div>
    <p v-if="error" class="login-error">{{ error }}</p>
    <p v-if="status && !status.enabled" class="muted">Enable the replication plugin in Helm to configure a peer.</p>
    <template v-if="status?.enabled">
      <section class="card settings-card">
        <h3>Configuration</h3>
        <p class="muted">Managed by Helm; GUI overrides are disabled.</p>
        <div class="settings-grid">
          <label>Mode<input :value="status.mode === 'push_pull' ? 'Push and pull' : status.mode === 'receive' ? 'Receive only' : 'Push only'" disabled /></label>
          <label v-if="status.mode !== 'receive'">Destination URL<input :value="status.peer_url || ''" :title="status.peer_url || ''" disabled /></label>
          <label v-if="status.mode !== 'receive'">Schedule<input :value="status.interval_minutes ? `Every ${status.interval_minutes} minutes` : 'Manual only'" disabled /></label>
        </div>
      </section>
      <section v-if="status.current_run" class="card">
        <h3>Current sync</h3>
        <p>Started {{ status.current_run.started_at }} ({{ status.current_run.trigger }})</p>
      </section>
      <section v-if="status.last_run" class="card">
        <h3>Last sync: {{ status.last_run.state }}</h3>
        <p>{{ status.last_run.finished_at }} ({{ status.last_run.trigger }})</p>
        <p v-if="status.last_run.error" class="login-error">{{ status.last_run.error }}</p>
        <p v-if="status.last_run.audit_error" class="login-error">Could not add this sync to the audit log: {{ status.last_run.audit_error }}</p>
        <ul v-if="status.last_run.entities">
          <li v-for="(result, name) in status.last_run.entities" :key="name">
            {{ name }}: {{ result.selected }} records, {{ result.remote.applied }} sent to peer<span v-if="result.local">, {{ result.local.applied }} received</span>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.settings-card { padding: 18px; margin-bottom: 16px; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 12px 0; }
.settings-grid label { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
</style>
