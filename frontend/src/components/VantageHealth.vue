<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRaceStore } from '@/stores/raceStore'
import { useTimezone } from '@/composables/useTimezone'
import type { VantageHealth } from '@/types'

const store = useRaceStore()
const { formatDatetime } = useTimezone()

/** Gate: only show health display when multiple vantage points exist */
const showHealth = computed(() => store.vantageCount >= 2)

/** Reactive tick updated every 30 seconds to recompute online/offline status */
const now = ref(Date.now())
let intervalId: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  intervalId = setInterval(() => {
    now.value = Date.now()
  }, 30_000)
})

onUnmounted(() => {
  if (intervalId !== null) {
    clearInterval(intervalId)
  }
})

/** Threshold for online/offline determination: 10 minutes in milliseconds */
const HEARTBEAT_TIMEOUT_MS = 10 * 60 * 1000

interface VantageStatus {
  region: string
  isOnline: boolean
  connectedPools: number
  lastHeartbeat: string
  lastHeartbeatFormatted: string
}

/**
 * Computes vantage statuses based on store.vantageHealth and reactive `now`.
 * A vantage is "online" if last_heartbeat_utc is within 10 minutes of now.
 */
const vantageStatuses = computed<VantageStatus[]>(() => {
  const healthMap = store.vantageHealth as Record<string, VantageHealth>
  const entries = Object.entries(healthMap)

  return entries
    .map(([region, health]) => {
      const heartbeatTime = new Date(health.last_heartbeat_utc).getTime()
      const gap = now.value - heartbeatTime
      const isOnline = gap <= HEARTBEAT_TIMEOUT_MS

      return {
        region,
        isOnline,
        connectedPools: health.connected_pools,
        lastHeartbeat: health.last_heartbeat_utc,
        lastHeartbeatFormatted: formatDatetime(health.last_heartbeat_utc),
      }
    })
    .sort((a, b) => a.region.localeCompare(b.region))
})

const hasVantages = computed(() => vantageStatuses.value.length > 0)
</script>

<template>
  <div v-if="showHealth && hasVantages" class="vantage-health" role="status" aria-label="Vantage point health status">
    <span
      v-for="vantage in vantageStatuses"
      :key="vantage.region"
      class="vantage-pill"
      :class="{ online: vantage.isOnline, offline: !vantage.isOnline }"
      :title="`${vantage.region}: ${vantage.isOnline ? 'Online' : 'Offline'}\nPools: ${vantage.connectedPools}\nLast heartbeat: ${vantage.lastHeartbeatFormatted}`"
      :aria-label="`${vantage.region} is ${vantage.isOnline ? 'online' : 'offline'}, ${vantage.connectedPools} pools connected`"
    >
      <span class="status-dot" :class="{ online: vantage.isOnline, offline: !vantage.isOnline }" aria-hidden="true"></span>
      <span class="region-label">{{ vantage.region }}</span>
      <span class="pool-count">{{ vantage.connectedPools }} pools</span>
    </span>
  </div>
</template>

<style scoped>
.vantage-health {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.vantage-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.25rem 0.625rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  border: 1px solid var(--border-color, #4a4a4a);
  background: var(--bg-secondary, #2a2a2a);
  color: var(--text-primary, #e0e0e0);
  cursor: default;
  white-space: nowrap;
  transition: background-color 0.15s ease;
}

.vantage-pill:hover {
  background: var(--bg-hover, #3a3a3a);
}

.status-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot.online {
  background-color: #4ade80;
  box-shadow: 0 0 4px rgba(74, 222, 128, 0.5);
}

.status-dot.offline {
  background-color: #f87171;
  box-shadow: 0 0 4px rgba(248, 113, 113, 0.5);
}

.region-label {
  color: var(--text-primary, #e0e0e0);
  font-weight: 500;
}

.pool-count {
  color: var(--text-secondary, #a0a0a0);
  font-size: 0.6875rem;
}
</style>
