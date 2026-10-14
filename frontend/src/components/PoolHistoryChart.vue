<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PoolHistoryPoint } from '@/types'

const props = withDefaults(defineProps<{
  data: PoolHistoryPoint[]
  metric: 'hashrate' | 'users' | 'workers'
  loading: boolean
  poolName: string
  hasWorkers?: boolean
}>(), {
  hasWorkers: true,
})

const emit = defineEmits<{
  'range-change': [range: string]
  'metric-change': [metric: string]
}>()

const gradientId = computed(() => `area-grad-${props.poolName}`)
const selectedRange = ref('7d')
const ranges = ['24h', '7d', '30d', '1yr']

function selectRange(r: string) {
  selectedRange.value = r
  emit('range-change', r)
}

// If the Workers tab is hidden for this pool but it's somehow the active
// metric, fall back to Hashrate so the chart never sits on a hidden tab.
watch(
  () => props.hasWorkers,
  (has) => {
    if (!has && props.metric === 'workers') {
      emit('metric-change', 'hashrate')
    }
  },
  { immediate: true },
)

const metricLabel = computed(() => {
  switch (props.metric) {
    case 'hashrate': return 'Hashrate'
    case 'users':    return 'Users'
    case 'workers':  return 'Workers'
  }
})

// ─── SVG chart ───────────────────────────────────────────────────────────────

const PAD = { top: 16, right: 16, bottom: 40, left: 72 }
const W = 600
const H = 300

const points = computed(() => props.data)

const yMax = computed(() => {
  if (!points.value.length) return 1
  return Math.max(Math.max(...points.value.map(p => p.value)) * 1.1, 1)
})

const xMin = computed(() => points.value.length ? points.value[0].timestamp : 0)
const xMax = computed(() => points.value.length ? points.value[points.value.length - 1].timestamp : 1)

function sx(ts: number): number {
  const range = xMax.value - xMin.value || 1
  return PAD.left + ((ts - xMin.value) / range) * (W - PAD.left - PAD.right)
}

function sy(val: number): number {
  return PAD.top + (1 - val / yMax.value) * (H - PAD.top - PAD.bottom)
}

