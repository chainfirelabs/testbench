export interface SuggestOption {
  value: any
  label?: string
}

/*
 * The most options a suggestion list renders at once.
 *
 * Not a cosmetic limit. Both pickers build real DOM — an `<li>` in the
 * dropdown, a `<button>` in the phone sheet — one node per option, in a single
 * tick. The callers with the most options are the ones offering every device
 * or every software version, and an empty box matches all of them: a fleet of
 * a few thousand became a few thousand nodes every time the field was focused.
 *
 * A hundred is far more than anyone reads before they start typing, and typing
 * is what the list is for.
 */
export const MAX_VISIBLE_OPTIONS = 100

/** Case-insensitive substring, so "r2" finds "Rack R2". */
export function matchOptions<T extends SuggestOption>(options: T[], query: string): T[] {
  const q = (query || '').trim().toLowerCase()
  if (!q) return options
  return options.filter((option) => String(option.value).toLowerCase().includes(q))
}

/**
 * What a suggestion list should show for a query, and what it is holding back.
 *
 * `hidden` is what the footer reports. It matters that it is a count and not a
 * flag: "and 4 more" and "and 12,000 more" are different messages, the second
 * one telling you that typing is not optional.
 */
export function visibleOptions<T extends SuggestOption>(
  options: T[],
  query: string,
  limit: number = MAX_VISIBLE_OPTIONS,
): { visible: T[]; hidden: number } {
  const matched = matchOptions(options, query)
  return {
    visible: matched.slice(0, limit),
    hidden: Math.max(0, matched.length - limit),
  }
}
