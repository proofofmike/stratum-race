<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRaceStore } from '@/stores/raceStore'
import ValidationStatusBadge from '@/components/ValidationStatusBadge.vue'
import type { ValidationStatus } from '@/types'

const store = useRaceStore()

// ─── Data loading ─────────────────────────────────────────────────────────────

onMounted(async () => {
  await Promise.all([
    store.loadPoolConfig(),
    store.loadRecentBlocks(),
    store.loadTimeFrame('7d'),
    store.loadPoolStatsLatest(),
  ])
})

// ─── Filters ──────────────────────────────────────────────────────────────────

const filterPayoutType = ref<string>('all')
const filterLocation   = ref<string>('all')
const filterStatus     = ref<string>('all')
const filterLastBlock  = ref<string>('all')  // 'all' | '7d' | '30d' | '1yr'

const payoutTypes = ['all', 'solo']

/** Determine if a pool offers solo mining based on payout_type or pool_type */
function isSoloPool(payoutType: string, poolType: string): boolean {
  const pt = (payoutType || '').toLowerCase()
  return pt.includes('solo') || pt === 'hybrid' || pt === 'tides' || poolType === 'solo'
}

const locations = computed(() => {
  const locs = new Set<string>()
  for (const p of store.poolConfig) {
    if (p.location) locs.add(p.location)
  }
  return ['all', ...Array.from(locs).sort()]
})

// ─── Sort ─────────────────────────────────────────────────────────────────────

const sortCol = ref<string>('sr_rank')
const sortAsc = ref(true)

function sort(col: string) {
  if (sortCol.value === col) {
    sortAsc.value = !sortAsc.value
  } else {
    sortCol.value = col
    sortAsc.value = true
  }
}

function sortIndicator(col: string): string {
  if (sortCol.value !== col) return ''
  return sortAsc.value ? ' ▲' : ' ▼'
}

// ─── Joined rows ─────────────────────────────────────────────────────────────

interface PoolRow {
  name: string
  displayName: string
  payoutType: string
  feePct: number | null
  fees: Record<string, number> | null
  location: string
  websiteUrl: string | null
  stratumsStr: string
  // Stats
  hashrateFormatted: string | null
  activeUsers: number | null
  fetchOk: boolean
  // SR data
  srRank: number | null
  srMedian: number | null
  // Last block
  lastBlockHeight: number | null
  lastBlockEpoch: number | null
  lastMinedEpoch: number | null
  lastMinedHeight: number | null
  // Validation
  validationStatus: ValidationStatus
  lastVerifiedUtc: string | null
}

// Compute SR ranks from leaderboard data
const srRanks = computed(() => {
  const sorted = Object.keys(store.leaderboardData)
    .map(name => ({
      name,
      median: store.leaderboardData[name]?.combined?.median_ms ?? null,
    }))
    .filter(p => p.median !== null)
    .sort((a, b) => (a.median as number) - (b.median as number))

  const ranks: Record<string, number> = {}
  sorted.forEach((p, i) => { ranks[p.name] = i + 1 })
  return ranks
})

// Find last block per pool from recent blocks
const lastBlockByPool = computed(() => {
  const map: Record<string, { height: number; epoch: number }> = {}
  for (const block of store.recentBlocks) {
    const winner = block.winner_nonempty || block.winner
    if (winner && !map[winner]) {
      map[winner] = { height: block.height, epoch: block.epoch }
    }
    if (block.winner_solo && !map[block.winner_solo]) {
      map[block.winner_solo] = { height: block.height, epoch: block.epoch }
    }
  }
  return map
})

const now = ref(Math.floor(Date.now() / 1000))
const nowTimer = setInterval(() => { now.value = Math.floor(Date.now() / 1000) }, 10000)
onBeforeUnmount(() => clearInterval(nowTimer))

