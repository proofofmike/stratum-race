<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRaceStore } from '@/stores/raceStore'
import { useVantageNames } from '@/composables/useVantageNames'

const store = useRaceStore()
const { formatVantage, getFlag } = useVantageNames()

/** Current sort column: 'pool', 'combined', or a vantage key */
const sortCol = ref<string>('combined')
const sortAsc = ref(true)

const vantagePoints = computed<string[]>(() => {
  const vantages = new Set<string>()
  for (const poolAgg of Object.values(store.leaderboardData)) {
    if (poolAgg.by_vantage) {
      for (const v of Object.keys(poolAgg.by_vantage)) vantages.add(v)
    }
  }
  return Array.from(vantages).sort()
})

const hasMultipleVantages = computed(() => vantagePoints.value.length >= 2)

interface PoolRow {
  poolName: string
  displayName: string
  combinedMedian: number | null
  byVantage: Record<string, number | null>
}

const poolRows = computed<PoolRow[]>(() => {
  return Object.keys(store.leaderboardData).map((poolName) => {
    const poolAgg = store.leaderboardData[poolName]
    const byVantage: Record<string, number | null> = {}
    for (const vp of vantagePoints.value) {
      byVantage[vp] = poolAgg.by_vantage?.[vp]?.median_ms ?? null
    }
    return {
      poolName,
      displayName: store.displayName(poolName),
      combinedMedian: poolAgg.combined?.median_ms ?? null,
      byVantage,
    }
  })
})

const sortedRows = computed<PoolRow[]>(() => {
  const rows = [...poolRows.value]
  rows.sort((a, b) => {
    let aVal: number | string | null
    let bVal: number | string | null

    if (sortCol.value === 'pool') {
      aVal = a.displayName
      bVal = b.displayName
      const cmp = (aVal as string).localeCompare(bVal as string)
      return sortAsc.value ? cmp : -cmp
    } else if (sortCol.value === 'combined') {
      aVal = a.combinedMedian
      bVal = b.combinedMedian
    } else {
      aVal = a.byVantage[sortCol.value] ?? null
      bVal = b.byVantage[sortCol.value] ?? null
    }

    if (aVal == null && bVal == null) return 0
    if (aVal == null) return 1
    if (bVal == null) return -1
    const cmp = (aVal as number) - (bVal as number)
    return sortAsc.value ? cmp : -cmp
  })
  return rows
})

function sort(col: string) {
  if (sortCol.value === col) {
    sortAsc.value = !sortAsc.value
  } else {
    sortCol.value = col
    sortAsc.value = true
  }
}

function sortIndicator(col: string): string {
  if (sortCol.value !== col) return ''
  return sortAsc.value ? ' ▲' : ' ▼'
}

/**
 * Color gradient based on absolute ms thresholds:
 * 0-100ms: bright green
 * 100-300ms: green → yellow
 * 300-500ms: yellow → red
 * 500-1000ms: dark red
 * 1000ms+: dark red + diagonal stripes (intensity scales with severity)
 */
function getCellStyle(value: number | null): Record<string, string> {
  if (value == null) return { backgroundColor: 'var(--surface-elevated)' }

  if (value <= 100) {
    const intensity = 0.25 + (1 - value / 100) * 0.15
    return { backgroundColor: `rgba(34, 197, 94, ${intensity.toFixed(2)})` }
  } else if (value <= 300) {
    const ratio = (value - 100) / 200
    const hue = 120 - ratio * 60
    return { backgroundColor: `hsla(${hue}, 75%, 45%, 0.3)` }
  } else if (value <= 500) {
    const ratio = (value - 300) / 200
    const hue = 60 - ratio * 60
    return { backgroundColor: `hsla(${hue}, 75%, 45%, 0.35)` }
  } else if (value <= 1000) {
    return { backgroundColor: 'rgba(220, 38, 38, 0.35)' }
  } else {
    // Over 1000ms: dark red + diagonal stripes with increasing width and opacity
    // Scale: 1000ms = thin faint stripes, 3000ms+ = thick bold stripes
    const severity = Math.min(1, (value - 1000) / 2000)
    // Stripe opacity: 0.2 at 1000ms → 0.7 at 3000ms+
    const stripeOpacity = (0.2 + severity * 0.5).toFixed(2)
    // Stripe width: 3px at 1000ms → 7px at 3000ms+
    const stripeWidth = Math.round(3 + severity * 4)
    const gapWidth = 6
    return {
      background: `repeating-linear-gradient(
        -45deg,
        rgba(180, 20, 20, 0.45),
        rgba(180, 20, 20, 0.45) ${gapWidth}px,
        rgba(0, 0, 0, ${stripeOpacity}) ${gapWidth}px,
        rgba(0, 0, 0, ${stripeOpacity}) ${gapWidth + stripeWidth}px
      )`,
    }
  }
}

