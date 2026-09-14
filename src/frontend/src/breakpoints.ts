/**
 * Viewport breakpoints, and reactive access to them from script.
 *
 * These four numbers are mirrored by the `@media` blocks in style.css. There is
 * no way to share one definition between CSS and TypeScript without a build
 * step, so the rule is: change a number here and change its twin there. Each
 * media query in style.css is tagged with the constant it mirrors.
 *
 * The app has exactly two layouts, either side of MOBILE. Everything narrower
 * than that gets the mobile shell; everything wider is untouched by any of this.
 * The other three are refinements inside the mobile layout, not tiers of their
 * own — a tablet is deliberately not a target, so nothing keys off a width
 * between MOBILE and the desktop layout.
 */

/** Below this the navigation shell, toolbars and control sizing go mobile. */
export const MOBILE = 900

/** Below this a DataTable renders as a card list instead of a grid. */
export const CARDS = 768

/** Below this two-column layouts (.kv, .form-grid) stack, and modals become sheets. */
export const STACK = 640

import { getCurrentScope, onScopeDispose, ref, type Ref } from 'vue'

/**
 * A ref tracking whether a media query currently matches.
 *
 * One listener per call site. That is a few more listeners than strictly
 * necessary when several components ask the same question, but matchMedia
 * listeners are cheap and a shared cache would need reference counting to know
 * when the last owner had gone — which is exactly the kind of bookkeeping that
 * goes wrong quietly.
 */
export function useMediaQuery(query: string): Ref<boolean> {
  // Guarded so the composable is safe to call from module scope (a store, a
  // router guard) and from a test environment without a DOM.
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return ref(false)
  }

  const mql = window.matchMedia(query)
  const matches = ref(mql.matches)
  const onChange = (e: MediaQueryListEvent) => {
    matches.value = e.matches
  }
  mql.addEventListener('change', onChange)

  // Only when there is a scope to attach to. Called outside one the listener
  // lives for the session, which is correct for a module-level caller.
  if (getCurrentScope()) {
    onScopeDispose(() => mql.removeEventListener('change', onChange))
  }

  return matches
}

/** True below MOBILE: the phone shell, sizing and toolbars are in effect. */
export function useIsMobile(): Ref<boolean> {
  return useMediaQuery(`(max-width: ${MOBILE - 1}px)`)
}

/** True below CARDS: a DataTable should render cards rather than a grid. */
export function useIsCardList(): Ref<boolean> {
  return useMediaQuery(`(max-width: ${CARDS - 1}px)`)
}

/** True below STACK: two-column layouts stack and modals become sheets. */
export function useIsStacked(): Ref<boolean> {
  return useMediaQuery(`(max-width: ${STACK - 1}px)`)
}
