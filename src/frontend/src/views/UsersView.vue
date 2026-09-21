<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DataTable, { type RemoteTableRequest, type RowAction } from '../components/DataTable.vue'
import FilterProfilesMenu from '../components/FilterProfilesMenu.vue'
import FormModal, { type FormField } from '../components/FormModal.vue'
import RolesPanel from '../components/RolesPanel.vue'
import { api } from '../api/client'
import { MIN_PASSWORD_LENGTH } from '../constants'
import { PERMISSION, useAuthStore } from '../stores/auth'
import { remoteTableParams } from '../remoteTable'
import { makeFilterValues } from '../suggestions'

const auth = useAuthStore()
const rows = ref<any[]>([])
const toast = ref('')
const toastError = ref(false)
const profiles = ref<InstanceType<typeof FilterProfilesMenu> | null>(null)
const table = ref<InstanceType<typeof DataTable> | null>(null)
const toggling = ref<Set<string>>(new Set())

// The three dialogs. Each holds the row it is acting on (or `true` for the
// create dialog, which has no row yet) plus the values the form edits.
const showNew = ref(false)
const newUser = ref<Record<string, any>>({})
const creating = ref(false)

const resetTarget = ref<any | null>(null)
const resetValues = ref<Record<string, any>>({})
const resetting = ref(false)

const roleTarget = ref<any | null>(null)
const roleValues = ref<Record<string, any>>({})
const savingRole = ref(false)

const fmt = (p: any) => (p.value ? new Date(p.value).toLocaleString() : '')

/*
 * The roles an account can be given, fetched rather than hard-coded.
 *
 * Every role this installation has, not a ceiling: assigning a role is guarded
 * by `users.manage`, and somebody who holds that already has the power to give
 * it away. (Minting an API *key* is different — a key must never carry more
 * than its owner, so that list is narrowed; see ProfileView.)
 */
const allRoles = ref<{ slug: string; name: string; description: string | null; permissions: string[] }[]>([])

/**
 * How much a role grants, as a colour.
 *
 * Three bands rather than a name lookup: one for roles that can hand out roles,
 * one for roles that can change something, and grey for the rest. That keeps
 * the column meaningful for a role this page has never heard of, which is the
 * normal case once an installation defines its own.
 */
function roleColour(slug: string): string {
  const permissions = allRoles.value.find((role) => role.slug === slug)?.permissions || []
  if (permissions.includes(PERMISSION.usersManage)) return 'var(--accent)'
  return permissions.length ? '#60a5fa' : '#9ca3af'
}

/*
 * Built from `allRoles` rather than fixed, because the Role column colours a
 * row by what the role grants. It used to colour by name — orange for "admin",
 * blue for "tester" — which turns every role an installation defines into
 * undifferentiated grey however much it grants.
 */
const columns = computed(() => [
  { field: 'username', headerName: 'Username', minWidth: 140 },
  { field: 'email', headerName: 'Email', minWidth: 160, valueFormatter: (p: any) => p.value || '—' },
  { field: 'auth_provider', headerName: 'Provider' },
  {
    field: 'role',
    headerName: 'Role',
    valueFormatter: (p: any) =>
      allRoles.value.find((role) => role.slug === p.value)?.name || p.value,
    cellStyle: (p: any) => ({
      color: roleColour(p.value),
      fontWeight: 600,
    }),
  },
  {
    field: 'is_online',
    headerName: 'Session',
    cellRenderer: (p: any) => {
      const row = p.data
      // green = active now, red = logged in before (expired), gray = never logged in
      const state = row.is_online ? 'online' : row.last_login_at ? 'offline' : 'unknown'
      const el = document.createElement('span')
      el.className = `status-dot ${state}`
      el.title = state === 'online' ? 'Active now' : state === 'offline' ? 'Session expired' : 'Never logged in'
      return el
    },
  },
  { field: 'created_at', headerName: 'Created', valueFormatter: fmt, minWidth: 170 },
  { field: 'last_login_at', headerName: 'Last Login', valueFormatter: (p: any) => (p.value ? new Date(p.value).toLocaleString() : '—'), minWidth: 170 },
])

