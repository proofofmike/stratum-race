<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRaceStore } from '@/stores/raceStore'
import { useTimezone } from '@/composables/useTimezone'
import { useVantageNames } from '@/composables/useVantageNames'
import type { RaceResult, RecentBlock } from '@/types'

const route = useRoute()
const router = useRouter()
const store = useRaceStore()
const { formatTimestamp } = useTimezone()
const { formatVantage } = useVantageNames()

/** Block height from route params */
const blockHeight = computed(() => Number(route.params.height))

/**
 * Find all matching races for this block height across vantage points.
 * Multiple vantage points may have observed the same block.
 */
const recentMatches = computed<RecentBlock[]>(() => {
  return store.recentBlocks.filter(
    (b) => b.height === blockHeight.value,
  )
})

/** Full race results fetched from the backend (contains per-pool offsets) */
const fullRaces = ref<RaceResult[]>([])
const isLoading = ref(false)
const loadError = ref(false)

/**
 * Build candidate S3 URLs for a block height we have no store entry for.
 * Tries the last 4 days × all known vantages, since we don't know the exact
 * date or which vantages observed it.
 */
function candidateUrls(height: number): string[] {
  const vantages = Object.keys(store.vantageDisplay)
  if (vantages.length === 0) return []

  const urls: string[] = []
  const now = new Date()
  for (let daysAgo = 0; daysAgo <= 3; daysAgo++) {
    const d = new Date(now.getTime() - daysAgo * 86400000)
    const year = d.getUTCFullYear()
    const month = String(d.getUTCMonth() + 1).padStart(2, '0')
    const day = String(d.getUTCDate()).padStart(2, '0')
    for (const vantage of vantages) {
      urls.push(`/api/races/${year}/${month}/${day}/${height}-${vantage}.json`)
    }
  }
  return urls
}

/**
 * Fetch full race JSON files for the given block.
 * When the block is in the store, uses the stored epoch+vantage to build exact
 * paths. When it has aged out of the store, probes candidate paths across the
 * last 4 days and all known vantages.
 *
 * Fetches the per-vantage race JSON for this block directly from S3.
 */
async function fetchFullRaces() {
  isLoading.value = true
  loadError.value = false
  fullRaces.value = []

  const results: RaceResult[] = []

  if (recentMatches.value.length > 0) {
    // Fast path: exact paths from store entries
    for (const block of recentMatches.value) {
      const dt = new Date(block.epoch * 1000)
      const year = dt.getUTCFullYear()
      const month = String(dt.getUTCMonth() + 1).padStart(2, '0')
      const day = String(dt.getUTCDate()).padStart(2, '0')
      const heightPart = block.height != null ? String(block.height) : `unknown-${Math.floor(block.epoch)}`
      const url = `/api/races/${year}/${month}/${day}/${heightPart}-${block.vantage}.json`
      try {
        const response = await fetch(url)
        if (response.ok) results.push(await response.json())
      } catch { /* skip */ }
    }
  } else {
    // Fallback: block aged out of store — probe candidate paths in parallel
    const urls = candidateUrls(blockHeight.value)
    const fetches = urls.map(async (url) => {
      try {
        const response = await fetch(url)
        if (response.ok) return await response.json() as RaceResult
      } catch { /* skip */ }
      return null
    })
    const settled = await Promise.all(fetches)
    for (const r of settled) {
      if (r != null) results.push(r)
    }
  }

  fullRaces.value = results
  if (results.length === 0) {
    loadError.value = true
  }
  isLoading.value = false
}

// Fetch full race data when the component mounts or block height changes
onMounted(fetchFullRaces)
watch(blockHeight, fetchFullRaces)
// Also retry when vantageDisplay loads (needed for the fallback probe path)
watch(
  () => Object.keys(store.vantageDisplay).length,
  (count) => {
    if (count > 0 && fullRaces.value.length === 0 && !isLoading.value) {
      fetchFullRaces()
    }
  },
)
// Also retry when recentMatches becomes populated (handles deep-link race condition)
watch(recentMatches, (newVal) => {
  if (newVal.length > 0 && fullRaces.value.length === 0 && !isLoading.value) {
    fetchFullRaces()
  }
})

/**
 * Use full race data when available, otherwise fall back to recent block summaries.
 */
