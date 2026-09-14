import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
  {
    path: '/',
    component: () => import('../views/DevicesView.vue'),
    meta: { title: 'Devices' },
  },
  {
    path: '/devices/type/:typeKey',
    name: 'devices-by-type',
    component: () => import('../views/DevicesView.vue'),
    meta: { title: 'Devices' },
  },
  {
    path: '/devices/:id',
    name: 'device-detail',
    component: () => import('../views/DeviceDetailView.vue'),
    meta: { title: 'Device' },
  },
  {
    path: '/software',
    name: 'software',
    component: () => import('../views/SoftwareView.vue'),
    meta: { title: 'Software' },
  },
  {
    // A bare name opens the software's current version; the optional :version
    // segment deep-links a specific one (the version switcher writes it).
    path: '/software/:id/:version?',
    name: 'software-detail',
    component: () => import('../views/SoftwareDetailView.vue'),
    meta: { title: 'Software' },
  },
  {
    path: '/tests',
    name: 'tests',
    component: () => import('../views/TestsView.vue'),
    meta: { title: 'Tests' },
  },
  {
    path: '/profile',
    name: 'profile',
    component: () => import('../views/ProfileView.vue'),
    meta: { title: 'Profile' },
  },
  {
    path: '/audit',
    name: 'audit',
    component: () => import('../views/AuditView.vue'),
    meta: { title: 'Audit Log', roles: ['admin'] },
  },
  {
    path: '/users',
    name: 'users',
    component: () => import('../views/UsersView.vue'),
    meta: { title: 'Users', roles: ['admin'] },
  },
  {
    path: '/settings/schema',
    name: 'schema',
    component: () => import('../views/DeviceSchemaView.vue'),
    meta: { title: 'Schema', roles: ['admin'] },
  },
  {
    path: '/settings/device-schema',
    redirect: { name: 'schema' },
  },
  {
    /*
     * The old Device Types page is part of the schema area now. `tab=types` is
     * no longer a tab; the view resolves it to Device Layouts opened on a
     * device type rather than on the inherited set, which is where this link
     * used to land.
     */
    path: '/settings/device-types',
    redirect: { name: 'schema', query: { tab: 'types' } },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

let authReady: Promise<void> | null = null
function ensureAuthLoaded(): Promise<void> {
  const auth = useAuthStore()
  if (!authReady) authReady = auth.loadMe().then(() => {})
  return authReady
}

router.beforeEach(async (to) => {
  if (to.path === '/login') return true
  await ensureAuthLoaded()
  const auth = useAuthStore()
  if (!auth.user) return { path: '/login' }
  const roles = to.meta.roles as string[] | undefined
  if (roles && !roles.includes(auth.user.role)) return { path: '/' }
  return true
})
