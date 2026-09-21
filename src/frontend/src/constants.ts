/**
 * Vocabularies shared by more than one view.
 *
 * These mirror the backend, which is what actually enforces them: a value the
 * API rejects should never have been offered in a dropdown, so the two have to
 * be changed together.
 */

/** Mirrors DEVICE_ARCHITECTURES in backend/app/models/device.py. */
export const ARCHITECTURES = [
  'x86_64',
  'x86',
  'mipsbe',
  'mipsel',
  'arm',
  'arm64',
  'aarch64',
  'ppc',
  'tilegx',
  'lexra_mips',
]

/**
 * Mirrors VENDOR_SUPPORT_STATUSES in backend/app/models/vendor_device.py.
 *
 * Shared by the software page's own vendor list and the cross-software
 * catalogue, which have to label the same claim the same way.
 */
export const SUPPORT_VALUES = ['supported', 'partial', 'unsupported', 'planned']

export const SUPPORT_LABELS: Record<string, string> = {
  supported: 'Supported',
  partial: 'Partial',
  unsupported: 'Unsupported',
  planned: 'Planned',
}

/*
 * Roles used to be mirrored here as a ladder of three. They are rows in the
 * database now, an installation can define its own, and which of them a given
 * user may grant depends on comparing permission sets rather than ranks — so
 * the lists come from the API (`/roles`, `/auth/api-key-roles`) and there is
 * nothing left to mirror. The permission KEYS are still a fixed property of the
 * build; they live in stores/auth.ts, next to the getter that checks them.
 */

/**
 * Mirrors MIN_PASSWORD_LENGTH in backend/app/schemas.py.
 *
 * Checked here only so a too-short password is refused before the round trip;
 * the API refuses it either way.
 */
export const MIN_PASSWORD_LENGTH = 8