const races = computed(() => fullRaces.value.length > 0 ? fullRaces.value : recentMatches.value)

/** Primary race data (first match or null) */
const primaryRace = computed(() => races.value[0] ?? null)

/** Whether this block has data from multiple vantage points */
const hasMultipleVantages = computed(() => races.value.length > 1)

/** All unique vantage points for this block (sorted for deterministic color assignment) */
const vantagePoints = computed(() => [...new Set(races.value.map((r: any) => r.vantage))].sort())

/** Fixed color palette for vantage points (assigned by sorted name, always consistent) */
const VANTAGE_COLORS = ['var(--accent)', '#f59e0b', '#10b981', '#ec4899', '#8b5cf6']

function getVantageColor(vantage: string): string {
  const idx = vantagePoints.value.indexOf(vantage)
  return VANTAGE_COLORS[idx >= 0 ? idx : 0] ?? 'var(--text-secondary)'
}

// ─── Time-aligned chart reconstruction ────────────────────────────────────────

/**
 * One plotted event on the shared absolute timeline.
 * `offsetMs` is the position in milliseconds from t0 (the first event across
 * ALL vantages), so every row shares one absolute x-axis.
 */
interface ChartEvent {
  pool: string
  vantage: string
  /** 'empty' = hollow dot (any/empty-first arrival), 'full' = filled dot */
  kind: 'empty' | 'full'
  /** ms from the shared timeline origin (t0) */
  offsetMs: number
  /** true only for the vantage's winner_nonempty FULL dot (blink target) */
  isFullWin: boolean
}

/**
 * Reconstruct absolute arrival times for every (pool, vantage, kind) and merge
 * them onto one shared timeline.
 *
 * Reconstruction math (per the collector's offset encoding):
 *   - any/empty arrival  = first_epoch + arrivals_offset_ms[pool]/1000
 *   - neBaseAbs (first FULL arrival at this vantage):
 *       wn = winner_nonempty
 *       if empty_first_pools includes wn:
 *         neBaseAbs = first_epoch + arrivals_offset_ms[wn]/1000 + empty_to_full_ms[wn]/1000
 *       else:
 *         neBaseAbs = first_epoch + arrivals_offset_ms[wn]/1000
 *   - full arrival       = neBaseAbs + nonempty_arrivals_offset_ms[pool]/1000
 *
 * Events are de-duplicated on (pool, vantage, kind, rounded-time) because some
 * pools appear in both offset maps and would otherwise double-plot.
 */
