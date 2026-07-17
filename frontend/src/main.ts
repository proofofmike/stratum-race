import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './assets/main.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'leaderboard',
      component: () => import('./views/LeaderboardView.vue'),
    },
    {
      path: '/blocks',
      name: 'recent-blocks',
      component: () => import('./views/RecentBlocksView.vue'),
    },
    {
      path: '/block/:height',
      name: 'block-detail',
      component: () => import('./views/BlockDetailView.vue'),
    },
    {
      path: '/history',
      name: 'history',
      component: () => import('./views/HistoricalView.vue'),
    },
    {
      path: '/compare',
      name: 'compare',
      component: () => import('./views/CrossVantageView.vue'),
    },
    {
      path: '/about',
      name: 'about',
      component: () => import('./views/AboutView.vue'),
    },
  ],
})

const pinia = createPinia()
const app = createApp(App)
app.use(pinia)
app.use(router)
app.mount('#app')

// Initialize data loading after app is mounted
import { useRaceStore } from './stores/raceStore'
import { useWebSocket } from './services/WebSocketManager'

const store = useRaceStore()
const { connect, onRaceResult, onNewBlock } = useWebSocket()

// Load runtime config first (populates vantageDisplay), then connect WebSocket
store.loadRuntimeConfig().then(() => {
  connect()
})

// Load initial data
store.loadRecentBlocks()
store.loadPoolConfig()
store.loadVantageHealth()

// Load the default time frame (7d) aggregate for the full leaderboard
store.loadTimeFrame('7d')
onRaceResult((race) => {
  store.addRaceResult(race)
})

// When polling detects a new block, refresh recent blocks (gets all vantage data)
onNewBlock(() => {
  store.loadRecentBlocks()
})

// Handle iOS Safari / mobile tab resume:
// When the page was backgrounded, WebSocket connections die silently.
// On visibility restore, reconnect WebSocket and refresh all data.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    // Reconnect WebSocket (will no-op if already connected)
    connect()
    // Refresh recent blocks in case we missed updates while backgrounded
    store.loadRecentBlocks()
    // Reload the aggregate for the user's CURRENT time frame selection —
    // never reset the displayed data to a fixed period
    store.reloadActiveTimeFrame()
  }
})
