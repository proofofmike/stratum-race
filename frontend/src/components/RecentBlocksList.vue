<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useRaceStore } from '@/stores/raceStore'
import { useTimezone } from '@/composables/useTimezone'
import { useVantageNames } from '@/composables/useVantageNames'
import type { RecentBlock } from '@/types'

const props = withDefaults(defineProps<{
  poolTypeFilter?: 'all' | 'solo'
}>(), {
  poolTypeFilter: 'all',
})

const PAGE_SIZE = 10
const currentPage = ref(0)

const router = useRouter()
const store = useRaceStore()
const { formatTimestamp } = useTimezone()
const { formatVantage, getFlag } = useVantageNames()

/**
 * Computed rows derived from the store's recentBlocks.
 * Filters by selected vantage point when not "combined".
 * When Solo filter is active, shows the fastest solo pool as winner/runner-up
 * (never hides blocks entirely).
 */
const allRows = computed(() => {
  let blocks = store.recentBlocks

  // Filter by vantage point if one is selected
  if (store.selectedVantage !== 'combined') {
    blocks = blocks.filter((b: RecentBlock) => b.vantage === store.selectedVantage)
  }

  return blocks.map((block: RecentBlock) => {
    const isSolo = props.poolTypeFilter === 'solo'

    // When Solo filter active: use solo-specific fields (with fallback to overall winner)
    let winner: string | null
    let runnerUp: string | null
    let gap: number | null

    if (isSolo && block.winner_solo !== undefined) {
      // New data with solo fields
      winner = block.winner_solo || null
      runnerUp = block.second_solo || null
      gap = block.second_solo_delay_ms ?? null
    } else if (isSolo) {
      // Old data without solo fields: fall back to winner_nonempty if it's a solo pool
      const w = block.winner_nonempty || block.winner
      const config = store.poolConfig.find((p) => p.name === w)
      winner = config && (config as any).pool_type === 'solo' ? w : null
      runnerUp = null
      gap = null
    } else {
      // Any/All filter: show overall full-template winner and runner-up
      winner = block.winner_nonempty || block.winner
      runnerUp = block.second_nonempty ?? block.second
      gap = block.second_nonempty_delay_ms ?? block.second_delay_ms
    }

    return {
      block_height: block.height,
      first_epoch: block.epoch,
      vantage: block.vantage || 'unknown',
      block_miner: block.miner,
      winner,
      runnerUp,
      gap,
      spread: block.spread_ms,
      poolsCount: block.pools_seen,
    }
  })
})

const totalPages = computed(() => {
  // Page by unique block heights (a block with 4 vantages is still 1 block)
  const uniqueHeights = new Set(allRows.value.map(r => r.block_height ?? `u${r.first_epoch}`))
  return Math.max(1, Math.ceil(uniqueHeights.size / PAGE_SIZE))
})

const rows = computed(() => {
  // Collect unique heights in order (allRows is already sorted newest-first)
  const seen = new Set<string | number>()
  const orderedHeights: (number | string)[] = []
  for (const r of allRows.value) {
    const key = r.block_height ?? `u${r.first_epoch}`
    if (!seen.has(key)) {
      seen.add(key)
      orderedHeights.push(key)
    }
  }
  // Slice to the current page's heights
  const pageHeights = new Set(orderedHeights.slice(currentPage.value * PAGE_SIZE, (currentPage.value + 1) * PAGE_SIZE))
  // Return all rows whose height is in this page's set (preserves vantage rows)
  return allRows.value.filter(r => pageHeights.has(r.block_height ?? `u${r.first_epoch}`))
})

// Reset to first page when filter or vantage changes
watch(
  () => [props.poolTypeFilter, store.selectedVantage],
  () => { currentPage.value = 0 },
)

function prevPage() {
  if (currentPage.value > 0) currentPage.value--
}

function nextPage() {
  if (currentPage.value < totalPages.value - 1) currentPage.value++
}

function navigateToBlock(height: number) {
  router.push({ name: 'block-detail', params: { height: String(height) } })
}
</script>