const chartData = computed(() => {
  interface AbsEvent {
    pool: string
    vantage: string
    kind: 'empty' | 'full'
    abs: number
    isFullWin: boolean
  }

  const absEvents: AbsEvent[] = []

  for (const race of races.value as any[]) {
    const fe: number | undefined = race.first_epoch
    const ao: Record<string, number> = race.arrivals_offset_ms || {}
    const neo: Record<string, number> = race.nonempty_arrivals_offset_ms || {}
    const efp: string[] = race.empty_first_pools || []
    const etf: Record<string, number> = race.empty_to_full_ms || {}
    const wn: string | undefined = race.winner_nonempty
    const vantage: string = race.vantage

    // Need first_epoch to place anything on an absolute axis.
    if (fe == null || !Number.isFinite(fe)) continue

    // Compute neBaseAbs: the absolute instant of the first FULL arrival here.
    let neBaseAbs: number | null = null
    if (wn != null && ao[wn] != null) {
      if (efp.includes(wn) && etf[wn] != null) {
        neBaseAbs = fe + ao[wn] / 1000 + etf[wn] / 1000
      } else {
        neBaseAbs = fe + ao[wn] / 1000
      }
    }

    // Empty-template arrivals (hollow dots). A pool's "any" arrival is only a
    // genuine empty template when the pool actually led with an empty one
    // (it is in empty_first_pools). For every other pool the "any" arrival IS
    // its full template, so emitting it here would plot a spurious hollow dot
    // that duplicates the full arrival — skip those.
    for (const [pool, ms] of Object.entries(ao)) {
      if (ms == null || !Number.isFinite(ms)) continue
      if (!efp.includes(pool)) continue
      absEvents.push({
        pool,
        vantage,
        kind: 'empty',
        abs: fe + ms / 1000,
        isFullWin: false,
      })
    }

    // Full arrivals (filled dots). Requires a valid neBaseAbs.
    if (neBaseAbs != null) {
      for (const [pool, ms] of Object.entries(neo)) {
        if (ms == null || !Number.isFinite(ms)) continue
        absEvents.push({
          pool,
          vantage,
          kind: 'full',
          abs: neBaseAbs + ms / 1000,
          // Blink ONLY the winner_nonempty full dot (offset == 0).
          isFullWin: pool === wn && ms === 0,
        })
      }
    }
  }

  if (absEvents.length === 0) {
    return { rows: [] as ChartRow[], t0: 0, maxMs: 0 }
  }

  // Shared timeline origin = earliest event across all vantages.
  const t0 = Math.min(...absEvents.map((e) => e.abs))

  // De-duplicate identical (pool, vantage, kind, time) events. Round the
  // offset to the nearest 0.1ms so floating-point noise doesn't defeat it.
  const seen = new Set<string>()
  const events: ChartEvent[] = []
  for (const e of absEvents) {
    const offsetMs = (e.abs - t0) * 1000
    const key = `${e.pool}|${e.vantage}|${e.kind}|${Math.round(offsetMs * 10)}`
    if (seen.has(key)) continue
    seen.add(key)
    events.push({
      pool: e.pool,
      vantage: e.vantage,
      kind: e.kind,
      offsetMs,
      isFullWin: e.isFullWin,
    })
  }

  // Group events into one row per pool.
  const byPool = new Map<string, ChartEvent[]>()
  for (const ev of events) {
    const list = byPool.get(ev.pool)
    if (list) list.push(ev)
    else byPool.set(ev.pool, [ev])
  }

  const rows: ChartRow[] = []
  for (const [pool, evs] of byPool) {
    evs.sort((a, b) => a.offsetMs - b.offsetMs)
    const earliestFull = evs.find((e) => e.kind === 'full')?.offsetMs ?? Infinity
    const earliest = evs[0]?.offsetMs ?? Infinity
    rows.push({
      pool,
      displayName: store.displayName(pool),
      events: evs,
      isWinner: evs.some((e) => e.isFullWin),
      sortKey: earliestFull !== Infinity ? earliestFull : earliest,
    })
  }

  // Sort rows by earliest full arrival (fastest winners at the top).
  rows.sort((a, b) => a.sortKey - b.sortKey)

  const maxMs = Math.max(...events.map((e) => e.offsetMs))

  return { rows, t0, maxMs }
})

interface ChartRow {
  pool: string
  displayName: string
  events: ChartEvent[]
  isWinner: boolean
  sortKey: number
}

const chartRows = computed(() => chartData.value.rows)
/** Full span of the data in ms (largest offset from t0) */
const dataMaxMs = computed(() => chartData.value.maxMs)

// ─── Dynamic range / zoom ─────────────────────────────────────────────────────

/**
 * Default visible window. Block windows can span 7-15s due to a handful of
 * very slow pools, while the common case lands within ~2s. We cap the
 * visible axis so the common case stays readable, and mark pools/events that
 * fall beyond the window rather than squashing everything to the left.
 */
const DEFAULT_WINDOW_MS = 2000

/** Whether the user has toggled to "fit all" (show the full span). */
const fitAll = ref(false)

/** The visible axis maximum in ms. Rounded up to a clean multiple of the
 *  adaptive major-tick step so the final gridline lands on the window edge. */
const windowMaxMs = computed(() => {
  const raw = fitAll.value
    ? Math.max(250, dataMaxMs.value)
    : Math.min(DEFAULT_WINDOW_MS, Math.max(250, dataMaxMs.value))
  // Derive the tick step from the raw span (avoids a circular dep on windowMaxMs)
  // and round the window up to a whole number of steps.
  const step = niceStepMs(raw)
  return Math.ceil(raw / step) * step
})

/** True when some events fall beyond the current visible window. */
const hasOverflow = computed(() => dataMaxMs.value > windowMaxMs.value + 0.5)

function toggleFitAll() {
  fitAll.value = !fitAll.value
}

/** Whether to show empty-template dots in the bars. Default OFF — empty
 *  templates are a warmup detail, not the competitive signal. */
