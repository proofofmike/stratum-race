import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  RaceResult,
  RecentBlock,
  PoolStats,
  VantageHealth,
  PoolConfig,
  TemplateMode,
  PoolTier,
  PoolAggregate,
  TimeFrame,
} from '@/types'

/** Map of leaderboard time frames to their pre-computed aggregate file paths */
export const TIME_FRAME_PATHS: Record<TimeFrame, string> = {
  last10: 'recent-10',
  last50: 'recent-50',
  '24h': 'last-24h',
  '7d': 'last-7d',
}

/**
 * Main Pinia store for all race data state management.
 * Manages leaderboard data, recent blocks, vantage health, pool config,
 * and user-selected filters (template mode, vantage selection).
 */
export const useRaceStore = defineStore('race', () => {
  // ─── State ────────────────────────────────────────────────────────────────

  /** Last 40 races (from recent-blocks.json + WebSocket updates) */
  const recentBlocks = ref<RecentBlock[]>([])

  /** Current pool stats keyed by pool name (from latest aggregate) */
  const leaderboardData = ref<Record<string, PoolAggregate>>({})

  /** Vantage point health statuses */
  const vantageHealth = ref<Record<string, VantageHealth>>({})

  /** Pool display names, operators, and groups */
  const poolConfig = ref<PoolConfig[]>([])

  /** Template ranking mode: 'full' (nonempty) or 'any' (all templates) */
  const templateMode = ref<TemplateMode>('full')

  /** Selected pool tier filter: 'all', 'big', or 'small' */
  const selectedTier = ref<PoolTier>('small')

  /** Selected operator filter: null means "All Operators" */
  const selectedOperator = ref<string | null>(null)

  /** Selected vantage point filter */
  const selectedVantage = ref<string | 'combined'>('combined')

  /** Epoch timestamp of the most recent race */
  const lastBlockEpoch = ref<number | null>(null)

  /** Whether initial data is currently loading */
  const isLoading = ref(false)

  /** Whether leaderboard was built from recent blocks (vs server aggregate) */
  const leaderboardFromRecent = ref(false)

  /** Currently selected leaderboard time frame (single source of truth for all
   *  loaders — the time filter UI, initial load, and tab-resume refresh) */
  const activeTimeFrame = ref<TimeFrame>('7d')

  /** generated_utc of the currently loaded aggregate (null when from fallback) */
  const aggregateLastUpdated = ref<string | null>(null)

  /** Vantage display data from runtime.json: { [vantageId]: { label, flag?, location? } } */
  const vantageDisplay = ref<Record<string, { label: string; flag?: string; location?: string }>>({})

  // ─── Getters ──────────────────────────────────────────────────────────────

  /**
   * Get the effective PoolStats for a given pool based on current vantage selection
   * and template mode (full vs any).
   */
  function getPoolStats(poolName: string): PoolStats | null {
    const poolAgg = leaderboardData.value[poolName]
    if (!poolAgg) return null

    // Determine which stat set to use based on template mode
    const useAny = templateMode.value === 'any'

    if (selectedVantage.value === 'combined') {
      if (useAny && poolAgg.any_combined) {
        return poolAgg.any_combined
      }
      return poolAgg.combined
    }

    if (useAny && poolAgg.any_by_vantage?.[selectedVantage.value]) {
      return poolAgg.any_by_vantage[selectedVantage.value]
    }
    return poolAgg.by_vantage?.[selectedVantage.value] ?? null
  }

  /**
   * Pools sorted by median offset (ascending), null values last.
   */
  const sortedLeaderboard = computed(() => {
    const entries = Object.keys(leaderboardData.value).map((poolName) => ({
      poolName,
      stats: getPoolStats(poolName),
    }))

    return entries.sort((a, b) => {
      const aMedian = a.stats?.median_ms
      const bMedian = b.stats?.median_ms

      // Null values sorted last
      if (aMedian == null && bMedian == null) return 0
      if (aMedian == null) return 1
      if (bMedian == null) return -1

      return aMedian - bMedian
    })
  })

  /**
   * Filter pools by tier classification.
   * Filter pools by type: 'solo' shows only solo pools, 'all' shows everything.
   * Uses pool_type field from pools.json config.
   */
  const filteredByTier = computed(() => {
    return (tier: 'big' | 'small' | 'all') => {
      if (tier === 'all') return sortedLeaderboard.value

      // 'small' is used internally to mean 'solo' (from the filter toggle)
      return sortedLeaderboard.value.filter(({ poolName }) => {
        const config = poolConfig.value.find((p) => p.name === poolName)
        if (!config) return false // Unknown pools excluded from solo filter
        return (config as any).pool_type === 'solo'
      })
    }
  })

  /**
   * Filter leaderboard stats by selected vantage point.
   * When 'combined' is selected, returns combined stats.
   * When a specific vantage is selected, returns only that vantage's data.
   */
  const filteredByVantage = computed(() => {
    return sortedLeaderboard.value.filter(({ stats }) => stats != null)
  })

  /**
   * Unique operator names derived from poolConfig.
   * Used by the OperatorFilter dropdown.
   */
  const uniqueOperators = computed<string[]>(() => {
    const operators = new Set<string>()
    for (const pool of poolConfig.value) {
      if (pool.operator) {
        operators.add(pool.operator)
      }
    }
    return [...operators].sort()
  })

  /**
   * Leaderboard filtered by selected operator.
   * When no operator is selected (null), returns the full sortedLeaderboard.
   * When an operator is selected, returns only pools belonging to that operator.
   * Pool rankings remain independent — filtering is purely visual convenience.
   */
  const filteredByOperator = computed(() => {
    if (!selectedOperator.value) return sortedLeaderboard.value

    return sortedLeaderboard.value.filter(({ poolName }) => {
      const config = poolConfig.value.find((p) => p.name === poolName)
      return config?.operator === selectedOperator.value
    })
  })

  /**
   * Computed seconds since the last block (reactive via lastBlockEpoch).
   * Returns null if no block has been observed yet.
   */
  const lastBlockAge = computed((): number | null => {
    if (lastBlockEpoch.value == null) return null
    return Math.floor(Date.now() / 1000 - lastBlockEpoch.value)
  })

  /**
   * Number of active vantage points, derived from the largest of:
   * vantageHealth keys, aggregate by_vantage keys, or vantageDisplay keys.
   */
  const vantageCount = computed((): number => {
    const fromHealth = Object.keys(vantageHealth.value).length
    const fromDisplay = Object.keys(vantageDisplay.value).length

    // Extract vantage keys from leaderboard aggregate data
    const vantageSet = new Set<string>()
    for (const poolAgg of Object.values(leaderboardData.value)) {
      if (poolAgg.by_vantage) {
        for (const v of Object.keys(poolAgg.by_vantage)) {
          vantageSet.add(v)
        }
      }
    }
    const fromAggregate = vantageSet.size

    return Math.max(fromHealth, fromDisplay, fromAggregate)
  })

  /**
   * Resolve an internal pool name to its display_name from pool config.
   * Falls back to the internal name if no config entry is found.
   */
  function displayName(poolName: string): string {
    const config = poolConfig.value.find((p) => p.name === poolName)
    return config?.display_name ?? poolName
  }

  // ─── Actions ──────────────────────────────────────────────────────────────

  /**
   * Add a new race result: prepend to recentBlocks (cap at 40) and update leaderboard stats.
   * Deduplicates by block height + vantage — if the same race already exists, it's replaced.
   */
  function addRaceResult(race: RaceResult) {
    // Build a RecentBlock summary from the full RaceResult
    const offsets = Object.values(race.arrivals_offset_ms)
    const sortedArrivals = Object.entries(race.arrivals_offset_ms).sort(([, a], [, b]) => a - b)
    const totalSpread = offsets.length > 0 ? Math.max(...offsets) - Math.min(...offsets) : 0

    const recentBlock: RecentBlock = {
      height: race.block_height,
      utc: race.first_utc,
      epoch: race.first_epoch,
      miner: race.block_miner,
      winner: race.winner,
      winner_nonempty: race.winner_nonempty,
      empty_jumpstart: race.empty_first_pools.length > 0 ? race.empty_first_pools[0] : null,
      second: sortedArrivals.length > 1 ? sortedArrivals[1][0] : null,
      second_delay_ms: sortedArrivals.length > 1 ? sortedArrivals[1][1] : null,
      spread_ms: totalSpread,
      // Matches the backend summary definition (pools that reported arrivals)
      pools_seen: Object.keys(race.arrivals_offset_ms).length,
      vantage: race.vantage,
    }

    // Remove any existing entry for the same block height + vantage (dedup).
    // Null-height blocks dedup by epoch instead so two different unknown
    // blocks never dedupe each other (mirrors the backend).
    const filtered = recentBlocks.value.filter((b) => {
      if (recentBlock.height != null) {
        return !(b.height === recentBlock.height && b.vantage === recentBlock.vantage)
      }
      return !(b.height == null && b.vantage === recentBlock.vantage && b.epoch === recentBlock.epoch)
    })

    // Prepend and cap at 40
    recentBlocks.value = [recentBlock, ...filtered].slice(0, 40)

    // Update last block epoch
    lastBlockEpoch.value = race.first_epoch

    // Rebuild leaderboard from recent blocks if no server aggregate is loaded
    if (Object.keys(leaderboardData.value).length === 0 || leaderboardFromRecent.value) {
      buildLeaderboardFromRecent()
    }
  }

  /**
   * Load runtime configuration from /api/config/runtime.json.
   * Populates vantageDisplay and returns the websocket_url (or empty string on failure).
   */
  async function loadRuntimeConfig(): Promise<string> {
    try {
      const response = await fetch('/api/config/runtime.json')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      if (data.vantages) {
        vantageDisplay.value = data.vantages
      }
      return data.websocket_url ?? ''
    } catch (error) {
      console.error('Failed to load runtime config:', error)
      return ''
    }
  }

  /**
   * Fetch recent-blocks.json from the backend API.
   * Also builds leaderboard data from recent blocks as a fallback
   * when no aggregate files exist.
   */
  async function loadRecentBlocks() {
    try {
      const response = await fetch('/api/recent/recent-blocks.json')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data: RecentBlock[] = await response.json()

      // Deduplicate by height + vantage (in case the backend has any or merge creates dupes).
      // Null-height blocks key on epoch so distinct unknown blocks are kept.
      const seen = new Set<string>()
      const deduped = data.filter((b) => {
        const key = b.height != null ? `${b.height}-${b.vantage}` : `u${b.epoch}-${b.vantage}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })

      // Sort by epoch descending (newest first) to ensure correct ordering
      deduped.sort((a, b) => b.epoch - a.epoch)

      recentBlocks.value = deduped.slice(0, 40)

      // Update lastBlockEpoch from most recent block (guaranteed newest after sort)
      if (recentBlocks.value.length > 0) {
        lastBlockEpoch.value = recentBlocks.value[0].epoch
      }

      // Build leaderboard from recent blocks if no aggregate data loaded
      if (Object.keys(leaderboardData.value).length === 0 && recentBlocks.value.length > 0) {
        buildLeaderboardFromRecent()
      }
    } catch (error) {
      console.error('Failed to load recent blocks:', error)
    }
  }

  /**
   * Build leaderboard data from recent blocks when no aggregate is available.
   * Computes per-pool win counts and approximate timing from the recent blocks list.
   */
  function buildLeaderboardFromRecent() {
    // Track per-pool: wins, appearances, and offsets (0 for winner, gap for runner-up)
    const poolStats: Record<string, { 
      wins: number; seen: number; offsets: number[];
      byVantage: Record<string, { wins: number; seen: number; offsets: number[] }> 
    }> = {}
    
    for (const block of recentBlocks.value) {
      const winner = block.winner_nonempty || block.winner
      const vantage = block.vantage || 'unknown'
      
      const ensurePool = (pool: string) => {
        if (!poolStats[pool]) poolStats[pool] = { wins: 0, seen: 0, offsets: [], byVantage: {} }
        if (!poolStats[pool].byVantage[vantage]) poolStats[pool].byVantage[vantage] = { wins: 0, seen: 0, offsets: [] }
      }
      
      if (winner) {
        ensurePool(winner)
        poolStats[winner].wins += 1
        poolStats[winner].seen += 1
        poolStats[winner].offsets.push(0)  // Winner has 0 offset
        poolStats[winner].byVantage[vantage].wins += 1
        poolStats[winner].byVantage[vantage].seen += 1
        poolStats[winner].byVantage[vantage].offsets.push(0)
      }
      
      if (block.second && block.second_delay_ms != null) {
        ensurePool(block.second)
        poolStats[block.second].seen += 1
        poolStats[block.second].offsets.push(block.second_delay_ms)
        poolStats[block.second].byVantage[vantage].seen += 1
        poolStats[block.second].byVantage[vantage].offsets.push(block.second_delay_ms)
      }
    }

    // Denominators for win %: the number of races in scope. Every pool
    // implicitly competes in every race, so win_pct = wins / races-in-scope
    // (matching the backend's wins / races_seen definition). Using podium
    // appearances as the denominator would show a pool that won 2 of 40
    // races as "100%".
    const totalRaces = recentBlocks.value.length
    const racesByVantage: Record<string, number> = {}
    for (const block of recentBlocks.value) {
      const v = block.vantage || 'unknown'
      racesByVantage[v] = (racesByVantage[v] ?? 0) + 1
    }

    // Helper to compute stats from offsets for a scope with scopeRaces races
    const computeStats = (s: { wins: number; offsets: number[] }, scopeRaces: number): PoolStats => {
      const offsets = s.offsets.sort((a, b) => a - b)
      const median = offsets.length > 0 ? offsets[Math.floor(offsets.length / 2)] : null
      const avg = offsets.length > 0 ? offsets.reduce((a, b) => a + b, 0) / offsets.length : null
      const p95 = offsets.length > 0 ? offsets[Math.min(offsets.length - 1, Math.ceil(offsets.length * 0.95) - 1)] : null
      
      return {
        median_ms: median != null ? Math.round(median * 10) / 10 : null,
        avg_ms: avg != null ? Math.round(avg * 10) / 10 : null,
        p95_ms: p95 != null ? Math.round(p95 * 10) / 10 : null,
        wins: s.wins,
        races_seen: scopeRaces,
        races_eligible: scopeRaces,
        win_pct: scopeRaces > 0 ? Math.round((s.wins / scopeRaces) * 1000) / 10 : 0,
        empty_first_pct: 0,
        waste_min_day: 0,
      }
    }

    // Convert to PoolAggregate format
    const newData: Record<string, PoolAggregate> = {}
    for (const [pool, stats] of Object.entries(poolStats)) {
      const byVantage: Record<string, PoolStats> = {}
      for (const [vp, vpStats] of Object.entries(stats.byVantage)) {
        byVantage[vp] = computeStats(vpStats, racesByVantage[vp] ?? 0)
      }
      
      newData[pool] = {
        combined: computeStats(stats, totalRaces),
        by_vantage: byVantage,
      }
    }
    leaderboardData.value = newData
    leaderboardFromRecent.value = true
  }

  /**
   * Fetch an aggregate file for a given date range identifier.
   * @param dateRange - e.g. "last-7d", "recent-10", "daily/2025-01-15"
   * @returns true when the aggregate loaded successfully
   */
  async function loadAggregate(dateRange: string): Promise<boolean> {
    isLoading.value = true
    try {
      const response = await fetch(`/api/aggregates/${dateRange}.json`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      leaderboardData.value = data.pools ?? {}
      leaderboardFromRecent.value = false
      aggregateLastUpdated.value = data.generated_utc ?? null
      return true
    } catch (error) {
      console.error('Failed to load aggregate:', error)
      return false
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Load the aggregate for a leaderboard time frame and record the selection.
   * On failure the selection reverts, so the active filter button never
   * disagrees with the data actually displayed.
   */
  async function loadTimeFrame(frame: TimeFrame): Promise<boolean> {
    const previous = activeTimeFrame.value
    activeTimeFrame.value = frame
    const ok = await loadAggregate(TIME_FRAME_PATHS[frame])
    if (!ok) {
      activeTimeFrame.value = previous
    }
    return ok
  }

  /**
   * Re-fetch the aggregate for the CURRENTLY selected time frame.
   * Used on tab resume — always respects the user's active selection
   * instead of resetting the displayed data to a fixed period.
   */
  async function reloadActiveTimeFrame(): Promise<boolean> {
    return loadAggregate(TIME_FRAME_PATHS[activeTimeFrame.value])
  }

  /**
   * Fetch vantage point health status from status/vantages.json.
   */
  async function loadVantageHealth() {
    try {
      const response = await fetch('/api/status/vantages.json')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      vantageHealth.value = data.vantages ?? {}
    } catch (error) {
      console.error('Failed to load vantage health:', error)
    }
  }

  /**
   * Fetch pool configuration from config/pools.json.
   */
  async function loadPoolConfig() {
    try {
      const response = await fetch('/api/config/pools.json')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      poolConfig.value = data.pools ?? []
    } catch (error) {
      console.error('Failed to load pool config:', error)
    }
  }

  /**
   * Toggle template ranking mode between 'full' and 'any'.
   */
  function setTemplateMode(mode: TemplateMode) {
    templateMode.value = mode
  }

  /**
   * Set the active pool tier filter.
   */
  function setTier(tier: PoolTier) {
    selectedTier.value = tier
  }

  /**
   * Set the active operator filter.
   * Pass null to show all operators.
   */
  function setOperator(operator: string | null) {
    selectedOperator.value = operator
  }

  /**
   * Set the active vantage point filter.
   */
  function setSelectedVantage(vantage: string | 'combined') {
    selectedVantage.value = vantage
  }

  return {
    // State
    recentBlocks,
    leaderboardData,
    leaderboardFromRecent,
    vantageHealth,
    vantageDisplay,
    poolConfig,
    templateMode,
    selectedTier,
    selectedOperator,
    selectedVantage,
    lastBlockEpoch,
    isLoading,
    activeTimeFrame,
    aggregateLastUpdated,

    // Getters
    getPoolStats,
    sortedLeaderboard,
    filteredByTier,
    filteredByVantage,
    uniqueOperators,
    filteredByOperator,
    lastBlockAge,
    vantageCount,
    displayName,

    // Actions
    addRaceResult,
    loadRuntimeConfig,
    loadRecentBlocks,
    loadAggregate,
    loadTimeFrame,
    reloadActiveTimeFrame,
    loadVantageHealth,
    loadPoolConfig,
    setTemplateMode,
    setTier,
    setOperator,
    setSelectedVantage,
  }
})
