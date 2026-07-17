<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRaceStore } from '@/stores/raceStore'
import type { PoolStats } from '@/types'

const store = useRaceStore()

/** Column definitions for sorting */
type SortColumn =
  | 'rank'
  | 'pool'
  | 'median_ms'
  | 'avg_ms'
  | 'p95_ms'
  | 'wins'
  | 'win_pct'
  | 'empty_first_pct'
  | 'waste_min_day'
  | 'races_seen'

const sortColumn = ref<SortColumn>('median_ms')
const sortAscending = ref(true)

interface LeaderboardEntry {
  poolName: string
  displayName: string
  stats: PoolStats | null
}

/** Build rows from the store's leaderboard data, filtered by selected pool type */
const rows = computed<LeaderboardEntry[]>(() => {
  return Object.keys(store.leaderboardData)
    .filter((poolName) => {
      if (store.selectedTier === 'all') return true
      // If pool config isn't loaded yet, show all pools
      if (store.poolConfig.length === 0) return true
      // 'small' = solo filter
      const config = store.poolConfig.find((p) => p.name === poolName)
      if (!config) return false
      return (config as any).pool_type === 'solo'
    })
    .map((poolName) => ({
      poolName,
      displayName: store.displayName(poolName),
      stats: store.getPoolStats(poolName),
    }))
})

/** Sort rows based on active column and direction */
const sortedRows = computed<LeaderboardEntry[]>(() => {
  const copy = [...rows.value]

  copy.sort((a, b) => {
    const aVal = getColumnValue(a, sortColumn.value)
    const bVal = getColumnValue(b, sortColumn.value)

    // Nulls always sorted last regardless of direction
    if (aVal == null && bVal == null) return 0
    if (aVal == null) return 1
    if (bVal == null) return -1

    if (sortColumn.value === 'pool') {
      // String comparison for pool names
      const cmp = (aVal as string).localeCompare(bVal as string)
      return sortAscending.value ? cmp : -cmp
    }

    // Numeric comparison
    const cmp = (aVal as number) - (bVal as number)
    return sortAscending.value ? cmp : -cmp
  })

  return copy
})

/** Extract the value for a given column from a row */
function getColumnValue(row: LeaderboardEntry, col: SortColumn): number | string | null {
  switch (col) {
    case 'rank':
      return row.stats?.median_ms ?? null
    case 'pool':
      return row.displayName
    case 'median_ms':
      return row.stats?.median_ms ?? null
    case 'avg_ms':
      return row.stats?.avg_ms ?? null
    case 'p95_ms':
      return row.stats?.p95_ms ?? null
    case 'wins':
      return row.stats?.wins ?? null
    case 'win_pct':
      return row.stats?.win_pct ?? null
    case 'empty_first_pct':
      return row.stats?.empty_first_pct ?? null
    case 'waste_min_day':
      return row.stats?.waste_min_day ?? null
    case 'races_seen':
      return row.stats?.races_seen ?? null
    default:
      return null
  }
}

/** Toggle sort: if same column clicked, flip direction; otherwise set new column ascending */
function sort(col: SortColumn) {
  if (sortColumn.value === col) {
    sortAscending.value = !sortAscending.value
  } else {
    sortColumn.value = col
    sortAscending.value = true
  }
}

/** Sort indicator arrow for column headers */
function sortIndicator(col: SortColumn): string {
  if (sortColumn.value !== col) return ''
  return sortAscending.value ? ' ▲' : ' ▼'
}

/**
 * Linear-scaled bar width for median values.
 * Maps offset range [0, maxMedian] to [0%, 100%] using linear scale.
 * Fast pools get short bars, slow pools get long bars — making differences obvious.
 * Minimum bar width of 3% for non-null values.
 */
const maxMedian = computed(() => {
  let max = 0
  for (const row of rows.value) {
    const m = row.stats?.median_ms
    if (m != null && m > max) max = m
  }
  return max
})

function medianBarWidth(medianMs: number | null | undefined): string {
  if (medianMs == null || maxMedian.value <= 0) return '0%'
  if (medianMs <= 0) return '3%'
  const pct = Math.max(3, Math.round((medianMs / maxMedian.value) * 100))
  return `${pct}%`
}

/**
 * Color for the median bar: green (fast) → yellow (mid) → red (slow).
 * Uses linear interpolation across the range [0, maxMedian].
 */
