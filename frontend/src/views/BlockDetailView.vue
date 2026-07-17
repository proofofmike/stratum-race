<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRaceStore } from '@/stores/raceStore'
import { useTimezone } from '@/composables/useTimezone'
import { useVantageNames } from '@/composables/useVantageNames'
import type { RaceResult, RecentBlock } from '@/types'

const route = useRoute()
const router = useRouter()
const store = useRaceStore()
const { formatTimestamp } = useTimezone()
const { formatVantage } = useVantageNames()

/** Block height from route params */
const blockHeight = computed(() => Number(route.params.height))

/**
 * Find all matching races for this block height across vantage points.
 * Multiple vantage points may have observed the same block.
 */
const recentMatches = computed<RecentBlock[]>(() => {
  return store.recentBlocks.filter(
    (b) => b.height === blockHeight.value,
  )
})

/** Full race results fetched from the backend (contains per-pool offsets) */
const fullRaces = ref<RaceResult[]>([])
const isLoading = ref(false)
const loadError = ref(false)

/**
 * Fetch full race JSON files for the given block.
 * Uses the RecentBlock summary to construct the API path.
 */
async function fetchFullRaces() {
  if (recentMatches.value.length === 0) return
  isLoading.value = true
  loadError.value = false
  fullRaces.value = []

  const results: RaceResult[] = []

  for (const block of recentMatches.value) {
    // Build the /api/races/... path for the race file
    const dt = new Date(block.epoch * 1000)
    const year = dt.getUTCFullYear()
    const month = String(dt.getUTCMonth() + 1).padStart(2, '0')
    const day = String(dt.getUTCDate()).padStart(2, '0')
    // Null-height races are stored as unknown-<epoch>-<vantage>.json
    // (epoch disambiguates multiple unknown blocks on the same day)
    const heightPart = block.height != null ? String(block.height) : `unknown-${Math.floor(block.epoch)}`
    const vantage = block.vantage
    const url = `/api/races/${year}/${month}/${day}/${heightPart}-${vantage}.json`

    try {
      const response = await fetch(url)
      if (response.ok) {
        const data: RaceResult = await response.json()
        results.push(data)
      }
    } catch {
      // Silently skip — will show summary fallback
    }
  }

  fullRaces.value = results
  if (results.length === 0) {
    loadError.value = true
  }
  isLoading.value = false
}

// Fetch full race data when the component mounts or block height changes
onMounted(fetchFullRaces)
watch(blockHeight, fetchFullRaces)
// Also retry when recentMatches becomes populated (handles deep-link race condition)
watch(recentMatches, (newVal) => {
  if (newVal.length > 0 && fullRaces.value.length === 0 && !isLoading.value) {
    fetchFullRaces()
  }
})

/**
 * Use full race data when available, otherwise fall back to recent block summaries.
 * The races computed uses full data for timeline rendering.
 */
const races = computed(() => fullRaces.value.length > 0 ? fullRaces.value : recentMatches.value)

/** Primary race data (first match or null) */
const primaryRace = computed(() => races.value[0] ?? null)

/** Whether this block has data from multiple vantage points */
const hasMultipleVantages = computed(() => races.value.length > 1)

/** All unique vantage points for this block */
const vantagePoints = computed(() => [...new Set(races.value.map((r: any) => r.vantage))])

/** Accent colors for vantage points */
const vantageColors: Record<number, string> = {
  0: 'var(--accent)',
  1: '#f59e0b',
  2: '#10b981',
  3: '#ec4899',
  4: '#8b5cf6',
}

function getVantageColor(index: number): string {
  return vantageColors[index] ?? 'var(--text-secondary)'
}

/** Maximum offset across all races to scale the timeline */
const maxOffset = computed(() => {
  let max = 0
  for (const race of races.value) {
    const offsets: number[] = Object.values((race as any).nonempty_arrivals_offset_ms || {}) as number[]
    const anyOffsets: number[] = Object.values((race as any).arrivals_offset_ms || {}) as number[]
    const allValues = [...offsets, ...anyOffsets]
    if (allValues.length > 0) {
      max = Math.max(max, ...allValues)
    }
  }
  return max || 1 // Avoid division by zero
})

/**
 * Build timeline rows: one per pool, with entries for each vantage point.
 * Each entry has the offset, whether it's empty-first, and the vantage index.
 */
interface TimelineEntry {
  offset: number
  isEmptyFirst: boolean
  vantageIndex: number
  vantageName: string
  isFullTemplate: boolean
}

