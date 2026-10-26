/**
 * Someone else's checkout, as the API refuses it — and an admin's way past.
 *
 * Re-stating a checkout another user holds (a new purpose or return date while
 * the device stays checked out) is a 409 naming who has it. For an admin the
 * same 409 says `can_take_over`, and asking again with `?take_over=true` makes
 * the checkout theirs and tells the previous holder. The server decides who is
 * an admin; the page only asks the question it was told it may ask.
 */

export interface CheckoutConflict {
  code: 'checked_out_by_other'
  message: string
  holder: string
  checkout_due: string | null
  checkout_purpose: string | null
  can_take_over: boolean
}

/** The refusal, if this error is one. */
export function checkoutConflict(error: unknown): CheckoutConflict | null {
  const detail = (error as { status?: number; detail?: any } | null)?.detail
  if ((error as { status?: number })?.status !== 409) return null
  return detail && typeof detail === 'object' && detail.code === 'checked_out_by_other' ? detail : null
}

/** What an admin is asked before taking a device from someone. */
export function takeOverQuestion(uniqueId: string, conflict: CheckoutConflict): string {
  const due = conflict.checkout_due ? ` until ${conflict.checkout_due}` : ''
  const purpose = conflict.checkout_purpose ? ` for: ${conflict.checkout_purpose}` : ''
  return `${uniqueId} is already checked out by ${conflict.holder}${due}${purpose}.\n\n`
    + `Take it over? It will be checked out to you with the details you entered, `
    + `and ${conflict.holder} will be notified.`
}

/**
 * Send a device write; on someone else's checkout, offer the take-over.
 *
 * `send(takeOver)` makes the request, with `?take_over=true` when asked to.
 * Resolves to its result, or to null when an admin decides not to take the
 * device over. Anyone who may not take over gets the API's refusal as thrown,
 * which already says who holds the device.
 */
export async function saveWithTakeOver<T>(
  send: (takeOver: boolean) => Promise<T>,
  uniqueId: string,
  ask: (question: string) => boolean = (question) => window.confirm(question),
): Promise<T | null> {
  try {
    return await send(false)
  } catch (error) {
    const conflict = checkoutConflict(error)
    if (!conflict?.can_take_over) throw error
    if (!ask(takeOverQuestion(uniqueId, conflict))) return null
    return send(true)
  }
}

/** The device PATCH path, with the take-over flag when it is wanted. */
export function devicePatchPath(id: string, takeOver: boolean): string {
  return `/devices/${id}${takeOver ? '?take_over=true' : ''}`
}
