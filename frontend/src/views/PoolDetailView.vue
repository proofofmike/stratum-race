<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRaceStore } from '@/stores/raceStore'
import { useVantageNames } from '@/composables/useVantageNames'
import ValidationStatusBadge from '@/components/ValidationStatusBadge.vue'
import PoolHistoryChart from '@/components/PoolHistoryChart.vue'
import type { TimeFrame, PoolStats, RecentBlock, RaceResult, PoolHistoryPoint, ValidationStatus } from '@/types'

const route = useRoute()
const router = useRouter()
const store = useRaceStore()
const { formatVantage } = useVantageNames()

const VANTAGE_COLORS = ['var(--accent)', '#f59e0b', '#10b981', '#ec4899', '#8b5cf6']

const poolName = computed(() => {
  const raw = decodeURIComponent(route.params.poolName as string)
  return /^[a-z0-9_-]+$/.test(raw) ? raw : ''
})
const poolConfigEntry = computed(() => store.poolConfig.find(p => p.name === poolName.value))
const poolExists = computed(() => !!poolConfigEntry.value)

// ─── Section 2: Live stats ────────────────────────────────────────────────────

const snap = computed(() => store.poolStatsLatest?.pools?.[poolName.value] ?? null)
const networkDifficulty = computed(() => store.poolStatsLatest?.network_difficulty ?? null)

const expectedDays = computed(() => {
  const hr = snap.value?.hashrate_value
  const diff = networkDifficulty.value
  if (!hr || !diff || hr <= 0) return null
  const seconds = (diff * Math.pow(2, 32)) / hr
  return seconds / 86400
})

function formatExpected(days: number | null): string {
  if (days === null) return '—'
  if (days < 1/24) return `~${Math.round(days * 1440)} min`
  if (days < 1)    return `~${Math.round(days * 24)} hr`
  if (days < 30)   return `~${days.toFixed(1)} days`
  return `~${(days / 30).toFixed(1)} months`
}

function formatFetchedAgo(utc: string | null): string {
  if (!utc) return ''
  const diff = (Date.now() - new Date(utc).getTime()) / 1000
  if (diff < 60) return 'Updated just now'
  if (diff < 3600) return `Updated ${Math.floor(diff / 60)}m ago`
  return `Updated ${Math.floor(diff / 3600)}h ago`
}

// ─── History charts ───────────────────────────────────────────────────────────

const chartMetric = ref<'hashrate' | 'users' | 'workers'>('hashrate')
const chartRange = ref('7d')
const chartData = ref<PoolHistoryPoint[]>([])
const chartLoading = ref(false)

async function loadChartData() {
  if (!poolExists.value) return
  chartLoading.value = true
  try {
    // Fetch the rolling history JSON file (static, no Lambda)
    const suffix = chartRange.value === '30d' ? '-30d' : chartRange.value === '1yr' ? '-1yr' : ''
    const url = `/api/pool-history/${poolName.value}${suffix}.json`
    const resp = await fetch(url)
    if (resp.ok) {
      const file = await resp.json()
      let points = file.data ?? []
      // Filter to requested range for the 7d file
      if (!suffix && chartRange.value === '24h') {
        const cutoff = Date.now() / 1000 - 86400
        points = points.filter((p: any) => p.timestamp >= cutoff)
      }
      // Map to PoolHistoryPoint based on selected metric
      chartData.value = points.map((p: any) => ({
        timestamp: p.timestamp,
        value: chartMetric.value === 'hashrate' ? (p.hashrate_value ?? 0)
             : chartMetric.value === 'users' ? (p.active_users ?? 0)
             : (p.active_workers ?? 0),
        formatted: chartMetric.value === 'hashrate' ? (p.hashrate_formatted ?? '—')
                 : chartMetric.value === 'users' ? String(p.active_users ?? 0)
                 : String(p.active_workers ?? 0),
      }))
    } else {
      chartData.value = []
    }
  } catch {
    chartData.value = []
  } finally {
    chartLoading.value = false
  }
}

// ─── Blocks Found (from pool-stats-latest.json, populated by Lambda) ─────────

const blocksFound = computed<number | null>(() => snap.value?.blocks_found ?? null)
const recentPoolBlocks = computed<any[]>(() => snap.value?.recent_blocks ?? [])

// Whether this pool exposes a worker count. Some pool APIs report no usable
// worker number (e.g. SoloPool, whose API counts stratum connections, not
// devices), so the Workers chart tab is hidden rather than showing a flat zero.
const hasWorkers = computed<boolean>(() => (snap.value?.active_workers ?? null) !== null)

