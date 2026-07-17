<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRaceStore } from '@/stores/raceStore'
import type { PoolStats } from '@/types'

const store = useRaceStore()

// ─── Time Range State ───────────────────────────────────────────────────────

type PresetRange = '24h' | '7d' | '30d' | 'custom'
const activePreset = ref<PresetRange>('7d')
const customStart = ref('')
const customEnd = ref('')

/** Whether data is currently being fetched */
const isLoadingAggregate = ref(false)

/** Whether the last fetch returned 404 (no data) */
const noData = ref(false)

/** Loaded aggregate pool data for the selected period */
const aggregateData = ref<Record<string, { combined: PoolStats; by_vantage: Record<string, PoolStats> }>>({})

// ─── Date Helpers ───────────────────────────────────────────────────────────

function formatDate(date: Date): string {
  return date.toISOString().slice(0, 10)
}

/**
 * Compute the list of aggregate file paths to fetch for a given date range.
 * Uses daily files for ranges ≤ 31 days, monthly files for longer ranges.
 */
function computeAggregateFiles(start: string, end: string): string[] {
  const startDate = new Date(start + 'T00:00:00Z')
  const endDate = new Date(end + 'T00:00:00Z')

  const diffDays = Math.ceil((endDate.getTime() - startDate.getTime()) / (1000 * 60 * 60 * 24)) + 1

  if (diffDays <= 31) {
    // Use daily aggregate files
    const files: string[] = []
    const current = new Date(startDate)
    while (current <= endDate) {
      files.push(`daily/${formatDate(current)}`)
      current.setUTCDate(current.getUTCDate() + 1)
    }
    return files
  } else {
    // Use monthly files for months fully contained, daily for partial months at edges
    const files: string[] = []
    const current = new Date(startDate)
    while (current <= endDate) {
      const monthStart = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), 1))
      const monthEnd = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 1, 0))

      if (monthStart >= startDate && monthEnd <= endDate) {
        // Full month contained — use monthly aggregate
        const yyyy = current.getUTCFullYear()
        const mm = String(current.getUTCMonth() + 1).padStart(2, '0')
        files.push(`monthly/${yyyy}-${mm}`)
        current.setUTCMonth(current.getUTCMonth() + 1)
        current.setUTCDate(1)
      } else {
        // Partial month — use daily files
        while (current <= endDate && current <= monthEnd) {
          files.push(`daily/${formatDate(current)}`)
          current.setUTCDate(current.getUTCDate() + 1)
        }
      }
    }
    return files
  }
}

// ─── Data Loading ───────────────────────────────────────────────────────────

async function fetchAggregate(dateRange: string): Promise<Record<string, { combined: PoolStats; by_vantage: Record<string, PoolStats> }> | null> {
  try {
    const response = await fetch(`/api/aggregates/${dateRange}.json`)
    if (response.status === 404) return null
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json()
    return data.pools ?? {}
  } catch {
    return null
  }
}

/**
 * Merge multiple aggregate pool records by averaging their stats.
 */
function mergeAggregates(
  allData: Array<Record<string, { combined: PoolStats; by_vantage: Record<string, PoolStats> }>>
): Record<string, { combined: PoolStats; by_vantage: Record<string, PoolStats> }> {
  if (allData.length === 0) return {}
  if (allData.length === 1) return allData[0]

  // Collect all pool names across all fetched files
  const poolNames = new Set<string>()
  for (const data of allData) {
    for (const name of Object.keys(data)) poolNames.add(name)
  }

  const merged: Record<string, { combined: PoolStats; by_vantage: Record<string, PoolStats> }> = {}

  for (const poolName of poolNames) {
    const combinedStats = mergePoolStats(
      allData.map((d) => d[poolName]?.combined).filter(Boolean) as PoolStats[]
    )
    // Merge by_vantage
    const vantageNames = new Set<string>()
    for (const d of allData) {
      const byV = d[poolName]?.by_vantage
      if (byV) {
        for (const v of Object.keys(byV)) vantageNames.add(v)
      }
    }
    const byVantage: Record<string, PoolStats> = {}
    for (const v of vantageNames) {
      byVantage[v] = mergePoolStats(
        allData.map((d) => d[poolName]?.by_vantage?.[v]).filter(Boolean) as PoolStats[]
      )
    }

    merged[poolName] = { combined: combinedStats, by_vantage: byVantage }
  }

  return merged
}

/**
 * Merge multiple PoolStats into a weighted aggregate.
 * Uses races_seen as weight for averages and sums wins/races.
 */