function timeAgo(epoch: number): string {
  const diff = now.value - epoch
  if (diff < 60) return '<1m ago'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function epochWithinWindow(epoch: number | null, window: string): boolean {
  if (!epoch) return false
  const diff = now.value - epoch
  if (window === '7d')  return diff <= 86400 * 7
  if (window === '30d') return diff <= 86400 * 30
  if (window === '1yr') return diff <= 86400 * 365
  return true
}

const rows = computed<PoolRow[]>(() => {
  return store.poolConfig.map(pool => {
    const p = pool
    const snap = store.poolStatsLatest?.pools?.[pool.name]
    const lastBlock = lastBlockByPool.value[pool.name] ?? null
    const srMedian = store.leaderboardData[pool.name]?.combined?.median_ms ?? null

    // Derive stratum display string
    const endpoints = p.stratum_endpoints as Array<{ host: string; port: number; label?: string }> | undefined
    let stratumsStr = ''
    if (endpoints?.length) {
      stratumsStr = endpoints.map(e => `${e.host}:${e.port}${e.label ? ` (${e.label})` : ''}`).join(', ')
    } else if (pool.host) {
      stratumsStr = `${pool.host}:${pool.port}`
    }

    return {
      name: pool.name,
      displayName: pool.display_name,
      payoutType: p.payout_type ?? (p.pool_type === 'solo' ? 'Solo' : 'Shared'),
      feePct: p.fee_pct ?? null,
      fees: p.fees ?? null,
      location: p.location ?? '—',
      websiteUrl: p.website_url ?? null,
      stratumsStr,
      hashrateFormatted: snap?.hashrate_formatted ?? null,
      activeUsers: snap?.active_users ?? null,
      fetchOk: snap?.fetch_ok ?? false,
      srRank: srRanks.value[pool.name] ?? null,
      srMedian,
      lastBlockHeight: lastBlock?.height ?? null,
      lastBlockEpoch: lastBlock?.epoch ?? null,
      lastMinedEpoch: snap?.recent_blocks?.[0]?.timestamp ?? null,
      lastMinedHeight: snap?.recent_blocks?.[0]?.height ?? null,
      validationStatus: (snap?.validation_status as ValidationStatus) ?? 'pending',
      lastVerifiedUtc: snap?.last_verified_utc ?? null,
    }
  })
})

// ─── Filtered + sorted rows ───────────────────────────────────────────────────

const filteredRows = computed<PoolRow[]>(() => {
  let r = rows.value

  if (filterPayoutType.value !== 'all') {
    r = r.filter(p => isSoloPool(p.payoutType, store.poolConfig.find(c => c.name === p.name)?.pool_type || ''))
  }
  if (filterLocation.value !== 'all') {
    r = r.filter(p => p.location === filterLocation.value || p.location === 'Global')
  }
  if (filterStatus.value !== 'all') {
    r = r.filter(p => p.validationStatus === filterStatus.value)
  }
  if (filterLastBlock.value !== 'all') {
    r = r.filter(p => epochWithinWindow(p.lastMinedEpoch, filterLastBlock.value))
  }

  // Sort
  r = [...r].sort((a, b) => {
    let av: any, bv: any
    switch (sortCol.value) {
      case 'name':       av = a.displayName;       bv = b.displayName; break
      case 'payout':     av = a.payoutType;        bv = b.payoutType; break
      case 'fee':        av = a.feePct ?? 999;     bv = b.feePct ?? 999; break
      case 'hashrate':   av = store.poolStatsLatest?.pools?.[a.name]?.hashrate_value ?? -1
                         bv = store.poolStatsLatest?.pools?.[b.name]?.hashrate_value ?? -1; break
      case 'users':      av = a.activeUsers ?? -1; bv = b.activeUsers ?? -1; break
      case 'sr_rank':    av = a.srRank ?? 999;     bv = b.srRank ?? 999; break
      case 'last_block': av = a.lastMinedEpoch ?? 0; bv = b.lastMinedEpoch ?? 0
                         // reverse: more recent = lower sort value
                         return sortAsc.value ? bv - av : av - bv
      default:           av = a.srRank ?? 999;     bv = b.srRank ?? 999
    }
    if (typeof av === 'string') {
      const cmp = av.localeCompare(bv)
      return sortAsc.value ? cmp : -cmp
    }
    return sortAsc.value ? av - bv : bv - av
  })

  return r
})

function formatFee(fee: number | null, fees: Record<string, number> | null): string {
  if (fees && Object.keys(fees).length > 0) {
    // When solo filter is active, show only the solo fee
    if (filterPayoutType.value === 'solo') {
      const soloFee = fees['Solo'] ?? fees['Hybrid'] ?? fees['TIDES'] ?? fees['Lotto']
      if (soloFee !== undefined) return soloFee === 0 ? '0%' : `${soloFee}%`
      // Fallback to first entry
      const first = Object.values(fees)[0]
      return first === 0 ? '0%' : `${first}%`
    }
    const entries = Object.entries(fees)
    if (entries.length === 1) {
      return entries[0][1] === 0 ? '0%' : `${entries[0][1]}%`
    }
    return entries.map(([m, f]) => `${m}: ${f}%`).join('<br>')
  }
  if (fee === null) return '—'
  return fee === 0 ? '0%' : `${fee}%`
}

function formatFeeCompact(fee: number | null, fees: Record<string, number> | null): string {
  if (fees && Object.keys(fees).length > 0) {
    const values = Object.values(fees)
    if (values.length === 1) return values[0] === 0 ? '0%' : `${values[0]}%`
    const min = Math.min(...values)
    const max = Math.max(...values)
    if (min === max) return min === 0 ? '0%' : `${min}%`
    return `${min}-${max}%`
  }
  if (fee === null) return '—'
  return fee === 0 ? '0%' : `${fee}%`
}

function formatMedian(ms: number | null): string {
  if (ms === null) return '—'
  return ms.toFixed(0) + ' ms'
}
</script>

<template>
  <div class="pools-view">
    <header class="view-header">
      <div>
        <h2>Mining Pools</h2>
        <p class="subtitle">
          Authoritative list of Bitcoin mining pools tracked by StratumRace.
          All listed pools have been independently verified to be delivering
          work on the correct prevhash.
        </p>
      </div>
    </header>

    <!-- Filters -->
    <div class="filters">
      <div class="filter-group">
        <label>Pool Type</label>
        <select v-model="filterPayoutType">
          <option v-for="t in payoutTypes" :key="t" :value="t">
            {{ t === 'all' ? 'All Types' : 'Solo Only' }}
          </option>
        </select>
      </div>
      <div class="filter-group">
        <label>Location</label>
        <select v-model="filterLocation">
          <option v-for="l in locations" :key="l" :value="l">
            {{ l === 'all' ? 'All Locations' : l }}
          </option>
        </select>
      </div>
      <div class="filter-group">
        <label>Status</label>
        <select v-model="filterStatus">
          <option value="all">All</option>
          <option value="verified">Verified</option>
          <option value="pending">Pending</option>
          <option value="warning">Warning</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Block Found In</label>
        <select v-model="filterLastBlock">
          <option value="all">Any Time</option>
          <option value="7d">Last 7 Days</option>
          <option value="30d">Last 30 Days</option>
          <option value="1yr">Last Year</option>
        </select>
      </div>
    </div>

    <!-- Mobile cards (shown on small screens only) -->
    <div class="mobile-cards">
      <router-link
        v-for="row in filteredRows"
        :key="'card-' + row.name"
        :to="`/pool/${row.name}`"
        class="pool-card"
      >
        <div class="card-top">
          <span class="card-name">{{ row.displayName }}</span>
          <ValidationStatusBadge
            :status="row.validationStatus"
            :last-verified-utc="row.lastVerifiedUtc"
          />
        </div>
        <div class="card-meta">
          <span class="tag tag-type">{{ row.payoutType }}</span>
          <span class="card-fee">{{ formatFeeCompact(row.feePct, row.fees) }}</span>
          <span v-if="row.location" class="card-loc">{{ row.location }}</span>
        </div>
        <div class="card-stats">
          <div class="card-stat" v-if="row.srRank !== null">
            <span class="card-stat-label">SR Rank</span>
            <span class="card-stat-value rank-badge">#{{ row.srRank }}</span>
          </div>
          <div class="card-stat" v-if="row.hashrateFormatted">
            <span class="card-stat-label">Hashrate</span>
            <span class="card-stat-value">{{ row.hashrateFormatted }}</span>
          </div>
          <div class="card-stat" v-if="row.lastMinedEpoch">
            <span class="card-stat-label">Last Block</span>
            <span class="card-stat-value">{{ timeAgo(row.lastMinedEpoch) }}</span>
          </div>
        </div>
        <span class="card-cta">View details →</span>
      </router-link>
    </div>

    <!-- Table (hidden on mobile) -->
    <div class="table-container">
      <table class="pools-table">
        <thead>
          <tr>
            <th class="col-name sticky-col" @click="sort('name')">Pool{{ sortIndicator('name') }}</th>
            <th @click="sort('payout')">Type{{ sortIndicator('payout') }}</th>
            <th @click="sort('fee')">Fee{{ sortIndicator('fee') }}</th>
            <th class="col-location">Location</th>
            <th
              @click="sort('hashrate')"
              title="Reported by pool. Not independently verified by StratumRace."
            >Hashrate ⓘ{{ sortIndicator('hashrate') }}</th>
            <th
              @click="sort('users')"
              title="Reported by pool. Not independently verified by StratumRace."
            >Users ⓘ{{ sortIndicator('users') }}</th>
            <th @click="sort('sr_rank')">SR 7d Rank{{ sortIndicator('sr_rank') }}</th>
            <th class="col-median">SR Median</th>
            <th @click="sort('last_block')">Last Block{{ sortIndicator('last_block') }}</th>
            <th class="col-status">Validation</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filteredRows" :key="row.name">
            <!-- Pool name -->
            <td class="col-name sticky-col">
              <div class="pool-name-cell">
                <router-link :to="`/pool/${row.name}`" class="pool-link">
                  {{ row.displayName }}
                </router-link>
                <a
                  v-if="row.websiteUrl"
                  :href="row.websiteUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="ext-link"
                  :aria-label="`Visit ${row.displayName} website`"
                >↗</a>
              </div>
            </td>

            <!-- Type -->
            <td>
              <span class="tag tag-type">{{ row.payoutType }}</span>
            </td>

            <!-- Fee -->
            <td class="mono fee-cell">
              <span v-for="(line, i) in formatFee(row.feePct, row.fees).split('<br>')" :key="i" class="fee-line">{{ line }}</span>
            </td>

            <!-- Location -->
            <td class="col-location">{{ row.location }}</td>

            <!-- Hashrate -->
            <td class="mono">
              <span v-if="row.hashrateFormatted">{{ row.hashrateFormatted }}</span>
              <span v-else class="muted">—</span>
            </td>

            <!-- Users -->
            <td class="mono">
              <span v-if="row.activeUsers !== null">{{ row.activeUsers.toLocaleString() }}</span>
              <span v-else class="muted">—</span>
            </td>

            <!-- SR rank -->
            <td class="mono center">
              <span v-if="row.srRank !== null" class="rank-badge">#{{ row.srRank }}</span>
              <span v-else class="muted">—</span>
            </td>

            <!-- SR median -->
            <td class="mono col-median">{{ formatMedian(row.srMedian) }}</td>

            <!-- Last block mined -->
            <td class="mono">
              <span v-if="row.lastMinedHeight && row.lastMinedEpoch">
                <a :href="`https://mempool.space/block/${row.lastMinedHeight}`" target="_blank" rel="noopener" class="block-link">
                  #{{ row.lastMinedHeight }}
                </a>
                <br><span class="muted time-ago">{{ timeAgo(row.lastMinedEpoch) }}</span>
              </span>
              <span v-else class="muted">—</span>
            </td>

            <!-- Validation -->
            <td class="col-status">
              <ValidationStatusBadge
                :status="row.validationStatus"
                :last-verified-utc="row.lastVerifiedUtc"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Footer disclaimer -->
    <p class="disclaimer">
      ⓘ Hashrate, user counts, and pool statistics marked with ⓘ are reported
      by the pool operator and are not independently verified by StratumRace.
      Validation status is derived solely from StratumRace's own race measurements.
    </p>

    <div class="operator-callout">
      <strong>Pool operators:</strong> Want your pool listed or stats displayed?
      <a href="https://github.com/proofofmike/stratum-race/issues" target="_blank" rel="noopener">Open a request on GitHub</a>
      to get added to the directory.
    </div>
  </div>
</template>

<style scoped>
.pools-view {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;

  --row-bg: #1c1c22;
  --row-alt: #282836;
  --row-hover: #33334a;
  --row-line: #3d3d50;
  --text-dim: #b9b9c9;
}

.view-header h2 {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.25rem;
}

.subtitle {
  color: var(--text-dim);
  font-size: 0.9375rem;
  line-height: 1.55;
  margin: 0;
  max-width: 680px;
}

/* Filters */
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: flex-end;
}

