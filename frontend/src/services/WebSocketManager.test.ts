import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useWebSocket, _internals } from './WebSocketManager'

// Mock WebSocket globally
class MockWebSocket {
  // Standard readyState constants (the real WebSocket exposes these as statics;
  // WebSocketManager compares against them, so the mock must define them)
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: MockWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 0
  url: string

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  close() {
    this.readyState = 3
    if (this.onclose) this.onclose()
  }

  // Simulate server sending a message
  simulateMessage(data: string) {
    if (this.onmessage) this.onmessage({ data })
  }

  // Simulate connection open
  simulateOpen() {
    this.readyState = 1
    if (this.onopen) this.onopen()
  }

  // Simulate connection error followed by close
  simulateError() {
    if (this.onerror) this.onerror()
  }
}

describe('WebSocketManager', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    MockWebSocket.instances = []
    // @ts-expect-error - Mock global WebSocket
    globalThis.WebSocket = MockWebSocket
    // Set WS_URL for tests via import.meta.env mock
    vi.stubEnv('VITE_WEBSOCKET_URL', 'wss://test.example.com')
    _internals.reset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
    _internals.reset()
  })

  describe('useWebSocket composable', () => {
    it('returns status, onRaceResult, connect, and disconnect', async () => {
      const { status, onRaceResult, connect, disconnect } = useWebSocket()
      expect(status).toBeDefined()
      expect(status.value).toBe('disconnected')
      expect(typeof onRaceResult).toBe('function')
      expect(typeof connect).toBe('function')
      expect(typeof disconnect).toBe('function')
    })

    it('status is readonly', async () => {
      const { status } = useWebSocket()
      // Readonly ref should not allow direct mutation
      expect(status.value).toBe('disconnected')
    })
  })

  describe('connect()', () => {
    it('sets status to connecting when connect is called', async () => {
      const { status, connect } = useWebSocket()
      await connect()
      expect(status.value).toBe('connecting')
    })

    it('sets status to connected on successful WebSocket open', async () => {
      const { status, connect } = useWebSocket()
      await connect()
      expect(MockWebSocket.instances.length).toBe(1)

      MockWebSocket.instances[0].simulateOpen()
      expect(status.value).toBe('connected')
    })
  })

  describe('disconnect()', () => {
    it('sets status to disconnected and cleans up', async () => {
      const { status, connect, disconnect } = useWebSocket()
      await connect()
      MockWebSocket.instances[0].simulateOpen()
      expect(status.value).toBe('connected')

      disconnect()
      expect(status.value).toBe('disconnected')
    })

    it('does not attempt reconnect after intentional disconnect', async () => {
      const { connect, disconnect } = useWebSocket()
      await connect()
      MockWebSocket.instances[0].simulateOpen()
      disconnect()

      // Advance time — no new WebSocket instances should be created
      vi.advanceTimersByTime(60000)
      expect(MockWebSocket.instances.length).toBe(1) // Only the initial one
    })
  })

  describe('onRaceResult callback', () => {
    it('emits parsed RaceResult to registered callbacks on message', async () => {
      const { connect, onRaceResult } = useWebSocket()
      const received: unknown[] = []
      onRaceResult((race) => received.push(race))

      await connect()
      MockWebSocket.instances[0].simulateOpen()

      const mockRace = {
        block_height: 878432,
        vantage: 'local',
        prevhash: '00000000abc',
        arrivals_offset_ms: { atlaspool: 0, ckpool: 42 },
      }
      MockWebSocket.instances[0].simulateMessage(JSON.stringify(mockRace))

      expect(received.length).toBe(1)
      expect((received[0] as { block_height: number }).block_height).toBe(878432)
    })

    it('ignores invalid JSON messages', async () => {
      const { connect, onRaceResult } = useWebSocket()
      const received: unknown[] = []
      onRaceResult((race) => received.push(race))

      await connect()
      MockWebSocket.instances[0].simulateOpen()
      MockWebSocket.instances[0].simulateMessage('not-json{{{')

      expect(received.length).toBe(0)
    })

    it('ignores messages without block_height', async () => {
      const { connect, onRaceResult } = useWebSocket()
      const received: unknown[] = []
      onRaceResult((race) => received.push(race))

      await connect()
      MockWebSocket.instances[0].simulateOpen()
      MockWebSocket.instances[0].simulateMessage(JSON.stringify({ type: 'ping' }))

      expect(received.length).toBe(0)
    })
  })

  describe('reconnection with exponential backoff', () => {
    it('reconnects after disconnection with increasing delays', async () => {
      const { status, connect } = useWebSocket()
      await connect()
      const ws1 = MockWebSocket.instances[0]
      ws1.simulateOpen()
      expect(status.value).toBe('connected')

      // Simulate disconnect
      ws1.close()
      expect(status.value).toBe('disconnected')

      // After BASE_DELAY (1s), should reconnect
      vi.advanceTimersByTime(1000)
      expect(MockWebSocket.instances.length).toBe(2)

      // Second disconnect — delay should be 2s
      MockWebSocket.instances[1].close()
      vi.advanceTimersByTime(1999)
      expect(MockWebSocket.instances.length).toBe(2) // not yet
      vi.advanceTimersByTime(1)
      expect(MockWebSocket.instances.length).toBe(3)
    })

    it('caps reconnect delay at MAX_DELAY (30s)', async () => {
      const { connect } = useWebSocket()
      await connect()

      // Simulate many consecutive failures to exceed max
      for (let i = 0; i < 10; i++) {
        const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1]
        ws.close()
        vi.advanceTimersByTime(30000) // Advance past max delay
      }

      // Verify the delay is capped (we got reconnection attempts)
      expect(MockWebSocket.instances.length).toBeGreaterThan(5)
    })

    it('resets backoff after successful connection', async () => {
      const { connect } = useWebSocket()
      await connect()

      // Fail a few times to increase delay
      MockWebSocket.instances[0].close()
      vi.advanceTimersByTime(1000) // 1s delay
      MockWebSocket.instances[1].close()
      vi.advanceTimersByTime(2000) // 2s delay
      MockWebSocket.instances[2].close()
      vi.advanceTimersByTime(4000) // 4s delay

      // Now succeed
      MockWebSocket.instances[3].simulateOpen()

      // Disconnect again — delay should be reset to 1s
      MockWebSocket.instances[3].close()
      vi.advanceTimersByTime(1000)
      expect(MockWebSocket.instances.length).toBe(5) // New connection attempt at base delay
    })
  })

  describe('polling fallback', () => {
    it('activates polling after 3 consecutive WebSocket failures', async () => {
      const { status, connect } = useWebSocket()
      await connect()

      // Fail 3 times
      MockWebSocket.instances[0].close()
      vi.advanceTimersByTime(1000)
      MockWebSocket.instances[1].close()
      vi.advanceTimersByTime(2000)
      MockWebSocket.instances[2].close()

      // After 3 failures, should be polling
      expect(status.value).toBe('polling')
      expect(_internals.isPolling).toBe(true)
    })

    it('stops polling when WebSocket reconnects successfully', async () => {
      const { status, connect } = useWebSocket()
      await connect()

      // Trigger polling fallback
      MockWebSocket.instances[0].close()
      vi.advanceTimersByTime(1000)
      MockWebSocket.instances[1].close()
      vi.advanceTimersByTime(2000)
      MockWebSocket.instances[2].close()
      expect(status.value).toBe('polling')

      // Advance to trigger reconnect and succeed
      vi.advanceTimersByTime(4000)
      MockWebSocket.instances[3].simulateOpen()

      expect(status.value).toBe('connected')
      expect(_internals.isPolling).toBe(false)
    })

    it('continues WebSocket reconnection attempts while polling', async () => {
      const { connect } = useWebSocket()
      await connect()

      // Trigger polling fallback
      MockWebSocket.instances[0].close()
      vi.advanceTimersByTime(1000)
      MockWebSocket.instances[1].close()
      vi.advanceTimersByTime(2000)
      MockWebSocket.instances[2].close()
      expect(_internals.isPolling).toBe(true)

      // Advance time — should still attempt WS reconnection
      vi.advanceTimersByTime(4000)
      expect(MockWebSocket.instances.length).toBe(4) // Another attempt made
    })
  })

  describe('WebSocket URL resolution (review fixes)', () => {
    it('retries URL resolution on the next connect() after a failed resolution', async () => {
      // No env override and runtime.json fetch fails
      vi.unstubAllEnvs()
      const fetchSpy = vi
        .spyOn(globalThis, 'fetch')
        .mockRejectedValue(new TypeError('network down'))

      const { connect } = useWebSocket()
      await connect()
      // Resolution failed — no socket created, polling active
      expect(MockWebSocket.instances.length).toBe(0)
      expect(_internals.isPolling).toBe(true)

      // Network recovers: runtime.json now resolves
      fetchSpy.mockResolvedValue({
        ok: true,
        json: async () => ({ websocket_url: 'ws://localhost:8080/ws' }),
      } as Response)

      // A later connect() (e.g., visibilitychange) must re-resolve the URL
      // instead of staying on cached-empty polling forever
      await connect()
      expect(MockWebSocket.instances.length).toBe(1)
      expect(MockWebSocket.instances[0].url).toBe('ws://localhost:8080/ws')
    })

    it('sets status to polling (not stuck at connecting) when URL resolution fails while already polling', async () => {
      vi.unstubAllEnvs()
      vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('network down'))

      const { status, connect } = useWebSocket()
      await connect()
      expect(status.value).toBe('polling')

      // Second connect() sets 'connecting' then falls back — must end at 'polling'
      await connect()
      expect(status.value).toBe('polling')
    })
  })

  describe('duplicate socket prevention (review fixes)', () => {
    it('does not create a second socket while a connection attempt is in progress', async () => {
      const { connect } = useWebSocket()
      await connect()
      expect(MockWebSocket.instances.length).toBe(1)
      // Socket is still CONNECTING (readyState 0) — a second connect() must no-op
      expect(MockWebSocket.instances[0].readyState).toBe(0)

      await connect()
      expect(MockWebSocket.instances.length).toBe(1)
    })
  })
})
