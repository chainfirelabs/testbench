import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { PERMISSION, useAuthStore } from '../stores/auth'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
  {
    path: '/',
    name: 'home',
    component: () => import('../views/DashboardView.vue'),
    meta: { title: 'Home' },
  },
  {
    path: '/devices',
    name: 'devices',
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
    // Vendor claims across every software at once — the question a per-software
    // list cannot be asked, because it needs a software to start from.
    path: '/vendor-devices',
    name: 'vendor-devices',
    component: () => import('../views/VendorDevicesView.vue'),
    meta: { title: 'Vendor Claims' },
  },
  {
    path: '/tests',
    name: 'tests',
    component: () => import('../views/TestsView.vue'),
    meta: { title: 'Tests' },
  },
  // Runs are reached from their tests, so there is no list of them: the
  // Tests list's Test Run column links to each, and filters by it.
  { path: '/test-runs', redirect: '/tests' },
  {
    // One pipeline or suite execution: its tests and the files it shares
    // with all of them.
    path: '/test-runs/:id',
    name: 'test-run-detail',
    component: () => import('../views/TestRunDetailView.vue'),
    meta: { title: 'Test Run' },
  },
  {
    // How to use the API and the Python client, with a reference read from
    // this server. Hosted by the app so the backend's own /docs need not be
    // exposed.
    path: '/docs/:path(.*)?',
    name: 'docs',
    component: () => import('../views/DocsView.vue'),
    meta: { title: 'Docs', admin: true },
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
    meta: { title: 'Audit Log', permission: PERMISSION.auditView },
  },
  {
    path: '/users',
    name: 'users',
    component: () => import('../views/UsersView.vue'),
    meta: { title: 'Users', permission: PERMISSION.usersManage },
  },
  {
    path: '/settings/ai',
    name: 'ai-providers',
    component: () => import('../views/AiProvidersView.vue'),
    meta: { title: 'AI Providers', permission: PERMISSION.settingsManage },
  },
  {
    path: '/settings/replication',
    name: 'replication',
    component: () => import('../views/ReplicationView.vue'),
    meta: { title: 'Replication', permission: PERMISSION.settingsManage },
  },
  {
    path: '/settings/schema',
    name: 'schema',
    component: () => import('../views/DeviceSchemaView.vue'),
    meta: { title: 'Schema', permission: PERMISSION.schemaManage },
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
  // Gated on what the role grants, not on what it is called: an installation
  // can define a role that reads the audit log without being called admin,
  // and the API would let it through even when this did not.
  const permission = to.meta.permission as string | undefined
  if (permission && !auth.can(permission)) return { path: '/' }
  if (to.meta.admin && !auth.isAdmin) return { path: '/' }
  return true
})
