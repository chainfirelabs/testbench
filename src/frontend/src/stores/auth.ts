import { defineStore } from 'pinia'
import { api, getToken, setToken } from '../api/client'

export interface User {
  id: string
  username: string
  email: string | null
  auth_provider: string
  /*
   * A role's slug. Not a union of three any more: an installation defines its
   * own roles, so the name is a label, and what the holder may do is
   * `permissions` below. Anything deciding what to show reads that.
   */
  role: string
  is_active: boolean
  /** What this user's role grants, as the API resolved it (see services/permissions.py). */
  permissions: string[]
}

interface AuthState {
  user: User | null
  loaded: boolean
}

/** Mirrors the permission keys in backend/app/services/permissions.py. */
export const PERMISSION = {
  devicesEdit: 'devices.edit',
  softwareEdit: 'software.edit',
  testsEdit: 'tests.edit',
  viewsSave: 'views.save',
  auditView: 'audit.view',
  usersManage: 'users.manage',
  schemaManage: 'schema.manage',
  pluginsManage: 'plugins.manage',
  settingsManage: 'settings.manage',
} as const

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({ user: null, loaded: false }),
  getters: {
    /**
     * Whether this user holds a permission.
     *
     * Everything that hides a button asks this rather than comparing role
     * names: the name stopped being a reliable answer the moment installations
     * could define roles of their own, and the API decides by permission too,
     * so asking the same question keeps the button and the endpoint in step.
     */
    can: (s) => (permission: string) => (s.user?.permissions || []).includes(permission),
    /**
     * Shorthand for "may change the inventory", which is what the list pages
     * mean by editable: the grids on Devices, Software and Tests each check
     * their own permission for the destructive buttons, but they all share one
     * "is this grid editable at all" switch.
     */
    canWrite: (s) =>
      (s.user?.permissions || []).some((permission) =>
        [PERMISSION.devicesEdit, PERMISSION.softwareEdit, PERMISSION.testsEdit].includes(
          permission as never,
        ),
      ),
    /** Holds everything there is — what "admin" used to mean by name. */
    isAdmin: (s) =>
      Object.values(PERMISSION).every((permission) =>
        (s.user?.permissions || []).includes(permission),
      ),
  },
  actions: {
    async login(username: string, password: string) {
      const res = await api<{ access_token: string; user: User }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      this.user = res.user
      setToken(res.access_token)
    },
    async loadMe() {
      if (!getToken()) {
        this.loaded = true
        return
      }
      try {
        this.user = await api<User>('/auth/me')
      } catch {
        setToken(null)
        this.user = null
      } finally {
        this.loaded = true
      }
    },
    logout() {
      setToken(null)
      this.user = null
    },
  },
})