function formatBlockAge(ts: number | null): string {
  if (!ts) return ''
  const diff = Math.floor(Date.now() / 1000) - ts
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function truncateAddr(addr: string): string {
  if (!addr || addr.length < 20) return addr
  return addr.slice(0, 10) + '...' + addr.slice(-6)
}

// ─── Section 3: SR perf (from existing PoolDetailView logic) ─────────────────

const localTimeFrame = ref<TimeFrame>('last50')
const isLoadingStats = ref(false)
const localLeaderboard = ref<Record<string, any>>({})
const raceFiles = ref<RaceResult[]>([])
const isLoadingRaces = ref(false)

const TIME_FRAME_PATHS: Record<string, string> = {
  last10: 'recent-10', last50: 'recent-50',
}

async function loadStats() {
  if (!poolExists.value) return
  isLoadingStats.value = true
  try {
    const resp = await fetch(`/api/aggregates/${TIME_FRAME_PATHS[localTimeFrame.value]}.json`)
    if (!resp.ok) throw new Error()
    const data = await resp.json()
    localLeaderboard.value = data.pools ?? {}
  } catch { localLeaderboard.value = {} }
  finally { isLoadingStats.value = false }
}

async function loadRaceFiles() {
  if (!poolExists.value) return
  isLoadingRaces.value = true
  let blocks: RecentBlock[] = []
  try {
    const resp = await fetch('/api/recent/recent-blocks.json')
    if (resp.ok) blocks = await resp.json()
  } catch {}
  if (!blocks.length) blocks = store.recentBlocks
  if (!blocks.length) { isLoadingRaces.value = false; return }

  const limit = localTimeFrame.value === 'last10' ? 10 : 50
  const seenHeights = new Set<number>()
  const toFetch: RecentBlock[] = []
  for (const block of blocks) {
    if (block.height == null) continue
    seenHeights.add(block.height)
    if (seenHeights.size > limit) break
    toFetch.push(block)
  }

  const results: RaceResult[] = []
  for (let i = 0; i < toFetch.length; i += 10) {
    const batch = toFetch.slice(i, i + 10)
    const fetched = await Promise.all(batch.map(async block => {
      const dt = new Date(block.epoch * 1000)
      const y = dt.getUTCFullYear()
      const m = String(dt.getUTCMonth() + 1).padStart(2, '0')
      const d = String(dt.getUTCDate()).padStart(2, '0')
      const h = block.height != null ? String(block.height) : `unknown-${Math.floor(block.epoch)}`
      try {
        const r = await fetch(`/api/races/${y}/${m}/${d}/${h}-${block.vantage}.json`)
        if (r.ok) return await r.json() as RaceResult
      } catch {}
      return null
    }))
    fetched.forEach(r => r && results.push(r))
  }
  raceFiles.value = results
  isLoadingRaces.value = false
}

const poolAggregate = computed(() => localLeaderboard.value[poolName.value])
const combinedStats = computed<PoolStats | null>(() => poolAggregate.value?.combined ?? null)
const vantageStats = computed<Record<string, PoolStats>>(() => poolAggregate.value?.by_vantage ?? {})

const allVantages = computed(() => {
  const set = new Set<string>()
  for (const agg of Object.values(localLeaderboard.value) as any[]) {
    if (agg?.by_vantage) Object.keys(agg.by_vantage).forEach(v => set.add(v))
  }
  return [...set].sort()
})

function getVantageColor(v: string): string {
  const idx = allVantages.value.indexOf(v)
  return VANTAGE_COLORS[idx >= 0 ? idx % VANTAGE_COLORS.length : 0]
}

// ─── Scatter plot (preserved from original) ───────────────────────────────────

interface ScatterPoint { height: number; offset: number; vantage: string; isWin: boolean }

const scatterPoints = computed<ScatterPoint[]>(() => {
  const pts: ScatterPoint[] = []
  for (const race of raceFiles.value) {
    const offset = (race.nonempty_arrivals_offset_ms ?? {})[poolName.value]
    if (offset != null && race.block_height != null) {
      pts.push({ height: race.block_height, offset, vantage: race.vantage, isWin: offset === 0 })
    }
  }
  return pts.sort((a, b) => a.height - b.height)
})

const uniqueHeights = computed(() => [...new Set(scatterPoints.value.map(p => p.height))].sort((a, b) => a - b))

interface TooltipBlock { height: number; miner: string; entries: { vantage: string; offset: number; isWin: boolean; isOverallWinner: boolean }[] }

const tooltipByHeight = computed<Map<number, TooltipBlock>>(() => {
  const map = new Map<number, TooltipBlock>()
  for (const race of raceFiles.value) {
    if (race.block_height == null) continue
    const h = race.block_height
    if (!map.has(h)) map.set(h, { height: h, miner: (race as any).block_miner || 'Unknown', entries: [] })
    const offset = (race.nonempty_arrivals_offset_ms ?? {})[poolName.value]
    if (offset != null) map.get(h)!.entries.push({ vantage: race.vantage, offset, isWin: offset === 0, isOverallWinner: race.winner_nonempty === poolName.value })
  }
  return map
})

const tooltipVisible = ref(false)
const tooltipX = ref(0); const tooltipY = ref(0)
const tooltipContent = ref<TooltipBlock | null>(null)

function showTooltip(e: MouseEvent, p: ScatterPoint) {
  tooltipContent.value = tooltipByHeight.value.get(p.height) ?? { height: p.height, miner: 'Unknown', entries: [{ vantage: p.vantage, offset: p.offset, isWin: p.isWin, isOverallWinner: p.isWin }] }
  tooltipX.value = e.clientX; tooltipY.value = e.clientY; tooltipVisible.value = true
}
function showTooltipForHeight(e: MouseEvent, h: number) {
  const b = tooltipByHeight.value.get(h)
  if (b) { tooltipContent.value = b; tooltipX.value = e.clientX; tooltipY.value = e.clientY; tooltipVisible.value = true }
}
function hideTooltip() { tooltipVisible.value = false }

const CP = { top: 20, right: 20, bottom: 50, left: 85 }
const CW = 600; const CH = 300

const xMin = computed(() => scatterPoints.value.length ? Math.min(...scatterPoints.value.map(p => p.height)) : 0)
const xMax = computed(() => scatterPoints.value.length ? Math.max(...scatterPoints.value.map(p => p.height)) : 1)
const yMax = computed(() => { if (!scatterPoints.value.length) return 100; const m = Math.max(...scatterPoints.value.map(p => p.offset)); return m > 0 ? m * 1.1 : 100 })

function scaleX(h: number) { return CP.left + ((h - xMin.value) / (xMax.value - xMin.value || 1)) * (CW - CP.left - CP.right) }
function scaleY(o: number) { return CP.top + (1 - o / yMax.value) * (CH - CP.top - CP.bottom) }

const yTicks = computed(() => { const s = yMax.value / 5; return Array.from({ length: 6 }, (_, i) => Math.round(i * s)) })
const xTicks = computed(() => { if (!scatterPoints.value.length) return []; const mn = xMin.value; const s = (xMax.value - mn || 1) / 5; return Array.from({ length: 6 }, (_, i) => Math.round(mn + i * s)) })

function bandX(idx: number): number {
  const h = uniqueHeights.value[idx]; const prev = idx > 0 ? uniqueHeights.value[idx - 1] : null
  const x = scaleX(h); const hl = prev != null ? (x - scaleX(prev)) / 2 : (CW - CP.left - CP.right) / (uniqueHeights.value.length || 1) / 2
  return x - hl
}
function bandWidth(idx: number): number {
  const h = uniqueHeights.value[idx]; const prev = idx > 0 ? uniqueHeights.value[idx - 1] : null; const next = idx < uniqueHeights.value.length - 1 ? uniqueHeights.value[idx + 1] : null
  const x = scaleX(h); const hl = prev != null ? (x - scaleX(prev)) / 2 : (next != null ? (scaleX(next) - x) / 2 : 20); const hr = next != null ? (scaleX(next) - x) / 2 : hl
  return hl + hr
}

async function selectFrame(frame: TimeFrame) {
  localTimeFrame.value = frame
  await Promise.all([loadStats(), loadRaceFiles()])
}

// ─── Lifecycle ────────────────────────────────────────────────────────────────

onMounted(async () => {
  if (!store.poolConfig.length) await store.loadPoolConfig()
  if (!store.recentBlocks.length) await store.loadRecentBlocks()
  await store.loadPoolStatsLatest()
  await Promise.all([loadStats(), loadRaceFiles(), loadChartData()])
})

watch(poolName, async () => {
  await store.loadPoolStatsLatest()
  await Promise.all([loadStats(), loadRaceFiles(), loadChartData()])
})

// ─── Helpers ─────────────────────────────────────────────────────────────────

const p = computed(() => poolConfigEntry.value as any)

function connectStr(host: string, port: number): string {
  return `stratum+tcp://${host}:${port}`
}

const validationStatus = computed<ValidationStatus>(() =>
  (snap.value?.validation_status as ValidationStatus) ?? 'pending'
)

const poolRank = computed<number | null>(() => {
  const sorted = Object.keys(store.leaderboardData)
    .map(name => ({ name, median: store.leaderboardData[name]?.combined?.median_ms ?? null }))
    .filter(p => p.median !== null)
    .sort((a, b) => (a.median as number) - (b.median as number))
  const idx = sorted.findIndex(p => p.name === poolName.value)
  return idx >= 0 ? idx + 1 : null
})
</script>

<template>
  <div class="pool-detail-view">
    <button class="back-btn" @click="router.push('/pools')">← Back to Pools</button>

    <div v-if="store.poolConfig.length === 0" class="loading-state">Loading pool data...</div>

    <div v-else-if="!poolExists && !isLoadingStats" class="not-found">
      <h2>Pool Not Found</h2>
      <p>"{{ poolName }}" is not a recognised pool.</p>
      <button @click="router.push('/pools')">Return to Pools</button>
    </div>

    <template v-else-if="poolExists">

      <!-- ═══════════════════════════════════════════════════════════════════
           SECTION 1 — Pool Profile
           ═══════════════════════════════════════════════════════════════════ -->
      <section class="section identity-section">
        <div class="identity-header">
          <div>
            <h2 class="pool-title">
              <span v-if="poolRank" class="rank-number">#{{ poolRank }}</span>
              {{ p.display_name }}
              <a v-if="p.website_url" :href="p.website_url" target="_blank" rel="noopener" class="website-link" :aria-label="`Visit ${p.display_name} website`">↗</a>
            </h2>
            <p v-if="p.tagline" class="pool-tagline">{{ p.tagline }}</p>
            <div class="meta-tags">
              <span class="tag tag-type">{{ p.payout_type ?? (p.pool_type === 'solo' ? 'Solo' : 'Shared') }}</span>
              <span v-if="p.fees" class="tag tag-fee">
                <template v-if="Object.keys(p.fees).length === 1">{{ Object.values(p.fees)[0] === 0 ? '0% fee' : `${Object.values(p.fees)[0]}% fee` }}</template>
                <template v-else>{{ Object.entries(p.fees).map(([m,f]) => `${m}: ${f}%`).join(' · ') }}</template>
              </span>
              <span v-else-if="p.fee_pct !== undefined && p.fee_pct !== null" class="tag tag-fee">{{ p.fee_pct === 0 ? '0% fee' : `${p.fee_pct}% fee` }}</span>
              <span v-if="p.location" class="tag tag-loc">{{ p.location }}</span>
              <span v-if="p.founded" class="tag tag-loc">Est. {{ p.founded }}</span>
              <span v-if="p.features?.tls" class="tag tag-feature">TLS</span>
              <span v-if="p.features?.lightning" class="tag tag-feature">⚡ Lightning</span>
              <span v-if="p.features?.tor" class="tag tag-feature">Tor</span>
              <span v-if="p.features?.sv2" class="tag tag-feature">SV2</span>
            </div>
          </div>
          <ValidationStatusBadge :status="validationStatus" :last-verified-utc="snap?.last_verified_utc ?? null" />
        </div>

        <!-- Description -->
        <p v-if="p.description" class="pool-description">{{ p.description }}</p>

        <!-- Key details in clean prose layout -->
        <div class="detail-grid">
          <div v-if="p.payout_method" class="detail-item">
            <h4>Payouts</h4>
            <p>{{ p.payout_method }}</p>
          </div>

          <div class="detail-item">
            <h4>Primary Stratum</h4>
            <code class="connect-str">{{ connectStr(p.host, p.port) }}</code>
          </div>

          <div v-if="p.geographic_presence" class="detail-item">
            <h4>Infrastructure</h4>
            <p>{{ p.geographic_presence }}</p>
          </div>

          <div v-if="p.software?.stratum || p.software?.bitcoin" class="detail-item">
            <h4>Software</h4>
            <p>
              <span v-if="p.software?.stratum">{{ p.software.stratum }}</span>
              <span v-if="p.software?.stratum && p.software?.bitcoin"> · </span>
              <span v-if="p.software?.bitcoin">{{ p.software.bitcoin }}</span>
            </p>
          </div>
        </div>

        <div v-if="p.website_url" class="detail-item" style="margin-top: 1.25rem;">
          <h4>Pool Website</h4>
          <p><a :href="p.website_url" target="_blank" rel="noopener">{{ p.website_url.replace('https://', '').replace(/\/$/, '') }}</a></p>
        </div>
      </section>

      <!-- ═══════════════════════════════════════════════════════════════════
           SECTION 1.5 — Blocks Found
           ═══════════════════════════════════════════════════════════════════ -->
      <section v-if="blocksFound !== null" class="section">
        <h3 class="section-title">Blocks Found · {{ blocksFound.toLocaleString() }} total</h3>
        <div v-if="recentPoolBlocks.length" class="blocks-table-wrap">
          <table class="blocks-table">
            <thead>
              <tr>
                <th>Height</th>
                <th>Date</th>
                <th>Payouts</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="block in recentPoolBlocks" :key="block.height">
                <td class="mono">
                  <a :href="`https://mempool.space/block/${block.height}`" target="_blank" rel="noopener" class="block-link">#{{ block.height }}</a>
                  <span class="blocks-ago">{{ formatBlockAge(block.timestamp) }}</span>
                </td>
                <td class="mono">{{ block.date }}</td>
                <td class="payouts-cell">
                  <div v-for="(payout, i) in block.payouts" :key="i" class="payout-row">
                    <span class="payout-pct">{{ payout.percentage }}%</span>
                    <span class="payout-addr">{{ truncateAddr(payout.address) }}</span>
                    <span class="payout-btc">{{ payout.value_btc }} BTC</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- ═══════════════════════════════════════════════════════════════════
           SECTION 2 — Live Pool Stats (pool-provided)
           ═══════════════════════════════════════════════════════════════════ -->
      <section class="section">
        <h3 class="section-title">Pool Stats</h3>

        <div v-if="!snap || !snap.fetch_ok" class="no-stats-box">
          <p>No live stats available for this pool. Pool operators can <a href="https://github.com/proofofmike/stratum-race/issues" target="_blank" rel="noopener">open a request on GitHub</a> to get hashrate and worker data displayed here.</p>
        </div>
        <template v-else>
          <p class="stats-note">Stats sourced from pool. <span v-if="snap?.fetched_utc" class="fetched-ago">{{ formatFetchedAgo(snap.fetched_utc) }}</span></p>
          <div class="live-stats-grid">
            <div class="stat-tile">
              <div class="tile-label">Hashrate</div>
              <div class="tile-value">{{ snap.hashrate_formatted ?? '—' }}</div>
            </div>
            <div class="stat-tile">
              <div class="tile-label">Active Users</div>
              <div class="tile-value">{{ snap.active_users?.toLocaleString() ?? '—' }}</div>
            </div>
            <div v-if="snap.active_workers !== null" class="stat-tile">
              <div class="tile-label">Active Workers</div>
              <div class="tile-value">{{ snap.active_workers.toLocaleString() }}</div>
            </div>
            <div v-if="snap.pool_fee !== null" class="stat-tile">
              <div class="tile-label">Pool Fee</div>
              <div class="tile-value">{{ snap.pool_fee }}%</div>
            </div>
            <div class="stat-tile">
              <div class="tile-label">Expected Time to Block</div>
              <div class="tile-value">{{ formatExpected(expectedDays) }}</div>
            </div>
          </div>

          <!-- History chart -->
          <PoolHistoryChart
            :data="chartData"
            :metric="chartMetric"
            :loading="chartLoading"
            :pool-name="poolName"
            :has-workers="hasWorkers"
            @range-change="r => { chartRange = r; loadChartData() }"
            @metric-change="m => { chartMetric = m as any; loadChartData() }"
          />

          <!-- Device breakdown -->
          <div v-if="snap.miner_types?.length" class="device-table-wrap">
            <h4 class="subsection-title">Connected Miner Types</h4>
            <table class="device-table">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Workers</th>
                  <th>Hashrate</th>
                  <th>Best Difficulty</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="mt in snap.miner_types" :key="mt.device">
                  <td>{{ mt.device }}</td>
                  <td class="mono">{{ mt.workers.toLocaleString() }}</td>
                  <td class="mono">{{ mt.hashrate_formatted }}</td>
                  <td class="mono">{{ mt.best_difficulty_formatted }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </section>

      <!-- ═══════════════════════════════════════════════════════════════════
           SECTION 3 — StratumRace Performance
           ═══════════════════════════════════════════════════════════════════ -->
      <section class="section">
        <h3 class="section-title">StratumRace Performance <span class="provider-note">independently verified</span></h3>

        <!-- Stats cards -->
        <div v-if="isLoadingStats && !combinedStats" class="loading-state">Loading…</div>
        <div v-else-if="!combinedStats" class="no-data-state">No SR data for this pool in the selected window.</div>
        <template v-else>
          <div class="sr-controls">
            <div class="frame-buttons">
              <button :class="{ active: localTimeFrame === 'last10' }" @click="selectFrame('last10')" :disabled="isLoadingStats">Last 10</button>
              <button :class="{ active: localTimeFrame === 'last50' }" @click="selectFrame('last50')" :disabled="isLoadingStats">Last 50</button>
            </div>
          </div>

          <div class="stats-grid">
            <div class="stat-card stat-card-combined">
              <h4 class="card-label">Combined</h4>
              <div class="stat-row"><span class="stat-name">Median Offset</span><span class="stat-value">{{ combinedStats.median_ms != null ? combinedStats.median_ms.toFixed(1) + ' ms' : '—' }}</span></div>
              <div class="stat-row"><span class="stat-name">Wins</span><span class="stat-value">{{ combinedStats.wins }}</span></div>
              <div class="stat-row"><span class="stat-name">Win %</span><span class="stat-value">{{ combinedStats.win_pct.toFixed(1) }}%</span></div>
              <div class="stat-row"><span class="stat-name">Races Seen</span><span class="stat-value">{{ combinedStats.races_seen }}</span></div>
              <div class="stat-row"><span class="stat-name">P95</span><span class="stat-value">{{ combinedStats.p95_ms != null ? combinedStats.p95_ms.toFixed(1) + ' ms' : '—' }}</span></div>
            </div>
            <div v-for="vantage in allVantages" :key="vantage" class="stat-card" :style="{ borderTopColor: getVantageColor(vantage) }">
              <h4 class="card-label"><span class="vantage-dot" :style="{ backgroundColor: getVantageColor(vantage) }"></span>{{ formatVantage(vantage) }}</h4>
              <template v-if="vantageStats[vantage]">
                <div class="stat-row"><span class="stat-name">Median</span><span class="stat-value">{{ vantageStats[vantage].median_ms != null ? vantageStats[vantage].median_ms!.toFixed(1) + ' ms' : '—' }}</span></div>
                <div class="stat-row"><span class="stat-name">Wins</span><span class="stat-value">{{ vantageStats[vantage].wins }}</span></div>
                <div class="stat-row"><span class="stat-name">Win %</span><span class="stat-value">{{ vantageStats[vantage].win_pct.toFixed(1) }}%</span></div>
                <div class="stat-row"><span class="stat-name">Races Seen</span><span class="stat-value">{{ vantageStats[vantage].races_seen }}</span></div>
              </template>
              <template v-else><p class="muted">No data</p></template>
            </div>
          </div>

          <!-- Scatter plot -->
          <div class="scatter-section">
            <h4 class="subsection-title">Offset Timeline</h4>
            <div v-if="isLoadingRaces && !scatterPoints.length" class="loading-state">Loading race data…</div>
            <div v-else-if="!scatterPoints.length" class="no-data-state">No race data for this window.</div>
            <div v-else class="scatter-chart-container">
              <div class="vantage-legend">
                <span v-for="vp in allVantages" :key="vp" class="legend-item">
                  <span class="legend-dot" :style="{ backgroundColor: getVantageColor(vp) }"></span>{{ formatVantage(vp) }}
                </span>
                <span class="legend-item"><span class="legend-marker-filled"></span>Win</span>
                <span class="legend-item"><span class="legend-marker-hollow"></span>Non-win</span>
              </div>
              <svg class="scatter-svg" :viewBox="`0 0 ${CW} ${CH}`" preserveAspectRatio="xMidYMid meet" aria-label="Pool offset scatter plot">
                <rect v-for="(h, idx) in uniqueHeights" :key="'band-'+h" :x="bandX(idx)" :y="CP.top" :width="bandWidth(idx)" :height="CH-CP.top-CP.bottom" :fill="idx%2===0?'rgba(255,255,255,0.02)':'rgba(255,255,255,0.05)'" />
                <line v-for="tick in yTicks" :key="'yg-'+tick" :x1="CP.left" :y1="scaleY(tick)" :x2="CW-CP.right" :y2="scaleY(tick)" class="grid-line" />
                <text v-for="tick in yTicks" :key="'yl-'+tick" :x="CP.left-8" :y="scaleY(tick)+4" class="axis-label" text-anchor="end">{{ tick }}</text>
                <text :x="14" :y="CH/2" class="axis-title" text-anchor="middle" transform="rotate(-90,14,150)">Offset (ms)</text>
                <text v-for="tick in xTicks" :key="'xl-'+tick" :x="scaleX(tick)" :y="CH-CP.bottom+18" class="axis-label" text-anchor="middle">{{ tick }}</text>
                <text :x="(CW-CP.left-CP.right)/2+CP.left" :y="CH-CP.bottom+38" class="axis-title" text-anchor="middle">Block Height</text>
                <line :x1="CP.left" :y1="scaleY(0)" :x2="CW-CP.right" :y2="scaleY(0)" class="zero-line" />
                <template v-for="(point, idx) in scatterPoints" :key="idx">
                  <circle v-if="point.isWin" :cx="scaleX(point.height)" :cy="scaleY(point.offset)" r="6" :fill="getVantageColor(point.vantage)" stroke="none" class="data-point" @mouseenter="showTooltip($event,point)" @mouseleave="hideTooltip" />
                  <circle v-else :cx="scaleX(point.height)" :cy="scaleY(point.offset)" r="5" fill="transparent" :stroke="getVantageColor(point.vantage)" stroke-width="2" class="data-point" @mouseenter="showTooltip($event,point)" @mouseleave="hideTooltip" />
                </template>
                <rect v-for="(h, idx) in uniqueHeights" :key="'hover-'+h" :x="bandX(idx)" :y="CP.top" :width="bandWidth(idx)" :height="CH-CP.top-CP.bottom" fill="transparent" class="band-hover" @mouseenter="showTooltipForHeight($event,h)" @mousemove="showTooltipForHeight($event,h)" @mouseleave="hideTooltip" />
              </svg>
              <div v-if="tooltipVisible && tooltipContent" class="custom-tooltip" :style="{ left: tooltipX+12+'px', top: tooltipY-10+'px' }">
                <div class="tooltip-header">Block {{ tooltipContent.height }}</div>
                <div class="tooltip-miner">Mined by: {{ tooltipContent.miner }}</div>
                <div v-for="entry in tooltipContent.entries" :key="entry.vantage" class="tooltip-row" :class="{ 'tooltip-winner': entry.isOverallWinner }">
                  <span class="tooltip-vantage">{{ formatVantage(entry.vantage) }}</span>
                  <span class="tooltip-offset">{{ entry.offset.toFixed(1) }} ms</span>
                  <span v-if="entry.isOverallWinner" class="tooltip-badge">★ WINNER</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </section>

    </template>
  </div>
</template>

<style scoped>
.pool-detail-view { max-width: 1100px; margin: 0 auto; padding: 1.5rem; display: flex; flex-direction: column; gap: 1.25rem; }

.back-btn { align-self: flex-start; background: var(--surface-elevated); color: var(--accent); font-weight: 500; font-size: 0.875rem; padding: 0.5rem 1rem; border-radius: 0.375rem; border: 1px solid var(--border); }
.back-btn:hover { background: var(--border); }

.not-found { text-align: center; padding: 3rem 1rem; background: var(--surface); border: 1px solid var(--border); border-radius: 0.5rem; }

/* Sections */
.section { background: var(--surface); border: 1px solid var(--border); border-radius: 0.5rem; padding: 1.25rem; }
.section-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; flex-wrap: wrap; gap: 0.5rem; }
.section-title { font-size: 1rem; font-weight: 600; color: var(--text-primary); margin: 0 0 1rem; }
.subsection-title { font-size: 0.875rem; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em; margin: 1rem 0 0.5rem; }
.provider-note { font-size: 0.6875rem; font-weight: 400; color: var(--text-secondary); text-transform: none; letter-spacing: 0; margin-left: 0.5rem; }
.fetched-ago { font-size: 0.75rem; color: var(--text-secondary); }

/* Identity */
.identity-header { display: flex; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; gap: 0.75rem; margin-bottom: 1rem; }
.pool-title { font-size: 1.5rem; font-weight: 700; color: var(--text-primary); margin: 0 0 0.25rem; display: flex; align-items: center; gap: 0.5rem; }
.rank-number { font-family: var(--font-mono); font-size: 1.25rem; font-weight: 800; color: var(--accent); }
.blocks-section { margin-top: 1.25rem; }
.blocks-table-wrap { overflow-x: auto; margin-top: 0.5rem; }
.blocks-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
.blocks-table th { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }
.blocks-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.blocks-table tbody tr:last-child td { border-bottom: none; }
.blocks-ago { font-size: 0.6875rem; color: var(--text-secondary); margin-left: 0.5rem; }
.block-link { color: var(--accent); text-decoration: none; font-family: var(--font-mono); }
.block-link:hover { text-decoration: underline; }
.payouts-cell { min-width: 200px; }
.payout-row { display: flex; gap: 0.5rem; align-items: center; padding: 0.125rem 0; font-size: 0.75rem; }
.payout-pct { font-family: var(--font-mono); font-weight: 700; min-width: 50px; color: var(--text-primary); }
.payout-addr { font-family: var(--font-mono); color: var(--text-secondary); font-size: 0.6875rem; }
.payout-btc { font-family: var(--font-mono); color: var(--text-secondary); font-size: 0.6875rem; margin-left: auto; }
.pool-tagline { font-size: 1rem; color: var(--accent); font-weight: 500; margin: 0 0 0.5rem; font-style: italic; }
.website-link { font-size: 1rem; color: var(--text-secondary); text-decoration: none; }
.website-link:hover { color: var(--accent); }

.pool-description { font-size: 0.9375rem; line-height: 1.7; color: var(--text-primary); margin: 1rem 0; max-width: 720px; }

.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin: 1.25rem 0; }
.detail-item h4 { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); margin: 0 0 0.375rem; }
.detail-item p { font-size: 0.875rem; color: var(--text-primary); margin: 0; line-height: 1.5; }
.connect-str { font-family: var(--font-mono); font-size: 0.875rem; color: var(--accent); background: var(--surface-elevated); padding: 0.375rem 0.75rem; border-radius: 0.25rem; display: inline-block; border: 1px solid var(--border); }
.detail-subtext { font-size: 0.75rem; color: var(--text-secondary); margin: 0.375rem 0 0; line-height: 1.4; }

