<script setup lang="ts">
import { computed } from 'vue'
import PoolFilters from '@/components/PoolFilters.vue'
import VantageSelector from '@/components/VantageSelector.vue'
import TimeFrameFilter from '@/components/TimeFrameFilter.vue'
import StatsCards from '@/components/StatsCards.vue'
import LeaderboardTable from '@/components/LeaderboardTable.vue'
import { useRaceStore } from '@/stores/raceStore'
import { useWebSocket } from '@/services/WebSocketManager'

const store = useRaceStore()
const { status } = useWebSocket()

/**
 * True when no race data has been collected yet — a fresh install,
 * first standalone start, or just after a data reset. Everything is
 * working; the system is simply waiting for the first Bitcoin block.
 */
const awaitingFirstRace = computed(
  () =>
    !store.isLoading &&
    store.recentBlocks.length === 0 &&
    Object.keys(store.leaderboardData).length === 0
)

const connectionText = computed(() => {
  switch (status.value) {
    case 'connected':
      return 'Live connection established — results will appear automatically.'
    case 'polling':
      return 'Checking for new results every 30 seconds.'
    case 'connecting':
      return 'Connecting to the live feed…'
    default:
      return ''
  }
})
</script>

<template>
  <div class="leaderboard-view">
    <!-- Controls bar: filters on left, vantage selector on right -->
    <div class="controls-bar">
      <div class="controls-left">
        <TimeFrameFilter />
        <PoolFilters />
      </div>
      <VantageSelector />
    </div>

    <!-- Waiting-for-first-block notice (fresh install / data reset) -->
    <div v-if="awaitingFirstRace" class="waiting-banner" role="status">
      <span class="waiting-spinner" aria-hidden="true"></span>
      <div class="waiting-text">
        <strong>Waiting for the first block…</strong>
        <p>
          The collector is connected and listening to the configured pools.
          Bitcoin blocks are found every ~10 minutes on average, so the first
          results can take a little while to arrive. This page updates
          automatically — no refresh needed. {{ connectionText }}
        </p>
      </div>
    </div>

    <!-- Summary stat cards -->
    <StatsCards />

    <!-- Main leaderboard table -->
    <LeaderboardTable />
  </div>
</template>

<style scoped>
.leaderboard-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* Waiting-for-first-block banner */
.waiting-banner {
  display: flex;
  align-items: flex-start;
  gap: 0.875rem;
  padding: 1rem 1.25rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 0.5rem;
}

.waiting-spinner {
  width: 16px;
  height: 16px;
  margin-top: 0.125rem;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  flex-shrink: 0;
  animation: waiting-spin 1s linear infinite;
}

@keyframes waiting-spin {
  to { transform: rotate(360deg); }
}

.waiting-text strong {
  display: block;
  color: var(--text-primary);
  font-size: 0.9375rem;
  margin-bottom: 0.25rem;
}

.waiting-text p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.8125rem;
  line-height: 1.5;
}

@media (prefers-reduced-motion: reduce) {
  .waiting-spinner {
    animation: none;
  }
}

.controls-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
}

.controls-left {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  flex-wrap: wrap;
}

@media (max-width: 640px) {
  .controls-bar {
    flex-direction: column;
    align-items: flex-start;
  }

  .controls-left {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.75rem;
  }
}
</style>