function mergePoolStats(statsArr: PoolStats[]): PoolStats {
  if (statsArr.length === 0) {
    return { median_ms: null, avg_ms: null, p95_ms: null, wins: 0, races_seen: 0, races_eligible: 0, win_pct: 0, empty_first_pct: 0, waste_min_day: 0 }
  }
  if (statsArr.length === 1) return statsArr[0]

  let totalRaces = 0
  let totalEligible = 0
  let totalWins = 0
  let weightedMedian = 0
  let weightedAvg = 0
  let weightedP95 = 0
  let weightedEmpty = 0
  let totalWaste = 0

  for (const s of statsArr) {
    totalRaces += s.races_seen
    totalEligible += s.races_eligible
    totalWins += s.wins
    if (s.median_ms != null) weightedMedian += s.median_ms * s.races_seen
    if (s.avg_ms != null) weightedAvg += s.avg_ms * s.races_seen
    if (s.p95_ms != null) weightedP95 += s.p95_ms * s.races_seen
    weightedEmpty += s.empty_first_pct * s.races_seen
    totalWaste += s.waste_min_day
  }

  return {
    median_ms: totalRaces > 0 ? weightedMedian / totalRaces : null,
    avg_ms: totalRaces > 0 ? weightedAvg / totalRaces : null,
    p95_ms: totalRaces > 0 ? weightedP95 / totalRaces : null,
    wins: totalWins,
    races_seen: totalRaces,
    races_eligible: totalEligible,
    win_pct: totalRaces > 0 ? (totalWins / totalRaces) * 100 : 0,
    empty_first_pct: totalRaces > 0 ? weightedEmpty / totalRaces : 0,
    waste_min_day: totalWaste,
  }
}

async function loadRange(start: string, end: string) {
  isLoadingAggregate.value = true
  noData.value = false
  aggregateData.value = {}

  const files = computeAggregateFiles(start, end)
  const results = await Promise.all(files.map((f) => fetchAggregate(f)))

  const validResults = results.filter((r) => r != null) as Array<Record<string, { combined: PoolStats; by_vantage: Record<string, PoolStats> }>>

  if (validResults.length === 0) {
    noData.value = true
  } else {
    aggregateData.value = mergeAggregates(validResults)
  }

  isLoadingAggregate.value = false
}

// ─── Preset Actions ─────────────────────────────────────────────────────────

function selectPreset(preset: PresetRange) {
  activePreset.value = preset
  if (preset === 'custom') return

  isLoadingAggregate.value = true
  noData.value = false
  aggregateData.value = {}

  let path: string

  switch (preset) {
    case '24h':
      path = 'last-24h'
      break
    case '7d':
      path = 'last-7d'
      break
    case '30d':
      // True rolling 30-day window (pre-computed by the aggregate Lambda).
      // The month-to-date file would show ~1 day of data on the 2nd of a month.
      path = 'last-30d'
      break
  }

  fetchAggregate(path!).then((result) => {
    if (result) {
      aggregateData.value = result
    } else {
      noData.value = true
    }
    isLoadingAggregate.value = false
  })
}

function applyCustomRange() {
  if (!customStart.value || !customEnd.value) return
  if (customStart.value > customEnd.value) return
  activePreset.value = 'custom'
  loadRange(customStart.value, customEnd.value)
}

// ─── Bar Chart Data ─────────────────────────────────────────────────────────

/** Solo/All filter for chart */
const showSoloOnly = ref(true)

interface HistoricalRow {
  poolName: string
  displayName: string
  stats: PoolStats
  anyMedian: number | null  // median from arrivals_offset_ms (any template)
}

const rows = computed<HistoricalRow[]>(() => {
  return Object.entries(aggregateData.value)
    .filter(([poolName]) => {
      if (!showSoloOnly.value) return true
      // If pool config isn't loaded yet, show all pools
      if (store.poolConfig.length === 0) return true
      const config = store.poolConfig.find((p) => p.name === poolName)
      if (!config) return false
      return (config as any).pool_type === 'solo'
    })
    .map(([poolName, data]) => ({
      poolName,
      displayName: store.displayName(poolName),
      stats: data.combined,
      anyMedian: (data as any).any_combined?.median_ms ?? null,
    }))
})

/** Rows sorted by median ascending for the bar chart (fastest at top) */
const chartRows = computed(() => {
  return [...rows.value]
    .filter((r) => r.stats.median_ms != null)
    .sort((a, b) => (a.stats.median_ms ?? Infinity) - (b.stats.median_ms ?? Infinity))
})

