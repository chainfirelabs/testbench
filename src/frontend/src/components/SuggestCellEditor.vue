<script lang="ts">
/**
 * AG Grid v32 cell editor for a free-text column that repeats itself.
 *
 * The same value as an ordinary text cell — free text, nothing enforced — with
 * the values already in that column offered as you type, so "Rack 4 — Lab B" is
 * picked rather than re-typed as "Rack 4 - Lab B".
 *
 * Two things about the Vue wrapper decide the shape of this file.
 *
 * 1. Params arrive as ONE frozen `params` prop (VueComponentFactory mounts with
 *    `{ params: Object.freeze(params) }`), not as individual props. Declaring
 *    `value`, `entity` and so on separately gets undefined for every one.
 *
 * 2. This is deliberately NOT `<script setup>`. The wrapper finds the editor's
 *    lifecycle methods through `instance.proxy[name]` and
 *    `instance.$.setupState[name]`, and never reads what `defineExpose` puts on
 *    `instance.exposed`. In a production build `<script setup>` compiles the
 *    template inline, so `setup()` returns a render function and `setupState`
 *    is empty — the wrapper then logs "Framework component is missing the
 *    method getValue()" and its method proxy returns null, so committing an
 *    edit wrote null over the cell. It works under `vite serve`, where the
 *    template is compiled separately, and fails only in the built app. Setup
 *    returns an object here so the methods are in `setupState` either way.
 *
 * Suggestions render through SuggestInput rather than a native `<datalist>`:
 * the native popup is browser chrome, so it resizes on every keystroke and is
 * clipped by the grid viewport near the bottom edge. iOS Safari never reaches
 * this editor at all — inline cell editing is off below the card-list
 * breakpoint, where the dialogs use OptionPicker instead.
 */
import { defineComponent, nextTick, onMounted, ref } from 'vue'
import SuggestInput from './SuggestInput.vue'
import { useSuggestions, type SuggestEntity } from '../suggestions'

interface EditorParams {
  value?: any
  /** Which column's existing values to offer; from cellEditorParams. */
  entity: SuggestEntity
  field: string
  stopEditing?: (suppressNavigateAfterEdit?: boolean) => void
}

export default defineComponent({
  name: 'SuggestCellEditor',
  components: { SuggestInput },
  props: {
    params: { type: Object as () => EditorParams, required: true },
  },
  setup(props) {
    const options = useSuggestions(props.params.entity, props.params.field)
    const text = ref(props.params.value != null ? String(props.params.value) : '')
    const cancelled = ref(false)
    const input = ref<InstanceType<typeof SuggestInput> | null>(null)

    function takeFocus() {
      nextTick(() => {
        input.value?.focus()
        // Select rather than place a caret: the cell's current value is the
        // thing most often being replaced outright.
        input.value?.select()
      })
    }

    /**
     * Called by AG Grid once the editor is rendered — and again from onMounted.
     *
     * Both, deliberately: the wrapper builds this method's proxy with
     * mandatory=false, so an editor relying on it alone silently never focuses
     * if the grid takes a path that does not call it. Focusing twice costs
     * nothing.
     */
    function afterGuiAttached() {
      takeFocus()
    }

    onMounted(takeFocus)

    /** Called by AG Grid when editing ends (Enter / blur) for the new value. */
    function getValue(): any {
      const trimmed = text.value.trim()
      // Blank clears the field rather than storing "", which would read back as
      // a value that is present and empty — and be offered as a suggestion.
      return trimmed === '' ? null : trimmed
    }

    function isCancelAfterEnd(): boolean {
      return cancelled.value
    }

    /** Enter with no suggestion highlighted: commit, as a text cell does. */
    function commit() {
      input.value?.el?.blur()
    }

    function cancel() {
      cancelled.value = true
      props.params.stopEditing?.(true)
    }

    return {
      text,
      options,
      input,
      commit,
      cancel,
      afterGuiAttached,
      getValue,
      isCancelAfterEnd,
      focusIn: () => input.value?.focus(),
    }
  },
})
</script>

<template>
  <div class="suggest-editor">
    <SuggestInput
      ref="input"
      v-model="text"
      :options="options"
      input-class="suggest-editor-input"
      @enter="commit"
      @escape="cancel"
    />
  </div>
</template>

<style scoped>
.suggest-editor {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
}
</style>
