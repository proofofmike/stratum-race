import { useRaceStore } from '@/stores/raceStore'

/**
 * Composable for displaying vantage point names using data-driven labels
 * from runtime.json (loaded into the store's vantageDisplay map).
 * Falls back to the raw vantage ID when no display data is available.
 */
export function useVantageNames() {
  const store = useRaceStore()

  /**
   * Convert a vantage ID to its display label.
   * Falls back to the raw ID if no mapping exists.
   */
  function formatVantage(id: string): string {
    return store.vantageDisplay[id]?.label ?? id
  }

  /**
   * Get the flag emoji for a vantage ID.
   * Returns empty string if no flag is mapped.
   */
  function getFlag(id: string): string {
    return store.vantageDisplay[id]?.flag ?? ''
  }

  return {
    formatVantage,
    getFlag,
  }
}
