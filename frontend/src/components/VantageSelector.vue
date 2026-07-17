<script setup lang="ts">
import { computed } from 'vue'
import { useRaceStore } from '@/stores/raceStore'
import { useVantageNames } from '@/composables/useVantageNames'

const store = useRaceStore()
const { formatVantage, getFlag } = useVantageNames()

/** Gate: only show selector when multiple vantage points exist */
const showSelector = computed(() => store.vantageCount >= 2)

/**
 * Available vantage points derived from vantageHealth state keys
 * or from the leaderboard aggregate data's by_vantage keys.
 */
const availableVantages = computed<string[]>(() => {
  // Primary source: vantageHealth keys
  const fromHealth = Object.keys(store.vantageHealth)
  if (fromHealth.length > 0) return fromHealth.sort()

  // Fallback: extract vantage keys from leaderboard aggregate data
  const vantageSet = new Set<string>()
  for (const poolAgg of Object.values(store.leaderboardData)) {
    if (poolAgg.by_vantage) {
      for (const v of Object.keys(poolAgg.by_vantage)) {
        vantageSet.add(v)
      }
    }
  }
  return [...vantageSet].sort()
})

function onSelect(event: Event) {
  const target = event.target as HTMLSelectElement
  store.setSelectedVantage(target.value as string | 'combined')
}
</script>

<template>
  <div v-if="showSelector" class="vantage-selector">
    <label for="vantage-select" class="vantage-label">Vantage:</label>
    <select
      id="vantage-select"
      class="vantage-dropdown"
      :value="store.selectedVantage"
      @change="onSelect"
      aria-label="Select vantage point"
    >
      <option value="combined">All Vantages</option>
      <option
        v-for="vantage in availableVantages"
        :key="vantage"
        :value="vantage"
      >
        {{ getFlag(vantage) }} {{ formatVantage(vantage) }}
      </option>
    </select>
  </div>
</template>

<style scoped>
.vantage-selector {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

.vantage-label {
  font-size: 0.75rem;
  color: var(--text-secondary, #a0a0a0);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.vantage-dropdown {
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border-color, #4a4a4a);
  border-radius: 0.25rem;
  background: var(--bg-secondary, #2a2a2a);
  color: var(--text-primary, #e0e0e0);
  font-size: 0.875rem;
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  cursor: pointer;
  min-height: 44px;
  min-width: 44px;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23a0a0a0' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 0.5rem center;
  padding-right: 1.75rem;
}

.vantage-dropdown:hover {
  background-color: var(--bg-hover, #3a3a3a);
}

.vantage-dropdown:focus-visible {
  outline: 2px solid var(--focus-color, #5b9bd5);
  outline-offset: 2px;
}

.vantage-dropdown option {
  background: var(--bg-secondary, #2a2a2a);
  color: var(--text-primary, #e0e0e0);
}
</style>
