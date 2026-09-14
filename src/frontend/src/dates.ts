/**
 * Dates, as dates.
 *
 * `checked_out_at`, `checkout_due` and a test's `run_at` are DATE columns: the
 * API sends and takes `YYYY-MM-DD`, with no time and no zone. That makes them
 * easy to get wrong in one specific way — `new Date('2026-08-27')` is parsed as
 * UTC midnight, so `toLocaleDateString()` west of Greenwich renders the day
 * before. Everything here builds dates from their parts in local time instead,
 * so a date is the day it says it is wherever it is read.
 */

/** Split `YYYY-MM-DD` into a local-midnight Date, or null if it is not one. */
export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value))
  if (!m) return null
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}

/** `YYYY-MM-DD` rendered in the reader's locale. Blank for no value. */
export function formatDate(value: string | null | undefined): string {
  const d = parseDate(value)
  return d ? d.toLocaleDateString() : ''
}

/** Today, as the API spells it. */
export function todayISO(): string {
  return toISO(new Date())
}

export function toISO(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** `YYYY-MM-DD` a number of days from today — the default a checkout offers. */
export function daysFromToday(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return toISO(d)
}

/**
 * Whole days from today until `value`. Negative once it is in the past, which
 * is what "overdue by 3" reads from.
 *
 * Both sides are local midnight, so this counts calendar days rather than
 * 24-hour periods and never comes back with a fraction.
 */
export function daysUntil(value: string | null | undefined): number | null {
  const then = parseDate(value)
  if (!then) return null
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((then.getTime() - today.getTime()) / 86400000)
}

/** An AG Grid valueFormatter for a date column. */
export const fmtDate = (p: any) => formatDate(p.value)

/**
 * A grid column holding a date.
 *
 * `agDateStringCellEditor` edits the `YYYY-MM-DD` string in place rather than
 * converting to a Date and back, which is what keeps the value the API sent
 * identical to the value sent back when the cell is opened and closed untouched.
 */
export function dateColumn(col: Record<string, any>) {
  return {
    ...col,
    cellEditor: 'agDateStringCellEditor',
    valueFormatter: fmtDate,
  }
}
