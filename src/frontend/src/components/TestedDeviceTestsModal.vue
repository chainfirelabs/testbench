<script setup lang="ts">
/**
 * The tests behind one Software → Tested Devices row.
 *
 * That row is a count — this many runs, this many passed — grouped by device
 * and component, and on its own it gives no way to see which runs those were.
 * This lists them, newest first, with the details a run carries. Editing and
 * artifacts stay on the device's Tests tab, which each run links to. A detail
 * hidden from lists under Schema → Tests is left out here as well.
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import { BLANK_TEXT } from '../blankCell'
import { formatDate } from '../dates'
import { isEmptyDetail } from '../detail'
import { useEntityFields } from '../entityFields'
import { listFields } from '../schemaTable'
import { TEST_ARTIFACTS_FIELD, TEST_OUTPUT_FIELD } from '../testArtifacts'
import DetailValue from './DetailValue.vue'
import TestOutputModal from './TestOutputModal.vue'

export interface TestedDeviceGroup {
  softwareId: string
  deviceId: string
  deviceUniqueId: string
  /** Null for tests run against the software as a whole. */
  componentId: string | null
  componentLabel: string
}

/** More than this is not a list anyone reads in a dialog; the device page has them all. */
const PAGE_SIZE = 200

const props = defineProps<{ group: TestedDeviceGroup }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const dialog = ref<HTMLDialogElement | null>(null)
const items = ref<any[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const outputTest = ref<any | null>(null)
const { fields: testFields, loadFields } = useEntityFields('tests')
const labels = computed(() => new Map(listFields(testFields.value).map((field) => [field.key, field.label])))
const shows = (key: string) => labels.value.has(key)

function deviceTestHref(test: any) {
  return `/devices/${encodeURIComponent(props.group.deviceUniqueId)}?tab=tests&test=${encodeURIComponent(test.id)}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({
      device_id: props.group.deviceId,
      software_id: props.group.softwareId,
      component_id: props.group.componentId || 'none',
      // The list endpoint orders by when a test was recorded; the run date is
      // what a reader means by "most recent", so that ordering happens here.
      sort: 'created_at',
      order: 'desc',
      page_size: String(PAGE_SIZE),
    })
    const result = await api<{ items: any[]; total: number }>(`/tests?${params}`)
    items.value = [...result.items].sort((a, b) =>
      (b.run_at || '').localeCompare(a.run_at || '') || b.created_at.localeCompare(a.created_at))
    total.value = result.total
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(() => { dialog.value?.showModal(); load(); loadFields() })
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="tested-tests-modal" aria-labelledby="tested-tests-title" @close="emit('close')">
      <header>
        <div>
          <h2 id="tested-tests-title">Tests on {{ group.deviceUniqueId }}</h2>
          <p class="muted">{{ group.componentLabel }}</p>
        </div>
        <button class="btn" @click="dialog?.close()">Close</button>
      </header>
      <p v-if="error" role="alert" class="login-error">{{ error }} <button class="btn btn-mini" @click="load()">Retry</button></p>
      <p v-else-if="loading" role="status">Loading tests…</p>
      <p v-else-if="!items.length" class="muted">No tests found for this device.</p>
      <template v-else>
        <p v-if="total > items.length" class="muted">
          Showing the {{ items.length }} most recent of {{ total }} tests. The device's Tests tab lists them all.
        </p>
        <ul class="test-list">
          <li v-for="t in items" :key="t.id">
            <div class="test-summary">
              <span :class="['outcome-badge', t.outcome]">{{ t.outcome }}</span>
              <span v-if="shows('run_at')">{{ formatDate(t.run_at) || BLANK_TEXT }}</span>
              <span v-if="shows('tag')" class="muted">{{ t.tag || BLANK_TEXT }}</span>
              <span v-if="shows('created_by_username')" class="muted">by {{ t.created_by_username || BLANK_TEXT }}</span>
              <span v-if="shows(TEST_ARTIFACTS_FIELD)" class="muted">{{ t.artifact_count || 0 }} artifact{{ t.artifact_count === 1 ? '' : 's' }}</span>
              <span class="test-links">
                <button v-if="t.output_filename && shows(TEST_OUTPUT_FIELD)" class="btn btn-mini" type="button" :title="t.output_filename"
                  @click="outputTest = t">View output</button>
                <a class="grid-link" :href="deviceTestHref(t)">Open on device</a>
              </span>
            </div>
            <details v-if="shows('notes') && !isEmptyDetail(t.notes)">
              <summary>{{ labels.get('notes') }}</summary>
              <DetailValue :value="t.notes" />
            </details>
            <details v-if="shows('misc_data') && !isEmptyDetail(t.misc_data)">
              <summary>{{ labels.get('misc_data') }}</summary>
              <DetailValue :value="t.misc_data" />
            </details>
          </li>
        </ul>
      </template>
    </dialog>
    <TestOutputModal v-if="outputTest" :test-id="outputTest.id"
      :title="`Output — ${group.deviceUniqueId}`" @close="outputTest = null"
      @updated="(output) => { outputTest.output_filename = output?.filename ?? null; outputTest.output_size_bytes = output?.size_bytes ?? null }" />
  </Teleport>
</template>

<style scoped>
.tested-tests-modal { width: min(820px, 94vw); max-height: 85vh; overflow: auto; padding: 24px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text); }
.tested-tests-modal::backdrop { background: rgb(0 0 0 / 50%); }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
h2 { margin: 0; overflow-wrap: anywhere; }
header p { margin: 4px 0 0; }
.test-list { list-style: none; padding: 0; margin: 16px 0 0; }
.test-list li { padding: 12px 0; border-top: 1px solid var(--border); }
.test-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; }
.test-links { display: inline-flex; align-items: center; gap: 12px; margin-left: auto; }
details { margin-top: 8px; }
summary { cursor: pointer; color: var(--text-muted); }
</style>
