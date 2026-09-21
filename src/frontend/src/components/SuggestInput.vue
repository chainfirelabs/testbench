<script setup lang="ts">
/**
 * A text input that suggests values already in use.
 *
 * Replaces `<datalist>`, which cannot be made to behave: its popup is browser
 * chrome, so its width is whatever the longest currently-matching option needs
 * and it resizes on every keystroke as the list narrows. Nothing in CSS reaches
 * it. This renders the list itself, at the input's own width, which does not
 * change as you type.
 *
 * The list is teleported to `<body>` and positioned `fixed`, so it is never
 * clipped by whatever contains the input — the grid's viewport when this is a
 * cell editor, the dialog's scroll container when it is a form field.
 *
 * Free text is the point: this suggests, it does not constrain. Anything typed
 * commits, whether or not it is on the list.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { visibleOptions } from '../suggestOptions'

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: { value: string; label: string }[]
    placeholder?: string
    inputId?: string
    inputClass?: string
  }>(),
  { placeholder: '', inputId: undefined, inputClass: '' },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'enter'): void
  (e: 'escape'): void
}>()

const inputEl = ref<HTMLInputElement | null>(null)
const open = ref(false)
const activeIndex = ref(-1)
const rect = ref({ top: 0, left: 0, width: 0 })

/*
 * What is rendered, and what the cap is holding back.
 *
 * Keyboard navigation, the active-index clamp and Enter all read `matches`
 * rather than the full match set on purpose: you can only arrow to, or choose,
 * something that is on screen. See `suggestOptions` for why there is a cap.
 */
const shown = computed(() => visibleOptions(props.options, props.modelValue || ''))
const matches = computed(() => shown.value.visible)
const hiddenCount = computed(() => shown.value.hidden)

const showList = computed(() => open.value && matches.value.length > 0)

function measure() {
  const el = inputEl.value
  if (!el) return
  const r = el.getBoundingClientRect()
  // Width comes from the input, not the content: that is the whole point.
  rect.value = { top: r.bottom, left: r.left, width: r.width }
}

/*
 * Re-measured on scroll and resize while open. Capture phase, because the
 * element that scrolls is usually an ancestor (the grid body, the dialog), and
 * scroll events do not bubble.
 */
function bindReposition() {
  window.addEventListener('scroll', measure, true)
  window.addEventListener('resize', measure)
}

function unbindReposition() {
  window.removeEventListener('scroll', measure, true)
  window.removeEventListener('resize', measure)
}

watch(open, (isOpen) => {
  if (isOpen) {
    measure()
    bindReposition()
  } else {
    activeIndex.value = -1
    unbindReposition()
  }
})

onBeforeUnmount(unbindReposition)

// A narrowing list can leave the highlight past the end.
watch(matches, () => {
  if (activeIndex.value >= matches.value.length) activeIndex.value = matches.value.length - 1
})

function onInput(e: Event) {
  emit('update:modelValue', (e.target as HTMLInputElement).value)
  open.value = true
  // Typing means the typed text is the candidate, not whatever was highlighted.
  activeIndex.value = -1
}

function choose(value: string) {
  emit('update:modelValue', value)
  open.value = false
  inputEl.value?.focus()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!open.value) {
      open.value = true
      // Opening with a key press should not also move the highlight.
      if (e.key === 'ArrowDown') return e.preventDefault()
    }
    e.preventDefault()
    const n = matches.value.length
    if (!n) return
    const step = e.key === 'ArrowDown' ? 1 : -1
    activeIndex.value = (activeIndex.value + step + n + 1) % (n + 1)
    // The extra slot is "nothing highlighted", so arrowing past either end
    // returns to the text as typed rather than wrapping straight round.
    if (activeIndex.value === n) activeIndex.value = -1
    return
  }
  if (e.key === 'Enter') {
    if (open.value && activeIndex.value >= 0) {
      e.preventDefault()
      choose(matches.value[activeIndex.value].value)
      return
    }
    open.value = false
    emit('enter')
    return
  }
  if (e.key === 'Escape') {
    /*
     * One press, always: close the list AND tell the caller.
     *
     * A two-stage Escape (close the list first, cancel second) is the usual
     * combobox behaviour, but the list here opens on focus — so it would be
     * open every time, and cancelling a cell edit would always need two
     * presses where every other cell needs one. The event is left to bubble
     * for the same reason: in a dialog, Escape closes the dialog from this
     * field exactly as it does from a plain input.
     */
    open.value = false
    emit('escape')
    return
  }
  if (e.key === 'Tab') open.value = false
}

function onFocus() {
  open.value = true
  nextTick(measure)
}

function onBlur() {
  // Not immediate: a click on an option blurs the input first. The option's
  // own mousedown handler prevents that, but a click landing between options
  // still needs the list to survive long enough to be a no-op.
  setTimeout(() => (open.value = false), 120)
}

defineExpose({
  focus: () => inputEl.value?.focus(),
  select: () => inputEl.value?.select(),
  el: inputEl,
})
</script>

<template>
  <input
    :id="inputId"
    ref="inputEl"
    type="text"
    :class="inputClass"
    :value="modelValue"
    :placeholder="placeholder"
    autocomplete="off"
    spellcheck="false"
    role="combobox"
    aria-autocomplete="list"
    :aria-expanded="showList"
    @input="onInput"
    @keydown="onKeydown"
    @focus="onFocus"
    @blur="onBlur"
  />
  <Teleport to="body">
    <ul
      v-if="showList"
      class="suggest-list"
      role="listbox"
      :style="{ top: `${rect.top}px`, left: `${rect.left}px`, width: `${rect.width}px` }"
    >
      <li
        v-for="(o, i) in matches"
        :key="o.value"
        role="option"
        :aria-selected="i === activeIndex"
        :class="{ active: i === activeIndex }"
        @mousedown.prevent="choose(o.value)"
        @mouseenter="activeIndex = i"
      >
        {{ o.label }}
      </li>
      <li v-if="hiddenCount" class="suggest-more" role="presentation">
        and {{ hiddenCount.toLocaleString() }} more — keep typing to narrow
      </li>
    </ul>
  </Teleport>
</template>

<style scoped>
/* Not an option: it cannot be hovered, arrowed to or chosen, and it should not
   invite the attempt. */
.suggest-more {
  padding: 6px 8px;
  border-top: 1px solid var(--border-soft);
  color: var(--text-muted);
  font-size: 12px;
  cursor: default;
}

.suggest-list {
  position: fixed;
  /* Above the modal backdrop (100), below the toast (1000). */
  z-index: 500;
  margin: 4px 0 0;
  padding: 4px;
  list-style: none;
  max-height: 240px;
  overflow-y: auto;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-lg);
}

.suggest-list li {
  padding: 6px 9px;
  border-radius: var(--r-sm);
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.suggest-list li.active {
  background: var(--accent-a16);
}
</style>
