<script setup lang="ts">
/**
 * A searchable value picker, used on phones in place of `<datalist>`.
 *
 * The New Test form is built on three dependent datalist fields — device,
 * software, then that software's versions — and iOS Safari's support for
 * `datalist` is shallow and inconsistent enough that the suggestions may simply
 * never appear. That leaves three fields to be typed exactly right from memory,
 * on the form most likely to be filled in standing in front of a device.
 *
 * So on a phone the field becomes a button showing the current value; tapping
 * it opens a sheet with a filter box over the same `options` the datalist was
 * given. Free text is still allowed — that is what a datalist means, and the
 * software field relies on it to name something new — so whatever is typed can
 * always be committed as-is.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: any
    options?: { value: any; label?: string }[]
    label: string
    placeholder?: string
    /** Commit whatever was typed, even when it matches no option. */
    allowFreeText?: boolean
  }>(),
  { options: () => [], placeholder: '', allowFreeText: true },
)

const emit = defineEmits<{ (e: 'update:modelValue', value: any): void }>()

const open = ref(false)
const filter = ref('')
const filterInput = ref<HTMLInputElement | null>(null)

const matches = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return props.options
  return props.options.filter((o) => String(o.value).toLowerCase().includes(q))
})

/** True when the typed text is worth offering as its own answer. */
const freeTextOffer = computed(() => {
  if (!props.allowFreeText) return ''
  const q = filter.value.trim()
  if (!q) return ''
  const exact = props.options.some((o) => String(o.value).toLowerCase() === q.toLowerCase())
  return exact ? '' : q
})

async function openSheet() {
  open.value = true
  // Start from the current value so re-picking a near-miss is a small edit
  // rather than retyping.
  filter.value = props.modelValue ? String(props.modelValue) : ''
  await nextTick()
  filterInput.value?.focus()
  filterInput.value?.select()
}

function close() {
  open.value = false
}

function choose(value: any) {
  emit('update:modelValue', value)
  close()
}

function clear() {
  emit('update:modelValue', '')
  close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    close()
    return
  }
  // Enter commits the typed text when it is a legal answer, which is what the
  // native datalist did.
  if (e.key === 'Enter') {
    e.preventDefault()
    if (freeTextOffer.value) choose(freeTextOffer.value)
    else if (matches.value.length) choose(matches.value[0].value)
  }
}

watch(open, (isOpen) => {
  document.removeEventListener('keydown', onDocKeydown)
  if (isOpen) document.addEventListener('keydown', onDocKeydown)
})

function onDocKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

onBeforeUnmount(() => document.removeEventListener('keydown', onDocKeydown))
</script>

<template>
  <div class="option-picker">
    <button type="button" class="picker-trigger" :class="{ empty: !modelValue }" @click="openSheet">
      <span class="picker-value">{{ modelValue || placeholder || 'Choose…' }}</span>
      <span class="picker-caret" aria-hidden="true">▾</span>
    </button>

    <div v-if="open" class="picker-sheet" role="dialog" aria-modal="true">
      <div class="picker-head">
        <input
          ref="filterInput"
          v-model="filter"
          type="search"
          class="picker-filter"
          :placeholder="`Search ${label.toLowerCase()}…`"
          @keydown="onKeydown"
        />
        <button type="button" class="picker-cancel" @click="close">Cancel</button>
      </div>

      <div class="picker-list">
        <button
          v-if="freeTextOffer"
          type="button"
          class="picker-option picker-free"
          @click="choose(freeTextOffer)"
        >
          Use “{{ freeTextOffer }}”
          <small>not in the list</small>
        </button>

        <button
          v-for="o in matches"
          :key="String(o.value)"
          type="button"
          class="picker-option"
          :class="{ current: String(o.value) === String(modelValue) }"
          @click="choose(o.value)"
        >
          {{ o.value }}
        </button>

        <p v-if="!matches.length && !freeTextOffer" class="picker-empty">
          {{ options.length ? 'Nothing matches that.' : 'No suggestions available.' }}
        </p>
      </div>

      <div v-if="modelValue" class="picker-foot">
        <button type="button" class="btn" @click="clear">Clear</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.option-picker {
  display: block;
}

.picker-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  min-height: var(--control-h);
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
  color: var(--text);
  font-family: inherit;
  /* Matches the 16px the other controls take below MOBILE so tapping one does
     not zoom the page while tapping the next does not. */
  font-size: 16px;
  text-align: left;
  cursor: pointer;
}

.picker-trigger.empty .picker-value {
  color: #6d6d78;
}

.picker-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picker-caret {
  flex-shrink: 0;
  color: var(--text-muted);
  font-size: 12px;
}

.picker-sheet {
  position: fixed;
  inset: 0;
  z-index: 400;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  padding: 10px;
  padding-top: max(10px, env(safe-area-inset-top));
  padding-left: max(10px, env(safe-area-inset-left));
  padding-right: max(10px, env(safe-area-inset-right));
  padding-bottom: max(10px, env(safe-area-inset-bottom));
}

.picker-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.picker-filter {
  flex: 1;
  min-width: 0;
  height: 44px;
  font-size: 16px;
}

.picker-cancel {
  flex-shrink: 0;
  border: none;
  background: none;
  color: var(--accent);
  font-family: inherit;
  font-size: 15px;
  padding: 10px 4px;
  cursor: pointer;
}

.picker-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.picker-option {
  display: block;
  width: 100%;
  text-align: left;
  min-height: 48px;
  padding: 12px;
  border: none;
  border-radius: var(--r-sm);
  background: var(--surface-2);
  color: var(--text);
  font-family: inherit;
  font-size: 15px;
  cursor: pointer;
}

.picker-option.current {
  background: var(--accent-a16);
  color: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent-a24);
}

.picker-free {
  background: var(--surface-3);
}

.picker-free small {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 12px;
}

.picker-empty {
  margin: 0;
  padding: 24px 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
}

.picker-foot {
  flex-shrink: 0;
  padding-top: 10px;
}

.picker-foot .btn {
  width: 100%;
  min-height: 44px;
}
</style>
