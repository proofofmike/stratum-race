<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useWebSocket } from '@/services/WebSocketManager'
import { useRaceStore } from '@/stores/raceStore'
import { useVantageNames } from '@/composables/useVantageNames'
import type { RaceResult } from '@/types'

const { onRaceResult } = useWebSocket()
const store = useRaceStore()
const { formatVantage, getFlag } = useVantageNames()

const AUTO_DISMISS_MS = 5000

interface VantageEntry {
  flag: string
  label: string
  winner: string
}

interface ActiveToast {
  blockHeight: number
  entries: VantageEntry[]
  timer: ReturnType<typeof setTimeout>
}

const activeToast = ref<ActiveToast | null>(null)

function dismissToast() {
  if (activeToast.value) {
    clearTimeout(activeToast.value.timer)
    activeToast.value = null
  }
}

function resetDismissTimer() {
  if (activeToast.value) {
    clearTimeout(activeToast.value.timer)
    activeToast.value.timer = setTimeout(dismissToast, AUTO_DISMISS_MS)
  }
}

function handleRace(race: RaceResult) {
  const height = race.block_height
  if (height == null) return

  const vantage = race.vantage || 'unknown'
  const winner = store.displayName(race.winner_nonempty || race.winner)
  const entry: VantageEntry = {
    flag: getFlag(vantage),
    label: formatVantage(vantage),
    winner,
  }

  if (activeToast.value && activeToast.value.blockHeight === height) {
    // Same block — add a line if this vantage isn't already listed
    const exists = activeToast.value.entries.some((e) => e.label === entry.label)
    if (!exists) {
      activeToast.value.entries.push(entry)
    }
    // Reset dismiss timer
    resetDismissTimer()
  } else {
    // New block — replace the toast
    if (activeToast.value) {
      clearTimeout(activeToast.value.timer)
    }
    activeToast.value = {
      blockHeight: height,
      entries: [entry],
      timer: setTimeout(dismissToast, AUTO_DISMISS_MS),
    }
  }
}

onMounted(() => {
  onRaceResult((race: RaceResult) => {
    handleRace(race)
  })
})

onUnmounted(() => {
  if (activeToast.value) {
    clearTimeout(activeToast.value.timer)
  }
})
</script>

<template>
  <Transition name="toast">
    <div
      v-if="activeToast"
      class="toast-container"
      role="alert"
      aria-live="polite"
      @click="dismissToast"
    >
      <div class="toast-header">
        <span class="toast-icon">⛏</span>
        <span class="toast-block">Block #{{ activeToast.blockHeight.toLocaleString() }}</span>
      </div>
      <div class="toast-entries">
        <div v-for="(entry, idx) in activeToast.entries" :key="idx" class="toast-entry">
          <span class="entry-vantage">{{ entry.flag }} {{ entry.label }}</span>
          <span class="entry-arrow">→</span>
          <span class="entry-winner">{{ entry.winner }}</span>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.toast-container {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 9999;
  background: var(--surface-elevated);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 0.5rem;
  padding: 0.75rem 1rem;
  min-width: 260px;
  max-width: 400px;
  max-height: 300px;
  overflow-y: auto;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.toast-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  padding-bottom: 0.375rem;
  border-bottom: 1px solid var(--border);
}

.toast-icon {
  font-size: 1rem;
}

.toast-block {
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 0.875rem;
  color: var(--text-primary);
}

.toast-entries {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.toast-entry {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
}

.entry-vantage {
  color: var(--text-secondary);
  white-space: nowrap;
  min-width: 100px;
}

.entry-arrow {
  color: var(--text-secondary);
}

.entry-winner {
  color: var(--accent);
  font-weight: 600;
  white-space: nowrap;
}

/* Slide-in from right */
.toast-enter-active {
  transition: all 0.3s ease-out;
}

.toast-leave-active {
  transition: all 0.3s ease-in;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(100%);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(30%);
}

@media (max-width: 768px) {
  .toast-container {
    top: auto;
    bottom: 1rem;
    right: 0.5rem;
    left: 0.5rem;
    max-width: 100%;
    min-width: unset;
  }
}
</style>
