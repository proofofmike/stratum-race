/**
 * TypeScript interfaces for StratumRace Platform v2
 * Matches the data models defined in the design document.
 */

/** Collector metadata included in each Race_Result */
export interface CollectorMeta {
  version: string
  uptime_seconds: number
  session_races: number
}

/** A single race result from a collector, matching Race_Result JSON schema */
export interface RaceResult {
  version: number
  vantage: string
  block_height: number
  prevhash: string
  prevhash_short: string
  first_epoch: number
  first_utc: string
  confirm_window_s: number
  winner: string
  winner_nonempty: string
  block_miner: string
  block_miner_source: string
  arrivals_offset_ms: Record<string, number>
  nonempty_arrivals_offset_ms: Record<string, number>
  empty_first_pools: string[]
  empty_to_full_ms: Record<string, number>
  missed_pools: string[]
  eligible_at_start: string[]
  pools_connected: number
  pools_eligible: number
  collector_meta: CollectorMeta
}

/** Per-pool statistics within an aggregate (combined or per-vantage) */
export interface PoolStats {
  median_ms: number | null
  avg_ms: number | null
  p95_ms: number | null
  wins: number
  races_seen: number
  races_eligible: number
  win_pct: number
  empty_first_pct: number
  waste_min_day: number
}

/** Pool data within a daily or monthly aggregate file */
export interface PoolAggregate {
  combined: PoolStats
  by_vantage: Record<string, PoolStats>
  /** Stats computed from arrivals_offset_ms (any template, including empty) */
  any_combined?: PoolStats
  any_by_vantage?: Record<string, PoolStats>
}

/** Daily aggregate file structure (aggregates/daily/YYYY-MM-DD.json) */
export interface DailyAggregate {
  date: string
  generated_utc: string
  total_races: number
  vantage_points: string[]
  pools: Record<string, PoolAggregate>
}

/** Monthly aggregate file structure (aggregates/monthly/YYYY-MM.json) */
export interface MonthlyAggregate extends DailyAggregate {
  month: string
}

/** Summary entry for recent-blocks.json */
export interface RecentBlock {
  height: number
  utc: string
  epoch: number
  miner: string
  winner: string
  winner_nonempty: string
  empty_jumpstart: string | null
  second: string | null
  second_delay_ms: number | null
  spread_ms: number
  pools_seen: number
  vantage: string
}

/** Vantage point health status from status/vantages.json */
export interface VantageHealth {
  status: 'online' | 'offline'
  last_heartbeat_utc: string
  last_race_utc: string
  connected_pools: number
}

/** Vantage health status file structure */
export interface VantageStatusFile {
  vantages: Record<string, VantageHealth>
}

/** A single pool entry from the pool configuration (config/pools.json) */
export interface PoolConfig {
  name: string
  display_name: string
  host: string
  port: number
  operator: string
  groups: string[]
}

/** Pool configuration file structure */
export interface PoolConfigFile {
  version: number
  updated_utc: string
  pools: PoolConfig[]
}

/** Leaderboard row displayed in the frontend */
export interface LeaderboardRow {
  rank: number
  pool_name: string
  display_name: string
  operator: string
  tier: 'big' | 'small'
  stats: PoolStats
}

/** WebSocket connection state */
export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected' | 'polling'

/** Template display mode */
export type TemplateMode = 'full' | 'any'

/** Leaderboard time frame selection (each maps to one pre-computed aggregate file) */
export type TimeFrame = 'last10' | 'last50' | '24h' | '7d'

/** Pool tier filter selection */
export type PoolTier = 'all' | 'big' | 'small'

/** Timezone display preference */
export type TimezonePreference = 'utc' | 'local'
