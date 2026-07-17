<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRaceStore } from '@/stores/raceStore'
import { useVantageNames } from '@/composables/useVantageNames'

const store = useRaceStore()
const { formatVantage, getFlag } = useVantageNames()

const HEALTH_THRESHOLD_MS = 90 * 60 * 1000 // 90 minutes
const MAX_INLINE_VANTAGES = 5

/** Tick every second for live "time ago" and health checks */
const now = ref(Date.now())
let tickInterval: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  tickInterval = setInterval(() => { now.value = Date.now() }, 1000)
})
onUnmounted(() => {
  if (tickInterval) clearInterval(tickInterval)
})

/** Current leader: pool with lowest median_ms, respects Solo/All filter */
const leader = computed(() => {
  let sorted = store.sortedLeaderboard
  // Apply pool type filter (matches LeaderboardTable behavior)
  if (store.selectedTier !== 'all' && store.poolConfig.length > 0) {
    sorted = sorted.filter(({ poolName }) => {
      const config = store.poolConfig.find((p) => p.name === poolName)
      if (!config) return false
      return (config as any).pool_type === 'solo'
    })
  }
  if (sorted.length === 0) return null
  const top = sorted[0]
  // Explicit null check: a median of exactly 0.0 ms is a valid (winning)
  // value and must not hide the leader card
  if (top.stats?.median_ms == null) return null
  return { name: store.displayName(top.poolName), median: top.stats.median_ms }
})

/** Total races: prefer aggregate races_seen (accurate), fall back to recentBlocks count */
const totalRaces = computed(() => {
  // When a specific vantage is selected, count only that vantage's observations
  if (store.selectedVantage !== 'combined') {
    let vantageMax = 0
    for (const poolAgg of Object.values(store.leaderboardData)) {
      const vpStats = poolAgg.by_vantage?.[store.selectedVantage]
      if (vpStats) {
        const seen = vpStats.races_seen ?? 0
        if (seen > vantageMax) vantageMax = seen
      }
    }
    if (vantageMax > 0) return vantageMax
  }

  // Combined: use aggregate's max races_eligible
  let aggregateMax = 0
  for (const poolAgg of Object.values(store.leaderboardData)) {
    const eligible = poolAgg.combined?.races_eligible ?? 0
    if (eligible > aggregateMax) aggregateMax = eligible
  }
  if (aggregateMax > 0) return aggregateMax
  return store.recentBlocks.length
})

/** Context detail for the races card */
const racesDetail = computed(() => {
  if (totalVantages.value === 0 || totalRaces.value === 0) return null
  if (store.selectedVantage !== 'combined') {
    // Single vantage selected — show just the vantage name
    return formatVantage(store.selectedVantage)
  }
  // Combined view — show blocks · vantages
  const blocks = Math.round(totalRaces.value / totalVantages.value)
  return `${blocks} blocks · ${totalVantages.value} vantage${totalVantages.value > 1 ? 's' : ''}`
})

/** Number of pools in the leaderboard */
const poolCount = computed(() => Object.keys(store.leaderboardData).length)

/** Last block info */
/** Vantage health statuses */
interface VantageStatus {
  id: string
  flag: string
  label: string
  healthy: boolean
}

const vantageStatuses = computed<VantageStatus[]>(() => {
  const entries = Object.entries(store.vantageHealth)
  return entries.map(([id, health]) => {
    let healthy = false
    // Check last_race_utc first, fall back to last_heartbeat_utc
    const lastActivity = health.last_race_utc || health.last_heartbeat_utc
    if (lastActivity) {
      const lastTime = new Date(lastActivity).getTime()
      healthy = (now.value - lastTime) < HEALTH_THRESHOLD_MS
    }
    return {
      id,
      flag: getFlag(id),
      label: formatVantage(id),
      healthy,
    }
  }).sort((a, b) => a.id.localeCompare(b.id))
})