const maxChartMedian = computed(() => {
  let max = 0
  for (const row of chartRows.value) {
    const m = row.stats.median_ms
    if (m != null && m > max) max = m
  }
  return max
})

function chartBarWidth(medianMs: number | null): string {
  if (medianMs == null || maxChartMedian.value <= 0) return '0%'
  if (medianMs <= 0) return '3%'
  const pct = Math.max(3, Math.round((medianMs / maxChartMedian.value) * 100))
  return `${pct}%`
}

/** Width for the "any template" portion (where empty template arrived) */
function anyBarWidth(anyMedian: number | null): string {
  if (anyMedian == null || maxChartMedian.value <= 0) return '0%'
  if (anyMedian <= 0) return '3%'
  const pct = Math.max(3, Math.round((anyMedian / maxChartMedian.value) * 100))
  return `${pct}%`
}

function fmt(val: number | null | undefined, decimals = 1): string {
  if (val == null) return '—'
  return val.toFixed(decimals)
}

// ─── Initial Load ───────────────────────────────────────────────────────────

// Load 7d on mount
selectPreset('7d')
</script>

<template>
  <div class="historical-view">
    <!-- Time Range Selector -->
    <div class="panel range-selector-panel">
      <div class="panel-head">
        <h2>Historical Performance</h2>
      </div>
      <div class="range-controls">
        <div class="preset-buttons">
          <button
            :class="{ active: activePreset === '24h' }"
            @click="selectPreset('24h')"
          >
            24h
          </button>
          <button
            :class="{ active: activePreset === '7d' }"
            @click="selectPreset('7d')"
          >
            7 days
          </button>
          <button
            :class="{ active: activePreset === '30d' }"
            @click="selectPreset('30d')"
          >
            30 days
          </button>
        </div>
        <div class="solo-toggle">
          <button :class="{ active: showSoloOnly }" @click="showSoloOnly = true">Solo</button>
          <button :class="{ active: !showSoloOnly }" @click="showSoloOnly = false">All</button>
        </div>
        <div class="custom-range">
          <input
            type="date"
            v-model="customStart"
            class="date-input"
            aria-label="Start date"
          />
          <span class="range-separator">to</span>
          <input
            type="date"
            v-model="customEnd"
            class="date-input"
            aria-label="End date"
          />
          <button
            class="apply-btn"
            :class="{ active: activePreset === 'custom' }"
            @click="applyCustomRange"
            :disabled="!customStart || !customEnd"
          >
            Apply
          </button>
        </div>
      </div>
    </div>

    <!-- Loading State -->
    <div v-if="isLoadingAggregate" class="panel loading-panel">
      <div class="loading-indicator">Loading...</div>
    </div>

    <!-- Empty State -->
    <div v-else-if="noData" class="panel empty-panel">
      <div class="empty-indicator">No data for this period</div>
    </div>

    <!-- Content -->
    <template v-else-if="Object.keys(aggregateData).length > 0">
      <!-- Performance Trend Bar Chart -->
      <div class="panel chart-panel">
        <div class="panel-head">
          <h2>Median Offset by Pool</h2>
          <span class="panel-note">sorted fastest → slowest</span>
        </div>
        <div class="chart-body">
          <div
            v-for="(row, idx) in chartRows"
            :key="row.poolName"
            class="chart-row"
          >
            <span class="chart-pool-name">{{ row.displayName }}</span>
            <div class="chart-bar-track">
              <!-- Any-template bar (empty first, lighter) — only shown if different from full -->
              <div
                v-if="row.anyMedian != null && row.anyMedian < (row.stats.median_ms ?? 0) - 1"
                class="chart-bar chart-bar-any"
                :style="{ width: anyBarWidth(row.anyMedian) }"
              ></div>
              <!-- Full template bar (solid) -->
              <div
                class="chart-bar"
                :class="{ leader: idx === 0 }"
                :style="{
                  width: chartBarWidth(row.stats.median_ms),
                  opacity: idx === 0 ? 1 : Math.max(0.4, 1 - idx * 0.06),
                }"
              ></div>
            </div>
            <span class="chart-value">
              <span v-if="row.anyMedian != null && row.anyMedian < (row.stats.median_ms ?? 0) - 1" class="any-value">{{ fmt(row.anyMedian) }} /</span>
              {{ fmt(row.stats.median_ms) }} ms
            </span>
          </div>
          <div v-if="chartRows.length === 0" class="empty-state">
            No pools with median data
          </div>
          <div class="chart-legend">
            <span class="legend-item"><span class="legend-bar legend-full"></span> Full template</span>
            <span class="legend-item"><span class="legend-bar legend-any"></span> First notification (empty)</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.historical-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* ─── Panels ───────────────────────────────────────────────────────────────── */

