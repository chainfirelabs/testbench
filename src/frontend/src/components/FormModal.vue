<script setup lang="ts">
/**
 * The dialog behind every "+ New" button.
 *
 * Rows used to be created by dropping a blank row into the grid, which looked
 * like an empty record had already been added and left the required fields to
 * be discovered by trial and error. Here the record is filled in first and only
 * reaches the API once it validates.
 *
 * `values` is the caller's object and is edited in place, so a caller can react
 * to a field changing (the tests dialog fills the software version in from the software
 * that was picked).
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useIsMobile, useIsStacked } from '../breakpoints'
import OptionPicker from './OptionPicker.vue'
import SuggestInput from './SuggestInput.vue'

export interface FormField {
  key: string
  label: string
  /**
   * text (default), password (masked, and never trimmed), select, textarea,
   * json (a textarea parsed as an object), date (a native picker that submits
   * `YYYY-MM-DD`), or datalist (free text with autocomplete over `options`).
   *
   * `datalist` is a historical name: the field is no longer a native
   * `<datalist>`, whose popup is unstyleable browser chrome that resizes on
   * every keystroke. It renders SuggestInput on a desktop and OptionPicker on
   * a phone. What the name means to a caller — free text, with suggestions —
   * is unchanged.
   */
  type?: 'text' | 'password' | 'select' | 'textarea' | 'json' | 'datalist' | 'date' | 'number' | 'boolean'
  /**
   * Choices for `select`, suggestions for `datalist`.
   *
   * A datalist uses `value` as what lands in the field, and shows `label`.
   * Callers set the two the same; they were required to when this was a native
   * `<datalist>`, where an option whose text differed from its value rendered
   * differently in every browser engine and some inserted the text instead of
   * the value. Anything explanatory goes in `hint`, which is ours to render.
   */
  options?: { value: any; label: string }[]
  /**
   * Required always (`true`), or only in some states — a predicate is handed
   * the whole form, so one field can be required because of another's value.
   * Checking a device out needs a purpose and a return date; creating an
   * available one does not, and the same dialog does both.
   */
  required?: boolean | ((values: Record<string, any>) => boolean)
  /**
   * Not editable, always (`true`) or only in some states. A disabled field
   * submits `null` rather than what happens to be sitting in it, the way a
   * disabled input is left out of a native form post: the purpose and return
   * date belong to a checkout, so a device that is not checked out submits
   * neither, whatever the dialog was opened holding.
   */
  disabled?: boolean | ((values: Record<string, any>) => boolean)
  /** Latest accepted value for inputs such as a checkout return date. */
  max?: string
  placeholder?: string
  /** Shown under the input, for anything the label cannot say on its own. */
  hint?: string
}

const props = withDefaults(
  defineProps<{
    title: string
    fields: FormField[]
    values: Record<string, any>
    submitLabel?: string
    busy?: boolean
  }>(),
  { submitLabel: 'Create', busy: false },
)

const emit = defineEmits<{
  (e: 'submit', values: Record<string, any>): void
  (e: 'cancel'): void
  (e: 'change', key: string, value: any): void
}>()

const error = ref('')
const card = ref<HTMLElement | null>(null)

/** `required` resolved against the values as they stand right now. */
function isRequired(f: FormField): boolean {
  return typeof f.required === 'function' ? f.required(props.values) : !!f.required
}

/** Likewise `disabled` — re-read on every render, so it tracks the form. */
function isDisabled(f: FormField): boolean {
  return typeof f.disabled === 'function' ? f.disabled(props.values) : !!f.disabled
}

/* ---------- long forms on a small screen ----------
 *
 * New Device is eleven fields, of which only Unique ID and Status are required
 * and Status arrives pre-filled. Presenting all eleven in a sheet buries the
 * submit button under a scroll; presenting the ones that matter and offering
 * the rest behind a disclosure does not.
 */
const isStacked = useIsStacked()
const showAllFields = ref(false)

/*
 * The datalist replacement keys off MOBILE, not STACK: an iPhone in landscape
 * is about 844px wide, which is past the stacking breakpoint but still Safari
 * on iOS, still with no usable datalist UI. The picker is a full-screen sheet
 * either way, so the wider trigger costs nothing.
 */
