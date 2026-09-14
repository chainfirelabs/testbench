<script setup lang="ts">
/**
 * Read-only dialog for one field's full value.
 *
 * Opened from the "View Notes" / "View Misc Data" cells: those columns hold
 * more than a grid row can show, so the row offers the value and this shows it.
 * The rendering itself is DetailValue, shared with the detail pages.
 *
 * Nothing is editable — the row's Edit action already opens the form that
 * writes these fields, and a second editor for the same value would be a second
 * place for it to be half-saved.
 */
import { computed, ref } from 'vue'
import DetailValue from './DetailValue.vue'

const props = defineProps<{
  title: string
  value: any
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const showRaw = ref(false)
const copied = ref(false)

const isStructured = computed(() => props.value != null && typeof props.value === 'object')

const asText = computed(() =>
  isStructured.value ? JSON.stringify(props.value, null, 2) : String(props.value ?? ''),
)

async function copy() {
  try {
    await navigator.clipboard.writeText(asText.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    // Clipboard access is denied outside a secure context; the text is on
    // screen and selectable either way, so there is nothing to report.
  }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')" @keydown.esc="emit('close')">
    <div class="modal-card modal-wide">
      <h3 class="modal-title">{{ title }}</h3>
      <div class="modal-body">
        <DetailValue :value="value" :show-raw="showRaw" />
      </div>
      <div class="modal-actions">
        <button v-if="isStructured" class="btn" @click="showRaw = !showRaw">
          {{ showRaw ? 'Friendly view' : 'Raw JSON' }}
        </button>
        <button class="btn" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</button>
        <button class="btn btn-primary" @click="emit('close')">Close</button>
      </div>
    </div>
  </div>
</template>