<template>
  <section class="recent-blocks-panel" aria-label="Recent blocks">
    <h2 class="panel-title">Recent Blocks</h2>
    <div class="table-wrapper">
      <table>
        <thead>
          <tr>
            <th class="col-height">Height</th>
            <th class="col-time">Time</th>
            <th class="col-vantage">Vantage</th>
            <th class="col-miner">Mined By</th>
            <th class="col-winner">Winner</th>
            <th class="col-runner-up">Runner-Up</th>
            <th class="col-gap">Gap (ms)</th>
            <th class="col-spread">Spread (ms)</th>
            <th class="col-pools">Pools</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, idx) in rows"
            :key="`${row.block_height ?? 'u'}-${row.vantage}-${idx}`"
            class="block-row"
            @click="row.block_height ? navigateToBlock(row.block_height) : undefined"
            tabindex="0"
            role="link"
            :aria-label="`Block ${row.block_height} details`"
            @keydown.enter="navigateToBlock(row.block_height)"
          >
            <td class="col-height">
              <span v-if="row.block_height != null">
                <router-link
                  :to="{ name: 'block-detail', params: { height: String(row.block_height) } }"
                  @click.stop
                >
                  {{ row.block_height }}
                </router-link>
              </span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="col-time">{{ formatTimestamp(row.first_epoch) }}</td>
            <td class="col-vantage"><span class="vantage-badge">{{ getFlag(row.vantage) }} {{ formatVantage(row.vantage) }}</span></td>
            <td class="col-miner">{{ row.block_miner || 'Unknown' }}</td>
            <td class="col-winner">
              <span v-if="row.winner">
                {{ store.displayName(row.winner) }}
              </span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="col-runner-up">
              <span v-if="row.runnerUp">{{ store.displayName(row.runnerUp) }}</span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="col-gap">
              <span v-if="row.gap != null">{{ row.gap.toFixed(1) }}</span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="col-spread">{{ row.spread.toFixed(1) }}</td>
            <td class="col-pools">{{ row.poolsCount }}</td>
          </tr>
          <tr v-if="rows.length === 0">
            <td colspan="9" class="empty-state">No recent blocks yet</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="totalPages > 1" class="pagination">
      <button
        class="page-btn"
        :disabled="currentPage === 0"
        @click="prevPage"
        aria-label="Previous page"
      >← Prev</button>
      <span class="page-info">{{ currentPage + 1 }} / {{ totalPages }}</span>
      <button
        class="page-btn"
        :disabled="currentPage >= totalPages - 1"
        @click="nextPage"
        aria-label="Next page"
      >Next →</button>
    </div>
  </section>
</template>

<style scoped>
.recent-blocks-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
}

.panel-title {
  font-size: 1rem;
  font-weight: 600;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
}

.table-wrapper {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.block-row {
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.block-row:hover {
  background: var(--surface-elevated);
}

.block-row:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.col-height a {
  color: var(--accent);
  font-weight: 500;
}

.col-height a:hover {
  color: var(--accent-hover);
  text-decoration: underline;
}

.col-time {
  white-space: nowrap;
  font-size: 0.8125rem;
}

.col-miner {
  font-size: 0.8125rem;
}

.col-winner {
  background: rgba(80, 200, 255, 0.12);
  font-weight: 700;
  color: var(--text-primary);
}

.col-vantage {
  font-size: 0.8125rem;
}

.vantage-badge {
  display: inline-block;
  padding: 0.125rem 0.5rem;
  border-radius: 0.25rem;
  background: var(--surface-elevated);
  color: var(--text-secondary);
  font-size: 0.8125rem;
  white-space: nowrap;
}

.col-gap,
.col-spread,
.col-pools {
  font-variant-numeric: tabular-nums;
}

.text-muted {
  color: var(--text-secondary);
}

.empty-state {
  text-align: center;
  padding: 2rem 1rem;
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-style: italic;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  padding: 0.75rem 1.25rem;
  border-top: 1px solid var(--border);
}

.page-btn {
  padding: 0.375rem 0.875rem;
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  background: var(--surface-elevated);
  color: var(--text-primary);
  font-size: 0.8125rem;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.page-btn:hover:not(:disabled) {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}

.page-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.page-info {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  min-width: 50px;
  text-align: center;
}

/* Mobile responsive: hide less important columns, allow horizontal scroll for the rest */
@media (max-width: 768px) {
  .col-runner-up,
  .col-gap,
  .col-spread,
  .col-pools {
    display: none;
  }

  .col-miner {
    max-width: 80px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