const isMobile = useIsMobile()

/** Past this many fields the disclosure is worth having. */
const FIELD_DISCLOSURE_THRESHOLD = 6

/*
 * Keys that stay on show: required ones, and ones that arrived with a value
 * (which is what makes an edit dialog show what it is editing).
 *
 * Accumulated rather than recomputed, and deliberately never removed from. If
 * this were a live filter over `values`, clearing a field would make it vanish
 * mid-edit. It also picks up fields that appear later — the software dialog
 * grows an "Inherit from" select once the name matches something.
 */
const shownKeys = ref(new Set<string>())

function hasValue(key: string): boolean {
  const v = props.values[key]
  return v != null && String(v).trim() !== '' && String(v).trim() !== '{}'
}

function refreshShownKeys() {
  for (const f of props.fields) {
    if (shownKeys.value.has(f.key)) continue
    if (isRequired(f) || hasValue(f.key)) shownKeys.value.add(f.key)
  }
}

refreshShownKeys()
watch(() => props.fields.map((f) => f.key).join('|'), refreshShownKeys)

const hiddenFields = computed(() => props.fields.filter((f) => !shownKeys.value.has(f.key)))

const useDisclosure = computed(
  () =>
    isStacked.value &&
    !showAllFields.value &&
    props.fields.length > FIELD_DISCLOSURE_THRESHOLD &&
    hiddenFields.value.length > 1,
)

const visibleFields = computed(() =>
  useDisclosure.value ? props.fields.filter((f) => shownKeys.value.has(f.key)) : props.fields,
)

const missing = computed(() =>
  props.fields.filter(
    (f) => isRequired(f) && !isDisabled(f) && !String(props.values[f.key] ?? '').trim(),
  ),
)

function onInput(field: FormField, value: any) {
  props.values[field.key] = value
  emit('change', field.key, value)
}

function submit() {
  if (missing.value.length) {
    error.value = `${missing.value.map((f) => f.label).join(', ')} ${missing.value.length > 1 ? 'are' : 'is'} required`
    return
  }
  const overMaximum = props.fields.find(
    (f) => !isDisabled(f) && f.max && props.values[f.key] && props.values[f.key] > f.max,
  )
  if (overMaximum) {
    error.value = `${overMaximum.label} must be on or before ${overMaximum.max}`
    return
  }
  // Build the payload: blank text becomes null, JSON fields become objects.
  const payload: Record<string, any> = {}
  for (const f of props.fields) {
    const raw = props.values[f.key]
    if (isDisabled(f)) {
      // Explicitly null rather than omitted: on a PATCH a missing key means
      // "leave it alone", and a field the form is refusing to edit should not
      // be able to keep a value it is no longer allowed to hold.
      payload[f.key] = null
    } else if (f.type === 'json') {
      try {
        const parsed = JSON.parse((raw ?? '').trim() || '{}')
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          throw new Error('not an object')
        }
        payload[f.key] = parsed
      } catch {
        error.value = `${f.label} must be a JSON object`
        return
      }
    } else if (f.type === 'password') {
      // Verbatim. A password is not text to tidy up: trimming one would store
      // something other than what was typed, and login does not trim what it
      // is given, so the account would reject its own password.
      payload[f.key] = raw
    } else if (f.type === 'number') {
      if (raw == null || String(raw).trim() === '') {
        payload[f.key] = null
      } else {
        const parsed = Number(raw)
        if (!Number.isFinite(parsed)) {
          error.value = `${f.label} must be a number`
          return
        }
        payload[f.key] = parsed
      }
    } else if (typeof raw === 'string') {
      payload[f.key] = raw.trim() || null
    } else {
      payload[f.key] = raw ?? null
    }
  }
  error.value = ''
  emit('submit', payload)
}

