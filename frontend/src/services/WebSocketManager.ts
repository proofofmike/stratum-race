import { ref, readonly } from 'vue'
import type { ConnectionStatus, RaceResult } from '@/types'

// Reconnect backoff
const BASE_DELAY = 1000 // 1s
const MAX_DELAY = 30000 // 30s cap
const POLL_INTERVAL = 30000 // 30s polling fallback
const MAX_FAILURES_BEFORE_POLL = 3

/**
 * Fetch the WebSocket URL from the runtime config served by the backend.
 * The backend writes /api/config/runtime.json containing the WebSocket endpoint.
 * Falls back to VITE_WEBSOCKET_URL env var for local development.
 */
async function resolveWsUrl(): Promise<string> {
  // Local dev override
  if (import.meta.env.VITE_WEBSOCKET_URL) {
    return import.meta.env.VITE_WEBSOCKET_URL as string
  }
  try {
    const resp = await fetch('/api/config/runtime.json', { cache: 'no-cache' })
    if (resp.ok) {
      const cfg = await resp.json()
      if (cfg?.websocket_url) return cfg.websocket_url
    }
  } catch {
    // Fall through — WebSocket unavailable, polling will take over
  }
  return ''
}

type RaceResultCallback = (race: RaceResult) => void
type NewBlockCallback = () => void

// Internal state
const status = ref<ConnectionStatus>('disconnected')
const callbacks: RaceResultCallback[] = []
const blockCallbacks: NewBlockCallback[] = []

let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let currentDelay = BASE_DELAY
let consecutiveFailures = 0
let lastPollEpoch: number | null = null
let isPolling = false
let intentionalDisconnect = false
let resolvedWsUrl: string | null = null

/**
 * Emit a RaceResult to all registered callbacks.
 */
function emit(race: RaceResult): void {
  for (const cb of callbacks) {
    try {
      cb(race)
    } catch (err) {
      console.error('[WebSocketManager] Callback error:', err)
    }
  }
}

/**
 * Schedule a WebSocket reconnect with exponential backoff.
 */
function scheduleReconnect(): void {
  if (intentionalDisconnect) return

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWebSocket()
  }, currentDelay)

  // Exponential backoff: double the delay, cap at MAX_DELAY
  currentDelay = Math.min(currentDelay * 2, MAX_DELAY)
}

/**
 * Reset backoff delay to the base value (called after successful connection).
 */
function resetBackoff(): void {
  currentDelay = BASE_DELAY
  consecutiveFailures = 0
}

/**
 * Start polling latest.json as a fallback when WebSocket is unavailable.
 * Polls the lightweight signal file (~50 bytes) every 30s.
 * When a new block is detected, notifies via blockCallbacks
 * so the app can fetch full data from recent-blocks.json.
 */
function startPolling(): void {
  // Always reflect polling in the status, even when the poll loop is
  // already running — otherwise a connect() that set status='connecting'
  // and then fell back here would leave the indicator stuck at "connecting"
  status.value = 'polling'
  if (isPolling) return
  isPolling = true

  const poll = async () => {
    try {
      const response = await fetch('/api/latest.json', { cache: 'no-cache' })
      if (!response.ok) return

      const data = await response.json()
      // Compare by epoch, not height: block_height can legitimately be null
      // (mempool.space indexing lag), and a reorg replacement at the same
      // height still changes the epoch. Epoch is always present.
      const epoch = data?.epoch
      if (epoch != null && epoch !== lastPollEpoch) {
        lastPollEpoch = epoch
        // Notify listeners that a new block is available
        for (const cb of blockCallbacks) {
          try { cb() } catch (e) { console.error('[WebSocketManager] Block callback error:', e) }
        }
      }
    } catch (err) {
      console.warn('[WebSocketManager] Poll failed:', err)
    }
  }

  // Poll immediately, then on interval
  poll()
  pollTimer = setInterval(poll, POLL_INTERVAL)
}

/**
 * Stop the polling fallback.
 */
function stopPolling(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  isPolling = false
}

/**
 * Establish a WebSocket connection to the configured URL.
 */
