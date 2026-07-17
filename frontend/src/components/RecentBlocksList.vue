<script setup lang="ts">
import { computed } from 'vue'
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

const router = useRouter()
const store = useRaceStore()
const { formatTimestamp } = useTimezone()
const { formatVantage, getFlag } = useVantageNames()

/**
 * Computed rows derived from the store's recentBlocks.
 * Filters by selected vantage point when not "combined".
 * Filters by pool type when "solo" is selected (winner must be a solo pool).
 */
const rows = computed(() => {
  let blocks = store.recentBlocks

  // Filter by vantage point if one is selected
  if (store.selectedVantage !== 'combined') {
    blocks = blocks.filter((b: RecentBlock) => b.vantage === store.selectedVantage)
  }

  // Filter by pool type: only show blocks where the winner is a solo pool
  if (props.poolTypeFilter === 'solo' && store.poolConfig.length > 0) {
    blocks = blocks.filter((b: RecentBlock) => {
      const winner = b.winner_nonempty || b.winner
      if (!winner) return false
      const config = store.poolConfig.find((p) => p.name === winner)
      if (!config) return false
      return (config as any).pool_type === 'solo'
    })
  }

  return blocks.map((block: RecentBlock) => {
    return {
      block_height: block.height,
      first_epoch: block.epoch,
      vantage: block.vantage || 'unknown',
      block_miner: block.miner,
      winner: block.winner_nonempty || block.winner,
      runnerUp: block.second,
      gap: block.second_delay_ms,
      spread: block.spread_ms,
      poolsCount: block.pools_seen,
    }
  })
})

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
                  {{ row.block_height.toLocaleString() }}
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
  </section>
</template>

<style scoped>
.recent-blocks-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  overflow: hidden;
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

/* Mobile responsive: show only Height, Time, Vantage, Mined By */
@media (max-width: 768px) {
  .col-winner,
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
