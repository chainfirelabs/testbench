/**
 * Values a free-text field already holds, for autocomplete.
 *
 * A fleet's free-text columns are meant to repeat — "Rack 4 — Lab B" is one
 * place, however many devices are in it — but nothing stops the second device
 * being filed under "Rack 4 - Lab B". Offering what is already there is the
 * only thing that keeps a free-text column worth filtering on.
 *
 * The whole distinct list is fetched once per field and filtered in the
 * browser, rather than querying per keystroke: the lists are short (a fleet has
 * tens of locations, not thousands), `<datalist>` filters natively and does not
 * tell us what was typed anyway, and a request per character on a column with
 * no index on its text would be the expensive way to get a worse result.
 *
 * The cache is module-level, so every grid cell and dialog that asks for
 * `devices.location` shares one fetch. `invalidate()` drops it after a write,
 * since the value just saved is exactly the one the next row wants to offer.
 */
import { ref, type Ref } from 'vue'
import { api } from './api/client'
import { resolveFilterValues, type FilterValueSource } from './filterValues'

/** Matches the whitelist in `backend/app/api/suggestions.py`. */
export type SuggestEntity =
  | 'devices'
  | 'software'
  | 'vendor-devices'
  | 'tests'
  | 'audit_logs'
  | 'users'

export interface SuggestOption {
  value: string
  label: string
}

interface Entry {
  options: Ref<SuggestOption[]>
  loaded: boolean
  /** The request in flight, so concurrent callers share one fetch. */
  inflight: Promise<void> | null
}

const cache = new Map<string, Entry>()

function entryFor(entity: SuggestEntity, field: string): Entry {
  const key = `${entity}/${field}`
  let entry = cache.get(key)
  if (!entry) {
    entry = { options: ref([]), loaded: false, inflight: null }
    cache.set(key, entry)
  }
  return entry
}

function fetchInto(entity: SuggestEntity, field: string, entry: Entry): Promise<void> {
  if (entry.loaded) return Promise.resolve()
  // Held rather than re-issued: the grid's column filters and the cell editors
  // ask for the same field at the same moment, and a caller that needs the
  // values (rather than a ref that fills in) has to be able to await the one
  // request rather than racing it.
  if (entry.inflight) return entry.inflight
  entry.inflight = api<{ values: string[] }>(
    `/suggestions/${entity}/${encodeURIComponent(field)}?limit=500`,
  )
    .then((res) => {
      entry.options.value = (res.values || []).map((v) => ({ value: v, label: v }))
      entry.loaded = true
    })
    .catch(() => {
      // A field with no suggestions behaves exactly like one whose suggestions
      // failed to load: you type the value. Never surface this.
    })
    .finally(() => {
      entry.inflight = null
    })
  return entry.inflight
}

/**
 * The distinct values of one field, awaited rather than watched.
 *
 * The same cache `useSuggestions` reads, so a column filter and the cell
 * editor under it never disagree about what is in the column, and one write
 * invalidates both.
 */
export async function suggestionValues(
  entity: SuggestEntity,
  field: string,
): Promise<string[]> {
  const entry = entryFor(entity, field)
  await fetchInto(entity, field, entry)
  return entry.options.value.map((option) => option.value)
}

/**
 * Suggestions for one field, fetched on first use.
 *
 * Returns a ref that starts empty and fills in — safe to bind straight into a
 * `<datalist>`, which simply offers nothing until it arrives.
 */
export function useSuggestions(entity: SuggestEntity, field: string): Ref<SuggestOption[]> {
  const entry = entryFor(entity, field)
  void fetchInto(entity, field, entry)
  return entry.options
}

/**
 * Forget what was cached for an entity, so the next read re-fetches.
 *
 * Call after creating or editing a row: the value just written is the one the
 * next row is most likely to want.
 */
export function invalidateSuggestions(entity: SuggestEntity): void {
  for (const [key, entry] of cache) {
    if (!key.startsWith(`${entity}/`)) continue
    entry.loaded = false
    // Left in place rather than cleared: an open dialog keeps showing the list
    // it had until the replacement lands, instead of blinking to empty.
    void fetchInto(entity, key.slice(entity.length + 1), entry)
  }
}

/**
 * A `filterValues` function for DataTable, bound to one collection.
 *
 * The policy — local vocabulary first, then the server, then nothing — is in
 * filterValues.ts; this is where it meets the request that answers it, which
 * is the cache above. That shared cache is the point: a column's filter and
 * the cell editor under it offer the same values, and one write invalidates
 * both.
 */
export function makeFilterValues(
  source: FilterValueSource,
): (colId: string) => Promise<any[]> {
  return (colId: string) => resolveFilterValues(source, colId, suggestionValues)
}