.website-full-link { margin: 0.75rem 0 0; }
.website-full-link a { font-size: 0.8125rem; color: var(--accent); text-decoration: none; }
.website-full-link a:hover { text-decoration: underline; }
.meta-tags { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.tag { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; padding: 0.2rem 0.5rem; border-radius: 0.25rem; }
.tag-type    { background: rgba(74,154,240,0.15); color: var(--accent); }
.tag-fee     { background: rgba(52,211,153,0.12); color: var(--success); }
.tag-loc     { background: rgba(148,163,184,0.12); color: var(--text-secondary); }
.tag-feature { background: rgba(139,92,246,0.12); color: #a78bfa; }

.info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.75rem; margin-bottom: 0.75rem; }
.info-block { display: flex; flex-direction: column; gap: 0.25rem; }
.info-block--full { grid-column: 1 / -1; }
.info-label { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); }
.info-value { font-size: 0.8125rem; color: var(--text-primary); }
.stratum-row { display: flex; align-items: center; gap: 0.5rem; }
.ep-label { font-size: 0.6875rem; color: var(--text-secondary); }
code { font-family: var(--font-mono); font-size: 0.8125rem; color: var(--accent); }
.notes-link { color: var(--accent); text-decoration: none; margin-left: 0.5rem; font-size: 0.75rem; }

