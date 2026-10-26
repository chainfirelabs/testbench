<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { renderMarkdown } from '../docs/markdown'

const props = withDefaults(defineProps<{ title: string; run: Record<string, any>; note?: string }>(), {
  note: "Kubernetes combines the container's stdout and stderr into this stream. This output may contain device credentials.",
})
const emit = defineEmits<{ (e: 'close'): void; (e: 'cancel'): void }>()
const output = ref<HTMLElement | null>(null)
const terminal = computed(() => ['completed', 'failed', 'cancelled', 'interrupted'].includes(props.run.state))
const now = ref(Date.now())
const followOutput = ref(true)
// A plugin that writes a report (AI Test Summary) returns it as markdown. It is
// rendered by the Docs page's renderer, which escapes every piece of text
// first, so nothing a model writes can become an element.
const reportHtml = computed(() =>
  typeof props.run.result?.markdown === 'string' ? renderMarkdown(props.run.result.markdown, [], 0) : '')
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
      <template v-if="reportHtml">
        <p v-if="run.result.file" class="plugin-run-saved">Saved to this run's shared files as <strong>{{ run.result.file.filename }}</strong>.</p>
        <div class="plugin-run-report" v-html="reportHtml"></div>
        <details class="plugin-run-result">
          <summary>Progress log</summary>
          <pre>{{ run.output }}</pre>
        </details>
      </template>
      <template v-else>
        <pre ref="output" class="plugin-run-output" @scroll="onOutputScroll">{{ run.output || (terminal ? 'No worker output was captured.' : 'Waiting for worker output…') }}</pre>
        <div v-if="run.error" class="plugin-run-error">{{ run.error }}</div>
        <details v-if="run.result" class="plugin-run-result">
          <summary>Result</summary>
          <pre>{{ JSON.stringify(run.result, null, 2) }}</pre>
        </details>
      </template>
      <p class="plugin-run-note">{{ note }}</p>
      <div class="modal-actions">
        <button v-if="!terminal" class="btn btn-danger" @click="emit('cancel')">Stop</button>
        <button class="btn btn-primary" @click="emit('close')">{{ terminal ? 'Close' : 'Hide' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.plugin-run-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.plugin-run-state { flex-shrink: 0; white-space: nowrap; padding: 3px 9px; border-radius: 999px; background: var(--surface-3); color: var(--text-muted); text-transform: capitalize; }
.plugin-run-state.running { color: var(--accent); }
.plugin-run-state.completed { color: var(--success); }
.plugin-run-state.failed { color: var(--danger); }
.plugin-run-state.cancelled { color: var(--danger); }
.plugin-run-state.interrupted { color: var(--danger); }
.plugin-run-meta, .plugin-run-note { color: var(--text-muted); font-size: 12px; }
.plugin-run-output, .plugin-run-result pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #111318; color: #e4e7ec; border-radius: var(--r-md); padding: 12px; font: 12px/1.5 ui-monospace, monospace; }
.plugin-run-output { min-height: 220px; max-height: 52vh; overflow: auto; }
.plugin-run-error { color: var(--danger); margin-top: 10px; }
.plugin-run-result { margin-top: 10px; }
.plugin-run-saved { margin: 4px 0 10px; }
@media (max-width: 639px) {
  /* The phone sheet's title is a full-width bar; beside the state it must wrap. */
  .plugin-run-head .modal-title { flex-shrink: 1; min-width: 0; }
  .plugin-run-head .plugin-run-state { margin-right: 18px; }
}
.plugin-run-report { max-height: 60vh; overflow: auto; padding: 4px 16px 12px; border: 1px solid var(--border); border-radius: var(--r-md); line-height: 1.6; overflow-wrap: anywhere; }
.plugin-run-report :deep(h2) { font-size: 1.25rem; margin: 14px 0 8px; }
.plugin-run-report :deep(h3) { font-size: 1.05rem; margin: 14px 0 6px; }
.plugin-run-report :deep(h4) { font-size: 0.95rem; margin: 12px 0 6px; }
.plugin-run-report :deep(table) { border-collapse: collapse; margin: 8px 0; font-size: 13px; display: block; overflow-x: auto; }
.plugin-run-report :deep(th), .plugin-run-report :deep(td) { border: 1px solid var(--border); padding: 4px 8px; text-align: left; }
.plugin-run-report :deep(code) { font: 12px ui-monospace, monospace; background: var(--surface-3); padding: 1px 4px; border-radius: 4px; }
.plugin-run-report :deep(pre) { white-space: pre-wrap; background: var(--surface-3); padding: 8px; border-radius: var(--r-md); }
</style>