function showToast(msg: string, isError = false) {
  toast.value = msg
  toastError.value = isError
  setTimeout(() => (toast.value = ''), 4000)
}

/** Re-run filter and sort over rows whose values changed underneath them. */
async function load() {
  table.value?.reapplyView()
}

/**
 * Re-read the list from the server, row count and all.
 *
 * For an account created or removed: a server-paged grid keeps the row count
 * it was last given, so refreshing the blocks it holds cannot show a row that
 * did not exist when it was told how many there were.
 */
async function reloadRows() {
  table.value?.reload()
}

/*
 * Values for the column filters' checklists — the server's to answer, since
 * the grid only ever holds the window on screen.
 */
const filterValues = makeFilterValues({
  entity: 'users',
  local: (colId) => (colId === 'is_online' ? [true, false] : undefined),
  skip: ['created_at', 'last_login_at'],
})

async function loadRemoteUsers(request: RemoteTableRequest) {
  const params = remoteTableParams(request)
  const page = await api<any>(`/users/paged?${params}`)
  rows.value = page.items
  return { rows: page.items, total: page.total }
}

function onGridReady() {
  profiles.value?.applyDefault()
}

/**
 * Fold an API response back into a grid row.
 *
 * `is_online` is derived, and only by the list endpoint — every other response
 * carries its `false` default. A blind assign would therefore blank the session
 * dot on any row that had just been edited.
 */
function mergeRow(row: any, updated: any) {
  Object.assign(row, updated, { is_online: row.is_online })
}