interface TimelineRow {
  poolName: string
  displayName: string
  entries: TimelineEntry[]
  isWinner: boolean
}

const timelineRows = computed<TimelineRow[]>(() => {
  // Collect all pools across all races
  const poolSet = new Set<string>()
  for (const race of races.value) {
    for (const pool of Object.keys((race as any).arrivals_offset_ms || {})) {
      poolSet.add(pool)
    }
    for (const pool of Object.keys((race as any).nonempty_arrivals_offset_ms || {})) {
      poolSet.add(pool)
    }
  }

  const rows: TimelineRow[] = []

  for (const poolName of poolSet) {
    const entries: TimelineEntry[] = []
    let isWinner = false

    for (let vi = 0; vi < races.value.length; vi++) {
      const race = races.value[vi]
      const isEmptyFirst = ((race as any).empty_first_pools ?? []).includes(poolName)

      // Full template offset (nonempty)
      const fullOffsets = (race as any).nonempty_arrivals_offset_ms || {}
      const fullOffset = fullOffsets[poolName]
      if (fullOffset != null) {
        entries.push({
          offset: fullOffset,
          isEmptyFirst: false,
          vantageIndex: vi,
          vantageName: (race as any).vantage,
          isFullTemplate: true,
        })
      }

      // Any-template offset (may differ from full if empty-first)
      const anyOffsets = (race as any).arrivals_offset_ms || {}
      const anyOffset = anyOffsets[poolName]
      if (anyOffset != null && isEmptyFirst) {
        // Show the earlier empty arrival as a separate marker
        entries.push({
          offset: anyOffset,
          isEmptyFirst: true,
          vantageIndex: vi,
          vantageName: (race as any).vantage,
          isFullTemplate: false,
        })
      }

      if ((race as any).winner_nonempty === poolName) {
        isWinner = true
      }
    }

    // Sort entries by offset for visual clarity
    entries.sort((a, b) => a.offset - b.offset)

    rows.push({
      poolName,
      displayName: store.displayName(poolName),
      entries,
      isWinner,
    })
  }

  // Sort rows by their earliest full-template offset
  rows.sort((a, b) => {
    const aMin = a.entries.find((e) => e.isFullTemplate)?.offset ?? Infinity
    const bMin = b.entries.find((e) => e.isFullTemplate)?.offset ?? Infinity
    return aMin - bMin
  })

  return rows
})

/** Block metadata */
const blockMiner = computed(() => {
  const race = primaryRace.value
  if (!race) return 'Unknown'
  return (race as any).block_miner || (race as any).miner || 'Unknown'
})
const winner = computed(() => {
  const race = primaryRace.value
  if (!race) return '—'
  return (race as any).winner_nonempty || (race as any).winner || '—'
})
const totalSpread = computed(() => {
  const race = primaryRace.value
  if (!race) return '—'
  // Full race has arrivals_offset_ms; summary has spread_ms
  if ((race as any).arrivals_offset_ms) {
    const offsets = Object.values((race as any).arrivals_offset_ms) as number[]
    if (offsets.length > 0) {
      return (Math.max(...offsets) - Math.min(...offsets)).toFixed(1)
    }
  }
  return (race as any).spread_ms?.toFixed(1) ?? '—'
})

/** Shortened prevhash is not in RecentBlock type, but we show block height context */
const formattedTime = computed(() => {
  if (!primaryRace.value) return '—'
  const race = primaryRace.value as any
  // Full race uses first_epoch; summary uses epoch
  const epoch = race.first_epoch ?? race.epoch
  return epoch ? formatTimestamp(epoch) : '—'
})

function goBack() {
  router.push({ name: 'leaderboard' })
}

/**
 * Compute the left position percentage for a marker on the timeline bar.
 */
function markerPosition(offset: number): string {
  if (maxOffset.value === 0) return '0%'
  return `${(offset / maxOffset.value) * 100}%`
}
</script>

