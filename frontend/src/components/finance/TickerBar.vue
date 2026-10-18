<script setup>
/** 顶部唯一语录栏：本地轮换 + 日频沪市融资摘要 + 全局搜索。 */
import { computed, ref, onMounted, onUnmounted } from 'vue'
import Icon from '../ui/Icon.vue'
import TermTooltip from '../ui/TermTooltip.vue'
import AlertBell from '../task/AlertBell.vue'
import { getFinanceQuoteBar, getInstitutionalFlowSummary } from '../../api'
import { isDark, toggleDark } from '../../composables/useTheme'
import { investmentQuoteFallbacks } from './investmentQuoteFallbacks'

const emit = defineEmits(['open-kyc', 'search', 'navigate'])
const searchQuery = ref('')
function handleSearch(event) {
  if (event?.isComposing || event?.keyCode === 229) return
  const q = searchQuery.value.trim()
  if (q) emit('search', q)
}

const quoteText = ref(investmentQuoteFallbacks[0])
let quotePool = [...investmentQuoteFallbacks]
let quoteQueue = []
let quoteTimer = null
let flowTimer = null
let disposed = false

function shuffledQuotes() {
  const items = [...quotePool]
  for (let i = items.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[items[i], items[j]] = [items[j], items[i]]
  }
  return items
}
function nextQuote() {
  if (!quoteQueue.length) {
    quoteQueue = shuffledQuotes()
    // 跨轮也不连续重复同一句。
    const last = quoteQueue.length - 1
    if (last > 0 && quoteQueue[last] === quoteText.value) {
      ;[quoteQueue[0], quoteQueue[last]] = [quoteQueue[last], quoteQueue[0]]
    }
  }
  quoteText.value = quoteQueue.pop()
}
async function loadQuotes() {
  try {
    const { data } = await getFinanceQuoteBar()
    if (disposed) return
    const items = Array.isArray(data?.quotes) ? data.quotes : []
    const valid = [...new Set(items.filter(q => typeof q === 'string' && q.trim()))]
    if (valid.length > 1) quotePool = valid
    else if (data?.quote) quotePool = [...new Set([data.quote, ...investmentQuoteFallbacks])]
    if (typeof data?.quote === 'string' && quotePool.includes(data.quote)) quoteText.value = data.quote
  } catch { /* 离线也使用备用文案继续轮换。 */ }
  if (!disposed) quoteQueue = shuffledQuotes().filter(q => q !== quoteText.value)
}

