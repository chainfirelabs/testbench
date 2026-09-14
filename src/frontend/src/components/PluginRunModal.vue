<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ title: string; run: Record<string, any> }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'cancel'): void }>()
const output = ref<HTMLElement | null>(null)
const terminal = computed(() => ['completed', 'failed', 'cancelled'].includes(props.run.state))
const now = ref(Date.now())
const followOutput = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const elapsed = computed(() => {
  const started = Date.parse(props.run.started_at || '')
  if (!Number.isFinite(started)) return '00:00'
  const finished = terminal.value ? Date.parse(props.run.finished_at || '') : now.value
  const total = Math.max(0, Math.floor(((Number.isFinite(finished) ? finished : now.value) - started) / 1000))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const clock = [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':')
  return days ? `${days}d ${clock}` : clock
})

onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 1000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

watch(() => props.run.output, async () => {
  if (!followOutput.value) return
  await nextTick()
  if (output.value) output.value.scrollTop = output.value.scrollHeight
})

function onOutputScroll() {
  const element = output.value
  if (!element) return
  // Keep following live output only while the viewer is already at the end.
  // Scrolling up opts out until they return to within one line of the bottom.
  followOutput.value = element.scrollHeight - element.scrollTop - element.clientHeight < 24
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')" @keydown.esc="emit('close')">
    <div class="modal-card modal-wide plugin-run-modal">
      <div class="plugin-run-head">
        <h3 class="modal-title">{{ title }}</h3>
        <span class="plugin-run-state" :class="run.state">{{ run.state }}</span>
      </div>
      <p class="plugin-run-meta">
        Run {{ run.run_id }}<template v-if="run.started_at"> · Started {{ run.started_at }}</template> · Elapsed {{ elapsed }}
      </p>
      <pre ref="output" class="plugin-run-output" @scroll="onOutputScroll">{{ run.output || (terminal ? 'No worker output was captured.' : 'Waiting for worker output…') }}</pre>
      <div v-if="run.error" class="plugin-run-error">{{ run.error }}</div>
      <details v-if="run.result" class="plugin-run-result">
        <summary>Result</summary>
        <pre>{{ JSON.stringify(run.result, null, 2) }}</pre>
      </details>
      <p class="plugin-run-note">Kubernetes combines the container's stdout and stderr into this stream. This output may contain device credentials.</p>
      <div class="modal-actions">
        <button v-if="!terminal" class="btn btn-danger" @click="emit('cancel')">Stop</button>
        <button class="btn btn-primary" @click="emit('close')">{{ terminal ? 'Close' : 'Hide' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.plugin-run-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.plugin-run-state { padding: 3px 9px; border-radius: 999px; background: var(--surface-3); color: var(--text-muted); text-transform: capitalize; }
.plugin-run-state.running { color: var(--accent); }
.plugin-run-state.completed { color: var(--success); }
.plugin-run-state.failed { color: var(--danger); }
.plugin-run-state.cancelled { color: var(--danger); }
.plugin-run-meta, .plugin-run-note { color: var(--text-muted); font-size: 12px; }
.plugin-run-output, .plugin-run-result pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #111318; color: #e4e7ec; border-radius: var(--r-md); padding: 12px; font: 12px/1.5 ui-monospace, monospace; }
.plugin-run-output { min-height: 220px; max-height: 52vh; overflow: auto; }
.plugin-run-error { color: var(--danger); margin-top: 10px; }
.plugin-run-result { margin-top: 10px; }
</style>
