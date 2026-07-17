<script setup lang="ts">
import { useTimezone } from '@/composables/useTimezone'

const { preference, toggle, timezoneName } = useTimezone()
</script>

<template>
  <button
    class="tz-toggle"
    @click="toggle"
    :title="`Showing times in ${timezoneName}`"
    :aria-label="`Toggle timezone. Currently showing ${preference === 'utc' ? 'UTC' : 'local'} time`"
  >
    🕐 {{ preference === 'utc' ? 'UTC' : 'Local' }}
  </button>
</template>

<style scoped>
.tz-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border-color, #4a4a4a);
  border-radius: 0.25rem;
  background: var(--bg-secondary, #2a2a2a);
  color: var(--text-primary, #e0e0e0);
  font-size: 0.875rem;
  cursor: pointer;
  min-height: 44px;
  min-width: 44px;
  transition: background-color 0.15s ease;
}

.tz-toggle:hover {
  background: var(--bg-hover, #3a3a3a);
}

.tz-toggle:focus-visible {
  outline: 2px solid var(--focus-color, #5b9bd5);
  outline-offset: 2px;
}
</style>
