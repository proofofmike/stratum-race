import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useRaceStore } from './raceStore'
import type { RaceResult } from '@/types'

describe('raceStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function makeRaceResult(overrides: Partial<RaceResult> = {}): RaceResult {
    return {
      version: 1,
      vantage: 'v1',
      block_height: 878432,
      prevhash: '00000000000000000002a8f1',
      prevhash_short: '...a8f1d3e4b5c6',
      first_epoch: 1736956800.123,
      first_utc: '2025-01-15T12:00:00.123+00:00',
      confirm_window_s: 15.0,
      winner: 'atlaspool',
      winner_nonempty: 'atlaspool',
      block_miner: 'Foundry USA',
      block_miner_source: 'mempool.space',
      arrivals_offset_ms: { atlaspool: 0.0, ckpool: 42.3, ocean: 156.7 },
      nonempty_arrivals_offset_ms: { atlaspool: 0.0, ckpool: 42.3, ocean: 156.7 },
      empty_first_pools: [],
      empty_to_full_ms: {},
      missed_pools: [],
      eligible_at_start: ['atlaspool', 'ckpool', 'ocean'],
      pools_connected: 3,
      pools_eligible: 3,
      collector_meta: { version: 'stratum-race/0.5', uptime_seconds: 86400, session_races: 42 },
      ...overrides,
    }
  }

  describe('addRaceResult', () => {
    it('prepends race to recentBlocks', () => {
      const store = useRaceStore()
      const race = makeRaceResult()

      store.addRaceResult(race)

      expect(store.recentBlocks).toHaveLength(1)
      expect(store.recentBlocks[0].height).toBe(878432)
      expect(store.recentBlocks[0].winner).toBe('atlaspool')
    })

    it('caps recentBlocks at 40 entries', () => {
      const store = useRaceStore()

      for (let i = 0; i < 45; i++) {
        store.addRaceResult(makeRaceResult({ block_height: 878400 + i }))
      }

      expect(store.recentBlocks).toHaveLength(40)
      // Most recent should be first
      expect(store.recentBlocks[0].height).toBe(878444)
    })

    it('updates lastBlockEpoch', () => {
      const store = useRaceStore()
      expect(store.lastBlockEpoch).toBeNull()

      store.addRaceResult(makeRaceResult({ first_epoch: 1736956800.5 }))
      expect(store.lastBlockEpoch).toBe(1736956800.5)
    })

    it('computes total_spread_ms correctly', () => {
      const store = useRaceStore()
      store.addRaceResult(
        makeRaceResult({
          arrivals_offset_ms: { a: 0, b: 50, c: 200 },
        }),
      )
      expect(store.recentBlocks[0].spread_ms).toBe(200)
    })
  })

  describe('sortedLeaderboard', () => {
    it('sorts pools by median_ms ascending with nulls last', () => {
      const store = useRaceStore()
      store.leaderboardData = {
        poolA: { combined: { median_ms: 50, avg_ms: 55, p95_ms: 100, wins: 5, races_seen: 20, races_eligible: 20, win_pct: 25, empty_first_pct: 0, waste_min_day: 0.1 }, by_vantage: {} },
        poolB: { combined: { median_ms: 10, avg_ms: 12, p95_ms: 30, wins: 15, races_seen: 20, races_eligible: 20, win_pct: 75, empty_first_pct: 0, waste_min_day: 0.05 }, by_vantage: {} },
        poolC: { combined: { median_ms: null, avg_ms: null, p95_ms: null, wins: 0, races_seen: 0, races_eligible: 20, win_pct: 0, empty_first_pct: 0, waste_min_day: 0 }, by_vantage: {} },
      }

      const sorted = store.sortedLeaderboard
      expect(sorted[0].poolName).toBe('poolB') // median 10
      expect(sorted[1].poolName).toBe('poolA') // median 50
      expect(sorted[2].poolName).toBe('poolC') // null (last)
    })
  })

  describe('filteredByTier', () => {
    it('filters pools by tier using pool config pool_type', () => {
      const store = useRaceStore()

      store.poolConfig = [
        { name: 'poolA', display_name: 'Pool A', host: 'a.com', port: 3333, operator: 'OpA', groups: ['all', 'big'], pool_type: 'solo' },
        { name: 'poolB', display_name: 'Pool B', host: 'b.com', port: 3333, operator: 'OpB', groups: ['all'], pool_type: 'shared' },
      ] as any
      store.leaderboardData = {
        poolA: { combined: { median_ms: 10, avg_ms: 12, p95_ms: 30, wins: 10, races_seen: 20, races_eligible: 20, win_pct: 50, empty_first_pct: 0, waste_min_day: 0.05 }, by_vantage: {} },
        poolB: { combined: { median_ms: 20, avg_ms: 22, p95_ms: 40, wins: 5, races_seen: 20, races_eligible: 20, win_pct: 25, empty_first_pct: 0, waste_min_day: 0.1 }, by_vantage: {} },
      }

      // 'small' internally maps to solo pools
      const solo = store.filteredByTier('small')
      expect(solo).toHaveLength(1)
      expect(solo[0].poolName).toBe('poolA')

      const all = store.filteredByTier('all')
      expect(all).toHaveLength(2)
    })
  })

  describe('displayName', () => {
    it('returns display_name from pool config', () => {
      const store = useRaceStore()
      store.poolConfig = [
        { name: 'atlaspool', display_name: 'AtlasPool', host: 'solo.atlaspool.io', port: 3333, operator: 'AtlasPool', groups: ['all'] },
      ]
      expect(store.displayName('atlaspool')).toBe('AtlasPool')
    })

    it('falls back to internal name when config not found', () => {
      const store = useRaceStore()
      expect(store.displayName('unknown_pool')).toBe('unknown_pool')
    })
  })

  describe('setTemplateMode', () => {
    it('toggles template mode', () => {
      const store = useRaceStore()
      expect(store.templateMode).toBe('full')

      store.setTemplateMode('any')
      expect(store.templateMode).toBe('any')

      store.setTemplateMode('full')
      expect(store.templateMode).toBe('full')
    })
  })

  describe('setSelectedVantage', () => {
    it('updates the selected vantage filter', () => {
      const store = useRaceStore()
      expect(store.selectedVantage).toBe('combined')

      store.setSelectedVantage('v1')
      expect(store.selectedVantage).toBe('v1')

      store.setSelectedVantage('combined')
      expect(store.selectedVantage).toBe('combined')
    })
  })

  describe('setTier', () => {
    it('updates the selected tier filter', () => {
      const store = useRaceStore()
      expect(store.selectedTier).toBe('small')

      store.setTier('big')
      expect(store.selectedTier).toBe('big')

      store.setTier('all')
      expect(store.selectedTier).toBe('all')

      store.setTier('small')
      expect(store.selectedTier).toBe('small')
    })
  })

  describe('filteredByVantage', () => {
    it('returns only pools with stats for the selected vantage', () => {
      const store = useRaceStore()
      store.leaderboardData = {
        poolA: {
          combined: { median_ms: 10, avg_ms: 12, p95_ms: 30, wins: 10, races_seen: 20, races_eligible: 20, win_pct: 50, empty_first_pct: 0, waste_min_day: 0.05 },
          by_vantage: { 'v1': { median_ms: 8, avg_ms: 10, p95_ms: 25, wins: 5, races_seen: 10, races_eligible: 10, win_pct: 50, empty_first_pct: 0, waste_min_day: 0.04 } },
        },
        poolB: {
          combined: { median_ms: 20, avg_ms: 22, p95_ms: 40, wins: 5, races_seen: 20, races_eligible: 20, win_pct: 25, empty_first_pct: 0, waste_min_day: 0.1 },
          by_vantage: {},
        },
      }

      // Combined - both have stats
      store.setSelectedVantage('combined')
      expect(store.filteredByVantage).toHaveLength(2)

      // Specific vantage - only poolA has v1 stats
      store.setSelectedVantage('v1')
      expect(store.filteredByVantage).toHaveLength(1)
      expect(store.filteredByVantage[0].poolName).toBe('poolA')
    })
  })
})

