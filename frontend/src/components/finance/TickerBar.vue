<script setup>
/**
 * TickerBar — 行情速览顶栏
 *
 * 40px 高 glass 顶栏：左侧滚动行情 ticker + 中间搜索框 + 右侧画像入口/主题切换。
 * 数据源：getDashboard() 的 indices（市场指数），降级到理财语录轮播。
 *
 * 用于 App.vue 桌面布局主区顶部。
 */
import { ref, onMounted, onUnmounted } from 'vue'
import Icon from '../ui/Icon.vue'
import Sparkline from './Sparkline.vue'
import AlertBell from '../AlertBell.vue'
import { getDashboard, getFinanceQuoteBar, getInstitutionalFlowSummary } from '../../api'
import { isDark, toggleDark } from '../../composables/useTheme'

const emit = defineEmits(['open-kyc', 'search', 'navigate'])

const searchQuery = ref('')

function handleSearch() {
  const q = searchQuery.value.trim()
  if (q) emit('search', q)
}

const indices = ref([])
const quoteText = ref('')
const loading = ref(true)
// 机构动向（融资余额近5日净变化）
const flow = ref(null)
let timer = null

async function loadMarketData() {
  try {
    const { data } = await getDashboard()
    const idx = data?.market_overview?.indices || data?.indices || []
    if (idx.length) {
      // 取前 6 个主要指数
      indices.value = idx.slice(0, 6).map(i => ({
        name: i.name,
        price: i.price,
        changePct: i.change_pct,
        // 简单模拟 sparkline 数据（真实历史需后端补，这里用 change_pct 生成趋势）
        spark: [0, i.change_pct * 0.3, i.change_pct * 0.6, i.change_pct * 0.5, i.change_pct],
      }))
      loading.value = false
    } else {
      // 降级：语录轮播
      await loadQuote()
    }
  } catch { /* 降级 */ await loadQuote() }
  // 并行加载机构动向（独立于指数降级路径，失败不影响主流程）
  loadFlow()
}

async function loadQuote() {
  try {
    const { data } = await getFinanceQuoteBar()
    quoteText.value = data.quote || '市场永远在波动，情绪稳定才是最大的优势。'
  } catch {
    quoteText.value = '市场永远在波动，情绪稳定才是最大的优势。'
  }
  loading.value = false
}

async function loadFlow() {
  try {
    const { data } = await getInstitutionalFlowSummary()
    if (data && typeof data.recent_5d_change_yi === 'number') {
      flow.value = {
        change: data.recent_5d_change_yi,
        trend: data.trend || 'neutral',
        strength: data.strength || 'weak',
      }
    }
  } catch { /* 静默降级，不显示条目 */ }
}

// 机构动向条目显示文本
function flowLabel(s) {
  if (!s) return ''
  const sign = s.change > 0 ? '+' : ''
  return `${sign}${s.change.toFixed(2)}亿`
}