.disclaimer-inline { font-size: 0.75rem; color: var(--text-secondary); margin: 0.5rem 0 0; padding: 0.5rem 0.75rem; background: var(--surface-elevated); border-radius: 0.25rem; border-left: 2px solid var(--border); }

/* Live stats */
.no-stats-box { font-size: 0.875rem; color: var(--text-secondary); padding: 1.5rem; text-align: center; background: var(--surface-elevated); border: 1px solid var(--border); border-radius: 0.375rem; }
.no-stats-box p { margin: 0; line-height: 1.6; }
.no-stats-box a { color: var(--accent); text-decoration: none; }
.no-stats-box a:hover { text-decoration: underline; }
.stats-note { font-size: 0.75rem; color: var(--text-secondary); margin: 0 0 0.75rem; }
.fetched-ago { color: var(--text-secondary); }
.live-stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-bottom: 1rem; }
.stat-tile { background: var(--surface-elevated); border: 1px solid var(--border); border-radius: 0.375rem; padding: 0.75rem; }
.tile-label { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); margin-bottom: 0.375rem; }
.tile-value { font-family: var(--font-mono); font-size: 1.125rem; font-weight: 700; color: var(--text-primary); }

/* Device table */
.device-table-wrap { margin-bottom: 1rem; overflow-x: auto; }
.device-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
.device-table th { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); padding: 0.375rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }
.device-table td { padding: 0.375rem 0.75rem; border-bottom: 1px solid var(--border); color: var(--text-primary); }
.device-table tbody tr:last-child td { border-bottom: none; }

