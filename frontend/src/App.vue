<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { RouterView } from 'vue-router'
import NotificationToast from '@/components/NotificationToast.vue'
import { useRaceStore } from '@/stores/raceStore'

const store = useRaceStore()

/** Expose vantageCount for template gating */
const vantageCount = computed(() => store.vantageCount)

/** Mobile nav toggle state */
const mobileNavOpen = ref(false)

function toggleMobileNav() {
  mobileNavOpen.value = !mobileNavOpen.value
}

function closeMobileNav() {
  mobileNavOpen.value = false
}

/** Current timestamp in seconds, updates every second */
const now = ref(Math.floor(Date.now() / 1000))
let tickInterval: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  tickInterval = setInterval(() => {
    now.value = Math.floor(Date.now() / 1000)
  }, 1000)
})

onUnmounted(() => {
  if (tickInterval !== null) {
    clearInterval(tickInterval)
  }
})

/** Human-readable "X minutes ago" string */
const lastBlockText = computed((): string | null => {
  if (store.lastBlockEpoch == null) return null
  const seconds = now.value - store.lastBlockEpoch
  if (seconds < 60) return '<1 min ago'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  const remainingMin = minutes % 60
  return `${hours}h ${remainingMin}m ago`
})

/** Latest block height from recent blocks */
const lastBlockHeight = computed((): number | null => {
  if (store.recentBlocks.length === 0) return null
  return store.recentBlocks[0].height
})
</script>

<template>
  <div id="stratumrace-app">
    <header class="app-header">
      <div class="header-top">
        <div class="header-left">
          <router-link to="/" class="app-title-link"><h1 class="app-title">StratumRace</h1></router-link>
          <span v-if="lastBlockText" class="last-block-indicator">
            <a
              v-if="lastBlockHeight"
              :href="`https://mempool.space/block/${lastBlockHeight}`"
              target="_blank"
              rel="noopener"
              class="block-link"
            >#{{ lastBlockHeight.toLocaleString() }}</a>
            <span class="block-time">{{ lastBlockText }}</span>
          </span>
        </div>
        <button
          class="hamburger-btn"
          aria-label="Toggle navigation menu"
          :aria-expanded="mobileNavOpen"
          @click="toggleMobileNav"
        >
          <span class="hamburger-icon" :class="{ open: mobileNavOpen }">
            <span></span>
            <span></span>
            <span></span>
          </span>
        </button>
      </div>
      <nav class="app-nav" :class="{ 'nav-open': mobileNavOpen }">
        <router-link to="/" @click="closeMobileNav">Leaderboard</router-link>
        <router-link to="/blocks" @click="closeMobileNav">Recent Blocks</router-link>
        <router-link to="/history" @click="closeMobileNav">History</router-link>
        <router-link v-if="vantageCount >= 2" to="/compare" @click="closeMobileNav">Compare</router-link>
        <router-link to="/about" @click="closeMobileNav">About</router-link>
        <a href="https://github.com/proofofmike/stratum-race" target="_blank" rel="noopener" class="github-link" aria-label="View source on GitHub" @click="closeMobileNav">
          <svg width="20" height="20" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
          </svg>
        </a>
      </nav>
    </header>
    <main class="app-main">
      <RouterView />
    </main>
    <NotificationToast />
  </div>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 2rem;
  border-bottom: 1px solid var(--border);
}

.header-top {
  display: contents;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.app-title-link {
  text-decoration: none;
  color: inherit;
}

.app-title {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--accent);
  margin: 0;
}

.last-block-indicator {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  padding: 0.25rem 0.5rem;
  background: var(--surface-elevated);
  border-radius: 0.25rem;
  border: 1px solid var(--border);
}

.block-link {
  color: var(--accent);
  font-weight: 600;
  text-decoration: none;
}

.block-link:hover {
  text-decoration: underline;
}

.block-time {
  color: var(--text-secondary);
}

.hamburger-btn {
  display: none;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  padding: 0;
  background: transparent;
  border: none;
  cursor: pointer;
  border-radius: 0.375rem;
}

.hamburger-btn:hover {
  background: var(--surface-elevated);
}

.hamburger-icon {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  width: 24px;
  height: 24px;
  gap: 5px;
}

.hamburger-icon span {
  display: block;
  width: 20px;
  height: 2px;
  background: var(--text-primary);
  border-radius: 1px;
  transition: transform 0.2s ease, opacity 0.2s ease;
}

.hamburger-icon.open span:nth-child(1) {
  transform: translateY(7px) rotate(45deg);
}

.hamburger-icon.open span:nth-child(2) {
  opacity: 0;
}

.hamburger-icon.open span:nth-child(3) {
  transform: translateY(-7px) rotate(-45deg);
}

.app-nav {
  display: flex;
  gap: 1.5rem;
}

.app-nav a {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: 0 0.25rem;
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.875rem;
  transition: color 0.2s;
}

.app-nav a:hover,
.app-nav a.router-link-active {
  color: var(--accent);
}

.github-link {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: 0 0.25rem;
  color: var(--text-secondary);
  text-decoration: none;
  transition: color 0.2s;
}

.github-link:hover {
  color: var(--accent);
}

.app-main {
  padding: 2rem;
  max-width: 1400px;
  margin: 0 auto;
}

/* Mobile responsive: hamburger menu */
@media (max-width: 768px) {
  .app-header {
    flex-direction: column;
    align-items: stretch;
    padding: 0;
  }

  .header-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
  }

  .header-left {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.25rem;
  }

  .hamburger-btn {
    display: flex;
  }

  .app-nav {
    display: none;
    flex-direction: column;
    gap: 0;
    border-top: 1px solid var(--border);
  }

  .app-nav.nav-open {
    display: flex;
  }

  .app-nav a {
    padding: 0.75rem 1rem;
    min-height: 44px;
    border-bottom: 1px solid var(--border);
    font-size: 1rem;
  }

  .app-nav a:last-child {
    border-bottom: none;
  }

  .app-main {
    padding: 1rem;
  }
}

/* Large displays */
@media (min-width: 1920px) {
  .app-main {
    max-width: 1800px;
  }
}
</style>
