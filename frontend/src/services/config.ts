/**
 * Frontend configuration — reads environment variables via Vite's import.meta.env.
 *
 * Environment variables (set in .env or .env.local):
 *   VITE_WEBSOCKET_URL - WebSocket endpoint override for local development (e.g., ws://localhost:8080/ws)
 *   VITE_LATEST_URL    - Polling fallback URL for latest.json (e.g., /api/latest.json)
 */
export const config = {
  wsUrl: import.meta.env.VITE_WEBSOCKET_URL || '',
  pollUrl: import.meta.env.VITE_LATEST_URL || '/data/latest.json',
}
