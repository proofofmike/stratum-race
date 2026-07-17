<script setup lang="ts">
import { useRaceStore } from '@/stores/raceStore'

const store = useRaceStore()

function onSelect(event: Event) {
  const target = event.target as HTMLSelectElement
  const value = target.value
  store.setOperator(value === '' ? null : value)
}
</script>

<template>
  <div class="operator-filter">
    <label for="operator-select" class="operator-label">Operator:</label>
    <select
      id="operator-select"
      class="operator-dropdown"
      :value="store.selectedOperator ?? ''"
      @change="onSelect"
      aria-label="Filter by operator"
    >
      <option value="">All Operators</option>
      <option
        v-for="operator in store.uniqueOperators"
        :key="operator"
        :value="operator"
      >
        {{ operator }}
      </option>
    </select>
  </div>
</template>

<style scoped>
.operator-filter {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

.operator-label {
  font-size: 0.75rem;
  color: var(--text-secondary, #a0a0a0);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.operator-dropdown {
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

.operator-dropdown:hover {
  background-color: var(--bg-hover, #3a3a3a);
}

.operator-dropdown:focus-visible {
  outline: 2px solid var(--focus-color, #5b9bd5);
  outline-offset: 2px;
}

.operator-dropdown option {
  background: var(--bg-secondary, #2a2a2a);
  color: var(--text-primary, #e0e0e0);
}
</style>
