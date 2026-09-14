<script setup lang="ts">
/**
 * Secondary toolbar actions, collapsed on a phone.
 *
 * Above MOBILE this renders its slot inline and is otherwise invisible — a
 * desktop toolbar is unchanged. Below it, the same buttons move behind a single
 * "More" button, because a page header with seven of them wraps three rows deep
 * and eats a third of the screen before any data appears.
 *
 * Nothing is removed: every action stays reachable, one tap further away. Views
 * keep their primary action (the "+ New …" button) outside this, so the thing
 * you most often want is still one tap.
 *
 * Note for callers: keep hidden `<input type="file">` elements *outside* the
 * slot. The panel closes when something in it is clicked, which unmounts the
 * slot — and a file input removed from the document while its picker is open
 * never fires `change`.
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { useIsMobile } from '../breakpoints'

withDefaults(defineProps<{ label?: string }>(), { label: 'More' })

const isMobile = useIsMobile()
const open = ref(false)

function close() {
  open.value = false
}

/*
 * Both `click` and `touchstart`.
 *
 * iOS Safari does not bubble a click to `document` when the tap lands on an
 * element that is not itself interactive — no handler of its own, no
 * `cursor: pointer` — so a document-level click listener misses taps on plain
 * text and empty space, and the panel stays open. Listening for `touchstart`
 * as well covers those. Both firing is harmless: the handler checks whether the
 * tap was inside the panel before it closes anything, so a tap on the toggle
 * button is ignored by this and handled by the button.
 */
function onDocClick(e: Event) {
  if (!(e.target as HTMLElement)?.closest?.('.overflow-menu')) close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

watch(open, (isOpen) => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('touchstart', onDocClick)
  document.removeEventListener('keydown', onKeydown)
  if (isOpen) {
    document.addEventListener('click', onDocClick)
    document.addEventListener('touchstart', onDocClick, { passive: true })
    document.addEventListener('keydown', onKeydown)
  }
})

// Leaving mobile with the panel open would strand it over the desktop toolbar.
watch(isMobile, (mobile) => {
  if (!mobile) close()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('touchstart', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <slot v-if="!isMobile" />
  <div v-else class="overflow-menu">
    <button
      class="btn"
      type="button"
      :aria-expanded="open"
      aria-haspopup="true"
      @click="open = !open"
    >
      {{ label }} ▾
    </button>
    <!-- Acting on anything in the panel dismisses it, the way a menu should. -->
    <div v-if="open" class="overflow-panel" @click="close">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.overflow-menu {
  position: relative;
  display: inline-block;
}

.overflow-panel {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 120;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 210px;
  max-width: calc(100vw - 24px);
  padding: 8px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-lg);
}

/*
 * The slot holds the view's own toolbar buttons, which are laid out for a row.
 * In the panel they are a stacked menu, so they go full width and left-aligned.
 * :deep because they belong to the parent view, not to this component.
 */
.overflow-panel :deep(.btn) {
  width: 100%;
  justify-content: flex-start;
  min-height: 44px;
}

/* A nested dropdown (the Views menu) has to fill the panel too. */
.overflow-panel :deep(.profile-menu) {
  display: block;
}
</style>