<template>
  <div class="block-detail-view">
    <!-- Back button -->
    <button class="back-btn" @click="goBack" aria-label="Back to leaderboard">
      ← Back to Leaderboard
    </button>

    <!-- Not found state -->
    <div v-if="isLoading" class="not-found">
      <p>Loading race data...</p>
    </div>
    <div v-else-if="!primaryRace && recentMatches.length === 0" class="not-found">
      <h2>Block Not in Recent Data</h2>
      <p>Block {{ blockHeight.toLocaleString() }} is not available in the recent blocks list.</p>
      <button @click="goBack">Return to Leaderboard</button>
    </div>

    <!-- Block detail content -->
    <template v-else-if="primaryRace">
      <!-- Block metadata header -->
      <header class="block-header">
        <h2 class="block-title">Block {{ blockHeight.toLocaleString() }}</h2>
        <div class="metadata-grid">
          <div class="meta-item">
            <span class="meta-label">Time</span>
            <span class="meta-value">{{ formattedTime }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Block Miner</span>
            <span class="meta-value">{{ blockMiner }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Winner (Full Template)</span>
            <span class="meta-value winner-value">{{ store.displayName(winner) }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Total Spread</span>
            <span class="meta-value">{{ totalSpread }} ms</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Pools</span>
            <span class="meta-value">{{ timelineRows.length }}</span>
          </div>
        </div>
      </header>

      <!-- Vantage point legend (when multiple) -->
      <div v-if="hasMultipleVantages" class="vantage-legend">
        <span class="legend-label">Vantage Points:</span>
        <span
          v-for="(vp, idx) in vantagePoints"
          :key="vp"
          class="legend-item"
        >
          <span
            class="legend-dot"
            :style="{ backgroundColor: getVantageColor(idx) }"
          ></span>
          {{ formatVantage(vp) }}
        </span>
      </div>

      <!-- Marker type legend -->
      <div class="marker-legend">
        <span class="legend-item">
          <span class="legend-marker solid"></span>
          Full Template
        </span>
        <span class="legend-item">
          <span class="legend-marker hollow"></span>
          Empty (first)
        </span>
      </div>

      <!-- Timeline visualization -->
      <section class="timeline-section" aria-label="Race timeline">
        <!-- Timeline axis header -->
        <div class="timeline-axis-header">
          <span class="axis-label-start">0 ms</span>
          <span class="axis-label-end">{{ maxOffset.toFixed(0) }} ms</span>
        </div>

        <!-- Pool rows -->
        <div class="timeline-rows">
          <div
            v-for="row in timelineRows"
            :key="row.poolName"
            class="timeline-row"
            :class="{ 'is-winner': row.isWinner }"
          >
            <div class="row-pool-name">
              <span class="pool-display-name">{{ row.displayName }}</span>
              <span v-if="row.isWinner" class="winner-badge">🏆</span>
            </div>
            <div class="row-timeline-bar">
              <div class="bar-track">
                <div
                  v-for="(entry, i) in row.entries"
                  :key="i"
                  class="timeline-marker"
                  :class="{
                    'marker-full': entry.isFullTemplate,
                    'marker-empty': !entry.isFullTemplate,
                  }"
                  :style="{
                    left: markerPosition(entry.offset),
                    borderColor: getVantageColor(entry.vantageIndex),
                    backgroundColor: entry.isFullTemplate
                      ? getVantageColor(entry.vantageIndex)
                      : 'transparent',
                  }"
                  :title="`${entry.vantageName}: ${entry.offset.toFixed(1)} ms${entry.isEmptyFirst ? ' (empty)' : ''}`"
                >
                  <span class="marker-label">
                    {{ entry.offset.toFixed(1) }}
                    <span v-if="entry.isEmptyFirst" class="empty-label">empty</span>
                  </span>
                </div>
              </div>
            </div>
            <!-- Custom hover tooltip -->
            <div class="row-tooltip">
              <strong>{{ row.displayName }}</strong>
              <span v-if="row.isWinner" class="tooltip-winner"> 🏆 Winner</span>
              <div v-for="entry in row.entries" :key="`tip-${entry.vantageIndex}-${entry.isFullTemplate}`" class="tooltip-line">
                <span class="tooltip-type">{{ entry.isFullTemplate ? '▪ Full' : '▫ Empty' }}</span>
                <span class="tooltip-vantage">{{ formatVantage(entry.vantageName) }}</span>
                <span class="tooltip-ms">{{ entry.offset.toFixed(1) }} ms</span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.block-detail-view {
  max-width: 1100px;
  margin: 0 auto;
  padding: 1.5rem;
}

.back-btn {
  margin-bottom: 1.5rem;
  background: var(--surface-elevated);
  color: var(--accent);
  font-weight: 500;
  font-size: 0.875rem;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
}

.back-btn:hover {
  background: var(--border);
}

/* Not found state */
.not-found {
  text-align: center;
  padding: 3rem 1rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
}

.not-found h2 {
  margin-bottom: 0.75rem;
  color: var(--text-primary);
}

.not-found p {
  color: var(--text-secondary);
  margin-bottom: 1.5rem;
}

/* Block header */
.block-header {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1.25rem;
  margin-bottom: 1rem;
}

.block-title {
  font-size: 1.25rem;
  font-weight: 700;
  margin-bottom: 1rem;
  color: var(--text-primary);
}

.metadata-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.meta-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
}