const flow = ref(null)
const flowLoading = ref(true)
const flowFailed = ref(false)
let flowPending = false
async function loadFlow() {
  if (flowPending || disposed) return
  flowPending = true
  try {
    const { data } = await getInstitutionalFlowSummary()
    if (disposed) return
    flowFailed.value = false
    flow.value = data?.data_status === 'ok' && Number.isFinite(data.recent_5d_change_yi) && data.latest_date
      ? { change: data.recent_5d_change_yi, date: data.latest_date, trend: data.trend, strength: data.strength }
      : null
  } catch {
    if (!disposed) flowFailed.value = true
  } finally {
    flowPending = false
    if (!disposed) flowLoading.value = false
  }
}
const flowStatus = computed(() => flowFailed.value ? '更新失败' : flowLoading.value ? '加载中' : '暂无数据')
const flowDescription = computed(() => [
  '融资数据 · 辅助参考',
  flow.value ? `沪市近5个交易日融资余额变化：${flowLabel(flow.value.change)}元。\n截至 ${flow.value.date}，来源：上交所。` : `${flowStatus.value}，不代表融资余额变化为零。`,
  '融资余额是投资者向券商借钱买股票后，尚未偿还的借款余额。',
  '正值表示借款余额增加，负值表示减少，不等于市场资金净流入或净流出。',
  '只覆盖沪市，按交易日发布；周末、节假日通常不变，每分钟检查更新。',
  '仅辅助观察杠杆资金情绪，不作为买卖依据。',
  flowFailed.value ? flow.value ? '本次更新失败，显示的数值为上次成功获取的数据。' : '本次更新失败，暂时无法获取融资数据。' : '',
].filter(Boolean).join('\n'))
function flowLabel(value) {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}亿`
}

onMounted(() => {
  loadQuotes()
  loadFlow()
  quoteTimer = setInterval(nextQuote, 10000)
  flowTimer = setInterval(loadFlow, 60000)
})
onUnmounted(() => {
  disposed = true
  clearInterval(quoteTimer)
  clearInterval(flowTimer)
})
</script>

<template>
  <div class="ticker-bar">
    <div class="ticker-left">
      <button type="button" class="ticker-quote" :title="`${quoteText}（每10秒自动更换，点击换一条）`" aria-label="换一条投资语录" @click="nextQuote">
        <Icon name="lightbulb" size="13" class="ticker-quote-icon" />
        <Transition name="quote" mode="out-in">
          <span :key="quoteText" class="ticker-quote-text">{{ quoteText }}</span>
        </Transition>
      </button>
      <TermTooltip label="融资数据" :content="flowDescription" icon-only class="flow-help">
        <template #trigger><Icon name="info" size="16" /></template>
      </TermTooltip>
    </div>

    <div class="ticker-center">
      <div class="ticker-search" role="search">
        <input v-model="searchQuery" type="search" placeholder="搜索指数 / 持仓基金 / 文章…" aria-label="搜索指数、持仓基金或文章" class="search-input" @keydown.enter="handleSearch" />
        <button type="button" class="search-submit" aria-label="搜索" title="搜索（Enter）" :disabled="!searchQuery.trim()" @click="handleSearch">
          <Icon name="search" size="14" />
        </button>
      </div>
    </div>

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
  display: flex; align-items: center; min-height: 40px; padding: 0 .75rem; gap: .75rem;
  background: var(--glass-bg); border-bottom: 1px solid var(--color-border-light);
  backdrop-filter: blur(var(--glass-blur)) saturate(180%);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(180%);
  flex-shrink: 0; z-index: 30;
}
.ticker-left { display: flex; align-items: center; gap: .75rem; flex: 1; min-width: 0; }
.ticker-quote {
  display: flex; align-items: center; gap: .4rem; flex: 1; min-width: 0; padding: .35rem 0;
  color: var(--color-text-secondary); font-size: .72rem; text-align: left;
  overflow: hidden; white-space: nowrap; cursor: pointer;
}
.ticker-quote:hover { color: var(--color-text-primary); }
.ticker-quote:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.ticker-quote-icon { color: var(--color-warning); flex-shrink: 0; }
.ticker-quote-text { overflow: hidden; text-overflow: ellipsis; }
.quote-enter-active, .quote-leave-active { transition: opacity .15s ease; }
.quote-enter-from, .quote-leave-to { opacity: 0; }
.flow-help { flex-shrink: 0; }
.flow-help :deep(.term-help-button) { width: 28px; height: 28px; border: none; cursor: pointer; }
.flow-help :deep(.term-help-button:hover) { background: var(--color-bg-hover); }
.ticker-center { flex: 0 1 235px; min-width: 160px; }
.ticker-search {
  display: flex; align-items: center; gap: .4rem; padding: .2rem .4rem .2rem .6rem;
  background: var(--color-bg-input); border: 1px solid var(--color-border-light); border-radius: var(--radius-md);
}
.ticker-search:focus-within { border-color: var(--color-primary-border); }
.search-input { min-width: 0; width: 100%; border: none; background: none; outline: none; font-size: .75rem; color: var(--color-text-primary); font-family: inherit; }
.search-input::placeholder { color: var(--color-text-muted); }
.search-submit { display: flex; align-items: center; padding: .15rem; color: var(--color-text-secondary); flex-shrink: 0; }
.search-submit:disabled { opacity: .4; cursor: default; }
.ticker-right { display: flex; align-items: center; gap: .3rem; flex-shrink: 0; }
.ticker-action { display: flex; align-items: center; gap: .25rem; padding: .3rem .5rem; border-radius: var(--radius-sm); color: var(--color-text-secondary); }
.ticker-action:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.action-label { font-size: .72rem; font-weight: 500; }
@media (max-width: 1100px) {
  .ticker-bar { flex-wrap: wrap; gap: .4rem .6rem; padding: .35rem .6rem; }
  .ticker-left { flex-basis: 100%; }
  .ticker-center { flex: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .quote-enter-active, .quote-leave-active { transition: none; }
}
</style>