describe('raceStore — review fixes', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  function makeRaceResult(overrides: Partial<RaceResult> = {}): RaceResult {
    return {
      version: 1,
      vantage: 'v1',
      block_height: 878432,
      prevhash: '00000000000000000002a8f1',
      prevhash_short: '...a8f1d3e4b5c6',
      first_epoch: 1736956800.123,
      first_utc: '2025-01-15T12:00:00.123+00:00',
      confirm_window_s: 15.0,
      winner: 'atlaspool',
      winner_nonempty: 'atlaspool',
      block_miner: 'Foundry USA',
      block_miner_source: 'mempool.space',
      arrivals_offset_ms: { atlaspool: 0.0, ckpool: 42.3, ocean: 156.7 },
      nonempty_arrivals_offset_ms: { atlaspool: 0.0, ckpool: 42.3, ocean: 156.7 },
      empty_first_pools: [],
      empty_to_full_ms: {},
      missed_pools: [],
      eligible_at_start: ['atlaspool', 'ckpool', 'ocean'],
      pools_connected: 3,
      pools_eligible: 3,
      collector_meta: { version: 'stratum-race/0.5', uptime_seconds: 86400, session_races: 42 },
      ...overrides,
    }
  }

  function mockAggregateFetch(ok: boolean, pools: Record<string, unknown> = {}) {
    return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok,
      status: ok ? 200 : 404,
      json: async () => ({ pools, generated_utc: '2026-07-16T12:00:00Z' }),
    } as Response)
  }

  describe('loadTimeFrame', () => {
    it('sets activeTimeFrame and fetches the mapped aggregate file', async () => {
      const store = useRaceStore()
      const fetchSpy = mockAggregateFetch(true, { poolA: { combined: {}, by_vantage: {} } })

      const ok = await store.loadTimeFrame('last10')

      expect(ok).toBe(true)
      expect(store.activeTimeFrame).toBe('last10')
      expect(fetchSpy).toHaveBeenCalledWith('/api/aggregates/recent-10.json')
      expect(store.leaderboardFromRecent).toBe(false)
      expect(store.aggregateLastUpdated).toBe('2026-07-16T12:00:00Z')
    })

    it('reverts the selection when the fetch fails', async () => {
      const store = useRaceStore()
      mockAggregateFetch(false)

      const ok = await store.loadTimeFrame('24h')

      expect(ok).toBe(false)
      // Selection reverted: active button never disagrees with displayed data
      expect(store.activeTimeFrame).toBe('7d')
    })

    it('clears leaderboardFromRecent so WebSocket races do not overwrite server data', async () => {
      const store = useRaceStore()
      // Simulate fallback state
      store.leaderboardFromRecent = true
      mockAggregateFetch(true, { poolA: { combined: { median_ms: 5 }, by_vantage: {} } })

      await store.loadTimeFrame('last50')
      expect(store.leaderboardFromRecent).toBe(false)

      // A new WebSocket race must NOT rebuild the leaderboard from recents
      store.addRaceResult(makeRaceResult())
      expect(store.leaderboardData.poolA).toBeDefined()
      expect((store.leaderboardData.poolA.combined as { median_ms?: number }).median_ms).toBe(5)
    })
  })

  describe('reloadActiveTimeFrame', () => {
    it('refetches the aggregate for the CURRENT selection, not a fixed period', async () => {
      const store = useRaceStore()
      const fetchSpy = mockAggregateFetch(true)

      await store.loadTimeFrame('last10')
      fetchSpy.mockClear()

      await store.reloadActiveTimeFrame()
      expect(fetchSpy).toHaveBeenCalledWith('/api/aggregates/recent-10.json')
      expect(store.activeTimeFrame).toBe('last10')
    })
  })

  describe('buildLeaderboardFromRecent win % math', () => {
    it('uses races-in-scope as the win % denominator, not podium appearances', () => {
      const store = useRaceStore()
      // 10 races: poolA wins 2, poolB wins 8. poolA appears only when winning.
      for (let i = 0; i < 10; i++) {
        const winner = i < 2 ? 'poolA' : 'poolB'
        store.addRaceResult(
          makeRaceResult({
            block_height: 878400 + i,
            first_epoch: 1736956800 + i * 600,
            winner,
            winner_nonempty: winner,
            arrivals_offset_ms: { [winner]: 0.0, other: 50.0 },
          }),
        )
      }

      const poolA = store.leaderboardData.poolA
      expect(poolA).toBeDefined()
      // Old bug: 2 wins / 2 podium appearances = 100%. Fixed: 2/10 = 20%.
      expect(poolA.combined.win_pct).toBe(20)
      expect(poolA.combined.races_seen).toBe(10)
    })
  })

  describe('null-height dedup', () => {
    it('keeps two different null-height blocks from the same vantage', () => {
      const store = useRaceStore()
      store.addRaceResult(makeRaceResult({ block_height: null as unknown as number, first_epoch: 1736956800 }))
      store.addRaceResult(makeRaceResult({ block_height: null as unknown as number, first_epoch: 1736957400 }))

      expect(store.recentBlocks).toHaveLength(2)
    })

    it('replaces a re-posted null-height block with the same epoch', () => {
      const store = useRaceStore()
      const race = makeRaceResult({ block_height: null as unknown as number, first_epoch: 1736956800 })
      store.addRaceResult(race)
      store.addRaceResult(race)

      expect(store.recentBlocks).toHaveLength(1)
    })
  })

  describe('pools_seen matches backend definition', () => {
    it('counts pools that reported arrivals, not pools_eligible', () => {
      const store = useRaceStore()
      store.addRaceResult(
        makeRaceResult({
          arrivals_offset_ms: { a: 0, b: 10 }, // 2 pools reported
          pools_eligible: 9,
        }),
      )
      expect(store.recentBlocks[0].pools_seen).toBe(2)
    })
  })
})
