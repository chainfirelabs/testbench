<script setup lang="ts">
import { computed } from 'vue'
import type { DashboardWidget } from '../dashboardLayout'

const props = defineProps<{ widget: DashboardWidget, data: any }>()
const number = new Intl.NumberFormat()
const palette = ['#f6931d', '#c74d23', '#22c55e', '#eab308', '#6d8deb', '#b877dc', '#55a6a5', '#ef4444', '#737381']
const items = computed<any[]>(() => props.data?.items || [])
const maxCount = computed(() => Math.max(1, ...items.value.map((item) => item.count || item.total || 0)))
const pieBackground = computed(() => {
  const total = props.data?.total || 0
  if (!total) return 'var(--surface-3)'
  let start = 0
  const stops = items.value.map((item, index) => {
    const end = start + item.count / total * 100
    const stop = `${palette[index % palette.length]} ${start}% ${end}%`
    start = end
    return stop
  })
  return `conic-gradient(${stops.join(', ')})`
})
function pct(count: number, total: number): string {
  return `${total ? count / total * 100 : 0}%`
}
function dateText(value: string): string {
  if (!value) return 'Unknown date'
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : new Date(value).toLocaleDateString()
}
</script>

<template>
  <div v-if="widget.type === 'announcement'" class="announcement">{{ widget.body || 'No announcement text yet.' }}</div>

  <template v-else-if="widget.type === 'fleet_summary'">
    <div class="metric-grid fleet-grid" aria-label="Fleet counts">
      <router-link v-for="metric in [
        { label: 'Total devices', value: data?.total || 0, to: '/devices', caption: 'In inventory' },
        { label: 'Online', value: data?.online || 0, to: '/devices?scan_state=online', caption: 'Reached on last scan' },
        { label: 'Offline', value: data?.offline || 0, to: '/devices?scan_state=offline', caption: 'Unreachable on last scan' },
        { label: 'Never scanned', value: data?.never_scanned || 0, to: '/devices?scan_state=never_scanned', caption: 'No scan result yet' },
      ]" :key="metric.label" class="metric-card" :to="metric.to">
        <span>{{ metric.label }}</span><strong>{{ number.format(metric.value) }}</strong>
        <small>{{ metric.caption }}</small>
      </router-link>
    </div>
    <div v-if="widget.show_breakdown && data?.total" class="stacked-bar" role="img" :aria-label="`${data.online} online, ${data.offline} offline, ${data.never_scanned} never scanned`">
      <span class="online" :style="{ width: pct(data.online, data.total) }"></span>
      <span class="offline" :style="{ width: pct(data.offline, data.total) }"></span>
      <span class="unseen" :style="{ width: pct(data.never_scanned, data.total) }"></span>
    </div>
    <p v-if="!data?.total" class="empty">No devices yet.</p>
  </template>

  <template v-else-if="widget.type === 'devices_by_make' || widget.type === 'firmware_coverage'">
    <div v-if="data?.total" class="pie-layout">
      <div class="donut" :style="{ background: pieBackground }" role="img" :aria-label="items.map((item) => `${item.label}: ${item.count}`).join(', ')">
        <div class="donut-hole">{{ number.format(data.total) }}</div>
      </div>
      <ul class="legend-list">
        <li v-for="(item, index) in items" :key="item.label">
          <span class="legend-swatch" :style="{ background: palette[index % palette.length] }"></span>
          <router-link v-if="widget.type === 'devices_by_make' && item.label !== 'Other'" :to="`/devices?make_group=${encodeURIComponent(item.label)}`">{{ item.label }}</router-link><span v-else>{{ item.label }}</span><strong>{{ number.format(item.count) }}</strong>
          <small>{{ Math.round(item.count / data.total * 100) }}%</small>
        </li>
      </ul>
    </div>
    <p v-else class="empty">No devices to chart.</p>
  </template>

  <template v-else-if="widget.type === 'online_by_type'">
    <div v-if="items.length" class="chart-rows">
      <div v-for="item in items" :key="item.key" class="chart-row">
        <router-link :to="item.key === 'uncategorized' ? '/devices/type/uncategorized' : `/devices/type/${encodeURIComponent(item.key)}`">{{ item.label }}</router-link>
        <div class="stacked-bar" role="img" :aria-label="`${item.label}: ${item.online} online, ${item.offline} offline, ${item.never_scanned} never scanned`">
          <span class="online" :style="{ width: pct(item.online, item.online + item.offline + item.never_scanned) }"></span>
          <span class="offline" :style="{ width: pct(item.offline, item.online + item.offline + item.never_scanned) }"></span>
          <span class="unseen" :style="{ width: pct(item.never_scanned, item.online + item.offline + item.never_scanned) }"></span>
        </div>
        <small>{{ number.format(item.online + item.offline + item.never_scanned) }}</small>
      </div>
      <p class="legend-text">● Online &nbsp; ● Offline &nbsp; ● Never scanned</p>
    </div>
    <p v-else class="empty">No devices to chart.</p>
  </template>

  <template v-else-if="widget.type === 'inventory_status'">
    <div v-if="items.length" class="chart-rows">
      <div v-for="item in items" :key="item.label" class="chart-row">
        <router-link :to="`/devices?status=${encodeURIComponent(item.label)}`">{{ item.label.split('_').join(' ') }}</router-link>
        <div class="bar-track" role="img" :aria-label="`${item.label}: ${item.count}`"><span :style="{ width: pct(item.count, maxCount) }"></span></div>
        <strong>{{ number.format(item.count) }}</strong>
      </div>
      <router-link to="/devices" class="more-link">Open inventory</router-link>
    </div>
    <p v-else class="empty">No devices to chart.</p>
  </template>

  <template v-else-if="widget.type === 'checkouts_due'">
    <div class="metric-grid"><div class="metric-card"><span>Overdue</span><strong>{{ number.format(data?.overdue || 0) }}</strong></div><div class="metric-card"><span>Due within 7 days</span><strong>{{ number.format(data?.soon_due || 0) }}</strong></div></div>
    <ul v-if="items.length" class="item-list"><li v-for="item in items" :key="item.id"><router-link :to="`/devices/${item.id}`">{{ item.label }}</router-link><span>{{ item.days_remaining < 0 ? `${-item.days_remaining} days overdue` : item.days_remaining === 0 ? 'Due today' : `Due in ${item.days_remaining} days` }}</span></li></ul>
    <p v-else class="empty">No checkouts due soon.</p>
  </template>

  <template v-else-if="widget.type === 'recent_devices' || widget.type === 'untested_devices'">
    <p v-if="widget.type === 'untested_devices'" class="total-line">{{ number.format(data?.total || 0) }} untested devices</p>
    <ul v-if="items.length" class="item-list"><li v-for="item in items" :key="item.id"><router-link :to="`/devices/${item.id}`">{{ item.label }}</router-link><span>{{ item.type_label }}<template v-if="item.changed_at"> · {{ dateText(item.changed_at) }}</template></span></li></ul>
    <p v-else class="empty">{{ widget.type === 'untested_devices' ? 'Every device has a test record.' : 'No devices yet.' }}</p>
  </template>

  <template v-else-if="widget.type === 'test_activity'">
    <p class="total-line">{{ number.format(data?.total || 0) }} tests in the last {{ widget.days }} days</p>
    <div class="metric-grid"><div v-for="outcome in ['pass', 'fail', 'warn']" :key="outcome" class="metric-card"><span>{{ outcome }}</span><strong>{{ number.format(data?.outcomes?.[outcome] || 0) }}</strong></div></div>
    <router-link to="/tests" class="more-link">Open tests</router-link>
  </template>

  <template v-else-if="widget.type === 'most_tested_software' || widget.type === 'most_tested_devices'">
    <div v-if="items.length" class="chart-rows">
      <div v-for="item in items" :key="item.id" class="chart-row">
        <router-link :to="widget.type === 'most_tested_software' ? `/software/${item.id}` : `/devices/${item.id}`">{{ item.label }}<small v-if="item.version"> · {{ item.version }}</small><small v-if="item.type_label"> · {{ item.type_label }}</small></router-link>
        <div class="bar-track" role="img" :aria-label="`${item.label}: ${item.count} tests`"><span :style="{ width: pct(item.count, maxCount) }"></span></div>
        <strong>{{ number.format(item.count) }}</strong>
      </div>
      <p class="footnote">{{ widget.days ? `Tests with a run date in the last ${widget.days} days.` : 'All recorded tests.' }}</p>
    </div>
    <p v-else class="empty">No tests in this period.</p>
  </template>

  <template v-else-if="widget.type === 'recent_problem_tests'">
    <ul v-if="items.length" class="item-list"><li v-for="item in items" :key="item.id"><span><strong :class="item.outcome">{{ item.outcome }}</strong> · <router-link :to="`/devices/${item.device_id}`">{{ item.device_label }}</router-link> / <router-link :to="`/software/${item.software_id}`">{{ item.software_label }}</router-link></span><span>{{ dateText(item.run_at) }}</span></li></ul>
    <p v-else class="empty">No failed or warning tests in this period.</p>
  </template>

  <template v-else-if="widget.type === 'scan_freshness'">
    <p class="footnote">Recent means scanned within {{ widget.threshold_days }} days.</p>
    <div class="metric-grid"><div v-for="metric in [
      { label: 'Recent', value: data?.recent || 0 },
      { label: 'Stale', value: data?.stale || 0 },
      { label: 'Never scanned', value: data?.never_scanned || 0 },
    ]" :key="metric.label" class="metric-card"><span>{{ metric.label }}</span><strong>{{ number.format(metric.value) }}</strong></div></div>
  </template>

  <template v-else-if="widget.type === 'software_outcomes'">
    <div v-if="items.length" class="chart-rows">
      <div v-for="item in items" :key="item.id" class="chart-row outcomes-row">
        <router-link :to="`/software/${item.id}`">{{ item.label }}<small v-if="item.version"> · {{ item.version }}</small></router-link>
        <div class="stacked-bar" role="img" :aria-label="`${item.label}: ${item.pass} pass, ${item.fail} fail, ${item.warn} warn out of ${item.total}`">
          <span class="online" :style="{ width: pct(item.pass, item.total) }"></span><span class="offline" :style="{ width: pct(item.fail, item.total) }"></span><span class="warning" :style="{ width: pct(item.warn, item.total) }"></span>
        </div><small>{{ item.fail }} fail / {{ item.total }} tests</small>
      </div>
      <p class="footnote">At least 3 tests per software entry{{ widget.days ? ` in the last ${widget.days} days` : '' }}.</p>
    </div>
    <p v-else class="empty">No software has three tests in this period.</p>
  </template>
