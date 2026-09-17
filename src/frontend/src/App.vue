<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api/client'
import { useIsMobile } from './breakpoints'
import NavDrawer, { type NavLink } from './components/NavDrawer.vue'
import { useAuthStore } from './stores/auth'
import { useNotificationsStore } from './stores/notifications'
import { deviceTypes, loadDeviceTypes } from './deviceTypes'

const buildVersion = (import.meta.env.VITE_APP_VERSION || 'dev').trim().replace(/^v/, '')
const versionLabel = buildVersion === 'dev' ? 'TestBench dev' : `TestBench v${buildVersion}`

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const notifications = useNotificationsStore()

const isLogin = computed(() => route.path === '/login')

function logout() {
  notifications.reset()
  auth.logout()
  router.push('/login')
}

// ---------- notifications ----------
/*
 * The bell is the only place in the app that speaks to a person who is not
 * looking at the thing it is about — a device is due back, or is late. It lives
 * in the shell rather than on a page so it is on screen wherever they are.
 *
 * Polling starts once there is someone to poll for and stops at logout, so the
 * login screen never asks.
 */
const bellOpen = ref(false)

watch(
  () => auth.user?.id,
  (id) => (id ? notifications.start() : notifications.reset()),
  { immediate: true },
)

onBeforeUnmount(() => notifications.stop())

function openNotification(n: any) {
  notifications.markRead(n.id)
  bellOpen.value = false
  router.push('/profile')
}

const notificationTime = (iso: string) => new Date(iso).toLocaleString()

// The toast says its piece and goes. It is not a thing to dismiss; the bell
// still holds the notification it was announcing.
watch(
  () => notifications.alert,
  (message) => {
    if (message) setTimeout(() => notifications.clearAlert(), 6000)
  },
)

// ---------- navigation shell ----------
// Below MOBILE the bar keeps only the brand and two buttons; the links, the
// username and Log out move into the drawer. One list feeds both, so there is
// no second copy to forget to update.
const isMobile = useIsMobile()
const drawerOpen = ref(false)
const searchSheetOpen = ref(false)
const deviceMenuOpen = ref(false)

/*
 * Clicking Devices opens this submenu: All Devices and every enabled type.
 * Untyped records remain visible in All Devices without consuming a permanent
 * navigation entry. The drawer renders the same list, so desktop and mobile
 * navigation cannot drift apart.
 */
const deviceLinks = computed<NavLink[]>(() => [
  { to: '/', label: 'All Devices' },
  ...deviceTypes.value.map((type) => ({ to: `/devices/type/${type.key}`, label: type.label })),
])

const navLinks = computed<NavLink[]>(() => {
  const links: NavLink[] = [
    { to: '/software', label: 'Software' },
    { to: '/tests', label: 'Tests' },
  ]
  if (auth.isAdmin) {
    links.push({ to: '/audit', label: 'Audit Log' })
    links.push({ to: '/users', label: 'Users' })
    links.push({ to: '/settings/ai', label: 'AI Providers' })
    links.push({ to: '/settings/schema', label: 'Schema' })
  }
  return links
})

watch(
  () => auth.user?.id,
  (id) => {
    if (id) loadDeviceTypes().catch(() => { deviceTypes.value = [] })
    else deviceTypes.value = []
  },
  { immediate: true },
)

// Navigating is the drawer's job, so following a link closes it. Watching the
// path rather than handling click on each link also covers the back button and
// the profile link in the drawer's footer.
watch(
  () => route.path,
  () => {
    drawerOpen.value = false
    deviceMenuOpen.value = false
    closeSearchSheet()
  },
)

function drawerLogout() {
  drawerOpen.value = false
  logout()
}

async function openSearchSheet() {
  searchSheetOpen.value = true
  // The sheet is the input's only route to focus on a phone, and it has to
  // exist in the DOM before it can take it.
  await nextTick()
  searchInput.value?.focus()
}

function closeSearchSheet() {
  searchSheetOpen.value = false
  searchOpen.value = false
  query.value = ''
}

// Leaving mobile with the sheet open would strand a full-screen overlay over
// the desktop layout.
watch(isMobile, (mobile) => {
  if (!mobile) {
    searchSheetOpen.value = false
    drawerOpen.value = false
  }
})