const showEmpty = ref(false)

function toggleShowEmpty() {
  showEmpty.value = !showEmpty.value
}

/** Clamp a left-position percentage into [0, 100]. */
function markerLeftPct(offsetMs: number): number {
  const max = windowMaxMs.value || 1
  return Math.max(0, Math.min(100, (offsetMs / max) * 100))
}

/** Events for a row that are visible within the current window.
 *  Empty-template dots are hidden unless the user toggles them on. */
function visibleEvents(row: ChartRow): ChartEvent[] {
  return row.events.filter(
    (e) =>
      e.offsetMs <= windowMaxMs.value + 0.5 &&
      (showEmpty.value || e.kind !== 'empty'),
  )
}

/** Events listed in a row's hover tooltip. Mirrors the bar's empty-visibility
 *  toggle (and since non-empty-first pools have no empty events at all, their
 *  tooltips never show an empty line regardless of the toggle). */
function tooltipEvents(row: ChartRow): ChartEvent[] {
  return row.events.filter((e) => showEmpty.value || e.kind !== 'empty')
}

/**
 * The earliest event for a row that falls BEYOND the visible window, if any.
 * Used to render a "+Nms" off-window marker at the right edge.
 */
function overflowEvent(row: ChartRow): ChartEvent | null {
  const beyond = row.events.filter(
    (e) =>
      e.offsetMs > windowMaxMs.value + 0.5 &&
      (showEmpty.value || e.kind !== 'empty'),
  )
  if (beyond.length === 0) return null
  // Prefer the earliest FULL overflow, else the earliest overflow.
  const full = beyond.find((e) => e.kind === 'full')
  return full ?? beyond[0]
}

// ─── Gridlines ────────────────────────────────────────────────────────────────

interface Gridline {
  offsetMs: number
  leftPct: number
  major: boolean
  label: string
}

/**
 * Pick a "nice" major-tick step (in ms) for the current window so the axis
 * shows a readable number of labels (~6-8) regardless of window size. A fixed
 * step crowds the labels: 250ms is fine for the 2s common window but produces
 * dozens of overlapping labels once "Fit all events" stretches the window to
 * many seconds. Steps follow a 1/2/5 progression (…100, 250, 500, 1000, 2500…).
 */
function niceStepMs(windowMs: number, targetTicks = 7): number {
  const rough = Math.max(windowMs, 1) / targetTicks
  const pow10 = Math.pow(10, Math.floor(Math.log10(rough)))
  for (const mult of [1, 2, 2.5, 5, 10]) {
    const step = mult * pow10
    if (step >= rough) return step
  }
  return 10 * pow10
}

/** Major-tick step in ms, adaptive to the visible window. */
const majorStepMs = computed(() => niceStepMs(windowMaxMs.value))
/** Minor gridline step = half the major step (one minor line between majors). */
const minorStepMs = computed(() => majorStepMs.value / 2)

/** Format a tick label, dropping trailing zeros so small and large steps both
 *  read cleanly (e.g. "0.25s", "1s", "2.5s"). */
function formatTickLabel(ms: number): string {
  const s = ms / 1000
  // Up to 2 decimals, then strip trailing zeros and any trailing dot.
  return `${s.toFixed(2).replace(/\.?0+$/, '')}s`
}

const gridlines = computed<Gridline[]>(() => {
  const max = windowMaxMs.value
  const major = majorStepMs.value
  const minor = minorStepMs.value
  const lines: Gridline[] = []
  for (let ms = 0; ms <= max + 0.5; ms += minor) {
    const isMajor = Math.abs(ms % major) < 0.5 || Math.abs((ms % major) - major) < 0.5
    lines.push({
      offsetMs: ms,
      leftPct: (ms / (max || 1)) * 100,
      major: isMajor,
      label: isMajor ? formatTickLabel(ms) : '',
    })
  }
  return lines
})

/** Axis tick labels (major gridlines only), for the top and bottom rails. */
const axisTicks = computed(() => gridlines.value.filter((g) => g.major))

// ─── Marker helpers ───────────────────────────────────────────────────────────

