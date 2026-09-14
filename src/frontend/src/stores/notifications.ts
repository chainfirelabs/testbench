import { defineStore } from 'pinia'
import { api } from '../api/client'

export interface Notification {
  id: string
  kind: string
  title: string
  body: string | null
  entity_type: string | null
  entity_id: string | null
  created_at: string
  read_at: string | null
}

/**
 * How often the bell asks. Checkout notifications are day-grained and the
 * sweep runs hourly, so there is nothing to gain from asking harder.
 */
const POLL_MS = 60_000

/** How many the panel holds. The profile page pages through the rest. */
const PANEL_SIZE = 20

interface State {
  items: Notification[]
  unread: number
  loading: boolean
  /**
   * Devices already announced with a toast this session.
   *
   * Deliberately in memory rather than localStorage: a reload legitimately
   * re-announces, and a session is the right lifetime for "you have been told".
   * Persisting it would mean a device could go overdue while the tab was shut
   * and never interrupt anyone.
   */
  toasted: Set<string>
  /** Set for one tick when something newly overdue arrives; App.vue shows it. */
  alert: string
}

export const useNotificationsStore = defineStore('notifications', {
  state: (): State => ({
    items: [],
    unread: 0,
    loading: false,
    toasted: new Set(),
    alert: '',
  }),
  getters: {
    hasUnread: (s) => s.unread > 0,
  },
  actions: {
    /**
     * One poll: the badge count, and the panel's page.
     *
     * Failure is silent on purpose. A poll that cannot reach the API is a poll
     * skipped — the next one tries again, and an error toast every minute
     * would be worse than a stale badge.
     */
    async refresh() {
      if (this.loading || document.hidden) return
      this.loading = true
      try {
        const page = await api<{ items: Notification[] }>(`/notifications?page_size=${PANEL_SIZE}`)
        const previous = new Set(this.items.map((n) => n.id))
        this.items = page.items
        this.unread = page.items.filter((n) => !n.read_at).length
        // Only count beyond the page when the page is full of unread ones;
        // otherwise what is on screen is the whole story.
        if (this.unread === PANEL_SIZE) {
          const count = await api<{ unread: number }>('/notifications/unread_count')
          this.unread = count.unread
        }
        this.announce(page.items, previous)
      } catch {
        /* a poll that fails is a poll skipped */
      } finally {
        this.loading = false
      }
    },

    /**
     * Interrupt once for a device that has just gone overdue.
     *
     * Only for `checkout_overdue`: a device being late is worth a toast, and
     * the three warnings before it are not — they are why the badge exists.
     */
    announce(items: Notification[], previous: Set<string>) {
      const fresh = items.filter(
        (n) =>
          n.kind === 'checkout_overdue' &&
          !n.read_at &&
          !previous.has(n.id) &&
          !this.toasted.has(n.entity_id || n.id),
      )
      if (!fresh.length) return
      for (const n of fresh) this.toasted.add(n.entity_id || n.id)
      // One toast for the lot. Three devices going overdue on the same sweep
      // is one piece of news, not three.
      this.alert =
        fresh.length === 1 ? fresh[0].title : `${fresh.length} devices are overdue`
    },

    clearAlert() {
      this.alert = ''
    },

    async markRead(id: string) {
      const item = this.items.find((n) => n.id === id)
      if (!item || item.read_at) return
      // Optimistic: the badge should go down on the click, not on the round
      // trip. A failed write is corrected by the next poll.
      item.read_at = new Date().toISOString()
      this.unread = Math.max(0, this.unread - 1)
      try {
        await api(`/notifications/${id}/read`, { method: 'POST' })
      } catch {
        this.refresh()
      }
    },

    async markAllRead() {
      const stamp = new Date().toISOString()
      for (const n of this.items) if (!n.read_at) n.read_at = stamp
      this.unread = 0
      try {
        await api('/notifications/read_all', { method: 'POST' })
      } catch {
        this.refresh()
      }
    },

    /** Start polling. Idempotent, so a remount does not stack timers. */
    start() {
      if (pollTimer !== null) return
      this.refresh()
      pollTimer = window.setInterval(() => this.refresh(), POLL_MS)
      document.addEventListener('visibilitychange', onVisible)
    },

    stop() {
      if (pollTimer !== null) window.clearInterval(pollTimer)
      pollTimer = null
      document.removeEventListener('visibilitychange', onVisible)
    },

    /** Logging out must not leave one user's messages on screen for the next. */
    reset() {
      this.stop()
      this.items = []
      this.unread = 0
      this.toasted = new Set()
      this.alert = ''
    },
  },
})

let pollTimer: number | null = null

function onVisible() {
  // Coming back to the tab is when the badge is most out of date.
  if (!document.hidden) useNotificationsStore().refresh()
}