// ---------- global search ----------
// Per-group cap on what the dropdown lists. Deliberately generous — the
// dropdown scrolls — with the group headers showing "n of total" when a
// search matches more than this.
const SEARCH_LIMIT = 50

const query = ref('')
const results = ref<any>(emptyResults())
const searching = ref(false)
const searchOpen = ref(false)
const searchInput = ref<HTMLInputElement | null>(null)
let searchTimer: any = null

function emptyResults() {
  return {
    devices: [],
    software: [],
    tests: [],
    devices_total: 0,
    software_total: 0,
    tests_total: 0,
  }
}

/** "Devices" or "Devices · 50 of 137" when the group is capped. */
function groupLabel(name: string, shown: number, total: number) {
  return total > shown ? `${name} · ${shown} of ${total}` : name
}

function openResult(url: string) {
  router.push(url)
  searchOpen.value = false
  searchSheetOpen.value = false
  query.value = ''
}

function hasResults() {
  return (
    results.value.devices.length > 0 ||
    results.value.software.length > 0 ||
    results.value.tests.length > 0
  )
}

watch(query, (q) => {
  clearTimeout(searchTimer)
  if (!q || q.trim().length < 2) {
    results.value = emptyResults()
    searchOpen.value = false
    return
  }
  searchTimer = setTimeout(async () => {
    searching.value = true
    try {
      results.value = await api<any>(
        `/search?q=${encodeURIComponent(q.trim())}&limit=${SEARCH_LIMIT}`,
      )
      searchOpen.value = true
    } catch {
      results.value = emptyResults()
    } finally {
      searching.value = false
    }
  }, 300)
})

function onSearchKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    // On mobile the input lives in a full-screen sheet, so Escape should put
    // the sheet away too rather than leaving an empty overlay behind.
    if (searchSheetOpen.value) closeSearchSheet()
    else searchOpen.value = false
    ;(e.target as HTMLElement).blur()
  }
}

/*
 * Both `click` and `touchstart`.
 *
 * iOS Safari does not bubble a click to `document` when the tap lands on an
 * element that is not itself interactive — no handler of its own, no
 * `cursor: pointer` — so a document-level click listener misses taps on plain
 * text and empty space, and the panel stays open. Listening for `touchstart`
 * as well covers those. Both firing is harmless: the handler checks whether the
 * tap was inside the panel before it closes anything, so a tap on the toggle
 * button is ignored by this and handled by the button.
 */
function onDocumentClick(e: Event) {
  if (!(e.target as HTMLElement)?.closest?.('.device-menu')) deviceMenuOpen.value = false
  // The sheet covers the screen and has its own close button; dismissing it
  // from a stray tap would fight the on-screen keyboard opening.
  if (!searchOpen.value || searchSheetOpen.value) return
  const el = (e.target as HTMLElement)?.closest?.('.global-search')
  if (!el) searchOpen.value = false
}

document.addEventListener('click', onDocumentClick)
document.addEventListener('touchstart', onDocumentClick, { passive: true })
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('touchstart', onDocumentClick)
  clearTimeout(searchTimer)
})
</script>