const healthyCount = computed(() => vantageStatuses.value.filter(v => v.healthy).length)
const totalVantages = computed(() => vantageStatuses.value.length)
const showInline = computed(() => totalVantages.value <= MAX_INLINE_VANTAGES)
</script>

<template>
  <div class="stats-cards">
    <!-- Leader -->
    <div class="stat-card card-leader">
      <span class="stat-label">Current Leader</span>
      <span v-if="leader" class="stat-value leader-value">
        {{ leader.name }}
        <span class="leader-median">{{ leader.median.toFixed(1) }} ms</span>
      </span>
      <span v-else class="stat-value stat-muted">—</span>
    </div>

    <!-- Races -->
    <div class="stat-card card-races">
      <span class="stat-label">Races Tracked</span>
      <span class="stat-value">{{ totalRaces.toLocaleString() }}</span>
      <span v-if="racesDetail" class="stat-detail">{{ racesDetail }}</span>
    </div>

    <!-- Pools -->
    <div class="stat-card card-pools">
      <span class="stat-label">Pools Monitored</span>
      <span class="stat-value">{{ poolCount }}</span>
    </div>

    <!-- Vantages -->
    <div class="stat-card card-vantages">
      <span class="stat-label">Vantages</span>
      <div v-if="showInline && totalVantages > 0" class="vantage-list">
        <div v-for="vp in vantageStatuses" :key="vp.id" class="vantage-item">
          <span class="health-dot" :class="vp.healthy ? 'dot-healthy' : 'dot-stale'"></span>
          <span class="vantage-flag">{{ vp.flag }}</span>
          <span class="vantage-name">{{ vp.label }}</span>
        </div>
      </div>
      <div v-else-if="totalVantages > MAX_INLINE_VANTAGES" class="vantage-summary">
        <span class="stat-value">{{ healthyCount }}/{{ totalVantages }} healthy</span>
        <router-link to="/compare" class="vantage-link">View status →</router-link>
      </div>
      <span v-else class="stat-value stat-muted">—</span>
    </div>

  </div>
</template>

<style scoped>
.stats-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
}

.stat-detail {
  font-size: 0.6875rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.75rem 1rem;
  border-left: 3px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.card-leader { border-left-color: var(--accent); }
.card-races { border-left-color: var(--success); }
.card-pools { border-left-color: var(--warning); }
.card-vantages { border-left-color: #8b5cf6; }

.stat-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
}

.stat-value {
  font-family: var(--font-mono);
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.stat-muted {
  color: var(--text-secondary);
}

.leader-value {
  font-family: var(--font-sans);
  color: var(--accent);
}

.leader-median {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  font-weight: 400;
  color: var(--text-secondary);
}

/* Vantage list */
.vantage-list {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.vantage-item {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
}

.health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-healthy { background: var(--success); }
.dot-stale { background: var(--error); }

.vantage-flag {
  font-size: 0.875rem;
}

.vantage-name {
  color: var(--text-primary);
  font-size: 0.75rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.vantage-summary {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.vantage-link {
  font-size: 0.75rem;
  color: var(--accent);
  text-decoration: none;
}

.vantage-link:hover {
  text-decoration: underline;
}

/* Block card */
.block-height-link {
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
}

.block-height-link:hover {
  text-decoration: underline;
}

.block-ago {
  font-size: 0.75rem;
  font-weight: 400;
  color: var(--text-secondary);
}

/* Mobile: 2-column grid, vantage card spans full width */
@media (max-width: 768px) {
  .stats-cards {
    grid-template-columns: 1fr 1fr;
  }

  .card-vantages {
    grid-column: 1 / -1;
  }

  .stat-value {
    font-size: 0.875rem;
  }

  .vantage-list {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
}

@media (max-width: 480px) {
  .stats-cards {
    grid-template-columns: 1fr;
  }

  .card-vantages {
    grid-column: auto;
  }
}
</style>