function connectWebSocket(): void {
  const wsUrl = resolvedWsUrl ?? ''
  if (!wsUrl) {
    // No WebSocket URL available — go straight to polling.
    // resolvedWsUrl stays null (see connect()), so the next connect()
    // call retries URL resolution instead of polling forever.
    consecutiveFailures = MAX_FAILURES_BEFORE_POLL
    startPolling()
    return
  }

  // Drop any previous socket before creating a new one. An orphaned socket
  // that later opened would double-emit every race result.
  if (ws) {
    ws.onopen = null
    ws.onmessage = null
    ws.onclose = null
    ws.onerror = null
    try {
      ws.close()
    } catch {
      // Already closed
    }
    ws = null
  }

  status.value = 'connecting'

  try {
    ws = new WebSocket(wsUrl)
  } catch (err) {
    console.error('[WebSocketManager] Failed to create WebSocket:', err)
    handleConnectionFailure()
    return
  }

  ws.onopen = () => {
    status.value = 'connected'
    resetBackoff()
    // WebSocket is live — stop polling if it was active
    stopPolling()
  }

  ws.onmessage = (event: MessageEvent) => {
    try {
      const data = JSON.parse(event.data)
      // Race results always have arrivals_offset_ms and vantage fields
      if (data && data.vantage && data.arrivals_offset_ms) {
        emit(data as RaceResult)
      }
    } catch (err) {
      console.warn('[WebSocketManager] Invalid message received:', err)
    }
  }

  ws.onclose = () => {
    ws = null
    if (!intentionalDisconnect) {
      status.value = 'disconnected'
      handleConnectionFailure()
    }
  }

  ws.onerror = () => {
    // onerror is always followed by onclose, so we handle reconnect there
    ws?.close()
  }
}

/**
 * Handle a failed connection attempt — track failures and activate polling fallback.
 */
function handleConnectionFailure(): void {
  consecutiveFailures++

  // After 3+ consecutive failures, activate polling fallback
  if (consecutiveFailures >= MAX_FAILURES_BEFORE_POLL && !isPolling) {
    startPolling()
  }

  // Continue attempting WebSocket reconnect in background
  scheduleReconnect()
}

/**
 * Connect to the WebSocket API (public entry point).
 * Resolves the WebSocket URL from runtime config on first call.
 * Safe to call repeatedly — will reconnect if the existing connection is dead.
 */
async function connect(): Promise<void> {
  intentionalDisconnect = false

  // Signal connecting state immediately (before async URL resolution)
  status.value = 'connecting'

  // If WebSocket exists and is open, check if it's actually alive
  if (ws && ws.readyState === WebSocket.OPEN) {
    status.value = 'connected'
    return // Already connected and healthy
  }

  // A connection attempt is already in progress — let it finish rather than
  // racing it with a second socket (the loser would be orphaned and could
  // double-emit messages)
  if (ws && ws.readyState === WebSocket.CONNECTING) {
    return
  }

  // If WebSocket exists but is in CLOSING or CLOSED state (iOS kills silently),
  // clean it up so we can reconnect
  if (ws && (ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED)) {
    ws = null
    status.value = 'disconnected'
  }

  consecutiveFailures = 0
  currentDelay = BASE_DELAY

  // Resolve WebSocket URL on first successful resolution only. A failed
  // resolution (e.g., transient network error fetching runtime.json at page
  // load) must NOT be cached as '' — that would silently disable WebSocket
  // for the entire session. Leaving it null lets every subsequent connect()
  // (including visibilitychange reconnects) retry the resolution.
  if (resolvedWsUrl === null) {
    const url = await resolveWsUrl()
    resolvedWsUrl = url || null
  }

  connectWebSocket()
}

/**
 * Disconnect from the WebSocket API and stop all timers.
 */
function disconnect(): void {
  intentionalDisconnect = true

  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  stopPolling()

  if (ws) {
    ws.close()
    ws = null
  }

  status.value = 'disconnected'
}

/**
 * Register a callback to receive new RaceResult data (from WebSocket).
 */
function onRaceResult(callback: RaceResultCallback): void {
  callbacks.push(callback)
}

/**
 * Register a callback for when a new block is detected via polling.
 * The callback should fetch recent-blocks.json to get full multi-vantage data.
 */
function onNewBlock(callback: NewBlockCallback): void {
  blockCallbacks.push(callback)
}

/**
 * Vue composable for WebSocket connection management.
 *
 * Provides reactive connection status and methods to connect/disconnect
 * and subscribe to incoming race results.
 */
export function useWebSocket() {
  return {
    status: readonly(status),
    onRaceResult,
    onNewBlock,
    connect,
    disconnect,
  }
}

// Export internals for testing
export const _internals = {
  BASE_DELAY,
  MAX_DELAY,
  POLL_INTERVAL,
  MAX_FAILURES_BEFORE_POLL,
  get currentDelay() {
    return currentDelay
  },
  get consecutiveFailures() {
    return consecutiveFailures
  },
  get isPolling() {
    return isPolling
  },
  reset() {
    disconnect()
    callbacks.length = 0
    blockCallbacks.length = 0
    lastPollEpoch = null
    currentDelay = BASE_DELAY
    consecutiveFailures = 0
    resolvedWsUrl = null
  },
}