onMounted(() => {
  loadMarketData()
  timer = setInterval(loadMarketData, 60000) // 每分钟刷新
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="ticker-bar">
    <!-- 左侧：行情 ticker -->
    <div class="ticker-left">
      <template v-if="indices.length">
        <div v-for="idx in indices" :key="idx.name" class="ticker-item"
          :class="idx.changePct > 0 ? 'up' : idx.changePct < 0 ? 'down' : ''">
          <span class="ticker-name font-jet">{{ idx.name }}</span>
          <span class="ticker-price font-jet">{{ idx.price?.toFixed(2) ?? '--' }}</span>
          <span class="ticker-change font-jet">
            {{ idx.changePct > 0 ? '+' : '' }}{{ idx.changePct?.toFixed(2) }}%
          </span>
          <Sparkline v-if="idx.spark" :data="idx.spark" :width="40" :height="16" :fill="false" class="ticker-spark" />
        </div>
      </template>
      <!-- 机构动向：融资余额近5日净变化（辅助信号） -->
      <div v-if="flow" class="ticker-item flow-item"
        :class="flow.trend === 'inflow' ? 'up' : flow.trend === 'outflow' ? 'down' : ''">
        <Icon name="landmark" size="12" class="flow-icon" />
        <span class="term-with-tip flow-term">
          <span class="ticker-name font-jet">融资5日</span>
          <Icon name="info" size="11" class="flow-info-icon" />
          <span class="term-tip flow-tip">
            <b>融资余额近5日净变化</b><br/>
            <span class="flow-tip-source">数据来源：上交所融资融券余额（akshare）</span><br/>
            近5个交易日融资余额净变化，正值=杠杆资金加仓，负值=减仓。<br/>
            <span class="flow-tip-strength">强度判定：|z-score|≥1.5 强信号，0.5-1.5 中等，&lt;0.5 弱。</span><br/>
            <span class="flow-tip-note">辅助信号，不作为独立决策依据，与估值/持仓信号共振时增强置信度。</span>
          </span>
        </span>
        <span class="ticker-change font-jet" :class="flow.strength === 'strong' ? 'strong' : ''">
          {{ flowLabel(flow) }}
        </span>
      </div>
      <div v-else-if="!loading && !indices.length" class="ticker-quote">
        <Icon name="lightbulb" size="13" class="ticker-quote-icon" />
        <span class="ticker-quote-text">{{ quoteText }}</span>
      </div>
      <div v-else class="ticker-loading">
        <Icon name="spinner" size="13" class="spinning" />
      </div>
    </div>

    <!-- 中间：搜索框 -->
    <div class="ticker-center">
      <div class="ticker-search">
        <Icon name="search" size="14" class="search-icon" />
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索指数 / 基金 / 文章…"
          class="search-input"
          @keydown.enter="handleSearch"
        />
      </div>
    </div>

    <!-- 右侧：画像入口 + 主动提醒 + 主题切换 -->
    <div class="ticker-right">
      <AlertBell @navigate="emit('navigate', $event)" />
      <button class="ticker-action" @click="emit('open-kyc')" title="我的投资画像">
        <Icon name="circle-user" size="16" />
        <span class="action-label">画像</span>
      </button>
      <button class="ticker-action" @click="toggleDark()" :title="isDark ? '亮色模式' : '暗色模式'">
        <Icon :name="isDark ? 'sun' : 'moon'" size="16" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.ticker-bar {
  display: flex;
  align-items: center;
  height: 40px;
  padding: 0 0.75rem;
  gap: 1rem;
  background: var(--glass-bg);
  border-bottom: 1px solid var(--color-border-light);
  backdrop-filter: blur(var(--glass-blur)) saturate(180%);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(180%);
  flex-shrink: 0;
  z-index: 30;
}

/* 左侧行情 */
.ticker-left {
  display: flex;
  align-items: center;
  gap: 1.2rem;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}
.ticker-item {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  white-space: nowrap;
  font-size: 0.72rem;
}
.ticker-item.up { color: var(--color-profit); }
.ticker-item.down { color: var(--color-loss); }
.ticker-name { color: var(--color-text-secondary); font-weight: 500; }
.ticker-price { font-weight: 600; }
.ticker-change { font-weight: 600; min-width: 48px; }
.ticker-spark { opacity: 0.7; }
/* 机构动向条目（融资余额）— 与左侧指数 ticker 视觉分隔 */
.flow-item {
  padding-left: 0.8rem;
  margin-left: 0.2rem;
  border-left: 1px solid var(--color-border-light);
}
.flow-icon { opacity: 0.65; flex-shrink: 0; }
.ticker-change.strong { font-weight: 700; }
/* 融资5日 term-tip 覆写：TickerBar 在顶部，tooltip 向下弹出 */
.flow-term { display: inline-flex; align-items: center; gap: 0.2rem; }
.flow-info-icon { opacity: 0.5; }
.flow-tip {
  bottom: auto;
  top: calc(100% + 8px);
  width: 280px;
  text-align: left;
  font-weight: 400;
}
.flow-tip::after {
  top: auto;
  bottom: 100%;
  border-top-color: transparent;
  border-bottom-color: var(--color-bg-card);
}
.flow-tip b { color: var(--color-text-primary); font-weight: 600; }
.flow-tip-source { color: var(--color-text-muted); font-size: 0.7rem; }
.flow-tip-strength { color: var(--color-text-secondary); }
.flow-tip-note { color: var(--color-warning); }
.ticker-quote {
  display: flex; align-items: center; gap: 0.4rem;
  color: var(--color-text-muted); font-size: 0.72rem;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ticker-quote-icon { color: var(--color-warning); flex-shrink: 0; }
.ticker-quote-text { overflow: hidden; text-overflow: ellipsis; }
.ticker-loading { color: var(--color-text-muted); }

/* 中间搜索 */
.ticker-center { flex-shrink: 0; }
.ticker-search {
  display: flex; align-items: center; gap: 0.4rem;
  width: 220px; padding: 0.3rem 0.6rem;
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  transition: border-color var(--transition-fast);
}
.ticker-search:focus-within { border-color: var(--color-primary-border); }
.search-icon { color: var(--color-text-muted); flex-shrink: 0; }
.search-input {
  border: none; background: none; outline: none;
  font-size: 0.75rem; color: var(--color-text-primary);
  width: 100%; font-family: inherit;
}
.search-input::placeholder { color: var(--color-text-muted); }

/* 右侧操作 */
.ticker-right { display: flex; align-items: center; gap: 0.3rem; flex-shrink: 0; }
.ticker-action {
  display: flex; align-items: center; gap: 0.25rem;
  padding: 0.3rem 0.5rem; border-radius: var(--radius-sm);
  color: var(--color-text-secondary); transition: all var(--transition-fast);
}
.ticker-action:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.action-label { font-size: 0.72rem; font-weight: 500; }

@media (max-width: 1024px) {
  .ticker-center { display: none; }
  .ticker-left { gap: 0.8rem; }
  .ticker-item { gap: 0.2rem; }
}
</style>