.filter-group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.filter-group label {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
}

.filter-group select {
  background: var(--surface-elevated);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  color: var(--text-primary);
  font-size: 0.875rem;
  padding: 0.375rem 0.625rem;
  min-height: 36px;
  min-width: 130px;
}

/* Table */
.table-container {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--row-bg);
}

.pools-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.pools-table thead {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--surface-elevated);
}

.pools-table th {
  padding: 0.75rem;
  text-align: left;
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #d2d2e0;
  background: var(--surface-elevated);
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
  border-bottom: 2px solid var(--row-line);
}

.pools-table th:hover { color: var(--accent-hover); }

.pools-table td {
  padding: 0.6875rem 0.75rem;
  border-bottom: 1px solid var(--row-line);
  vertical-align: middle;
  font-family: var(--font-sans);
  font-size: 0.875rem;
  line-height: 1.45;
  color: var(--text-primary);
  background: var(--row-bg);
}

.pools-table td.mono,
.pools-table .mono { font-family: var(--font-mono); }

.pools-table tbody tr:nth-child(even) td { background: var(--row-alt); }
.pools-table tbody tr:hover td { background: var(--row-hover); }
.pools-table tbody tr:last-child td { border-bottom: none; }

.sticky-col {
  position: sticky;
  left: 0;
  z-index: 1;
  min-width: 170px;
  border-right: 1px solid var(--row-line);
}