const linePath = computed(() => {
  if (!points.value.length) return ''
  return points.value
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.timestamp).toFixed(1)},${sy(p.value).toFixed(1)}`)
    .join(' ')
})

const areaPath = computed(() => {
  if (!points.value.length) return ''
  const baseline = sy(0)
  return `${linePath.value} L${sx(xMax.value).toFixed(1)},${baseline} L${sx(xMin.value).toFixed(1)},${baseline} Z`
})

// Y-axis ticks
const yTicks = computed(() => {
  const max = yMax.value
  const count = 4
  return Array.from({ length: count + 1 }, (_, i) => (max / count) * i)
})

function formatYTick(val: number): string {
  if (props.metric === 'hashrate') {
    if (val >= 1e18) return (val / 1e18).toFixed(1) + ' EH/s'
    if (val >= 1e15) return (val / 1e15).toFixed(1) + ' PH/s'
    if (val >= 1e12) return (val / 1e12).toFixed(1) + ' TH/s'
    if (val >= 1e9)  return (val / 1e9).toFixed(1)  + ' GH/s'
    return val.toFixed(0)
  }
  if (val >= 1000) return (val / 1000).toFixed(1) + 'k'
  return Math.round(val).toString()
}

// X-axis ticks: evenly spaced across the full data range (including the last
// point), so ticks always span the whole plotted width even with few points.
const xTicks = computed(() => {
  const pts = points.value
  if (!pts.length) return []
  if (pts.length === 1) return [pts[0]]
  const count = Math.min(5, pts.length)
  const lastIdx = pts.length - 1
  const seen = new Set<number>()
  const ticks: typeof pts = []
  for (let i = 0; i < count; i++) {
    const idx = Math.round((i / (count - 1)) * lastIdx)
    if (!seen.has(idx)) {
      seen.add(idx)
      ticks.push(pts[idx])
    }
  }
  return ticks
})

// True when every plotted point falls on the same UTC calendar day — e.g. a
// fresh pool or one whose history was recently reset. In that case all date
// labels would read identically, so we show the time instead.
const allSameDay = computed(() => {
  const pts = points.value
  if (pts.length < 2) return true
  const dayOf = (ts: number) => new Date(ts * 1000).toISOString().slice(0, 10)
  const first = dayOf(pts[0].timestamp)
  return pts.every(p => dayOf(p.timestamp) === first)
})

function formatXTick(ts: number): string {
  const d = new Date(ts * 1000)
  if (selectedRange.value === '24h' || allSameDay.value) {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' })
  }
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

// ─── Tooltip ─────────────────────────────────────────────────────────────────

const tooltipVisible = ref(false)
const tooltipX = ref(0)
const tooltipY = ref(0)
const tooltipPoint = ref<PoolHistoryPoint | null>(null)

function onMouseMove(event: MouseEvent) {
  if (!points.value.length) return
  const svg = (event.currentTarget as SVGElement).closest('svg')!
  const rect = svg.getBoundingClientRect()
  const scaleX = W / rect.width
  const mouseX = (event.clientX - rect.left) * scaleX

  // Find closest data point
  let closest = points.value[0]
  let minDist = Math.abs(sx(closest.timestamp) - mouseX)
  for (const p of points.value) {
    const d = Math.abs(sx(p.timestamp) - mouseX)
    if (d < minDist) { minDist = d; closest = p }
  }

  tooltipPoint.value = closest
  tooltipX.value = event.clientX
  tooltipY.value = event.clientY
  tooltipVisible.value = true
}

function onMouseLeave() {
  tooltipVisible.value = false
}

function formatTooltipDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'UTC',
  }) + ' UTC'
}
</script>

<template>
  <div class="chart-wrapper">
    <!-- Header row: metric tabs + range selector -->
    <div class="chart-controls">
      <div class="metric-tabs">
        <button
          :class="{ active: metric === 'hashrate' }"
          @click="emit('metric-change', 'hashrate')"
        >Hashrate</button>
        <button
          :class="{ active: metric === 'users' }"
          @click="emit('metric-change', 'users')"
        >Users</button>
        <button
          v-if="hasWorkers"
          :class="{ active: metric === 'workers' }"
          @click="emit('metric-change', 'workers')"
        >Workers</button>
      </div>
      <div class="range-tabs">
        <button
          v-for="r in ranges"
          :key="r"
          :class="{ active: selectedRange === r }"
          @click="selectRange(r)"
        >{{ r }}</button>
      </div>
    </div>

    <!-- Loading state -->
    <div v-if="loading" class="chart-loading">
      <span>Loading {{ metricLabel.toLowerCase() }} data…</span>
    </div>

    <!-- Empty state -->
    <div v-else-if="!data.length" class="chart-empty">
      No {{ metricLabel.toLowerCase() }} history available for this pool.
    </div>

    <!-- Chart -->
    <div v-else class="chart-svg-container">
      <svg
        :viewBox="`0 0 ${W} ${H}`"
        preserveAspectRatio="xMidYMid meet"
        class="chart-svg"
        @mousemove="onMouseMove"
        @mouseleave="onMouseLeave"
      >
        <defs>
          <linearGradient :id="gradientId" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stop-color="var(--accent)" stop-opacity="0.25" />
            <stop offset="100%" stop-color="var(--accent)" stop-opacity="0.02" />
          </linearGradient>
        </defs>

        <!-- Y grid lines + labels -->
        <g v-for="tick in yTicks" :key="tick">
          <line
            :x1="PAD.left" :y1="sy(tick)"
            :x2="W - PAD.right" :y2="sy(tick)"
            class="grid-line"
          />
          <text
            :x="PAD.left - 6" :y="sy(tick) + 4"
            class="axis-label" text-anchor="end"
          >{{ formatYTick(tick) }}</text>
        </g>

        <!-- X axis labels -->
        <text
          v-for="p in xTicks"
          :key="p.timestamp"
          :x="sx(p.timestamp)"
          :y="H - PAD.bottom + 16"
          class="axis-label" text-anchor="middle"
        >{{ formatXTick(p.timestamp) }}</text>

        <!-- Area fill -->
        <path :d="areaPath" :fill="`url(#${gradientId})`" />

        <!-- Line -->
        <path :d="linePath" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round" />

        <!-- Tooltip crosshair -->
        <g v-if="tooltipVisible && tooltipPoint">
          <line
            :x1="sx(tooltipPoint.timestamp)" :y1="PAD.top"
            :x2="sx(tooltipPoint.timestamp)" :y2="H - PAD.bottom"
            stroke="var(--text-secondary)" stroke-width="1" stroke-dasharray="3 3"
          />
          <circle
            :cx="sx(tooltipPoint.timestamp)"
            :cy="sy(tooltipPoint.value)"
            r="4"
            fill="var(--accent)"
            stroke="var(--surface)"
            stroke-width="2"
          />
        </g>

        <!-- Invisible hover overlay -->
        <rect
          :x="PAD.left" :y="PAD.top"
          :width="W - PAD.left - PAD.right"
          :height="H - PAD.top - PAD.bottom"
          fill="transparent"
          style="cursor: crosshair"
        />
      </svg>

      <!-- Tooltip -->
      <div
        v-if="tooltipVisible && tooltipPoint"
        class="chart-tooltip"
        :style="{ left: tooltipX + 14 + 'px', top: tooltipY - 14 + 'px' }"
      >
        <div class="tooltip-date">{{ formatTooltipDate(tooltipPoint.timestamp) }}</div>
        <div class="tooltip-value">{{ tooltipPoint.formatted }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chart-wrapper {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1rem;
}

.chart-controls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.metric-tabs,
.range-tabs {
  display: flex;
  gap: 0.25rem;
}

.metric-tabs button,
.range-tabs button {
  padding: 0.25rem 0.625rem;
  font-size: 0.75rem;
  font-weight: 500;
  border-radius: 0.25rem;
  border: 1px solid var(--border);
  background: var(--surface-elevated);
  color: var(--text-secondary);
  transition: all 0.15s;
  min-height: 28px;
}

.metric-tabs button:hover,
.range-tabs button:hover {
  color: var(--text-primary);
  background: var(--border);
}

.metric-tabs button.active,
.range-tabs button.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.chart-loading,
.chart-empty {
  height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.chart-svg-container {
  position: relative;
}

.chart-svg {
  width: 100%;
  height: auto;
  display: block;
}

.grid-line {
  stroke: var(--border);
  stroke-width: 0.5;
}

.axis-label {
  fill: var(--text-secondary);
  font-size: 10px;
  font-family: var(--font-mono);
}

.chart-tooltip {
  position: fixed;
  z-index: 100;
  background: var(--surface-elevated);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  padding: 0.375rem 0.625rem;
  pointer-events: none;
  white-space: nowrap;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}

.tooltip-date {
  font-size: 0.6875rem;
  color: var(--text-secondary);
  margin-bottom: 0.125rem;
}

.tooltip-value {
  font-family: var(--font-mono);
  font-size: 0.875rem;
  font-weight: 700;
  color: var(--accent);
}
</style>
