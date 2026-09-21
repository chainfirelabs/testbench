<script setup lang="ts">
/**
 * Define what a role is called and what it lets people do.
 *
 * A role used to be one of three names the application knew about; it is a row
 * now, and what it grants is the set of permissions ticked here. That is the
 * point of the screen: an installation that wants somebody who reads the audit
 * trail without also being able to reset passwords writes that role itself
 * instead of handing out `admin`.
 *
 * Deliberately not a grid. There are a handful of roles and each carries a
 * dozen checkboxes; a table of nine boolean columns is a worse way to read
 * "what does Auditor actually do?" than a card per role with its permissions
 * grouped under it.
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'

export interface Role {
  id: string
  slug: string
  name: string
  description: string | null
  permissions: string[]
  is_builtin: boolean
  user_count: number
}

interface Permission {
  key: string
  label: string
  description: string
  group: string
}

const emit = defineEmits<{
  (e: 'toast', message: string, isError?: boolean): void
  /** A role's permissions changed; the signed-in user's own may have too. */
  (e: 'changed'): void
}>()

const props = withDefaults(
  defineProps<{
    /** Whether this user may change anything here, or only read it. */
    editable?: boolean
  }>(),
  { editable: false },
)

const roles = ref<Role[]>([])
const permissions = ref<Permission[]>([])
const loading = ref(true)
const error = ref('')
const saving = ref('')
const expanded = ref<string[]>([])

/** Unsaved edits, by role id. A role with no entry is showing what is stored. */
const drafts = ref<Record<string, { name: string; description: string; permissions: string[] }>>({})

const creating = ref(false)
const draftNew = ref({ slug: '', name: '', description: '', permissions: [] as string[] })