<template>
  <router-view v-if="isLogin" />
  <div v-else class="layout">
    <header class="topnav">
      <button
        class="nav-toggle"
        type="button"
        aria-label="Open menu"
        :aria-expanded="drawerOpen"
        @click="drawerOpen = true"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="3" y1="6" x2="21" y2="6" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="18" x2="21" y2="18" />
        </svg>
      </button>
      <div class="brand" @click="router.push('/')" style="cursor: pointer">
        <img src="/logo-banner.png" alt="TestBench" class="brand-logo" />
        <span class="brand-name">TestBench</span>
      </div>
      <nav class="nav">
        <div class="device-menu">
          <button
            type="button"
            class="device-menu-trigger"
            :class="{ active: route.path === '/' || route.path.startsWith('/devices') }"
            :aria-expanded="deviceMenuOpen"
            @click="deviceMenuOpen = !deviceMenuOpen"
          >
            Devices <span aria-hidden="true">▾</span>
          </button>
          <div v-if="deviceMenuOpen" class="device-menu-panel">
            <router-link v-for="link in deviceLinks" :key="link.to" :to="link.to">{{ link.label }}</router-link>
          </div>
        </div>
        <router-link v-for="l in navLinks" :key="l.to" :to="l.to">{{ l.label }}</router-link>
      </nav>
      <div class="spacer"></div>
      <button
        class="search-toggle"
        type="button"
        aria-label="Search"
        @click="openSearchSheet"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="11" cy="11" r="7" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
      </button>
      <div class="global-search" :class="{ 'search-sheet': searchSheetOpen }">
        <div class="search-bar">
          <input
            ref="searchInput"
            v-model="query"
            type="search"
            placeholder="Search devices, software, tests…"
            class="search-input"
            @keydown="onSearchKeydown"
          />
          <button
            v-if="searchSheetOpen"
            class="search-cancel"
            type="button"
            @click="closeSearchSheet"
          >
            Cancel
          </button>
        </div>
        <div v-if="searchOpen && query.trim().length >= 2" class="search-dropdown">
          <p v-if="searching" class="search-status">Searching…</p>
          <p v-else-if="!hasResults()" class="search-status">No results for “{{ query.trim() }}”.</p>
          <template v-else>
            <div v-if="results.devices.length" class="search-group">
              <div class="search-group-label">
                {{ groupLabel('Devices', results.devices.length, results.devices_total) }}
              </div>
              <button
                v-for="d in results.devices"
                :key="d.id"
                class="search-item"
                @click="openResult(`/devices/${d.unique_id}`)"
              >
                <span class="search-item-title">{{ d.unique_id }}</span>
                <span class="search-item-sub">{{ [d.make, d.model, d.location].filter(Boolean).join(' · ') }}</span>
              </button>
            </div>
            <div v-if="results.software.length" class="search-group">
              <div class="search-group-label">
                {{ groupLabel('Software', results.software.length, results.software_total) }}
              </div>
              <button
                v-for="t in results.software"
                :key="t.id"
                class="search-item"
                @click="openResult(`/software/${encodeURIComponent(t.name)}`)"
              >
                <span class="search-item-title">{{ t.name }}</span>
                <span class="search-item-sub">{{ t.version ? `v${t.version}` : '' }}</span>
              </button>
            </div>
            <div v-if="results.tests.length" class="search-group">
              <div class="search-group-label">
                {{ groupLabel('Tests', results.tests.length, results.tests_total) }}
              </div>
              <button
                v-for="t in results.tests"
                :key="t.id"
                class="search-item"
                @click="openResult(`/tests`)"
              >
                <span class="search-item-title">{{ t.software_name }} on {{ t.device_unique_id }}</span>
                <span class="search-item-sub">{{ t.outcome }} · {{ t.tag }}{{ t.run_at ? ' · ' + new Date(t.run_at).toLocaleString() : '' }}</span>
              </button>
            </div>
          </template>
        </div>
      </div>
      <button
        v-if="auth.user"
        class="bell"
        type="button"
        :aria-label="notifications.unread ? `Notifications (${notifications.unread} unread)` : 'Notifications'"
        :aria-expanded="bellOpen"
        @click="bellOpen = !bellOpen"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        <span v-if="notifications.unread" class="bell-badge">{{ notifications.unread }}</span>
      </button>
      <div v-if="bellOpen" class="bell-panel">
        <div class="bell-head">
          <span>Notifications</span>
          <button
            v-if="notifications.unread"
            class="bell-mark"
            type="button"
            @click="notifications.markAllRead()"
          >
            Mark all read
          </button>
        </div>
        <p v-if="!notifications.items.length" class="bell-empty">Nothing to report.</p>
        <button
          v-for="n in notifications.items"
          :key="n.id"
          class="bell-item"
          :class="{ unread: !n.read_at, overdue: n.kind === 'checkout_overdue' }"
          @click="openNotification(n)"
        >
          <span class="bell-item-title">{{ n.title }}</span>
          <span v-if="n.body" class="bell-item-body">{{ n.body }}</span>
          <span class="bell-item-time">{{ notificationTime(n.created_at) }}</span>
        </button>
      </div>
      <div class="user-area">
        <router-link to="/profile" class="user-name" title="Profile and API keys">
          <strong>{{ auth.user?.username }}</strong>
          <span class="user-role">{{ auth.user?.role }}</span>
        </router-link>
        <button class="btn" @click="logout">Log out</button>
      </div>
    </header>
    <!-- Outside the header on purpose. `.topnav` sets `backdrop-filter`, which
         makes it the containing block for fixed-position descendants — a
         backdrop inside it would cover the header and nothing else, so a click
         on the page would never close the panel. -->
    <div v-if="bellOpen" class="bell-backdrop" @click="bellOpen = false"></div>
    <div class="content">
      <router-view />
    </div>
    <!-- One interruption, for a device that has just gone late. Everything
         quieter than that waits in the bell. -->
    <div v-if="notifications.alert" class="toast toast-error app-toast" @click="notifications.clearAlert()">
      {{ notifications.alert }}
    </div>
    <NavDrawer
      :open="drawerOpen"
      :links="navLinks"
      :device-links="deviceLinks"
      :username="auth.user?.username"
      :role="auth.user?.role"
      :notification-count="notifications.unread"
      @close="drawerOpen = false"
      @logout="drawerLogout"
      @notifications="drawerOpen = false; bellOpen = true"
    />
  </div>
  <span class="site-version">{{ versionLabel }}</span>
  <a
    class="site-credit"
    href="https://chainfirelabs.com"
    target="_blank"
    rel="noopener noreferrer"
  >© ChainFire Labs</a>
