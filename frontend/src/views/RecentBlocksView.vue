<script setup lang="ts">
import { ref } from 'vue'
import RecentBlocksList from '@/components/RecentBlocksList.vue'
import VantageSelector from '@/components/VantageSelector.vue'

const poolTypeFilter = ref<'all' | 'solo'>('all')
</script>

<template>
  <div class="recent-blocks-view">
    <div class="controls-bar">
      <h2 class="page-title">Recent Blocks</h2>
      <div class="controls-right">
        <div class="filter-group" role="group" aria-label="Pool type filter">
          <button
            class="filter-btn"
            :class="{ active: poolTypeFilter === 'solo' }"
            :aria-pressed="poolTypeFilter === 'solo'"
            @click="poolTypeFilter = 'solo'"
          >Solo</button>
          <button
            class="filter-btn"
            :class="{ active: poolTypeFilter === 'all' }"
            :aria-pressed="poolTypeFilter === 'all'"
            @click="poolTypeFilter = 'all'"
          >Any</button>
        </div>
        <VantageSelector />
      </div>
    </div>
    <RecentBlocksList :pool-type-filter="poolTypeFilter" />
  </div>
</template>

<style scoped>
.recent-blocks-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.controls-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
}

.controls-right {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}

.page-title {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
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
</style>