/** Permissions in the order the API lists them, grouped for the screen. */
const groups = computed(() => {
  const out: { name: string; items: Permission[] }[] = []
  for (const permission of permissions.value) {
    const group = out.find((item) => item.name === permission.group)
    if (group) group.items.push(permission)
    else out.push({ name: permission.group, items: [permission] })
  }
  return out
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [roleList, permissionList] = await Promise.all([
      api<Role[]>('/roles'),
      api<Permission[]>('/roles/permissions'),
    ])
    roles.value = roleList
    permissions.value = permissionList
    drafts.value = {}
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function draftFor(role: Role) {
  if (!drafts.value[role.id]) {
    drafts.value[role.id] = {
      name: role.name,
      description: role.description || '',
      permissions: [...role.permissions],
    }
  }
  return drafts.value[role.id]
}

function isDirty(role: Role): boolean {
  const draft = drafts.value[role.id]
  if (!draft) return false
  return (
    draft.name !== role.name ||
    draft.description !== (role.description || '') ||
    draft.permissions.slice().sort().join() !== role.permissions.slice().sort().join()
  )
}

function togglePermission(list: string[], key: string, on: boolean) {
  const at = list.indexOf(key)
  if (on && at === -1) list.push(key)
  if (!on && at !== -1) list.splice(at, 1)
}

function toggleExpanded(id: string) {
  const at = expanded.value.indexOf(id)
  if (at === -1) expanded.value.push(id)
  else expanded.value.splice(at, 1)
}

async function saveRole(role: Role) {
  const draft = drafts.value[role.id]
  if (!draft) return
  saving.value = role.id
  try {
    const updated = await api<Role>(`/roles/${role.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        permissions: draft.permissions,
      }),
    })
    Object.assign(role, updated)
    delete drafts.value[role.id]
    emit('toast', `${updated.name} saved`)
    // The person saving may have just changed their own role's permissions.
    emit('changed')
  } catch (e: any) {
    emit('toast', e.message, true)
  } finally {
    saving.value = ''
  }
}

function revert(role: Role) {
  delete drafts.value[role.id]
}

async function createRole() {
  const slug = draftNew.value.slug.trim()
  const name = draftNew.value.name.trim()
  if (!slug || !name) {
    emit('toast', 'A role needs a name and an identifier', true)
    return
  }
  saving.value = 'new'
  try {
    const created = await api<Role>('/roles', {
      method: 'POST',
      body: JSON.stringify({
        slug,
        name,
        description: draftNew.value.description.trim() || null,
        permissions: draftNew.value.permissions,
      }),
    })
    roles.value.push(created)
    expanded.value.push(created.id)
    creating.value = false
    draftNew.value = { slug: '', name: '', description: '', permissions: [] }
    emit('toast', `Role ${created.name} created`)
  } catch (e: any) {
    emit('toast', e.message, true)
  } finally {
    saving.value = ''
  }
}

async function removeRole(role: Role) {
  if (!confirm(`Delete the ${role.name} role?`)) return
  saving.value = role.id
  try {
    await api(`/roles/${role.id}`, { method: 'DELETE' })
    roles.value = roles.value.filter((item) => item.id !== role.id)
    emit('toast', `Role ${role.name} deleted`)
  } catch (e: any) {
    emit('toast', e.message, true)
  } finally {
    saving.value = ''
  }
}

/** The slug a name would get, so the field can fill itself in. */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 50)
}

function onNewName(value: string) {
  const previous = slugify(draftNew.value.name)
  draftNew.value.name = value
  // Stop guessing once the identifier has been typed by hand.
  if (!draftNew.value.slug || draftNew.value.slug === previous) {
    draftNew.value.slug = slugify(value)
  }
}

/** What a role grants, in one line, for the collapsed card. */
function summary(role: Role): string {
  if (!role.permissions.length) return 'Read only — grants nothing beyond looking.'
  const labels = role.permissions
    .map((key) => permissions.value.find((item) => item.key === key)?.label || key)
  return labels.join(', ')
}

onMounted(load)

defineExpose({ load })
</script>

<template>
  <div class="roles-panel">
    <div class="roles-header">
      <p class="muted intro">
        A role is a set of permissions. The three built-in roles can be renamed
        and their permissions changed, but they cannot be deleted — and the
        permission to manage users and roles cannot be taken off the last role
        that has it, since that would leave nobody able to put it back.
      </p>
      <button
        v-if="editable && !creating"
        class="btn btn-primary"
        type="button"
        @click="creating = true"
      >
        + New role
      </button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading" class="muted">Loading…</p>

    <form v-if="creating" class="role-card new-role" @submit.prevent="createRole">
      <div class="role-head">
        <strong>New role</strong>
      </div>
      <div class="role-fields">
        <label>
          Name
          <input
            :value="draftNew.name"
            placeholder="Auditor"
            @input="onNewName(($event.target as HTMLInputElement).value)"
          />
        </label>
        <label>
          Identifier
          <input v-model="draftNew.slug" placeholder="auditor" />
          <small class="muted">
            What accounts and API keys carry. It cannot be changed later.
          </small>
        </label>
        <label class="wide">
          Description
          <input v-model="draftNew.description" placeholder="Reads the audit trail." />
        </label>
      </div>
      <div class="permission-groups">
        <fieldset v-for="group in groups" :key="group.name">
          <legend>{{ group.name }}</legend>
          <label v-for="item in group.items" :key="item.key" class="permission">
            <input
              type="checkbox"
              :checked="draftNew.permissions.includes(item.key)"
              @change="togglePermission(draftNew.permissions, item.key, ($event.target as HTMLInputElement).checked)"
            />
            <span>
              <strong>{{ item.label }}</strong>
              <small class="muted">{{ item.description }}</small>
            </span>
          </label>
        </fieldset>
      </div>
      <div class="role-actions">
        <button class="btn btn-primary" type="submit" :disabled="saving === 'new'">
          {{ saving === 'new' ? 'Creating…' : 'Create role' }}
        </button>
        <button class="btn" type="button" @click="creating = false">Cancel</button>
      </div>
    </form>

    <div v-for="role in roles" :key="role.id" class="role-card">
      <button class="role-head" type="button" @click="toggleExpanded(role.id)">
        <span class="role-name">
          <strong>{{ role.name }}</strong>
          <code>{{ role.slug }}</code>
          <span v-if="role.is_builtin" class="role-tag">Built in</span>
        </span>
        <span class="role-meta">
          {{ role.user_count }} {{ role.user_count === 1 ? 'account' : 'accounts' }}
          <span class="chevron">{{ expanded.includes(role.id) ? '▾' : '▸' }}</span>
        </span>
      </button>
      <p class="role-summary muted">{{ role.description || summary(role) }}</p>

      <template v-if="expanded.includes(role.id)">
        <div v-if="editable" class="role-fields">
          <label>
            Name
            <input v-model="draftFor(role).name" />
          </label>
          <label class="wide">
            Description
            <input v-model="draftFor(role).description" />
          </label>
        </div>
        <div class="permission-groups">
          <fieldset v-for="group in groups" :key="group.name">
            <legend>{{ group.name }}</legend>
            <label v-for="item in group.items" :key="item.key" class="permission">
              <input
                type="checkbox"
                :disabled="!editable"
                :checked="(editable ? draftFor(role).permissions : role.permissions).includes(item.key)"
                @change="togglePermission(draftFor(role).permissions, item.key, ($event.target as HTMLInputElement).checked)"
              />
              <span>
                <strong>{{ item.label }}</strong>
                <small class="muted">{{ item.description }}</small>
              </span>
            </label>
          </fieldset>
        </div>
        <div v-if="editable" class="role-actions">
          <button
            class="btn btn-primary"
            type="button"
            :disabled="!isDirty(role) || saving === role.id"
            @click="saveRole(role)"
          >
            {{ saving === role.id ? 'Saving…' : 'Save' }}
          </button>
          <button class="btn" type="button" :disabled="!isDirty(role)" @click="revert(role)">
            Revert
          </button>
          <button
            v-if="!role.is_builtin"
            class="btn danger"
            type="button"
            :disabled="saving === role.id"
            :title="role.user_count
              ? 'Move the accounts holding this role to another one first'
              : `Delete the ${role.name} role`"
            @click="removeRole(role)"
          >
            Delete
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.roles-panel {
  display: grid;
  gap: 12px;
}

.roles-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.intro {
  margin: 0;
  max-width: 80ch;
}

.role-card {
  padding: 12px 14px;
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  background: var(--surface);
}

.new-role {
  border-color: var(--accent-a45);
}

/* The whole header is the disclosure control, so it is a button — but it has to
   lay out like a row, not like a pill. */
.role-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.role-name {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
}

.role-name code {
  color: var(--text-muted);
  font-size: 12px;
}

.role-tag {
  padding: 1px 8px;
  border-radius: var(--r-pill);
  background: var(--surface-3);
  color: var(--text-muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.role-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  color: var(--text-muted);
  font-size: 12px;
  white-space: nowrap;
}

.role-summary {
  margin: 6px 0 0;
  font-size: 13px;
}

.role-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.role-fields label {
  display: grid;
  gap: 4px;
  font-size: 12px;
}

.role-fields .wide {
  grid-column: 1 / -1;
}

.role-fields input {
  width: 100%;
}

.permission-groups {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.permission-groups fieldset {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
}

.permission-groups legend {
  padding: 0 6px;
  color: var(--text-muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.permission {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 5px 0;
  cursor: pointer;
}

.permission input {
  margin-top: 3px;
  flex: 0 0 auto;
}

.permission span {
  display: grid;
  gap: 2px;
}

.permission strong {
  font-size: 13px;
  font-weight: 550;
  color: var(--text);
}

.permission small {
  font-size: 12px;
  line-height: 1.4;
}

.role-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

@media (max-width: 640px) {
  .roles-header {
    flex-direction: column;
  }
}
</style>