/* SR performance */
.sr-controls { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; }
.frame-buttons { display: flex; gap: 0.25rem; }
.frame-buttons button { padding: 0.375rem 0.75rem; font-size: 0.8125rem; border-radius: 0.25rem; border: 1px solid var(--border); background: var(--surface-elevated); color: var(--text-secondary); min-height: 36px; transition: all 0.15s; }
.frame-buttons button:hover:not(:disabled) { background: var(--border); color: var(--text-primary); }
.frame-buttons button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.frame-buttons button:disabled { opacity: 0.5; cursor: not-allowed; }

.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.75rem; margin-bottom: 1.25rem; }
.stat-card { background: var(--surface-elevated); border: 1px solid var(--border); border-radius: 0.5rem; padding: 0.875rem; border-top: 3px solid var(--border); }
.stat-card-combined { border-top-color: var(--accent); }
.card-label { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); margin: 0 0 0.5rem; display: flex; align-items: center; gap: 0.375rem; }
.vantage-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.stat-row { display: flex; justify-content: space-between; padding: 0.25rem 0; border-bottom: 1px solid var(--border); }
.stat-row:last-child { border-bottom: none; }
.stat-name { font-size: 0.75rem; color: var(--text-secondary); }
.stat-value { font-family: var(--font-mono); font-size: 0.8125rem; font-weight: 600; color: var(--text-primary); }