</template>

<style scoped>
/* Build version and ownership mark shared by login and authenticated screens. */
.site-version,
.site-credit {
  position: fixed;
  bottom: max(16px, calc(env(safe-area-inset-bottom) + 12px));
  z-index: 40;
  padding: 4px 8px;
  border-radius: var(--r-sm);
  background: rgba(11, 11, 13, 0.88);
  box-shadow: 0 0 10px rgba(11, 11, 13, 0.85);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.025em;
  line-height: 1.4;
  text-decoration: none;
  opacity: 0.55;
  transition: color var(--dur) var(--ease), opacity var(--dur) var(--ease);
}

.site-version {
  left: max(20px, calc(env(safe-area-inset-left) + 12px));
  pointer-events: none;
}

.site-credit {
  right: max(20px, calc(env(safe-area-inset-right) + 12px));
}

.site-credit:hover,
.site-credit:focus-visible {
  color: var(--accent-soft);
  opacity: 0.9;
}

/* ---------- notifications ---------- */
.bell {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h);
  height: var(--control-h);
  margin-right: 10px;
  padding: 0;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.bell:hover {
  color: var(--text);
  background: var(--surface-2);
}

.bell:focus-visible {
  outline: none;
  box-shadow: var(--ring);
}

.bell svg {
  width: 18px;
  height: 18px;
}

.bell-badge {
  position: absolute;
  top: 1px;
  right: 0;
  min-width: 15px;
  padding: 0 4px;
  border-radius: var(--r-pill);
  background: var(--red);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  line-height: 15px;
  text-align: center;
}

/* Catches the click that closes the panel. Sits below the header's stacking
   context (z-index 30), so the panel — which lives inside it — stays clickable
   while everything behind is not. */
.bell-backdrop {
  position: fixed;
  inset: 0;
  z-index: 29;
}

.bell-panel {
  position: absolute;
  top: calc(100% - 6px);
  right: 16px;
  z-index: 31;
  width: min(360px, calc(100vw - 32px));
  max-height: min(70vh, 460px);
  overflow-y: auto;
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: inset 0 0 0 1px var(--border), var(--shadow-lg);
}

.bell-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--border-soft);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}

.bell-mark {
  border: 0;
  background: none;
  padding: 0;
  color: var(--accent);
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
}

.bell-empty {
  margin: 0;
  padding: 18px 14px;
  color: var(--text-muted);
  font-size: 12.5px;
}

.bell-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 11px 14px;
  border: 0;
  border-bottom: 1px solid var(--border-soft);
  background: none;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.bell-item:last-child {
  border-bottom: 0;
}

.bell-item:hover {
  background: var(--row-hover);
}

/* Unread carries the accent rail; overdue overrides it in red. Read items keep
   their text and lose the rail, so the panel stays a history rather than
   emptying itself. */
.bell-item.unread {
  box-shadow: inset 3px 0 0 var(--accent);
}

.bell-item.unread.overdue {
  box-shadow: inset 3px 0 0 var(--red);
}

.bell-item:not(.unread) .bell-item-title {
  color: var(--text-muted);
  font-weight: 500;
}

.bell-item-title {
  font-size: 13px;
  font-weight: 600;
}

