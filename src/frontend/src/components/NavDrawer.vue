<script setup lang="ts">
/**
 * The mobile navigation panel.
 *
 * Below MOBILE the top bar has no room for the nav pills, the username or the
 * log out button, so they move in here. A drawer rather than a bottom tab bar
 * because an admin has seven destinations — five links plus Profile and Log
 * out — and a tab bar holds about five before it needs an overflow slot of its
 * own.
 *
 * The links are passed in rather than declared here: App.vue owns the list and
 * renders the same one into the desktop bar, so there is a single source of
 * truth for what the navigation contains.
 */
import { onBeforeUnmount, ref, watch } from 'vue'

export interface NavLink {
  to: string
  label: string
}

const props = defineProps<{
  open: boolean
  links: NavLink[]
  deviceLinks: NavLink[]
  username?: string
  role?: string
  /** Unread notifications. The bell is in the top bar, which is not here. */
  notificationCount?: number
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'logout'): void
  (e: 'notifications'): void
}>()

const panel = ref<HTMLElement | null>(null)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}

watch(
  () => props.open,
  (open) => {
    document.removeEventListener('keydown', onKeydown)
    if (open) {
      document.addEventListener('keydown', onKeydown)
      // Move focus into the panel so a keyboard or screen-reader user lands
      // inside what just opened rather than back at the top of the page.
      requestAnimationFrame(() => panel.value?.focus())
    }
  },
)

onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Transition name="drawer">
    <div v-if="open" class="drawer-root">
      <div class="drawer-backdrop" @click="emit('close')"></div>
      <aside
        ref="panel"
        class="drawer-panel"
        tabindex="-1"
        role="dialog"
        aria-modal="true"
        aria-label="Navigation"
      >
        <div class="drawer-head">
          <span class="drawer-title">Menu</span>
          <button class="drawer-close" type="button" aria-label="Close menu" @click="emit('close')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <nav class="drawer-nav">
          <details class="drawer-group" :open="$route.path === '/' || $route.path.startsWith('/devices')">
            <summary class="drawer-link">Devices</summary>
            <router-link v-for="l in deviceLinks" :key="l.to" :to="l.to" class="drawer-link drawer-sublink">
              {{ l.label }}
            </router-link>
          </details>
          <router-link v-for="l in links" :key="l.to" :to="l.to" class="drawer-link">
            {{ l.label }}
          </router-link>
          <!-- The top bar collapses to the hamburger on a phone, and the bell
               collapses with it. Without this entry there is no way to reach a
               notification from a phone at all. -->
          <button type="button" class="drawer-link drawer-bell" @click="emit('notifications')">
            Notifications
            <span v-if="notificationCount" class="drawer-count">{{ notificationCount }}</span>
          </button>
        </nav>

        <div class="drawer-foot">
          <router-link to="/profile" class="drawer-user">
            <strong>{{ username }}</strong>
            <span class="drawer-role">{{ role }}</span>
          </router-link>
          <button class="btn" type="button" @click="emit('logout')">Log out</button>
        </div>
      </aside>
    </div>
  </Transition>
</template>

<style scoped>
.drawer-root {
  position: fixed;
  inset: 0;
  z-index: 300;
}

.drawer-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}

.drawer-panel {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: min(300px, 84vw);
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border-right: 1px solid var(--border);
  box-shadow: var(--shadow-lg);
  outline: none;
  /* Clears the notch in landscape and the home indicator at the bottom. */
  padding-left: env(safe-area-inset-left);
  padding-bottom: env(safe-area-inset-bottom);
}

.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 12px 14px 18px;
  padding-top: max(14px, env(safe-area-inset-top));
  border-bottom: 1px solid var(--border-soft);
}

.drawer-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.drawer-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  border: none;
  background: transparent;
  border-radius: var(--r-md);
  color: var(--text-muted);
  cursor: pointer;
}

.drawer-close svg {
  width: 20px;
  height: 20px;
}

.drawer-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px;
  overflow-y: auto;
  overscroll-behavior: contain;
  flex: 1;
}

.drawer-link {
  display: flex;
  align-items: center;
  min-height: 48px;
  padding: 0 14px;
  border-radius: var(--r-md);
  color: var(--text);
  text-decoration: none;
  font-size: 15px;
  font-weight: 500;
}

/* A button among the links, so it has to be talked back into looking like one. */
.drawer-bell {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  border: 0;
  background: none;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.drawer-count {
  min-width: 18px;
  padding: 0 5px;
  border-radius: var(--r-pill);
  background: var(--red);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
}

.drawer-link.router-link-active {
  background: var(--accent-a16);
  color: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent-a24);
}

.drawer-group summary { cursor: pointer; list-style: none; }
.drawer-group summary::-webkit-details-marker { display: none; }
.drawer-group summary::after { content: '▾'; margin-left: auto; transition: transform 120ms ease; }
.drawer-group:not([open]) summary::after { transform: rotate(-90deg); }
.drawer-sublink { min-height: 42px; margin-left: 18px; padding-left: 18px; font-size: 14px; }

.drawer-foot {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-top: 1px solid var(--border-soft);
}

.drawer-user {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 44px;
  padding: 0 4px;
  color: var(--text-muted);
  text-decoration: none;
  font-size: 14px;
}

.drawer-user strong {
  color: var(--text);
  font-weight: 600;
}

.drawer-role {
  padding: 2px 8px;
  border-radius: var(--r-pill);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  font-size: 11px;
  text-transform: capitalize;
}

/* The panel slides, the backdrop fades. Both are suppressed by the global
   prefers-reduced-motion rule in style.css. */
.drawer-enter-active .drawer-panel,
.drawer-leave-active .drawer-panel {
  transition: transform 180ms var(--ease);
}

.drawer-enter-from .drawer-panel,
.drawer-leave-to .drawer-panel {
  transform: translateX(-100%);
}

.drawer-enter-active .drawer-backdrop,
.drawer-leave-active .drawer-backdrop {
  transition: opacity 180ms var(--ease);
}

.drawer-enter-from .drawer-backdrop,
.drawer-leave-to .drawer-backdrop {
  opacity: 0;
}
</style>