thead .sticky-col { z-index: 3; }

.pool-name-cell {
  display: flex;
  align-items: center;
  gap: 0.375rem;
}

.pool-link {
  color: var(--text-primary);
  font-weight: 600;
  font-size: 0.9375rem;
  text-decoration: none;
}

.pool-link:hover { color: var(--accent-hover); text-decoration: underline; }

.ext-link {
  color: var(--text-dim);
  font-size: 0.8125rem;
  text-decoration: none;
  line-height: 1;
}

.ext-link:hover { color: var(--accent-hover); }

.block-link {
  color: var(--accent-hover);
  text-decoration: none;
  font-family: var(--font-mono);
  font-weight: 600;
}

.block-link:hover { text-decoration: underline; }

.tag {
  display: inline-block;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  white-space: nowrap;
}

.tag-type {
  background: rgba(74, 154, 240, 0.18);
  color: #8cc2fb;
}

.time-ago {
  font-size: 0.8125rem;
  display: block;
  color: var(--text-dim);
}
.center { text-align: center; }
.muted { color: var(--text-dim); }

.rank-badge {
  display: inline-block;
  font-family: var(--font-mono);
  font-weight: 700;
  color: #8cc2fb;
  background: rgba(74, 154, 240, 0.18);
  border-radius: 0.25rem;
  padding: 0.1rem 0.4rem;
}