function markerTooltip(ev: ChartEvent): string {
  const tpl = ev.kind === 'full' ? 'full template' : 'empty template'
  const win = ev.isFullWin ? ' — full-template WIN (highlighted)' : ''
  return `${formatVantage(ev.vantage)} · ${tpl} · +${ev.offsetMs.toFixed(1)} ms${win}`
}

// ─── Block metadata (header) ───────────────────────────────────────────────────

const blockMiner = computed(() => {
  const race = primaryRace.value
  if (!race) return 'Unknown'
  return (race as any).block_miner || (race as any).miner || 'Unknown'
})

/** Winners per vantage point (full template) */
const winnersPerVantage = computed(() => {
  return races.value.map((race: any) => ({
    vantage: race.vantage,
    winner: race.winner_nonempty || race.winner || '—',
  }))
})

const formattedTime = computed(() => {
  if (!primaryRace.value) return '—'
  const race = primaryRace.value as any
  const epoch = race.first_epoch ?? race.epoch
  return epoch ? formatTimestamp(epoch) : '—'
})

function goBack() {
  router.push({ name: 'recent-blocks' })
}
</script>

<template>
  <div class="block-detail-view">
    <!-- Back button -->
    <button class="back-btn" @click="goBack" aria-label="Back to Recent Blocks">
      ← Back to Recent Blocks
    </button>

    <!-- Loading / not-found states -->
    <div v-if="isLoading" class="not-found">
      <p>Loading race data...</p>
    </div>
    <div v-else-if="!primaryRace && recentMatches.length === 0" class="not-found">
      <h2>Block Not Available</h2>
      <p>Race data for block {{ blockHeight.toLocaleString() }} could not be found. It may be older than the retention window.</p>
      <button @click="goBack">Return to Recent Blocks</button>
    </div>

    <!-- Block detail content -->
    <template v-else-if="primaryRace">
      <!-- Block metadata header -->
      <header class="block-header">
        <div class="title-row">
          <h2 class="block-title">Block {{ blockHeight.toLocaleString() }}</h2>
        </div>
        <div class="metadata-grid">
          <div class="meta-item">
            <span class="meta-label">Time</span>
            <span class="meta-value">{{ formattedTime }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Block Miner</span>
            <span class="meta-value">{{ blockMiner }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Winner (Full Template)</span>
            <div class="meta-winners">
              <div v-for="w in winnersPerVantage" :key="w.vantage" class="winner-entry">
                <span class="winner-vantage">{{ formatVantage(w.vantage) }}:</span>
                <span class="meta-value winner-value">{{ store.displayName(w.winner) }}</span>
              </div>
            </div>
          </div>
          <div class="meta-item">
            <span class="meta-label">Pools</span>
            <span class="meta-value">{{ chartRows.length }}</span>
          </div>
        </div>
      </header>

      <!-- Legends + zoom control -->
      <div class="controls-row">
        <div v-if="hasMultipleVantages" class="vantage-legend">
          <span class="legend-label">Vantage Points:</span>
          <span v-for="vp in vantagePoints" :key="vp" class="legend-item">
            <span class="legend-dot" :style="{ backgroundColor: getVantageColor(vp) }"></span>
            {{ formatVantage(vp) }}
          </span>
        </div>

        <div class="marker-legend">
          <span class="legend-item">
            <span class="legend-marker solid"></span>
            Full template
          </span>
          <span v-if="showEmpty" class="legend-item">
            <span class="legend-marker hollow"></span>
            Empty template
          </span>
          <span class="legend-item">
            <span class="legend-marker win-ring"></span>
            Full-template win
          </span>
        </div>
      </div>

      <p class="axis-caption">
        Every row shares one absolute timeline. 0.00s is the first notification
        observed across all vantages; each dot is positioned at its true arrival
        time, so a vertical slice reads as one instant.
        <span v-if="hasOverflow && !fitAll">
          Some pools arrived after {{ (windowMaxMs / 1000).toFixed(2) }}s — shown
          as “+Nms” markers at the right edge. Use “Fit all events” to see them in place.
        </span>
      </p>

      <!-- Chart controls: dedicated row directly above the chart, with a fixed
           button order/position so toggling never reflows the layout. -->
      <div class="chart-toolbar">
        <button
          type="button"
          class="toolbar-btn"
          :aria-pressed="showEmpty"
          @click="toggleShowEmpty"
        >
          {{ showEmpty ? 'Hide empty templates' : 'Show empty templates' }}
        </button>
        <button
          type="button"
          class="toolbar-btn"
          :aria-pressed="fitAll"
          @click="toggleFitAll"
        >
          {{ fitAll ? 'Zoom to common window' : 'Fit all events' }}
        </button>
      </div>

      <!-- Time-aligned chart -->
      <section class="chart-section" aria-label="Time-aligned block notification chart">
        <!-- Top axis rail -->
        <div class="axis-rail top">
          <span class="axis-spacer"></span>
          <div class="axis-track">
            <span
              v-for="tick in axisTicks"
              :key="`top-${tick.offsetMs}`"
              class="axis-tick"
              :style="{ left: `${tick.leftPct}%` }"
            >{{ tick.label }}</span>
          </div>
          <span class="axis-overflow-spacer" v-if="hasOverflow"></span>
        </div>

        <!-- Pool rows -->
        <div class="chart-rows">
          <div
            v-for="row in chartRows"
            :key="row.pool"
            class="chart-row"
            :class="{ 'is-winner': row.isWinner }"
          >
            <div class="row-pool-name">
              <span class="pool-display-name">{{ row.displayName }}</span>
              <span v-if="row.isWinner" class="winner-badge" title="Won at least one full-template race">🏆</span>
            </div>

            <div class="row-track">
              <!-- Gridlines -->
              <span
                v-for="g in gridlines"
                :key="`grid-${g.offsetMs}`"
                class="gridline"
                :class="{ major: g.major, minor: !g.major }"
                :style="{ left: `${g.leftPct}%` }"
                aria-hidden="true"
              ></span>

              <!-- Dots -->
              <span
                v-for="(ev, i) in visibleEvents(row)"
                :key="`ev-${i}`"
                class="dot"
                :class="{
                  'dot-full': ev.kind === 'full',
                  'dot-empty': ev.kind === 'empty',
                  'dot-win': ev.isFullWin,
                }"
                :style="{
                  left: `${markerLeftPct(ev.offsetMs)}%`,
                  borderColor: getVantageColor(ev.vantage),
                  backgroundColor: ev.kind === 'full' ? getVantageColor(ev.vantage) : 'transparent',
                }"
                :title="markerTooltip(ev)"
                tabindex="0"
                role="img"
                :aria-label="markerTooltip(ev)"
              ></span>
            </div>

            <!-- Off-window overflow marker -->
            <div class="row-overflow" v-if="hasOverflow">
              <span
                v-if="overflowEvent(row)"
                class="overflow-chip"
                :class="{
                  'chip-full': overflowEvent(row)!.kind === 'full',
                  'chip-empty': overflowEvent(row)!.kind === 'empty',
                }"
                :style="{ color: getVantageColor(overflowEvent(row)!.vantage) }"
                :title="markerTooltip(overflowEvent(row)!)"
              >+{{ Math.round(overflowEvent(row)!.offsetMs) }}ms</span>
            </div>

            <!-- Hover tooltip (full detail, also screen-reader accessible) -->
            <div class="row-tooltip" role="tooltip">
              <strong>{{ row.displayName }}</strong>
              <span v-if="row.isWinner" class="tooltip-winner"> 🏆 Full-template win</span>
              <div
                v-for="(ev, i) in tooltipEvents(row)"
                :key="`tip-${i}`"
                class="tooltip-line"
                :class="{ 'tip-win': ev.isFullWin }"
              >
                <span class="tooltip-type">{{ ev.kind === 'full' ? '● full' : '○ empty' }}</span>
                <span class="tooltip-vantage">{{ formatVantage(ev.vantage) }}</span>
                <span class="tooltip-ms">+{{ ev.offsetMs.toFixed(1) }} ms</span>
                <span v-if="ev.isFullWin" class="tooltip-winflag">win</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Bottom axis rail -->
        <div class="axis-rail bottom">
          <span class="axis-spacer"></span>
          <div class="axis-track">
            <span
              v-for="tick in axisTicks"
              :key="`bot-${tick.offsetMs}`"
              class="axis-tick"
              :style="{ left: `${tick.leftPct}%` }"
            >{{ tick.label }}</span>
          </div>
          <span class="axis-overflow-spacer" v-if="hasOverflow"></span>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.block-detail-view {
  max-width: 1100px;
  margin: 0 auto;
  padding: 1.5rem;
}

