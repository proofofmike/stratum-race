<script setup lang="ts">
import { useRaceStore } from '@/stores/raceStore'
import type { PoolTier, TemplateMode } from '@/types'

const store = useRaceStore()

const poolTypes: { label: string; value: PoolTier }[] = [
  { label: 'Solo', value: 'small' },
  { label: 'All', value: 'all' },
]

const templateModes: { label: string; value: TemplateMode }[] = [
  { label: 'Full Template', value: 'full' },
  { label: 'Any Template', value: 'any' },
]
</script>

<template>
  <div class="pool-filters" role="toolbar" aria-label="Leaderboard filters">
    <!-- Pool type filter (All / Solo) -->
    <div class="filter-group" role="group" aria-label="Pool type filter">
      <button
        v-for="pt in poolTypes"
        :key="pt.value"
        class="filter-btn"
        :class="{ active: store.selectedTier === pt.value }"
        :aria-pressed="store.selectedTier === pt.value"
        @click="store.setTier(pt.value)"
      >
        {{ pt.label }}
      </button>
    </div>

    <!-- Template toggle (segmented control) -->
    <div class="filter-group template-toggle" role="group" aria-label="Template mode">
      <button
        v-for="mode in templateModes"
        :key="mode.value"
        class="toggle-btn"
        :class="{ active: store.templateMode === mode.value }"
        :aria-pressed="store.templateMode === mode.value"
        @click="store.setTemplateMode(mode.value)"
      >
        {{ mode.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.pool-filters {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 0;
  flex-wrap: wrap;
}

.filter-group {
  display: inline-flex;
  align-items: center;
  gap: 0;
}

.filter-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 0.8125rem;
  font-family: var(--font-sans);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
  min-height: 36px;
}

.filter-btn:first-child {
  border-radius: 0.375rem 0 0 0.375rem;
}

.filter-btn:last-child {
  border-radius: 0 0.375rem 0.375rem 0;
}

.filter-btn:not(:first-child) {
  border-left: none;
}

.filter-btn:hover {
  background: var(--surface-elevated);
  color: var(--text-primary);
}

.filter-btn.active {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}

.filter-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  z-index: 1;
}

/* Template toggle */
.template-toggle {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 9999px;
  padding: 0.1875rem;
}

.toggle-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.3125rem 0.75rem;
  border: none;
  border-radius: 9999px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.8125rem;
  font-family: var(--font-sans);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
  min-height: 36px;
  white-space: nowrap;
}

.toggle-btn:hover {
  color: var(--text-primary);
}

.toggle-btn.active {
  background: var(--accent);
  color: white;
}

.toggle-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  z-index: 1;
}

@media (max-width: 480px) {
  .pool-filters {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
  }
}
</style>