onMounted(() =>
  nextTick(() => card.value?.querySelector<HTMLElement>('input, select, textarea')?.focus()),
)
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('cancel')" @keydown.esc="emit('cancel')">
    <div ref="card" class="modal-card modal-wide">
      <h3 class="modal-title">{{ title }}</h3>
      <div class="modal-body">
      <form class="form-grid" @submit.prevent="submit">
        <template v-for="f in visibleFields" :key="f.key">
          <label :for="`ff-${f.key}`">
            {{ f.label
            }}<span v-if="isRequired(f) && !isDisabled(f)" class="req" title="Required">*</span>
          </label>
          <div class="field">
            <select
              v-if="f.type === 'select'"
              :id="`ff-${f.key}`"
              :disabled="isDisabled(f)"
              :value="values[f.key]"
              @change="onInput(f, ($event.target as HTMLSelectElement).value)"
            >
              <!-- Without this, an unset required select shows its first option
                   as if it were chosen while the value is still empty. -->
              <option v-if="!isRequired(f)" value="">—</option>
              <option v-else-if="!values[f.key]" value="" disabled>Select {{ f.label.toLowerCase() }}…</option>
              <option v-for="o in f.options || []" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
            <textarea
              v-else-if="f.type === 'json' || f.type === 'textarea'"
              :id="`ff-${f.key}`"
              :class="{ 'json-editor': f.type === 'json' }"
              :style="f.type === 'json' ? 'min-height: 90px' : 'min-height: 60px'"
              spellcheck="false"
              :disabled="isDisabled(f)"
              :max="f.max"
              :placeholder="f.placeholder"
              :value="values[f.key]"
              @input="onInput(f, ($event.target as HTMLTextAreaElement).value)"
            ></textarea>
            <input
              v-else-if="f.type === 'boolean'"
              :id="`ff-${f.key}`"
              type="checkbox"
              :disabled="isDisabled(f)"
              :checked="!!values[f.key]"
              @change="onInput(f, ($event.target as HTMLInputElement).checked)"
            />
            <!-- On a phone a datalist becomes a searchable sheet: iOS Safari
                 may render no suggestion UI at all, and this form's device /
                 software / version fields are unusable typed from memory. Free
                 text still commits, so the field means the same thing. -->
            <OptionPicker
              v-else-if="f.type === 'datalist' && isMobile && !isDisabled(f)"
              :model-value="values[f.key]"
              :options="f.options || []"
              :label="f.label"
              :placeholder="f.placeholder"
              @update:model-value="onInput(f, $event)"
            />
            <!-- Suggestions only: the field stays free text, so a value that
                 is not on the list is still accepted. Not a `<datalist>` — its
                 popup is browser chrome, so it resizes on every keystroke as
                 the list narrows, and no CSS reaches it. -->
            <SuggestInput
              v-else-if="f.type === 'datalist' && !isDisabled(f)"
              :input-id="`ff-${f.key}`"
              :model-value="values[f.key] ?? ''"
              :options="f.options || []"
              :placeholder="f.placeholder"
              @update:model-value="onInput(f, $event)"
            />
            <input
              v-else
              :id="`ff-${f.key}`"
              :type="f.type === 'password' ? 'password' : f.type === 'date' ? 'date' : f.type === 'number' ? 'number' : 'text'"
              :disabled="isDisabled(f)"
              :placeholder="f.placeholder"
              :value="values[f.key]"
              :autocomplete="f.type === 'password' ? 'new-password' : 'off'"
              @input="onInput(f, f.type === 'number' ? ($event.target as HTMLInputElement).valueAsNumber : ($event.target as HTMLInputElement).value)"
            />
            <small v-if="f.hint" class="muted field-hint">{{ f.hint }}</small>
          </div>
        </template>
      </form>
      <button
        v-if="useDisclosure"
        type="button"
        class="btn field-disclosure"
        @click="showAllFields = true"
      >
        More fields ({{ hiddenFields.length }})
      </button>
      <p v-if="error" class="form-error">{{ error }}</p>
      </div>
      <div class="modal-actions">
        <button class="btn" :disabled="busy" @click="emit('cancel')">Cancel</button>
        <button class="btn btn-primary" :disabled="busy" @click="submit">
          {{ busy ? 'Saving…' : submitLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* The grid's second column holds the input plus its hint, so they stack. */
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-hint {
  font-size: 11.5px;
}

.req {
  color: var(--red);
  margin-left: 3px;
}

.form-error {
  margin: 14px 0 0;
  color: var(--red);
  font-size: 12.5px;
}

.field-disclosure {
  margin-top: 14px;
  width: 100%;
}
</style>