function formatMs(value: number | null): string {
  if (value == null) return '—'
  return value.toFixed(1)
}
</script>

<template>
  <div class="cross-vantage-view">
    <header class="view-header">
      <h2>Cross-Vantage Comparison</h2>
      <p class="subtitle">Median offset (ms) per pool from each measurement location</p>
    </header>

    <div v-if="!hasMultipleVantages" class="single-vantage-message">
      <p>Cross-vantage analysis requires data from multiple vantage points.</p>
    </div>

    <div v-else class="matrix-container">
      <table class="matrix-table">
        <thead>
          <tr>
            <th class="pool-col sticky-col" @click="sort('pool')">Pool{{ sortIndicator('pool') }}</th>
            <th class="combined-col" @click="sort('combined')">Combined{{ sortIndicator('combined') }}</th>
            <th v-for="vp in vantagePoints" :key="vp" class="vantage-col" @click="sort(vp)">
              {{ getFlag(vp) }} {{ formatVantage(vp) }}{{ sortIndicator(vp) }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sortedRows" :key="row.poolName">
            <td class="pool-col sticky-col">
              <span class="pool-name">{{ row.displayName }}</span>
            </td>
            <td class="data-cell" :style="getCellStyle(row.combinedMedian)">
              {{ formatMs(row.combinedMedian) }}
            </td>
            <td
              v-for="vp in vantagePoints"
              :key="vp"
              class="data-cell"
              :style="getCellStyle(row.byVantage[vp])"
            >
              {{ formatMs(row.byVantage[vp]) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.cross-vantage-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.view-header h2 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
}

.subtitle {
  margin: 0.25rem 0 0;
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.single-vantage-message {
  padding: 2rem;
  text-align: center;
  color: var(--text-secondary);
  background: var(--surface);
  border-radius: 0.5rem;
  border: 1px solid var(--border);
}

.matrix-container {
  overflow-x: auto;
  border-radius: 0.5rem;
  border: 1px solid var(--border);
  background: var(--surface);
}

.matrix-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.matrix-table thead {
  position: sticky;
  top: 0;
  z-index: 2;
}

.matrix-table th {
  padding: 0.75rem;
  text-align: center;
  font-weight: 600;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
  transition: color 0.15s;
}

.matrix-table th:hover {
  color: var(--accent);
}

.matrix-table th.pool-col {
  text-align: left;
}

.matrix-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border);
}

.matrix-table tbody tr:last-child td {
  border-bottom: none;
}

.matrix-table tbody tr:hover {
  background: rgba(255, 255, 255, 0.03);
}

.sticky-col {
  position: sticky;
  left: 0;
  z-index: 1;
  background: var(--surface);
  min-width: 160px;
}

thead .sticky-col {
  z-index: 3;
}

.pool-name {
  font-weight: 500;
  color: var(--text-primary);
}

.data-cell {
  text-align: center;
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  white-space: nowrap;
  min-width: 90px;
  border-radius: 0.125rem;
}

.combined-col {
  font-weight: 600;
}

@media (max-width: 768px) {
  .matrix-table th,
  .matrix-table td {
    padding: 0.375rem 0.5rem;
    font-size: 0.75rem;
  }

  .data-cell {
    min-width: 65px;
  }

  .sticky-col {
    min-width: 110px;
  }
}
</style>