</template>

<style scoped>
.empty, .footnote { color: var(--text-muted); font-size: 13px; }
.footnote { margin: 12px 0 0; }
.total-line { font-size: 18px; font-weight: 650; }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 145px), 1fr)); gap: 10px; margin: 18px 0; }
.metric-card { display: flex; flex-direction: column; gap: 10px; min-height: 104px; padding: 15px; border: 1px solid var(--border-soft); border-radius: var(--r-md); background: var(--surface-2); text-decoration: none; color: var(--text); }
.metric-card:hover { border-color: var(--accent); }
.metric-card span { color: var(--text-muted); text-transform: capitalize; }
.metric-card strong { font-size: 29px; font-variant-numeric: tabular-nums; }
.metric-card small { margin-top: auto; color: var(--text-muted); }
.fleet-grid .metric-card { min-height: 142px; }
.fleet-grid .metric-card:first-child { background: linear-gradient(145deg, var(--accent-a16), var(--surface-2) 68%); border-color: var(--accent-a24); }
.fleet-grid .metric-card:nth-child(2) strong { color: var(--success); }
.fleet-grid .metric-card:nth-child(3) strong { color: var(--danger); }
.stacked-bar, .bar-track { display: flex; height: 13px; overflow: hidden; border-radius: var(--r-pill); background: var(--surface-3); }
.stacked-bar span, .bar-track span { height: 100%; }
.online { background: var(--success); }.offline { background: var(--danger); }.unseen { background: #737381; }.warning { background: var(--warn); }
.bar-track span { background: var(--accent); border-radius: var(--r-pill); }
.chart-rows { display: grid; gap: 13px; margin-top: 18px; }
.chart-row { display: grid; grid-template-columns: minmax(100px, 1fr) minmax(100px, 2fr) auto; align-items: center; gap: 12px; }
.chart-row a { min-width: 0; overflow-wrap: anywhere; }
.chart-row strong { font-variant-numeric: tabular-nums; }
.chart-row small { color: var(--text-muted); }
.legend-text { color: var(--text-muted); font-size: 12px; }
.item-list, .legend-list { list-style: none; padding: 0; margin: 18px 0 0; }
.item-list li { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border-soft); }
.item-list li span:last-child { color: var(--text-muted); text-align: right; }
.more-link { display: inline-block; margin-top: 14px; }
.announcement { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 18px; line-height: 1.6; }
.pie-layout { display: flex; align-items: center; flex-wrap: wrap; gap: 24px; margin-top: 18px; }
.donut { width: min(220px, 100%); aspect-ratio: 1; border-radius: 50%; display: grid; place-items: center; }
.donut-hole { width: 57%; height: 57%; display: grid; place-items: center; border-radius: 50%; background: var(--surface); font-size: 25px; font-weight: 700; }
.legend-list { flex: 1 1 220px; margin: 0; }
.legend-list li { display: grid; grid-template-columns: 10px minmax(80px, 1fr) auto auto; align-items: center; gap: 8px; margin: 8px 0; }
.legend-list small { color: var(--text-muted); }
.legend-swatch { width: 10px; height: 10px; border-radius: 2px; }
.fail { color: var(--danger); }.warn { color: var(--warn); }
@media (max-width: 560px) { .chart-row { grid-template-columns: minmax(100px, 1fr) minmax(80px, 1fr) auto; } .item-list li { flex-wrap: wrap; } }
</style>
