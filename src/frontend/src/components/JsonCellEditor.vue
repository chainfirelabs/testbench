<script lang="ts">
/**
 * AG Grid v32 custom cell editor for JSON object fields (misc_data, data).
 * The user clicks the cell, types/edits JSON, and commits with Enter or blur.
 * Invalid JSON (or a non-object value) cancels the edit and reports back to the
 * view via the onInvalid param (passed via cellEditorParams).
 *
 * Two wrapper details decide the shape of this file; both were long-standing
 * bugs here, and both destroyed data rather than merely misbehaving.
 *
 * 1. Params arrive as ONE frozen `params` prop, not as individual props.
 *    Declared individually, `value` was always undefined, so the editor opened
 *    at "{}" whatever the cell held. `onInvalid` and `stopEditing` were
 *    undefined too, so invalid JSON reported nothing and Escape did nothing.
 *
 * 2. This is deliberately NOT `<script setup>`. The wrapper finds these methods
 *    through `instance.proxy[name]` and `instance.$.setupState[name]`, never
 *    through `defineExpose`. A production build compiles the template inline,
 *    so `setup()` returns a render function and `setupState` is empty — the
 *    wrapper's method proxy then returns null for getValue(), and committing an
 *    edit wrote null over the field. Broken only in the built app, since
 *    `vite serve` compiles the template separately.
 */
import { defineComponent, nextTick, ref } from 'vue'

interface EditorParams {
  value?: any
  onInvalid?: (message: string) => void
  stopEditing?: (suppressNavigateAfterEdit?: boolean) => void
}

export default defineComponent({
  name: 'JsonCellEditor',
  props: {
    params: { type: Object as () => EditorParams, required: true },
  },
  setup(props) {
    const text = ref(props.params.value != null ? JSON.stringify(props.params.value) : '{}')
    const invalid = ref(false)
    const cancelled = ref(false)
    const inputEl = ref<HTMLInputElement | null>(null)

    // Called by AG Grid once the editor is rendered — focus the input.
    function afterGuiAttached() {
      nextTick(() => {
        inputEl.value?.focus()
        inputEl.value?.select()
      })
    }

    function tryParse(): any | null {
      try {
        const parsed = JSON.parse(text.value || '{}')
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
          return null
        }
        return parsed
      } catch {
        return null
      }
    }

    // Called by AG Grid when editing ends (Enter / blur) to get the new value.
    function getValue(): any {
      const parsed = tryParse()
      if (parsed === null) return props.params.value ?? {}
      return parsed
    }

    // Return true to discard the edit (invalid JSON or Escape).
    function isCancelAfterEnd(): boolean {
      if (cancelled.value) return true
      const parsed = tryParse()
      if (parsed === null) {
        invalid.value = true
        props.params.onInvalid?.('Invalid JSON object — edit cancelled')
        return true
      }
      return false
    }

    function onKeydown(e: KeyboardEvent) {
      if (e.key === 'Enter') {
        e.preventDefault()
        // Let the grid finish the edit (getValue / isCancelAfterEnd are called)
        ;(e.target as HTMLInputElement).blur()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        cancelled.value = true
        props.params.stopEditing?.(true)
      }
    }

    return {
      text,
      invalid,
      inputEl,
      onKeydown,
      afterGuiAttached,
      getValue,
      isCancelAfterEnd,
      focusIn: () => inputEl.value?.focus(),
    }
  },
})
</script>

<template>
  <input
    ref="inputEl"
    v-model="text"
    class="json-editor-input"
    :class="{ 'json-editor-invalid': invalid }"
    spellcheck="false"
    @keydown="onKeydown"
  />
</template>

<style scoped>
.json-editor-input {
  width: 100%;
  box-sizing: border-box;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.json-editor-invalid {
  outline: 2px solid var(--red);
}
</style>
