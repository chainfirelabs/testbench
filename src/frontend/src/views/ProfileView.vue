<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import { rolesUpTo } from '../constants'
import { useAuthStore } from '../stores/auth'
import { useNotificationsStore, type Notification } from '../stores/notifications'

interface ApiKey {
  id: string
  label: string
  prefix: string
  role: string
  created_at: string
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  is_active: boolean
}

const auth = useAuthStore()

const keys = ref<ApiKey[]>([])
const loading = ref(true)
const creating = ref(false)
const revoking = ref<Set<string>>(new Set())
const toast = ref('')
const toastError = ref(false)

const label = ref('')
const role = ref('readonly')
const expiresIn = ref<string>('90')

// Only the roles this user may actually grant. The backend enforces the same
// ceiling; this just keeps the UI from offering a choice that would 403.
const roleOptions = computed(() => rolesUpTo(auth.user?.role))

// The plaintext, held in memory only for as long as this page is open. There is
// no endpoint that can return it again.
const revealed = ref<{ label: string; plaintext: string } | null>(null)
const copied = ref(false)

const EXPIRY_CHOICES = [
  { value: '30', text: '30 days' },
  { value: '90', text: '90 days' },
  { value: '365', text: '1 year' },
  { value: '', text: 'Never' },
]

function fmt(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

function notify(message: string, isError = false) {
  toast.value = message
  toastError.value = isError
  setTimeout(() => (toast.value = ''), 4000)
}

function statusOf(key: ApiKey): { text: string; cls: string } {
  if (key.revoked_at) return { text: 'Revoked', cls: 'off' }
  if (!key.is_active) return { text: 'Expired', cls: 'off' }
  return { text: 'Active', cls: 'ok' }
}

async function load() {
  loading.value = true
  try {
    keys.value = await api<ApiKey[]>('/auth/api-keys')
  } catch (e: any) {
    notify(e.message, true)
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!label.value.trim() || creating.value) return
  creating.value = true
  try {
    const res = await api<{ key: ApiKey; plaintext: string }>('/auth/api-keys', {
      method: 'POST',
      body: JSON.stringify({
        label: label.value.trim(),
        role: role.value,
        expires_in_days: expiresIn.value ? Number(expiresIn.value) : null,
      }),
    })
    keys.value.unshift(res.key)
    revealed.value = { label: res.key.label, plaintext: res.plaintext }
    copied.value = false
    label.value = ''
    role.value = 'readonly'
  } catch (e: any) {
    notify(e.message, true)
  } finally {
    creating.value = false
  }
}

async function copyKey() {
  if (!revealed.value) return
  try {
    await navigator.clipboard.writeText(revealed.value.plaintext)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    // Clipboard access needs a secure context; over plain HTTP it throws and
    // the user has to select the text themselves, which is why it stays visible.
    notify('Could not reach the clipboard — select the key and copy it manually.', true)
  }
}

async function revoke(key: ApiKey) {
  if (revoking.value.has(key.id)) return
  if (!confirm(`Revoke “${key.label}”? Anything using this key stops working immediately.`)) return
  revoking.value.add(key.id)
  try {
    await api(`/auth/api-keys/${key.id}`, { method: 'DELETE' })
    key.revoked_at = new Date().toISOString()
    key.is_active = false
    notify(`Revoked “${key.label}”.`)
  } catch (e: any) {
    notify(e.message, true)
  } finally {
    revoking.value.delete(key.id)
  }
}

// ---------- notification archive ----------
/*
 * The bell holds what is recent; this is the whole history, read included, so
 * "what was I told last week" has an answer. Paged rather than capped — the
 * list only grows.
 */
const notifications = useNotificationsStore()
const notes = ref<Notification[]>([])
const loadingNotes = ref(true)
const notePage = ref(1)
const notePages = ref(1)
const NOTE_PAGE_SIZE = 20

async function loadNotes(page = 1) {
  loadingNotes.value = true
  try {
    const res = await api<{ items: Notification[]; total: number }>(
      `/notifications?page=${page}&page_size=${NOTE_PAGE_SIZE}`,
    )
    notes.value = res.items
    notePage.value = page
    notePages.value = Math.max(1, Math.ceil(res.total / NOTE_PAGE_SIZE))
  } catch (e: any) {
    notify(e.message, true)
  } finally {
    loadingNotes.value = false
  }
}

onMounted(() => {
  load()
  loadNotes()
  // Opening the archive is reading them. Leaving the badge up over a list the
  // user is looking at is the kind of thing that teaches people to ignore it.
  if (notifications.unread) notifications.markAllRead().then(() => loadNotes(notePage.value))
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2>Profile</h2>
    </div>

    <dl class="kv">
      <dt>Username</dt>
      <dd>{{ auth.user?.username }}</dd>
      <dt>Role</dt>
      <dd style="text-transform: capitalize">{{ auth.user?.role }}</dd>
      <dt>Email</dt>
      <dd>{{ auth.user?.email || '—' }}</dd>
      <dt>Sign-in method</dt>
      <dd>{{ auth.user?.auth_provider === 'local' ? 'Local account' : 'SSO (Authentik)' }}</dd>
    </dl>

    <h3 class="keys-heading">API keys</h3>
    <p class="muted" style="margin-top: 0; max-width: 90ch">
      An API key authenticates against <code>/api/v1</code> exactly like a browser session, but
      does not expire in eight hours — send it as
      <code>Authorization: Bearer tb_…</code>. A key can carry your role or any
      role below it, so a key handed to a script that only reads should be
      <strong>readonly</strong>. Actions taken with a key appear in the audit log
      under your name, alongside the key that took them.
    </p>

    <form class="create-key" @submit.prevent="create">
      <div class="form-grid">
        <label for="key-label">Label</label>
        <input
          id="key-label"
          v-model="label"
          type="text"
          maxlength="100"
          placeholder="e.g. mcp-server, ci-pipeline"
          required
        />
        <label for="key-role">Role</label>
        <select id="key-role" v-model="role">
          <option v-for="r in roleOptions" :key="r" :value="r">{{ r }}</option>
        </select>
        <label for="key-expiry">Expires</label>
        <select id="key-expiry" v-model="expiresIn">
          <option v-for="c in EXPIRY_CHOICES" :key="c.value" :value="c.value">{{ c.text }}</option>
        </select>
      </div>
      <button class="btn btn-primary" type="submit" :disabled="creating || !label.trim()">
        {{ creating ? 'Generating…' : 'Generate key' }}
      </button>
    </form>

    <div v-if="revealed" class="reveal">
      <div class="section-label">
        “{{ revealed.label }}” — copy it now, it will not be shown again
      </div>
      <pre class="code-block">{{ revealed.plaintext }}</pre>
      <div class="reveal-actions">
        <button class="btn btn-mini" type="button" @click="copyKey">
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
        <button class="btn btn-mini" type="button" @click="revealed = null">Dismiss</button>
      </div>
    </div>

    <p v-if="loading" class="muted">Loading…</p>
    <p v-else-if="!keys.length" class="muted">No API keys yet.</p>
    <div v-else class="table-scroll">
    <table class="keys-table">
      <thead>
        <tr>
          <th>Label</th>
          <th>Prefix</th>
          <th>Role</th>
          <th>Created</th>
          <th>Last used</th>
          <th>Expires</th>
          <th>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="key in keys" :key="key.id" :class="{ dim: !key.is_active }">
          <td class="row-heading" data-label="Label">{{ key.label }}</td>
          <td class="mono" data-label="Prefix">tb_{{ key.prefix }}…</td>
          <td style="text-transform: capitalize" data-label="Role">{{ key.role }}</td>
          <td data-label="Created">{{ fmt(key.created_at) }}</td>
          <td data-label="Last used">{{ fmt(key.last_used_at) }}</td>
          <td data-label="Expires">{{ key.expires_at ? fmt(key.expires_at) : 'Never' }}</td>
          <td data-label="Status"><span class="row-badge" :class="statusOf(key).cls">{{ statusOf(key).text }}</span></td>
          <td class="key-actions" style="text-align: right">
            <button
              v-if="key.is_active"
              class="btn btn-mini btn-danger"
              type="button"
              :disabled="revoking.has(key.id)"
              @click="revoke(key)"
            >
              Revoke
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    </div>

    <h3 class="keys-heading">Notifications</h3>
    <p class="muted" style="margin-top: 0; max-width: 90ch">
      Everything you have been sent, read or not — the bell in the header keeps
      only what is recent. Checkout reminders arrive here daily from three days
      before a device is due back, and every day it stays out after that.
    </p>

    <p v-if="loadingNotes" class="muted">Loading…</p>
    <p v-else-if="!notes.length" class="muted">Nothing yet.</p>
    <div v-else class="table-scroll">
      <table class="keys-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Notification</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="n in notes" :key="n.id" :class="{ dim: !!n.read_at }">
            <td data-label="When">{{ fmt(n.created_at) }}</td>
            <td class="row-heading" data-label="Notification">
              {{ n.title }}
              <span v-if="n.body" class="note-body">{{ n.body }}</span>
            </td>
            <td data-label="Status">
              <span class="row-badge" :class="n.read_at ? 'off' : 'error'">
                {{ n.read_at ? 'Read' : 'Unread' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="notePages > 1" class="note-paging">
      <button class="btn btn-mini" type="button" :disabled="notePage <= 1" @click="loadNotes(notePage - 1)">
        Previous
      </button>
      <span class="muted">Page {{ notePage }} of {{ notePages }}</span>
      <button class="btn btn-mini" type="button" :disabled="notePage >= notePages" @click="loadNotes(notePage + 1)">
        Next
      </button>
    </div>

    <div v-if="toast" class="toast" :class="{ 'toast-error': toastError }">{{ toast }}</div>
  </div>
</template>

<style scoped>
.keys-heading {
  margin: 28px 0 6px;
  font-size: 16px;
}
/* The digest lists a device per line, so its newlines have to survive. */
.note-body {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
  font-weight: 400;
  font-size: 12px;
  white-space: pre-wrap;
}
.note-paging {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  font-size: 12.5px;
}
.create-key {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
  margin: 14px 0 4px;
}
.reveal {
  margin: 16px 0 4px;
  max-width: 760px;
  padding: 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: var(--r-md);
}
.reveal .code-block {
  min-height: 0;
}
.reveal-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
/*
 * Eight columns of dates and identifiers, which is more than a phone can show.
 * Below STACK each key becomes a labelled block; between there and MOBILE the
 * .table-scroll wrapper lets it scroll in its own box.
 *
 * Mirrors STACK in src/breakpoints.ts.
 */
@media (max-width: 639px) {
  .keys-table thead {
    display: none;
  }

  .keys-table,
  .keys-table tbody,
  .keys-table tr,
  .keys-table td {
    display: block;
    width: 100%;
  }

  .keys-table tbody tr {
    border: 1px solid var(--border-soft);
    border-radius: var(--r-sm);
    background: var(--surface-2);
    padding: 9px 11px;
    margin-bottom: 8px;
  }

  .keys-table td {
    border: none;
    padding: 3px 0;
    display: grid;
    grid-template-columns: minmax(84px, 33%) 1fr;
    gap: 10px;
    align-items: baseline;
  }

  .keys-table td::before {
    content: attr(data-label);
    color: var(--text-muted);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .keys-table .row-heading {
    display: block;
    font-size: 15px;
    font-weight: 600;
    padding: 0 0 6px;
  }

  .keys-table .row-heading::before {
    content: none;
  }

  /* Revoke is the block's action, so it spans rather than sitting in a
     value column with an empty label beside it. */
  .keys-table .key-actions {
    display: block;
    text-align: left !important;
    padding-top: 8px;
  }

  .keys-table .key-actions::before {
    content: none;
  }
}

.keys-table {
  margin-top: 18px;
  width: 100%;
  max-width: 1100px;
  border-collapse: collapse;
  font-size: 13px;
}
.keys-table th {
  text-align: left;
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
}
.keys-table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--border-soft);
}
.keys-table tr.dim td {
  color: var(--text-muted);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
</style>
