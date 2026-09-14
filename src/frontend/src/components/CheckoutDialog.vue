<script setup lang="ts">
/**
 * What a checkout has to say for itself.
 *
 * Checking a device out needs a purpose and a return date, and the API refuses
 * the transition without them. That is easy to satisfy in a form with every
 * field on show and impossible to satisfy in a grid, where Status is a
 * dropdown with nowhere to type — so picking "Checked Out" there opens this
 * instead of saving, and the row is only written once it comes back filled in.
 *
 * Deliberately a wrapper around FormModal rather than its own dialog: it is the
 * same two fields the New and Edit dialogs already show, so it should look and
 * behave like them.
 */
import { computed, ref } from 'vue'
import FormModal, { type FormField } from './FormModal.vue'
import { daysFromToday, todayISO } from '../dates'

const props = withDefaults(
  defineProps<{
    /** What is being checked out, named the way the user names it. */
    deviceLabel: string
    /** Carried over when a device is already part-way filled in. */
    purpose?: string
    due?: string
    busy?: boolean
    /** How far ahead the date starts. A week is the common case. */
    defaultDays?: number
  }>(),
  { purpose: '', due: '', busy: false, defaultDays: 7 },
)

const emit = defineEmits<{
  (e: 'submit', values: { checkout_purpose: string; checkout_due: string }): void
  (e: 'cancel'): void
}>()

const values = ref<Record<string, any>>({
  checkout_purpose: props.purpose || '',
  checkout_due: props.due || daysFromToday(props.defaultDays),
})

const fields = computed<FormField[]>(() => [
  {
    key: 'checkout_due',
    label: 'Return by',
    type: 'date',
    required: true,
    max: daysFromToday(7),
    hint: 'Up to 7 days from today. The device turns red in the fleet view the day after this, and whoever has it is reminded daily from three days before.',
  },
  {
    key: 'checkout_purpose',
    label: 'Purpose',
    type: 'textarea',
    required: true,
    placeholder: 'Regression sweep for the 2.4 firmware',
    hint: 'Why the device is out. Cleared when it comes back.',
  },
])

function onSubmit(payload: Record<string, any>) {
  // FormModal has already refused a blank field; the date is the one thing it
  // cannot judge, because "required" says nothing about which day.
  if (payload.checkout_due < todayISO()) {
    error.value = 'The return date is in the past.'
    return
  }
  if (payload.checkout_due > daysFromToday(7)) {
    error.value = 'The return date cannot be more than 7 days away.'
    return
  }
  error.value = ''
  emit('submit', {
    checkout_purpose: payload.checkout_purpose,
    checkout_due: payload.checkout_due,
  })
}

const error = ref('')
</script>

<template>
  <FormModal
    :title="`Check out ${deviceLabel}`"
    :fields="fields"
    :values="values"
    submit-label="Check out"
    :busy="busy"
    @submit="onSubmit"
    @cancel="emit('cancel')"
  />
  <p v-if="error" class="checkout-error">{{ error }}</p>
</template>

<style scoped>
/* Sits above the dialog's own backdrop, which is fixed and painted at 40. */
.checkout-error {
  position: fixed;
  z-index: 60;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  margin: 0;
  padding: 8px 14px;
  border-radius: var(--r-md);
  background: var(--surface-2);
  box-shadow: inset 0 0 0 1px var(--red), var(--shadow-md);
  color: var(--red);
  font-size: 12.5px;
}
</style>
