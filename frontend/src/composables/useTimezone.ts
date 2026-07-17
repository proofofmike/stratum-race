import { ref, computed, readonly } from 'vue'
import type { TimezonePreference } from '@/types'

const STORAGE_KEY = 'stratumrace-timezone'

/**
 * Reads the stored timezone preference from localStorage.
 * Defaults to 'local' (browser timezone) on first visit per Requirement 19.6.
 */
function loadPreference(): TimezonePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'utc' || stored === 'local') {
      return stored
    }
  } catch {
    // localStorage may be unavailable (private browsing, etc.)
  }
  return 'local'
}

/**
 * Persists the timezone preference to localStorage.
 */
function savePreference(pref: TimezonePreference): void {
  try {
    localStorage.setItem(STORAGE_KEY, pref)
  } catch {
    // Silently fail if localStorage is unavailable
  }
}

// Module-level singleton state so all components share the same preference
const preference = ref<TimezonePreference>(loadPreference())

/**
 * Returns the IANA timezone string based on the current preference.
 */
function getTimeZone(): string {
  if (preference.value === 'utc') {
    return 'UTC'
  }
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/**
 * Composable providing timezone formatting and toggle functionality.
 *
 * - Reads preference from localStorage on initialization
 * - Defaults to 'local' (browser timezone) on first visit (Requirement 19.6)
 * - Provides a toggle() method to switch between 'utc' and 'local'
 * - Persists preference to localStorage on every change
 * - Exposes reactive preference ref
 *
 * Validates: Requirements 19.3, 19.4, 19.6
 */
export function useTimezone() {
  /**
   * Toggle between 'utc' and 'local' timezone display modes.
   * Persists the new preference to localStorage immediately.
   */
  function toggle(): void {
    preference.value = preference.value === 'utc' ? 'local' : 'utc'
    savePreference(preference.value)
  }

  /**
   * Human-readable timezone name (e.g., "UTC" or "America/New_York").
   */
  const timezoneName = computed<string>(() => getTimeZone())

  /**
   * Formats a Unix epoch timestamp (seconds) to a full display string
   * based on the current timezone preference.
   */
  function formatTimestamp(epoch: number): string {
    const date = new Date(epoch * 1000)
    return new Intl.DateTimeFormat(undefined, {
      timeZone: getTimeZone(),
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(date)
  }

  /**
   * Formats an ISO UTC string to a display string based on the current
   * timezone preference.
   */
  function formatDatetime(utcString: string): string {
    const date = new Date(utcString)
    return new Intl.DateTimeFormat(undefined, {
      timeZone: getTimeZone(),
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(date)
  }

  /**
   * Formats a Unix epoch timestamp (seconds) to a short time string (HH:MM:SS).
   */
  function formatTime(epoch: number): string {
    const date = new Date(epoch * 1000)
    return new Intl.DateTimeFormat(undefined, {
      timeZone: getTimeZone(),
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(date)
  }

  /**
   * Formats a Unix epoch timestamp (seconds) to a date-only string (YYYY-MM-DD).
   */
  function formatDate(epoch: number): string {
    const date = new Date(epoch * 1000)
    if (preference.value === 'utc') {
      // Use explicit YYYY-MM-DD format in UTC mode for consistency
      const year = date.getUTCFullYear()
      const month = String(date.getUTCMonth() + 1).padStart(2, '0')
      const day = String(date.getUTCDate()).padStart(2, '0')
      return `${year}-${month}-${day}`
    }
    // In local mode, use locale date format
    return new Intl.DateTimeFormat(undefined, {
      timeZone: getTimeZone(),
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(date)
  }

  return {
    preference: readonly(preference),
    toggle,
    formatTimestamp,
    formatDatetime,
    formatTime,
    formatDate,
    timezoneName,
  }
}