async function toggleActive(row: any) {
  if (toggling.value.has(row.id)) return
  toggling.value.add(row.id)
  try {
    const updated = await api<any>(`/users/${row.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active: !row.is_active }),
    })
    mergeRow(row, updated)
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    toggling.value.delete(row.id)
  }
}

// ---------------------------------------------------------------------------
// Local accounts
// ---------------------------------------------------------------------------

const roleOptions = computed(() =>
  allRoles.value.map((role) => ({ value: role.slug, label: `${role.name} (${role.slug})` })),
)

const ROLE_HINT = computed(() => {
  const described = allRoles.value
    .filter((role) => role.description)
    .map((role) => `${role.name}: ${role.description}`)
  return described.length
    ? described.join(' · ')
    : 'What each role grants is set on the Roles tab.'
})

async function loadRoles() {
  try {
    allRoles.value = await api<any[]>('/roles')
  } catch {
    // The dialog is still usable: the field accepts the slug either way, and
    // the API rejects one that does not exist.
    allRoles.value = []
  }
}

const PASSWORD_FIELDS: FormField[] = [
  {
    key: 'password',
    label: 'Password',
    type: 'password',
    required: true,
    hint: `At least ${MIN_PASSWORD_LENGTH} characters. Nobody can read it back afterwards — pass it on now; changing it later means another reset from this page.`,
  },
  { key: 'confirm', label: 'Confirm password', type: 'password', required: true },
]

const NEW_USER_FIELDS = computed<FormField[]>(() => [
  {
    key: 'username',
    label: 'Username',
    required: true,
    placeholder: 'e.g. jdoe',
    hint: 'What they type at the sign-in form.',
  },
  ...PASSWORD_FIELDS,
  {
    key: 'role',
    label: 'Role',
    type: 'select',
    required: true,
    options: roleOptions.value,
    hint: ROLE_HINT.value,
  },
  { key: 'email', label: 'Email', placeholder: 'Optional' },
])

const ROLE_FIELDS = computed<FormField[]>(() => [
  { key: 'role', label: 'Role', type: 'select', required: true, options: roleOptions.value, hint: ROLE_HINT.value },
])

/**
 * What is wrong with the password just typed, or '' if nothing is.
 *
 * The length is the API's rule restated so the answer arrives before the round
 * trip; the confirmation is only ever checked here, because the API is never
 * told about it — it exists to catch a typo in a password the admin is about
 * to read out to someone.
 */
function passwordProblem(values: Record<string, any>): string {
  const password = String(values.password ?? '')
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters`
  }
  if (password !== String(values.confirm ?? '')) return 'The two passwords do not match'
  return ''
}

function openNew() {
  newUser.value = { username: '', password: '', confirm: '', role: 'readonly', email: '' }
  showNew.value = true
}

async function createUser(values: Record<string, any>) {
  const problem = passwordProblem(values)
  if (problem) return showToast(problem, true)
  creating.value = true
  try {
    // `confirm` is a property of this form, not of a user.
    const { confirm: _confirm, ...payload } = values
    const created = await api<any>('/users', { method: 'POST', body: JSON.stringify(payload) })
    showNew.value = false
    showToast(`Local user ${created.username} created`)
    // Reload rather than splice: the list comes back sorted by username, a
    // brand new row has derived fields (session state) only that endpoint
    // sets, and the grid has to be told the list grew.
    await reloadRows()
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    creating.value = false
  }
}

function openReset(row: any) {
  resetValues.value = { password: '', confirm: '' }
  resetTarget.value = row
}

async function submitReset(values: Record<string, any>) {
  const target = resetTarget.value
  if (!target) return
  const problem = passwordProblem(values)
  if (problem) return showToast(problem, true)
  resetting.value = true
  try {
    const updated = await api<any>(`/users/${target.id}/password`, {
      method: 'POST',
      body: JSON.stringify({ password: values.password }),
    })
    mergeRow(target, updated)
    resetTarget.value = null
    showToast(
      target.id === auth.user?.id
        ? 'Your password has been changed'
        : `Password reset for ${updated.username}`,
    )
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    resetting.value = false
  }
}

function openRole(row: any) {
  roleValues.value = { role: row.role }
  roleTarget.value = row
}

async function submitRole(values: Record<string, any>) {
  const target = roleTarget.value
  if (!target) return
  if (values.role === target.role) {
    roleTarget.value = null
    return
  }
  savingRole.value = true
  try {
    const updated = await api<any>(`/users/${target.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ role: values.role }),
    })
    roleTarget.value = null
    showToast(`${updated.username} is now ${updated.role}`)
    // The role is on screen, so the grid has to be told; reloading is what
    // makes the cell repaint.
    await load()
  } catch (e: any) {
    showToast(e.message, true)
  } finally {
    savingRole.value = false
  }
}

// Feather-style icons
const POWER_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>'
const KEY_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>'
const SHIELD_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>'

function extraActions(row: any): RowAction[] {
  // The route is admin-only, so anyone reading this page can act on it.
  if (!auth.can(PERMISSION.usersManage)) return []
  const actions: RowAction[] = []
  const isLocal = row.auth_provider === 'local'
  const isSelf = row.id === auth.user?.id

  if (isLocal) {
    // Allowed on yourself: this page is the only place a password can be
    // changed, so excluding your own account would strand it.
    actions.push({
      label: 'Reset password',
      title: isSelf ? 'Set a new password for your account' : `Set a new password for ${row.username}`,
      icon: KEY_ICON,
      onClick: () => openReset(row),
    })
  }
  if (isLocal && !isSelf) {
    // Local only: an SSO user's role is re-resolved from their Authentik
    // groups at every login, so an edit here would not survive the next one.
    actions.push({
      label: 'Change role',
      title: `Change ${row.username}'s role`,
      icon: SHIELD_ICON,
      onClick: () => openRole(row),
    })
  }
  if (!isSelf) {
    actions.push({
      label: row.is_active ? 'Deactivate' : 'Activate',
      title: row.is_active ? 'Deactivate this user' : 'Activate this user',
      icon: POWER_ICON,
      disabled: toggling.value.has(row.id),
      onClick: () => toggleActive(row),
    })
  }
  return actions
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
// Accounts and roles are two halves of the same question — who may do what —
// and splitting them across two pages means assigning a role and deciding what
// it grants are never on screen together.

const tab = ref<'accounts' | 'roles'>('accounts')
const rolesPanel = ref<InstanceType<typeof RolesPanel> | null>(null)

async function onRolesChanged() {
  // Editing a role can change what the person editing it may do, including
  // whether this very page stays reachable. Re-reading their own record keeps
  // the navigation honest rather than waiting for the next sign-in.
  await Promise.all([auth.loadMe(), loadRoles()])
  // A role's user count is on screen here, and a role change may have moved one.
  table.value?.reload()
}

onMounted(() => {
  load()
  loadRoles()
})
</script>

<template>
  <!--
    Two tabs with opposite layout needs, so the modifier follows the tab.

    Accounts holds a DataTable, which sizes itself to the panel and scrolls
    internally: that wants plain `.page`, one viewport tall. Roles is a
    document that grows — a card per role, each unfolding a dozen permission
    checkboxes — and under `.page`'s `flex: 1; min-height: 0` it was capped at
    viewport height and painted outside its own rounded border. `.page-flow`
    lets the panel grow to its content and leaves the scrolling to `.content`.
  -->
  <div class="page" :class="{ 'page-flow': tab === 'roles' }">
    <div class="page-header">
      <h2>Users &amp; Roles</h2>
      <div class="toolbar">
        <button
          v-if="tab === 'accounts' && auth.can(PERMISSION.usersManage)"
          class="btn btn-primary"
          @click="openNew"
        >
          + New user
        </button>
      </div>
    </div>
    <div class="tabs">
      <button :class="{ active: tab === 'accounts' }" @click="tab = 'accounts'">
        Accounts
      </button>
      <button :class="{ active: tab === 'roles' }" @click="tab = 'roles'">
        Roles ({{ allRoles.length || '…' }})
      </button>
    </div>

    <RolesPanel
      v-if="tab === 'roles'"
      ref="rolesPanel"
      :editable="auth.can(PERMISSION.usersManage)"
      @toast="(message, isError) => showToast(message, isError)"
      @changed="onRolesChanged"
    />

    <template v-else>
    <p class="muted" style="margin-top: 0; max-width: 90ch">
      Users sign in via SSO (Authentik) or a local account. SSO users are created
      automatically on first login and their role is mapped from their Authentik
      groups on every login — role changes in the IdP apply the next time the user
      signs in, and neither their role nor their password can be changed here.
      Local accounts are created and managed on this page: their password is set
      by an admin and there is no self-service reset. “Active now” means the user
      logged in within the token lifetime.
    </p>
    <DataTable
      ref="table"
      :columns="columns"
      :rows="rows"
      :remote-loader="loadRemoteUsers"
      :filter-values="filterValues"
      :extra-row-actions="extraActions"
      @grid-ready="onGridReady"
    >
      <template #table-actions>
        <FilterProfilesMenu
          ref="profiles"
          entity="users"
          :get-state="() => table?.getState()"
          :apply-state="(s) => table?.applyState(s)"
        />
      </template>
    </DataTable>

    </template>

    <FormModal
      v-if="showNew"
      title="New Local User"
      :fields="NEW_USER_FIELDS"
      :values="newUser"
      :busy="creating"
      @submit="createUser"
      @cancel="showNew = false"
    />
    <FormModal
      v-if="resetTarget"
      :title="`Reset Password — ${resetTarget.username}`"
      :fields="PASSWORD_FIELDS"
      :values="resetValues"
      :busy="resetting"
      submit-label="Set password"
      @submit="submitReset"
      @cancel="resetTarget = null"
    />
    <FormModal
      v-if="roleTarget"
      :title="`Change Role — ${roleTarget.username}`"
      :fields="ROLE_FIELDS"
      :values="roleValues"
      :busy="savingRole"
      submit-label="Save role"
      @submit="submitRole"
      @cancel="roleTarget = null"
    />

    <div v-if="toast" class="toast" :class="{ 'toast-error': toastError }">{{ toast }}</div>
  </div>
</template>
