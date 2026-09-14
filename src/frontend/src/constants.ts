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
 * Mirrors ROLE_RANK in backend/app/models/user.py.
 *
 * Roles are a ladder: an API key may carry any role at or below its owner's,
 * and the backend rejects anything higher. The dropdown offers exactly the
 * roles the API would accept, so a user is never shown a choice that 403s.
 */
export const ROLE_RANK: Record<string, number> = { readonly: 0, tester: 1, admin: 2 }

export function rolesUpTo(role: string | undefined): string[] {
  const ceiling = ROLE_RANK[role ?? '']
  if (ceiling === undefined) return []
  return Object.keys(ROLE_RANK)
    .filter((r) => ROLE_RANK[r] <= ceiling)
    .sort((a, b) => ROLE_RANK[a] - ROLE_RANK[b])
}

/**
 * Mirrors MIN_PASSWORD_LENGTH in backend/app/schemas.py.
 *
 * Checked here only so a too-short password is refused before the round trip;
 * the API refuses it either way.
 */
export const MIN_PASSWORD_LENGTH = 8