function medianBarColor(medianMs: number | null | undefined): string {
  if (medianMs == null || maxMedian.value <= 0) return 'var(--accent)'
  const ratio = Math.min(1, Math.max(0, medianMs / maxMedian.value))
  // Green (0) → Yellow (0.5) → Red (1)
  if (ratio <= 0.5) {
    // Green to Yellow: hue 120 → 60
    const hue = 120 - (ratio * 2) * 60
    return `hsl(${hue}, 75%, 50%)`
  } else {
    // Yellow to Red: hue 60 → 0
    const hue = 60 - ((ratio - 0.5) * 2) * 60
    return `hsl(${hue}, 75%, 50%)`
  }
}

/** Format numeric values for display */
function fmt(val: number | null | undefined, decimals = 1): string {
  if (val == null) return '—'
  return val.toFixed(decimals)
}

function fmtPct(val: number | null | undefined): string {
  if (val == null) return '—'
  return val.toFixed(1) + '%'
}

function fmtInt(val: number | null | undefined): string {
  if (val == null) return '—'
  return String(val)
}
</script>

<template>
  <div class="leaderboard-panel">
    <div class="panel-head">
      <h2>Leaderboard</h2>
      <span class="panel-note">ranked by {{ sortColumn.replace('_', ' ') }} · {{ store.templateMode === 'full' ? 'full templates' : 'any template' }}</span>
    </div>
    <div class="table-scroll">
      <table class="leaderboard-table">
        <thead>
          <tr>
            <th class="col-rank" @click="sort('rank')">#{{ sortIndicator('rank') }}</th>
            <th class="col-pool" @click="sort('pool')">Pool{{ sortIndicator('pool') }}</th>
            <th class="col-numeric" @click="sort('median_ms')">Median (ms){{ sortIndicator('median_ms') }}</th>
            <th class="col-numeric" @click="sort('wins')">Wins{{ sortIndicator('wins') }}</th>
            <th class="col-numeric" @click="sort('win_pct')">Win%{{ sortIndicator('win_pct') }}</th>
            <th class="col-numeric" @click="sort('races_seen')">Races{{ sortIndicator('races_seen') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in sortedRows" :key="row.poolName" class="leaderboard-row">
            <td class="col-rank">{{ idx + 1 }}</td>
            <td class="col-pool">{{ row.displayName }}</td>
            <td class="col-numeric col-median">
              <div class="median-cell">
                <span class="median-value">{{ fmt(row.stats?.median_ms) }}</span>
                <div class="median-bar-track">
                  <div
                    class="median-bar"
                    :style="{
                      width: medianBarWidth(row.stats?.median_ms),
                      backgroundColor: medianBarColor(row.stats?.median_ms),
                    }"
                  ></div>
                </div>
              </div>
            </td>
            <td class="col-numeric">{{ fmtInt(row.stats?.wins) }}</td>
            <td class="col-numeric">{{ fmtPct(row.stats?.win_pct) }}</td>
            <td class="col-numeric">{{ fmtInt(row.stats?.races_seen) }}</td>
          </tr>
          <tr v-if="sortedRows.length === 0">
            <td colspan="6" class="empty-state">No pool data available</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.leaderboard-panel {
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

.table-scroll {
  overflow-x: auto;
}

.leaderboard-table {
  min-width: 900px;
}

.leaderboard-table thead th {
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: color 0.15s;
  position: sticky;
  top: 0;
  background: var(--surface);
}

.leaderboard-table thead th:hover {
  color: var(--accent);
}

.leaderboard-row {
  transition: background-color 0.15s;
}

.leaderboard-row:hover {
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

.col-median {
  min-width: 160px;
}

.median-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.25rem;
}

.median-value {
  font-family: var(--font-mono);
}

.median-bar-track {
  width: 100%;
  height: 3px;
  background: var(--border);
  border-radius: 2px;
  overflow: hidden;
}

.median-bar {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease, background-color 0.3s ease;
}

.empty-state {
  text-align: center;
  color: var(--text-secondary);
  padding: 2rem 1rem;
  font-family: var(--font-sans);
  font-style: italic;
}

/* Mobile responsive */
@media (max-width: 768px) {
  .leaderboard-table {
    min-width: unset;
  }

  .panel-head {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.25rem;
  }

  .col-median {
    min-width: 100px;
  }

  .col-pool {
    max-width: 120px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