.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  overflow: hidden;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
}

.panel-head h2 {
  font-size: 1rem;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary);
}

.panel-note {
  font-size: 0.75rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

/* ─── Range Selector ───────────────────────────────────────────────────────── */

.range-controls {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 1rem 1.25rem;
  flex-wrap: wrap;
}

.preset-buttons {
  display: flex;
  gap: 0.5rem;
}

.custom-range {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.range-separator {
  color: var(--text-secondary);
  font-size: 0.8125rem;
}

.date-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  padding: 0.4rem 0.75rem;
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  outline: none;
  transition: border-color 0.2s;
}

.date-input:focus {
  border-color: var(--accent);
}

/* Style the date picker icon for dark theme */
.date-input::-webkit-calendar-picker-indicator {
  filter: invert(0.7);
}

.apply-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ─── Loading / Empty States ───────────────────────────────────────────────── */

.loading-panel,
.empty-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 3rem 1rem;
}

.loading-indicator {
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 0.875rem;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.empty-indicator {
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-style: italic;
  font-size: 0.875rem;
}

/* ─── Bar Chart ────────────────────────────────────────────────────────────── */

.chart-body {
  padding: 1rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.chart-row {
  display: grid;
  grid-template-columns: 130px 1fr 80px;
  align-items: center;
  gap: 0.75rem;
}

.chart-pool-name {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chart-bar-track {
  height: 18px;
  background: var(--bg);
  border-radius: 3px;
  overflow: hidden;
  position: relative;
}

.chart-bar {
  height: 100%;
  background: var(--text-secondary);
  border-radius: 3px;
  transition: width 0.4s ease;
  position: relative;
  z-index: 0;
}

.chart-bar-any {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  background: rgba(251, 191, 36, 0.7);
  border-radius: 3px;
  z-index: 2;
}

.chart-bar.leader {
  background: var(--accent);
}

.chart-value {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-secondary);
  text-align: right;
}

.any-value {
  color: rgba(251, 191, 36, 0.8);
  font-size: 0.6875rem;
}

/* Solo/All toggle */
.solo-toggle {
  display: flex;
  border: 1px solid var(--border);
  border-radius: 0.25rem;
  overflow: hidden;
}

.solo-toggle button {
  padding: 0.375rem 0.75rem;
  font-size: 0.8125rem;
  border: none;
  background: var(--surface-elevated);
  color: var(--text-secondary);
  cursor: pointer;
  min-height: 36px;
}

.solo-toggle button.active {
  background: var(--accent);
  color: white;
}

/* Chart legend */
.chart-legend {
  display: flex;
  gap: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
  margin-top: 0.5rem;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.6875rem;
  color: var(--text-secondary);
}

.legend-bar {
  width: 16px;
  height: 8px;
  border-radius: 2px;
}

.legend-full {
  background: var(--text-secondary);
}

.legend-any {
  background: rgba(251, 191, 36, 0.4);
}

/* ─── Stats Table ──────────────────────────────────────────────────────────── */

.table-scroll {
  overflow-x: auto;
}

.stats-table {
  min-width: 900px;
}

.stats-table thead th {
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: color 0.15s;
  position: sticky;
  top: 0;
  background: var(--surface);
}

.stats-table thead th:hover {
  color: var(--accent);
}

.stats-row {
  transition: background-color 0.15s;
}

.stats-row:hover {
  background-color: var(--surface-elevated);
}

.col-rank {
  width: 3rem;
  text-align: center;
  color: var(--text-secondary);
}

.col-pool {
  font-family: var(--font-sans);
  font-weight: 500;
  color: var(--text-primary);
}

.col-numeric {
  text-align: right;
  font-family: var(--font-mono);
  font-size: 0.8125rem;
}

.empty-state {
  text-align: center;
  color: var(--text-secondary);
  padding: 2rem 1rem;
  font-family: var(--font-sans);
  font-style: italic;
}

/* ─── Mobile Responsive ────────────────────────────────────────────────────── */

@media (max-width: 768px) {
  .range-controls {
    flex-direction: column;
    align-items: flex-start;
  }

  .custom-range {
    flex-wrap: wrap;
  }

  .chart-row {
    grid-template-columns: 90px 1fr 60px;
  }

  .chart-pool-name {
    font-size: 0.75rem;
  }

  .stats-table {
    min-width: unset;
  }

  .panel-head {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.25rem;
  }
}
</style>
