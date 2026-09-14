import { defineStore } from 'pinia'
import { api, getToken, setToken } from '../api/client'

export interface User {
  id: string
  username: string
  email: string | null
  auth_provider: string
  role: 'admin' | 'tester' | 'readonly'
  is_active: boolean
}

interface AuthState {
  user: User | null
  loaded: boolean
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({ user: null, loaded: false }),
  getters: {
    canWrite: (s) => s.user?.role === 'admin' || s.user?.role === 'tester',
    isAdmin: (s) => s.user?.role === 'admin',
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