.bell-item-body {
  font-size: 12px;
  color: var(--text-muted);
  white-space: pre-wrap;
}

.bell-item-time {
  font-size: 11px;
  color: var(--text-dim);
}

/* Above the page's own toasts, which are positioned by the global .toast. */
.app-toast {
  z-index: 50;
  cursor: pointer;
}

.global-search {
  position: relative;
  margin-right: 14px;
}
.search-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.search-cancel {
  flex-shrink: 0;
  border: none;
  background: none;
  color: var(--accent);
  font-family: inherit;
  font-size: 15px;
  padding: 10px 4px;
  cursor: pointer;
}
.search-input {
  width: 280px;
  height: var(--control-h);
  padding: 0 14px;
  border-radius: var(--r-pill);
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text);
  font-size: 13px;
}
.search-input:focus {
  border-color: var(--accent);
  box-shadow: var(--ring);
}
/*
 * On a phone the search box is hidden until the magnifier is tapped, then it
 * takes the whole screen: a 280px input plus a 400px dropdown has nowhere to
 * sit in a 375px bar, and anchoring the dropdown to the right edge would put
 * most of it off-screen.
 *
 * The markup is the same in both cases — only this container changes — so the
 * results list, its grouping and its keyboard handling have one implementation.
 *
 * Mirrors MOBILE in src/breakpoints.ts.
 */
@media (max-width: 899px) {
  /* Scoped styles outrank the base `input` rule in style.css, so the 16px that
     stops iOS zooming on focus has to be repeated here. */
  .search-input {
    font-size: 16px;
  }

  .global-search {
    display: none;
  }

  .global-search.search-sheet {
    display: flex;
    flex-direction: column;
    position: fixed;
    inset: 0;
    z-index: 250;
    margin: 0;
    padding: 10px;
    padding-top: max(10px, env(safe-area-inset-top));
    padding-left: max(10px, env(safe-area-inset-left));
    padding-right: max(10px, env(safe-area-inset-right));
    background: var(--bg);
  }

  .search-sheet .search-input {
    width: 100%;
    height: 44px;
  }

  /* In the sheet the results are the body of the screen, not a popover. */
  .search-sheet .search-dropdown {
    position: static;
    width: auto;
    max-height: none;
    flex: 1;
    margin-top: 10px;
    padding: 4px;
    border: none;
    background: transparent;
    box-shadow: none;
    animation: none;
  }

  .search-sheet .search-group-label {
    background: var(--bg);
  }

  .search-sheet .search-item {
    padding: 12px 10px;
  }

  .search-sheet .search-item-title {
    font-size: 15px;
  }
}
.search-dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 400px;
  max-height: min(70vh, 620px);
  overflow-y: auto;
  overscroll-behavior: contain;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-lg);
  padding: 8px;
  z-index: 200;
  animation: search-pop 130ms var(--ease);
}
@keyframes search-pop {
  from {
    opacity: 0;
    transform: translateY(-6px);
  }
}
.search-status {
  color: var(--text-dim);
  font-size: 13px;
  padding: 10px 8px;
  margin: 0;
}
.search-group + .search-group {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid var(--border-soft);
}
.search-group-label {
  /* Sticky so the group a row belongs to stays visible while scrolling a
     long result list. */
  position: sticky;
  top: 0;
  background: var(--surface-2);
  font-size: 10.5px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--text-dim);
  padding: 6px 8px 4px;
}
.search-item {
  display: block;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  border-radius: var(--r-sm);
  padding: 7px 9px;
  cursor: pointer;
  font-family: inherit;
  transition: background var(--dur) var(--ease);
}
@media (hover: hover) {
  .search-item:hover {
    background: var(--surface-3);
  }
}
.search-item-title {
  display: block;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
}
.search-item-sub {
  display: block;
  color: var(--text-dim);
  font-size: 12px;
  margin-top: 1px;
}
.user-name {
  text-decoration: none;
  color: inherit;
  border-radius: var(--r-pill);
  transition: color var(--dur) var(--ease);
}
@media (hover: hover) {
  .user-name:hover strong {
    color: var(--accent);
  }
}
.user-role {
  display: inline-block;
  margin-left: 6px;
  padding: 2px 8px;
  border-radius: var(--r-pill);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  font-size: 11px;
  text-transform: capitalize;
}
</style>
