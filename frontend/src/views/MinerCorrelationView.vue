<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRaceStore } from '@/stores/raceStore'

const store = useRaceStore()

/** Minimum number of blocks required to display a miner */
const MIN_BLOCKS = 3

interface MinerGroup {
  miner: string
  blockCount: number
}

interface PoolCorrelation {
  pool: string
  displayName: string
  blocksWon: number
}

/** Unique key for a block: height, or epoch for null-height blocks */
function blockKey(height: number | null, epoch: number): string {
  return height != null ? `h${height}` : `e${epoch}`
}

/**
 * Group recent blocks by block_miner, keeping only miners with >= MIN_BLOCKS blocks.
 * Counts UNIQUE blocks: recentBlocks holds one entry per (block, vantage)
 * observation, so a raw entry count would multiply by the vantage count.
 * Sorted by block count descending.
 */
const minerGroups = computed<MinerGroup[]>(() => {
  const blockSets: Record<string, Set<string>> = {}

  for (const block of store.recentBlocks) {
    const miner = block.miner
    if (!miner) continue
    if (!blockSets[miner]) blockSets[miner] = new Set()
    blockSets[miner].add(blockKey(block.height, block.epoch))
  }

  return Object.entries(blockSets)
    .map(([miner, blocks]) => ({ miner, blockCount: blocks.size }))
    .filter((g) => g.blockCount >= MIN_BLOCKS)
    .sort((a, b) => b.blockCount - a.blockCount)
})

/** Currently selected miner — defaults to the miner with the most blocks */
const selectedMiner = ref<string | null>(null)

const activeMiner = computed<string | null>(() => {
  if (selectedMiner.value && minerGroups.value.some((g) => g.miner === selectedMiner.value)) {
    return selectedMiner.value
  }
  return minerGroups.value.length > 0 ? minerGroups.value[0].miner : null
})

/**
 * For the active miner, count unique blocks each pool won (full-template
 * winner). A block observed from multiple vantages counts once per pool
 * that won it from any vantage. Sorted by blocks won descending.
 */
const poolCorrelations = computed<PoolCorrelation[]>(() => {
  if (!activeMiner.value) return []

  // Collect all observations of the active miner's blocks
  const minerBlocks = store.recentBlocks.filter(
    (block) => block.miner === activeMiner.value
  )

  // Unique blocks won per pool
  const wonBlocks: Record<string, Set<string>> = {}

  for (const block of minerBlocks) {
    const winner = block.winner_nonempty || block.winner
    if (!winner) continue
    if (!wonBlocks[winner]) wonBlocks[winner] = new Set()
    wonBlocks[winner].add(blockKey(block.height, block.epoch))
  }

  // Build sorted result (most blocks won first)
  return Object.entries(wonBlocks)
    .map(([pool, blocks]) => ({
      pool,
      displayName: store.displayName(pool),
      blocksWon: blocks.size,
    }))
    .sort((a, b) => b.blocksWon - a.blocksWon)
})

function selectMiner(miner: string) {
  selectedMiner.value = miner
}
</script>

<template>
  <div class="miner-correlation-view">
    <div class="panel">
      <div class="panel-head">
        <h2>Block Miner Correlation</h2>
        <span class="panel-note">notification speed grouped by block miner</span>
      </div>

      <!-- Empty state -->
      <div v-if="minerGroups.length === 0" class="empty-state">
        <p>Not enough data yet. Miner correlation requires at least 3 blocks from the same miner.</p>
      </div>

      <!-- Miner selector chips -->
      <template v-else>
        <div class="miner-selector">
          <button
            v-for="group in minerGroups"
            :key="group.miner"
            class="miner-chip"
            :class="{ active: activeMiner === group.miner }"
            @click="selectMiner(group.miner)"
          >
            {{ group.miner }} ({{ group.blockCount }} blocks)
          </button>
        </div>

        <!-- Correlation results table -->
        <div v-if="activeMiner && poolCorrelations.length > 0" class="results-section">
          <div class="results-header">
            <span class="results-title">
              Pools that delivered <strong>{{ activeMiner }}</strong>'s blocks first
            </span>
          </div>
          <div class="table-scroll">
            <table class="correlation-table">
              <thead>
                <tr>
                  <th class="col-rank">#</th>
                  <th class="col-pool">Pool</th>
                  <th class="col-numeric">Blocks Won</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in poolCorrelations" :key="row.pool">
                  <td class="col-rank">{{ idx + 1 }}</td>
                  <td class="col-pool">{{ row.displayName }}</td>
                  <td class="col-numeric">{{ row.blocksWon }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.miner-correlation-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  overflow: hidden;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
}

.panel-head h2 {
  font-size: 1rem;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary);
}

.panel-note {
  font-size: 0.75rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.empty-state {
  padding: 3rem 2rem;
  text-align: center;
  color: var(--text-secondary);
  font-style: italic;
}

.empty-state p {
  margin: 0;
}

/* Miner selector chips */
.miner-selector {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
}

.miner-chip {
  appearance: none;
  border: 1px solid var(--border);
  background: var(--surface-elevated);
  color: var(--text-secondary);
  padding: 0.375rem 0.75rem;
  border-radius: 999px;
  font-size: 0.8125rem;
  font-family: var(--font-sans);
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.miner-chip:hover {
  color: var(--text-primary);
  border-color: var(--accent);
}

.miner-chip.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  font-weight: 500;
}

/* Results section */
.results-section {
  padding: 0;
}

.results-header {
  padding: 0.75rem 1.25rem;
  border-bottom: 1px solid var(--border);
}

.results-title {
  font-size: 0.8125rem;
  color: var(--text-secondary);
}

.results-title strong {
  color: var(--text-primary);
}

.table-scroll {
  overflow-x: auto;
}

.correlation-table {
  width: 100%;
  border-collapse: collapse;
}

.correlation-table thead th {
  position: sticky;
  top: 0;
  background: var(--surface);
  white-space: nowrap;
  user-select: none;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-secondary);
  padding: 0.625rem 1rem;
  border-bottom: 1px solid var(--border);
}

.correlation-table tbody tr {
  transition: background-color 0.15s;
}

.correlation-table tbody tr:hover {
  background-color: var(--surface-elevated);
}

.correlation-table td {
  padding: 0.625rem 1rem;
  border-bottom: 1px solid var(--border);
}

.correlation-table tbody tr:last-child td {
  border-bottom: none;
}

.col-rank {
  width: 3rem;
  text-align: center;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 0.8125rem;
}

.col-pool {
  font-family: var(--font-sans);
  font-weight: 500;
  color: var(--text-primary);
}

.col-numeric {
  text-align: right;
  font-family: var(--font-mono);
  font-size: 0.8125rem;
}

/* Highlight the fastest pool (rank 1) */
.correlation-table tbody tr:first-child .col-pool {
  color: var(--accent);
}

.correlation-table tbody tr:first-child .col-rank {
  color: var(--accent);
  font-weight: 700;
}

/* Mobile responsive */
@media (max-width: 768px) {
  .panel-head {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.25rem;
  }

  .miner-selector {
    padding: 0.75rem 1rem;
  }

  .miner-chip {
    font-size: 0.75rem;
    padding: 0.25rem 0.625rem;
  }
}
</style>
