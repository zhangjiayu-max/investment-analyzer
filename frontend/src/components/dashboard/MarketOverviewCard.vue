<script setup>
import { ref, onMounted, onActivated, onDeactivated, onUnmounted, computed } from 'vue'
import { getMarketOverview } from '../../api'

const emit = defineEmits(['navigate'])

const loading = ref(true)
const error = ref('')
const marketData = ref(null)
let pollTimer = null

const indices = computed(() => marketData.value?.data?.indices || [])
const sectorsTop = computed(() => marketData.value?.data?.sectors_top || [])
const sectorsBottom = computed(() => marketData.value?.data?.sectors_bottom || [])
const breadth = computed(() => marketData.value?.data?.breadth || {})

const upRatio = computed(() => {
  const b = breadth.value
  const total = (b.up || 0) + (b.down || 0)
  if (!total) return 50
  return Math.round((b.up / total) * 100)
})

async function loadData() {
  try {
    const { data } = await getMarketOverview()
    if (data.ok) {
      marketData.value = data
      error.value = ''
    } else {
      error.value = '行情数据格式异常'
    }
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '行情数据获取失败'
  } finally {
    loading.value = false
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(loadData, 5 * 60 * 1000) // 5 分钟
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function fmtPct(v) {
  if (v == null) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
}
function fmtVol(v) {
  if (v == null) return '--'
  return Number(v).toLocaleString()
}

onMounted(() => { loadData(); startPolling() })
onActivated(() => { loadData(); startPolling() })
onDeactivated(stopPolling)
onUnmounted(stopPolling)
</script>

<template>
  <div class="dash-card card editorial-card market-overview-card">
    <div class="card-header editorial-card-header">
      <div class="card-title-row">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z"/>
        </svg>
        <span class="title editorial-title">市场行情总览</span>
      </div>
      <div class="card-header-actions">
        <span class="card-data-time meta terminal-label">5 分钟缓存</span>
        <button class="btn-ai-action btn-card-refresh" :class="{ 'btn-loading': loading }" :disabled="loading" @click="loadData">
          <svg :class="['icon-spin', { 'spinning': loading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          <span>{{ loading ? '加载中...' : '刷新' }}</span>
        </button>
      </div>
    </div>

    <!-- 加载骨架 -->
    <div v-if="loading && !marketData" class="card-body">
      <div class="skeleton-row" v-for="i in 3" :key="i"></div>
    </div>

    <!-- 错误降级 -->
    <div v-else-if="error" class="card-body">
      <div class="market-error">
        <svg width="32" height="32" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="opacity:0.4">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"/>
        </svg>
        <p class="terminal-label">行情数据暂不可用</p>
        <p class="meta" style="font-size:12px;opacity:0.6">{{ error }}</p>
      </div>
    </div>

    <!-- 正常展示 -->
    <div v-else-if="marketData" class="card-body">
      <!-- 主要指数横向滚动 -->
      <div class="section-label terminal-label">主要指数</div>
      <div class="indices-scroll">
        <div
          v-for="idx in indices"
          :key="idx.name"
          class="index-chip"
          :class="{ 'is-up': idx.change_pct > 0, 'is-down': idx.change_pct < 0, 'is-flat': idx.change_pct === 0 }"
        >
          <div class="index-name">{{ idx.name }}</div>
          <div class="index-price font-jet">{{ Number(idx.price).toFixed(2) }}</div>
          <div class="index-change font-jet">
            <span class="change-arrow">{{ idx.change_pct > 0 ? '▲' : idx.change_pct < 0 ? '▼' : '—' }}</span>
            {{ fmtPct(idx.change_pct) }}
          </div>
        </div>
      </div>

      <!-- 涨跌家数 -->
      <template v-if="breadth.up || breadth.down">
        <div class="section-label terminal-label" style="margin-top:1rem">涨跌家数</div>
        <div class="breadth-bar">
          <div class="breadth-up" :style="{ width: upRatio + '%' }">
            <span class="font-jet">{{ fmtVol(breadth.up) }}</span>
          </div>
          <div class="breadth-down" :style="{ width: (100 - upRatio) + '%' }">
            <span class="font-jet">{{ fmtVol(breadth.down) }}</span>
          </div>
        </div>
        <div class="breadth-stats">
          <span class="stat-item">涨停 <b class="font-jet" style="color:var(--color-up)">{{ breadth.limit_up || 0 }}</b></span>
          <span class="stat-item">跌停 <b class="font-jet" style="color:var(--color-down)">{{ breadth.limit_down || 0 }}</b></span>
          <span class="stat-item">总成交 <b class="font-jet">{{ fmtVol(breadth.total_volume_yi) }}</b> 亿</span>
        </div>
      </template>

      <!-- 领涨/领跌板块 -->
      <template v-if="sectorsTop.length || sectorsBottom.length">
        <div class="section-label terminal-label" style="margin-top:1rem">板块涨跌</div>
        <div class="sectors-grid">
          <div class="sector-col">
            <div class="sector-col-header" style="color:var(--color-up)">领涨板块</div>
            <div v-for="s in sectorsTop.slice(0, 3)" :key="'top-' + s.name" class="sector-row">
              <div class="sector-info">
                <span class="sector-name">{{ s.name }}</span>
                <span class="sector-lead meta">{{ s.lead_stock }} <span class="font-jet" :class="{'text-up': s.lead_change > 0}">{{ fmtPct(s.lead_change) }}</span></span>
              </div>
              <span class="sector-pct font-jet" style="color:var(--color-up)">{{ fmtPct(s.change_pct) }}</span>
            </div>
          </div>
          <div class="sector-col">
            <div class="sector-col-header" style="color:var(--color-down)">领跌板块</div>
            <div v-for="s in sectorsBottom.slice(0, 3)" :key="'bot-' + s.name" class="sector-row">
              <div class="sector-info">
                <span class="sector-name">{{ s.name }}</span>
                <span class="sector-lead meta">{{ s.lead_stock }} <span class="font-jet" :class="{'text-down': s.lead_change < 0}">{{ fmtPct(s.lead_change) }}</span></span>
              </div>
              <span class="sector-pct font-jet" style="color:var(--color-down)">{{ fmtPct(s.change_pct) }}</span>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.market-overview-card .card-body { padding: 0 1rem 1rem; }

.section-label {
  font-size: 0.7rem;
  letter-spacing: 0.08em;
  opacity: 0.6;
  margin-bottom: 0.5rem;
  text-transform: uppercase;
}

/* 指数横向滚动 */
.indices-scroll {
  display: flex;
  gap: 0.5rem;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  -webkit-overflow-scrolling: touch;
  padding-bottom: 0.25rem;
}
.indices-scroll::-webkit-scrollbar { height: 3px; }
.indices-scroll::-webkit-scrollbar-thumb { background: var(--color-border); border-radius: 2px; }

.index-chip {
  flex: 0 0 auto;
  min-width: 100px;
  padding: 0.5rem 0.75rem;
  border-left: 2px solid var(--color-border);
  scroll-snap-align: start;
  transition: border-color 0.2s;
}
.index-chip.is-up { border-left-color: var(--color-up); }
.index-chip.is-down { border-left-color: var(--color-down); }
.index-chip.is-flat { border-left-color: var(--color-text-muted); }

.index-name { font-size: 0.75rem; opacity: 0.7; margin-bottom: 0.15rem; }
.index-price { font-size: 1rem; font-weight: 600; line-height: 1.2; }
.index-change { font-size: 0.8rem; margin-top: 0.15rem; }
.index-chip.is-up .index-change { color: var(--color-up); }
.index-chip.is-down .index-change { color: var(--color-down); }
.change-arrow { font-size: 0.65rem; margin-right: 1px; }

/* 涨跌家数条 */
.breadth-bar {
  display: flex;
  height: 22px;
  border-radius: 4px;
  overflow: hidden;
  font-size: 0.75rem;
}
.breadth-up {
  background: var(--color-up);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: width 0.5s ease;
  min-width: 30px;
}
.breadth-down {
  background: var(--color-down);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: width 0.5s ease;
  min-width: 30px;
}
.breadth-stats {
  display: flex;
  gap: 1rem;
  margin-top: 0.4rem;
  font-size: 0.75rem;
  opacity: 0.75;
}
.stat-item b { font-weight: 600; }

/* 板块网格 */
.sectors-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}
.sector-col-header {
  font-size: 0.72rem;
  font-weight: 600;
  margin-bottom: 0.35rem;
  padding-bottom: 0.25rem;
  border-bottom: 1px solid var(--color-border);
}
.sector-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.25rem 0;
  border-bottom: 1px solid var(--color-border-soft);
}
.sector-row:last-child { border-bottom: none; }
.sector-info { display: flex; flex-direction: column; gap: 1px; }
.sector-name { font-size: 0.78rem; }
.sector-lead { font-size: 0.65rem; opacity: 0.6; }
.sector-pct { font-size: 0.85rem; font-weight: 600; }
.text-up { color: var(--color-up); }
.text-down { color: var(--color-down); }

/* 骨架屏 */
.skeleton-row {
  height: 40px;
  border-radius: 4px;
  background: var(--color-skeleton);
  margin-bottom: 0.5rem;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: 0.6; } 50% { opacity: 0.3; } }

/* 错误状态 */
.market-error {
  text-align: center;
  padding: 1.5rem 0.5rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.4rem;
}

/* 移动端 */
@media (max-width: 768px) {
  .sectors-grid { grid-template-columns: 1fr; gap: 0.5rem; }
  .breadth-stats { flex-wrap: wrap; gap: 0.5rem; }
  .index-chip { min-width: 90px; }
}
</style>
