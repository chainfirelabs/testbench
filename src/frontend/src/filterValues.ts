/**
 * What a column's checklist filter offers to tick.
 *
 * Every list in this app is server-paged, so the browser holds one window of
 * the rows and the column holds the rest. Harvesting the row model — which is
 * what AG Grid's own set filter does, and what this one used to do — therefore
 * offered a page's worth of values and hid everything else in the column: on a
 * fleet of 2,000 devices the Location filter listed the locations of the first
 * hundred and nothing else. The distinct values have to come from the server.
 *
 * Two sources, in order:
 *
 * 1. `local` — values the page already knows without asking. A controlled
 *    vocabulary is in the schema (a select field's options), and a structural
 *    column like Device Type is a list the page already loaded. No request,
 *    and the list is complete even for a column where every row is blank.
 * 2. `/suggestions/{entity}/{field}` — the distinct values of a text column,
 *    shared with the cell editors' autocomplete, so a filter and the editor
 *    under it never disagree about what is in the column.
 *
 * A column neither can answer returns nothing, and the filter falls back to
 * the values the loaded rows happen to carry (see DataTable's
 * ValueChecklistFilter), which is what the grid could offer on its own anyway.
 */
import type { SuggestEntity } from './suggestions'

export interface FilterValueSource {
  /** The `/suggestions` collection these columns belong to, if any. */
  entity?: SuggestEntity
  /**
   * Values the page can answer itself, by column id. Called per lookup rather
   * than captured, so a schema or a type list that loads after the grid does
   * is still reflected.
   */
  local?: (colId: string) => any[] | undefined
  /**
   * Column ids the server has no distinct-value answer for — derived columns,
   * counts, anything computed in the browser. Named rather than discovered so
   * opening their filter does not cost a request that is going to 404.
   */
  skip?: readonly string[]
}

/** How a column's values are looked up when the page cannot answer itself. */
export type ValueFetcher = (entity: SuggestEntity, field: string) => Promise<string[]>

/**
 * Which source answers for one column.
 *
 * Split out from `makeFilterValues` so the decision — is this local, is it the
 * server's, is it nobody's — can be exercised without a network layer under
 * it, and so a caller can see whether the server was asked at all.
 */
export async function resolveFilterValues(
  source: FilterValueSource,
  colId: string,
  fetch: ValueFetcher,
): Promise<any[]> {
  const local = source.local?.(colId)
  if (local?.length) return local
  if (!source.entity || (source.skip || []).includes(colId)) return []
  return await fetch(source.entity, colId)
}

/*
 * `makeFilterValues` — the version bound to the real `/suggestions` fetch, and
 * the one the pages use — lives in suggestions.ts, which owns that request.
 * Keeping this module free of it leaves the decision above testable on its own
 * and keeps "which source answers?" separate from "how do we ask?".
 */