/* Scatter */
.scatter-section { margin-top: 0.75rem; }
.scatter-chart-container { position: relative; width: 100%; }
.vantage-legend { display: flex; flex-wrap: wrap; gap: 1rem; margin-bottom: 0.5rem; }
.legend-item { display: inline-flex; align-items: center; gap: 0.375rem; font-size: 0.75rem; color: var(--text-secondary); }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.legend-marker-filled { width: 10px; height: 10px; border-radius: 50%; background: var(--text-secondary); flex-shrink: 0; }
.legend-marker-hollow { width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--text-secondary); box-sizing: border-box; flex-shrink: 0; }
.scatter-svg { width: 100%; height: auto; }
.grid-line { stroke: var(--border); stroke-width: 0.5; stroke-dasharray: 3 3; }
.zero-line { stroke: var(--success); stroke-width: 1; stroke-dasharray: 5 3; opacity: 0.5; }
.axis-label { fill: var(--text-primary); font-size: 10px; font-family: var(--font-mono); font-weight: 600; }
.axis-title  { fill: var(--text-primary); font-size: 11px; }
.data-point { cursor: default; }
.band-hover { cursor: crosshair; }
.custom-tooltip { position: fixed; z-index: 1000; background: var(--surface-elevated); border: 1px solid var(--border); border-radius: 0.375rem; padding: 0.5rem 0.75rem; font-size: 0.75rem; box-shadow: 0 4px 12px rgba(0,0,0,0.5); pointer-events: none; white-space: nowrap; }
.tooltip-header { font-weight: 700; color: var(--text-primary); margin-bottom: 0.25rem; font-size: 0.8125rem; }
.tooltip-miner { font-size: 0.6875rem; color: var(--text-secondary); margin-bottom: 0.375rem; padding-bottom: 0.25rem; border-bottom: 1px solid var(--border); }
.tooltip-row { display: flex; align-items: center; gap: 0.75rem; padding: 0.125rem 0; color: var(--text-primary); font-family: var(--font-mono); }
.tooltip-row.tooltip-winner { color: var(--accent); font-weight: 700; }
.tooltip-vantage { min-width: 80px; }
.tooltip-offset { min-width: 60px; text-align: right; }
.tooltip-badge { color: var(--accent); font-size: 0.6875rem; font-weight: 700; }

.loading-state, .no-data-state { padding: 1.5rem; text-align: center; color: var(--text-secondary); font-size: 0.875rem; }
.muted { color: var(--text-secondary); }
.mono { font-family: var(--font-mono); }

.wallet-cell { max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.leaderboard-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
.leaderboard-table-wrap { overflow-x: auto; }
.top-shares { overflow-x: auto; }

@media (max-width: 768px) {
  .pool-detail-view { padding: 1rem; }
  .stats-grid { grid-template-columns: 1fr 1fr; }
  .live-stats-grid { grid-template-columns: 1fr 1fr; }
  .identity-header { flex-direction: column; }
  .detail-grid { grid-template-columns: 1fr; }
  .leaderboard-grid { grid-template-columns: 1fr; }
}
@media (max-width: 480px) {
  .stats-grid, .live-stats-grid { grid-template-columns: 1fr; }
}
</style>
