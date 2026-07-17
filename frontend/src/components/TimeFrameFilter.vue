<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useRaceStore } from '@/stores/raceStore'
import type { TimeFrame } from '@/types'

// All time frame state and loading lives in the store (single source of
// truth): the initial page load, this filter, and the tab-resume refresh in
// main.ts all go through store.loadTimeFrame / reloadActiveTimeFrame, so the
// active button always matches the data actually displayed.
const store = useRaceStore()
const { activeTimeFrame, isLoading, aggregateLastUpdated } = storeToRefs(store)

/** Human-readable "updated X min ago" text */
const updatedText = computed((): string | null => {
  if (!aggregateLastUpdated.value) return null
  const then = new Date(aggregateLastUpdated.value).getTime()
  const seconds = Math.floor((Date.now() - then) / 1000)
  if (seconds < 60) return 'Updated <1 min ago'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `Updated ${minutes} min ago`
  return `Updated ${Math.floor(minutes / 60)}h ago`
})

async function selectFrame(frame: TimeFrame) {
  await store.loadTimeFrame(frame)
}
</script>

<template>
  <div class="timeframe-filter">
    <span class="filter-label">Period:</span>
    <div class="frame-buttons">
      <button
        :class="{ active: activeTimeFrame === 'last10' }"
        @click="selectFrame('last10')"
        :disabled="isLoading"
      >
        Last 10
      </button>
      <button
        :class="{ active: activeTimeFrame === 'last50' }"
        @click="selectFrame('last50')"
        :disabled="isLoading"
      >
        Last 50
      </button>
      <button
        :class="{ active: activeTimeFrame === '24h' }"
        @click="selectFrame('24h')"
        :disabled="isLoading"
      >
        24h
      </button>
      <button
        :class="{ active: activeTimeFrame === '7d' }"
        @click="selectFrame('7d')"
        :disabled="isLoading"
      >
        7 days
      </button>
    </div>
    <span v-if="updatedText && (activeTimeFrame === 'last10' || activeTimeFrame === 'last50')" class="updated-text">
      {{ updatedText }}
    </span>
  </div>
</template>

<style scoped>
.timeframe-filter {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

.filter-label {
  font-size: 0.75rem;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.frame-buttons {
  display: flex;
  gap: 0.25rem;
}

.frame-buttons button {
  padding: 0.375rem 0.75rem;
  font-size: 0.8125rem;
  border-radius: 0.25rem;
  border: 1px solid var(--border);
  background: var(--surface-elevated);
  color: var(--text-secondary);
  transition: all 0.15s;
  min-height: 36px;
}

.frame-buttons button:hover:not(:disabled) {
  background: var(--border);
  color: var(--text-primary);
}

.frame-buttons button.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.frame-buttons button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.updated-text {
  font-size: 0.6875rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-style: italic;
}
</style>