.back-btn {
  margin-bottom: 1.5rem;
  background: var(--surface-elevated);
  color: var(--accent);
  font-weight: 500;
  font-size: 0.875rem;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
}

.back-btn:hover {
  background: var(--border);
}

/* Not found state */
.not-found {
  text-align: center;
  padding: 3rem 1rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
}

.not-found h2 {
  margin-bottom: 0.75rem;
  color: var(--text-primary);
}

.not-found p {
  color: var(--text-secondary);
  margin-bottom: 1.5rem;
}

/* Block header */
.block-header {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1.25rem;
  margin-bottom: 1rem;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.block-title {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text-primary);
}

.metadata-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.meta-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
}

.meta-value {
  font-family: var(--font-mono);
  font-size: 0.875rem;
  color: var(--text-primary);
}

.winner-value {
  color: var(--accent);
  font-weight: 600;
}

.meta-winners {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.winner-entry {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.winner-vantage {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

/* Controls row */
.controls-row {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 0.5rem 0;
  margin-bottom: 0.25rem;
}

.vantage-legend,
.marker-legend {
  display: flex;
  align-items: center;
  gap: 1rem;
  font-size: 0.8125rem;
  color: var(--text-secondary);
}

.legend-label {
  font-weight: 600;
  color: var(--text-primary);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-marker {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
  box-sizing: border-box;
}

.legend-marker.solid {
  background-color: var(--text-secondary);
}

.legend-marker.hollow {
  background-color: transparent;
  border: 2px solid var(--text-secondary);
}

.legend-marker.win-ring {
  background-color: var(--text-secondary);
  border: 3px solid #facc15;
}

.zoom-toggle {
  margin-left: auto;
  background: var(--surface-elevated);
  color: var(--accent);
  font-weight: 500;
  font-size: 0.8125rem;
  padding: 0.375rem 0.75rem;
  border-radius: 0.375rem;
  border: 1px solid var(--border);
}

.zoom-toggle:hover {
  background: var(--border);
}

/* Dedicated controls row directly above the chart. Fixed button order and a
   stable width per button so toggling a label never shifts the other button. */
.chart-toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  flex-wrap: wrap;
}

.toolbar-btn {
  background: var(--surface-elevated);
  color: var(--accent);
  font-weight: 500;
  font-size: 0.8125rem;
  padding: 0.375rem 0.75rem;
  border-radius: 0.375rem;
  border: 1px solid var(--border);
  cursor: pointer;
  /* Reserve enough width for the longest label state ("Zoom to common window")
     so the button doesn't resize (and nudge its neighbor) when the text
     toggles. white-space:nowrap keeps a label on one line. */
  min-width: 15rem;
  white-space: nowrap;
  text-align: center;
}

.toolbar-btn:hover {
  background: var(--border);
}

.toolbar-btn[aria-pressed='true'] {
  border-color: var(--accent);
}

.axis-caption {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 0.75rem;
  line-height: 1.4;
}

/* Chart section */
.chart-section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1rem 1.25rem;
}

/* Layout constants shared by rows and axis rails */
.axis-rail,
.chart-row {
  display: flex;
  align-items: center;
}

.axis-spacer,
.row-pool-name {
  width: 140px;
  flex-shrink: 0;
}

.axis-overflow-spacer,
.row-overflow {
  width: 64px;
  flex-shrink: 0;
  padding-left: 0.375rem;
}

.axis-track {
  position: relative;
  flex: 1;
  min-width: 0;
  height: 1rem;
}

.axis-rail .axis-tick {
  position: absolute;
  transform: translateX(-50%);
  font-size: 0.625rem;
  font-family: var(--font-mono);
  color: var(--text-secondary);
  white-space: nowrap;
}

.axis-rail.top {
  padding-bottom: 0.25rem;
  border-bottom: 1px solid var(--border);
  margin-bottom: 0.25rem;
}

.axis-rail.bottom {
  padding-top: 0.25rem;
  border-top: 1px solid var(--border);
  margin-top: 0.25rem;
}

/* Rows */
.chart-rows {
  display: flex;
  flex-direction: column;
}

.chart-row {
  position: relative;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--border);
}

.chart-row:last-child {
  border-bottom: none;
}

.chart-row.is-winner {
  background: rgba(57, 135, 229, 0.06);
  border-radius: 0.25rem;
}

.row-pool-name {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding-right: 0.75rem;
}

.pool-display-name {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.winner-badge {
  font-size: 0.75rem;
  flex-shrink: 0;
}

.row-track {
  position: relative;
  flex: 1;
  min-width: 0;
  height: 24px;
  background: var(--surface-elevated);
  border-radius: 0.25rem;
  border: 1px solid var(--border);
}

/* Gridlines */
.gridline {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
}

.gridline.major {
  background: var(--border);
}

.gridline.minor {
  background: var(--border);
  opacity: 0.4;
}

/* Dots */
.dot {
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid;
  box-sizing: border-box;
  z-index: 2;
  cursor: default;
}

.dot-empty {
  background-color: transparent !important;
}

/* Full-template win: gold border around the point.
   The dot's border-color is set via an INLINE style (the vantage color), which
   beats a normal stylesheet rule. A CSS animation, however, overrides inline
   styles without needing !important — and !important would defeat the flash,
   since a static !important value wins over an animated value. So we drive the
   gold border purely through animation:
     - no reduced-motion preference: flash the border on/off
     - reduced-motion: a steady (non-flashing) animation holds it solid gold,
       still overriding the inline vantage color. */
.dot-win {
  border-width: 3px;
  z-index: 3;
  animation: win-border-solid 1s step-end infinite;
}

@media (prefers-reduced-motion: no-preference) {
  .dot-win {
    animation: win-border-flash 1s step-end infinite;
  }
}

/* Steady gold border (reduced-motion fallback). */
@keyframes win-border-solid {
  0%, 100% {
    border-color: #facc15;
  }
}

/* Flash the gold border on/off. */
@keyframes win-border-flash {
  0%, 49% {
    border-color: #facc15;
  }
  50%, 100% {
    border-color: transparent;
  }
}

/* Overflow chips at the right edge */
.overflow-chip {
  font-size: 0.6875rem;
  font-family: var(--font-mono);
  font-weight: 600;
  white-space: nowrap;
}

.chip-empty {
  opacity: 0.85;
}

/* Hover tooltip */
.row-tooltip {
  display: none;
  position: absolute;
  left: 140px;
  top: 100%;
  z-index: 100;
  background: var(--surface-elevated);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  padding: 0.625rem 0.75rem;
  font-size: 0.75rem;
  white-space: nowrap;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
  pointer-events: none;
}

.chart-row:hover .row-tooltip,
.chart-row:focus-within .row-tooltip {
  display: block;
}

.row-tooltip strong {
  color: var(--text-primary);
  font-size: 0.8125rem;
}

.tooltip-winner {
  color: var(--accent);
  font-size: 0.75rem;
}

.tooltip-line {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.25rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.tooltip-line.tip-win {
  color: var(--text-primary);
}

.tooltip-type {
  width: 52px;
  flex-shrink: 0;
}

.tooltip-vantage {
  width: 100px;
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tooltip-ms {
  font-weight: 600;
  color: var(--text-primary);
  text-align: right;
  min-width: 64px;
}

.tooltip-winflag {
  color: #facc15;
  font-weight: 700;
}

/* Responsive */
@media (max-width: 768px) {
  .block-detail-view {
    padding: 1rem;
  }

  .metadata-grid {
    grid-template-columns: 1fr 1fr;
  }

  .axis-spacer,
  .row-pool-name {
    width: 100px;
  }

  .pool-display-name {
    font-size: 0.75rem;
  }

  .axis-overflow-spacer,
  .row-overflow {
    width: 54px;
  }

  .row-tooltip {
    left: 100px;
  }

  .vantage-legend,
  .marker-legend {
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .zoom-toggle {
    margin-left: 0;
  }
}

@media (max-width: 480px) {
  .metadata-grid {
    grid-template-columns: 1fr;
  }

  .axis-spacer,
  .row-pool-name {
    width: 80px;
  }

  .row-tooltip {
    left: 80px;
  }
}
</style>