.meta-value {
  font-family: var(--font-mono);
  font-size: 0.875rem;
  color: var(--text-primary);
}

.winner-value {
  color: var(--accent);
  font-weight: 600;
}

/* Legends */
.vantage-legend,
.marker-legend {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  margin-bottom: 0.75rem;
  font-size: 0.8125rem;
  color: var(--text-secondary);
}

.legend-label {
  font-weight: 600;
  color: var(--text-primary);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-marker {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-marker.solid {
  background-color: var(--accent);
}

.legend-marker.hollow {
  background-color: transparent;
  border: 2px solid var(--warning);
}

/* Timeline section */
.timeline-section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1.25rem;
}

.timeline-axis-header {
  display: flex;
  justify-content: space-between;
  padding: 0 0 0.75rem 140px;
  font-size: 0.6875rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  border-bottom: 1px solid var(--border);
  margin-bottom: 0.5rem;
}

/* Timeline rows */
.timeline-rows {
  display: flex;
  flex-direction: column;
}

.timeline-row {
  display: flex;
  align-items: center;
  padding: 0.625rem 0;
  border-bottom: 1px solid var(--border);
  transition: background-color 0.15s ease;
}

.timeline-row:last-child {
  border-bottom: none;
}

/* Custom hover tooltip */
.timeline-row {
  position: relative;
}

.row-tooltip {
  display: none;
  position: absolute;
  left: 140px;
  top: 100%;
  z-index: 100;
  background: var(--surface-elevated);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  padding: 0.625rem 0.75rem;
  font-size: 0.75rem;
  white-space: nowrap;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
  pointer-events: none;
}

.timeline-row:hover .row-tooltip {
  display: block;
}

.row-tooltip strong {
  color: var(--text-primary);
  font-size: 0.8125rem;
}

.tooltip-winner {
  color: var(--accent);
  font-size: 0.75rem;
}

.tooltip-line {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.25rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.tooltip-type {
  width: 50px;
  flex-shrink: 0;
}

.tooltip-vantage {
  width: 100px;
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tooltip-ms {
  font-weight: 600;
  color: var(--text-primary);
  text-align: right;
  min-width: 60px;
}

.timeline-row.is-winner {
  background: rgba(57, 135, 229, 0.06);
  border-radius: 0.25rem;
}

.row-pool-name {
  width: 140px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding-right: 0.75rem;
}

.pool-display-name {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.winner-badge {
  font-size: 0.75rem;
  flex-shrink: 0;
}

.row-timeline-bar {
  flex: 1;
  min-width: 0;
}

.bar-track {
  position: relative;
  height: 28px;
  background: var(--surface-elevated);
  border-radius: 0.25rem;
  border: 1px solid var(--border);
}

/* Timeline markers */
.timeline-marker {
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid;
  z-index: 1;
  cursor: default;
}

.timeline-marker.marker-full {
  /* backgroundColor set inline by vantage color */
}

.timeline-marker.marker-empty {
  /* Hollow circle with border color from vantage */
  background-color: transparent !important;
}

.marker-label {
  position: absolute;
  top: -20px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.625rem;
  font-family: var(--font-mono);
  color: var(--text-secondary);
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.timeline-marker:hover .marker-label {
  opacity: 1;
}

.empty-label {
  display: inline-block;
  margin-left: 0.25em;
  font-style: italic;
  color: var(--warning);
  font-size: 0.5625rem;
}

/* Responsive */
@media (max-width: 768px) {
  .block-detail-view {
    padding: 1rem;
  }

  .metadata-grid {
    grid-template-columns: 1fr 1fr;
  }

  .timeline-axis-header {
    padding-left: 100px;
  }

  .row-pool-name {
    width: 100px;
    font-size: 0.75rem;
  }

  .vantage-legend,
  .marker-legend {
    flex-wrap: wrap;
    gap: 0.5rem;
  }
}

@media (max-width: 480px) {
  .metadata-grid {
    grid-template-columns: 1fr;
  }

  .timeline-axis-header {
    padding-left: 80px;
  }

  .row-pool-name {
    width: 80px;
  }
}
</style>