.fee-cell {
  white-space: normal;
  max-width: 150px;
  font-size: 0.8125rem;
  line-height: 1.5;
}
.fee-line { display: block; }

.col-location { min-width: 100px; }
.col-median   { min-width: 90px; }
.col-status   { min-width: 110px; }

/* Disclaimer */
.disclaimer {
  font-size: 0.8125rem;
  color: var(--text-dim);
  margin: 0;
  padding: 0.75rem 1rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  line-height: 1.6;
}

.operator-callout {
  font-size: 0.875rem;
  color: var(--text-primary);
  padding: 0.875rem 1rem;
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: 0.375rem;
  line-height: 1.6;
}
.operator-callout a { color: var(--accent-hover); text-decoration: underline; }
.operator-callout a:hover { color: var(--accent-hover); }

/* Mobile cards */
.mobile-cards { display: none; }

@media (max-width: 768px) {
  .table-container { display: none; }
  .mobile-cards { display: flex; flex-direction: column; gap: 0.75rem; }

  .pool-card {
    display: block;
    background: var(--row-bg);
    border: 1px solid var(--row-line);
    border-radius: 0.5rem;
    padding: 0.875rem;
    text-decoration: none;
    color: inherit;
    transition: border-color 0.15s, background-color 0.15s;
  }
  .pool-card:hover { border-color: var(--accent); background: var(--row-alt); }

  .card-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
  }
  .card-name { font-weight: 600; font-size: 1.0625rem; color: var(--text-primary); }

  .card-meta {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 0.75rem;
    flex-wrap: wrap;
  }
  .card-fee { font-family: var(--font-mono); font-size: 0.8125rem; color: var(--text-primary); }
  .card-loc { font-size: 0.8125rem; color: var(--text-dim); }

  .card-stats {
    display: flex;
    gap: 1.25rem;
    flex-wrap: wrap;
  }
  .card-stat { display: flex; flex-direction: column; gap: 0.1875rem; }
  .card-stat-label { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); }
  .card-stat-value { font-family: var(--font-mono); font-size: 0.875rem; font-weight: 600; color: var(--text-primary); }
  .card-cta { display: block; margin-top: 0.75rem; font-size: 0.8125rem; color: var(--accent-hover); font-weight: 600; }
}</style>
