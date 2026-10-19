<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'

const props = defineProps<{ softwareId: string; claim: { id: string; make?: string; model?: string; firmware_version?: string } }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const dialog = ref<HTMLDialogElement | null>(null)
const items = ref<{ id: string; unique_id: string }[]>([])
const total = ref(0)
const page = ref(1)
const busy = ref(false)
const error = ref('')
async function load(target: number) {
  busy.value = true
  error.value = ''
  try {
    const result = await api<{ items: typeof items.value; total: number }>(
      `/software/${encodeURIComponent(props.softwareId)}/vendor-devices/${encodeURIComponent(props.claim.id)}/inventory-matches?page=${target}`,
    )
    items.value = result.items
    total.value = result.total
    page.value = target
  } catch (e: any) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
onMounted(() => { dialog.value?.showModal(); load(1) })
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" aria-labelledby="inventory-matches-title" @close="emit('close')"
      @click="(event) => { if (event.target === dialog) dialog?.close() }">
      <header>
        <h2 id="inventory-matches-title">Matching inventory devices</h2>
        <button class="btn" @click="dialog?.close()">Close</button>
      </header>
      <p>{{ [claim.make, claim.model, claim.firmware_version].filter(Boolean).join(' · ') }}</p>
      <p class="muted">Matches use the vendor claim’s device specifications. Vendor support status and recorded test results are separate.</p>
      <p v-if="error" role="alert">{{ error }} <button class="btn" @click="load(page)">Retry</button></p>
      <p v-else-if="busy" role="status">Loading matching devices…</p>
      <template v-else>
        <p>{{ total }} matching device{{ total === 1 ? '' : 's' }}</p>
        <ul>
          <li v-for="device in items" :key="device.id">
            <router-link :to="`/devices/${encodeURIComponent(device.unique_id)}`" @click="dialog?.close()">{{ device.unique_id }}</router-link>
          </li>
        </ul>
      </template>
      <footer v-if="total > 50">
        <button class="btn" :disabled="busy || page === 1" @click="load(page - 1)">Previous</button>
        <span>Page {{ page }}</span>
        <button class="btn" :disabled="busy || page * 50 >= total" @click="load(page + 1)">Next</button>
      </footer>
    </dialog>
  </Teleport>
</template>

<style scoped>
dialog { width: min(560px, 90vw); max-height: 80vh; overflow: auto; border: 1px solid var(--border); border-radius: 8px; padding: 24px; background: var(--bg); color: var(--text); }
dialog::backdrop { background: rgb(0 0 0 / 45%); }
header, footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
h2 { margin: 0; }
li { margin: 10px 0; }
</style>
