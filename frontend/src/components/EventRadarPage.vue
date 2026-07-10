<script setup>
/**
 * EventRadarPage — 机会雷达整合页
 *
 * 三个 Tab 形成「发现机会→跟踪观察→上车信号→落地验证」闭环：
 * - 事件雷达：未来 1-2 周即将发生的市场事件，候选基金可一键加入关注
 * - 关注机会：关注列表基金卡片，展示目标价/估值分位/上车信号状态
 * - 落地验证：已验证事件 + 板块准确率统计
 */
import { ref, computed, onMounted } from 'vue'
import {
  listMarketEvents, triggerEventRadarScan, triggerEventRadarVerify, getEventRadarAccuracy,
  listWatchlist, addToWatchlist, removeWatchlistItem, refreshWatchlistNavs,
  triggerWatchlistScan,
} from '../api'
import Icon from './ui/Icon.vue'
import { useToast } from '../composables/useToast'

const emit = defineEmits(['navigate'])

const activeTab = ref('events') // events / watchlist / verification
const events = ref([])
const watchlist = ref([])
const loading = ref(false)
const scanning = ref(false)
const verifying = ref(false)
const refreshingNavs = ref(false)
const scanningWatchlist = ref(false)
const accuracy = ref(null)
const activeFilter = ref('all') // all / holding_impact / watchlist_impact / opportunity / market_watch
const activeStatus = ref('active') // active / all / materialized / expired

const FILTERS = [
  { key: 'all', label: '全部', icon: 'satellite' },
  { key: 'holding_impact', label: '持仓影响', icon: 'alert-triangle' },
  { key: 'watchlist_impact', label: '关注机会', icon: 'bookmark' },
  { key: 'opportunity', label: '建仓机会', icon: 'trending-up' },
  { key: 'market_watch', label: '市场关注', icon: 'info' },
]

const STATUS_TABS = [
  { key: 'active', label: '进行中' },
  { key: 'materialized', label: '已落地' },
  { key: 'expired', label: '已过期' },
  { key: 'all', label: '全部' },
]

const filteredEvents = computed(() => {
  let list = events.value
  if (activeFilter.value !== 'all') {
    list = list.filter(e => e.relevance_to_user === activeFilter.value)
  }
  if (activeStatus.value === 'active') {
    list = list.filter(e => e.status === 'upcoming' || e.status === 'imminent')
  } else if (activeStatus.value !== 'all') {
    list = list.filter(e => e.status === activeStatus.value)
  }
  // 按预期日期升序
  return [...list].sort((a, b) => {
    const da = a.expected_date || ''
    const db = b.expected_date || ''
    return da.localeCompare(db)
  })
})

const verifiedEvents = computed(() => {
  return events.value
    .filter(e => parseVerification(e.verification_result))
    .sort((a, b) => (b.expected_date || '').localeCompare(a.expected_date || ''))
})

const stats = computed(() => {
  const total = events.value.length
  const holding = events.value.filter(e => e.relevance_to_user === 'holding_impact').length
  const watchlistHit = events.value.filter(e => e.relevance_to_user === 'watchlist_impact').length
  const opportunity = events.value.filter(e => e.relevance_to_user === 'opportunity').length
  const watch = events.value.filter(e => e.relevance_to_user === 'market_watch').length
  return { total, holding, watchlistHit, opportunity, watch }
})

const watchlistStats = computed(() => {
  const total = watchlist.value.length
  const withTarget = watchlist.value.filter(w => w.target_price || w.target_percentile).length
  const lowNav = watchlist.value.filter(w => {
    if (!w.target_price || !w.current_nav) return false
    return parseFloat(w.current_nav) <= parseFloat(w.target_price)
  }).length
  return { total, withTarget, lowNav }
})

async function loadEvents() {
  loading.value = true
  try {
    const { data } = await listMarketEvents({ limit: 100 })
    events.value = data?.events || []
  } catch (e) {
    useToast().showToast('加载事件失败', 'error')
  } finally {
    loading.value = false
  }
}

async function loadWatchlist() {
  try {
    const { data } = await listWatchlist('watching')
    watchlist.value = data?.items || []
  } catch (e) {
    useToast().showToast('加载关注列表失败', 'error')
  }
}

async function handleScan() {
  if (scanning.value) return
  scanning.value = true
  try {
    const { data } = await triggerEventRadarScan()
    useToast().showToast(
      `扫描完成：提取 ${data?.extracted || 0} 个事件，新增 ${data?.new || 0} 个`,
      'success'
    )
    await loadEvents()
  } catch (e) {
    useToast().showToast('扫描失败', 'error')
  } finally {
    scanning.value = false
  }
}

async function handleVerify() {
  if (verifying.value) return
  verifying.value = true
  try {
    const { data } = await triggerEventRadarVerify()
    useToast().showToast(
      `验证完成：${data?.verified || 0} 个事件，正确 ${data?.correct || 0}，偏差 ${data?.wrong || 0}`,
      'success'
    )
    await loadEvents()
    await loadAccuracy()
  } catch (e) {
    useToast().showToast('验证失败', 'error')
  } finally {
    verifying.value = false
  }
}

async function loadAccuracy() {
  try {
    const { data } = await getEventRadarAccuracy()
    accuracy.value = data
  } catch { /* 静默失败 */ }
}

async function handleAddToWatchlist(fund) {
  try {
    await addToWatchlist({
      fund_code: fund.fund_code,
      fund_name: fund.fund_name,
      index_code: fund.index_code || '',
      index_name: fund.index_name || '',
      notes: `来自事件雷达候选基金`,
    })
    useToast().showToast(`已加入关注：${fund.fund_name}`, 'success')
    if (activeTab.value === 'watchlist') await loadWatchlist()
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '加入失败'
    useToast().showToast(msg, 'error')
  }
}

async function handleRemoveWatchlist(item) {
  if (!confirm(`确认从关注列表移除「${item.fund_name}」？`)) return
  try {
    await removeWatchlistItem(item.id)
    useToast().showToast('已移除', 'success')
    await loadWatchlist()
  } catch (e) {
    useToast().showToast('移除失败', 'error')
  }
}

async function handleRefreshNavs() {
  if (refreshingNavs.value) return
  refreshingNavs.value = true
  try {
    const { data } = await refreshWatchlistNavs()
    const ok = (data || []).filter(r => !r.error).length
    const fail = (data || []).filter(r => r.error).length
    useToast().showToast(`刷新完成：成功 ${ok}，失败 ${fail}`, ok > 0 ? 'success' : 'warning')
    await loadWatchlist()
  } catch (e) {
    useToast().showToast('刷新净值失败', 'error')
  } finally {
    refreshingNavs.value = false
  }
}

async function handleScanWatchlistSignals() {
  if (scanningWatchlist.value) return
  scanningWatchlist.value = true
  try {
    const { data } = await triggerWatchlistScan()
    useToast().showToast(
      `扫描完成：扫描 ${data?.watchlist_scanned || 0} 只基金，生成 ${data?.alerts_created || 0} 个信号`,
      'success'
    )
    await loadWatchlist()
  } catch (e) {
    useToast().showToast('扫描失败', 'error')
  } finally {
    scanningWatchlist.value = false
  }
}

function parseVerification(str) {
  try { return JSON.parse(str || '') } catch { return null }
}

function verificationLabel(status) {
  return { correct: '验证正确', wrong: '验证偏差', flat: '波动平淡' }[status] || status
}

function verificationIcon(status) {
  return { correct: 'check-circle', wrong: 'alert-triangle', flat: 'info' }[status] || 'info'
}

function relevanceLabel(r) {
  return {
    holding_impact: '持仓影响',
    watchlist_impact: '关注机会',
    opportunity: '建仓机会',
    market_watch: '市场关注',
  }[r] || r
}

function statusLabel(s) {
  return { upcoming: '即将到来', imminent: '临近', materialized: '已落地', expired: '已过期' }[s] || s
}

function parseJsonArray(str) {
  try { return JSON.parse(str || '[]') } catch { return [] }
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  if (isNaN(d)) return dateStr
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.round((d - today) / (1000 * 60 * 60 * 24))
  if (diff === 0) return '今天'
  if (diff === 1) return '明天'
  if (diff > 1 && diff <= 7) return `${diff} 天后`
  return dateStr
}

function directionLabel(d) {
  if (d === 'positive') return { text: '利好', class: 'dir-positive' }
  if (d === 'negative') return { text: '利空', class: 'dir-negative' }
  return { text: '中性', class: 'dir-neutral' }
}

/** 判断关注基金当前是否处于上车信号状态 */
function watchlistSignalState(item) {
  if (!item.current_nav) return { state: 'no_data', label: '无净值', cls: 'sig-neutral' }
  if (item.target_price && parseFloat(item.current_nav) <= parseFloat(item.target_price)) {
    return { state: 'target_hit', label: '目标价到位', cls: 'sig-hit' }
  }
  if (item.target_percentile && item.current_percentile !== null && item.current_percentile !== undefined) {
    if (parseFloat(item.current_percentile) <= parseFloat(item.target_percentile)) {
      return { state: 'low_percentile', label: '估值低分位', cls: 'sig-hit' }
    }
  }
  return { state: 'waiting', label: '等待到位', cls: 'sig-waiting' }
}

onMounted(() => {
  loadEvents()
  loadAccuracy()
  loadWatchlist()
})
</script>

<template>
  <div class="event-radar-page">
    <!-- 顶部标题区 -->
    <div class="page-header">
      <div class="header-left">
        <div class="page-title-row">
          <Icon name="satellite" size="22" class="page-title-icon" />
          <h2 class="page-title">机会雷达</h2>
        </div>
        <p class="page-subtitle">发现机会 → 跟踪观察 → 上车信号 → 落地验证，形成投资闭环</p>
      </div>
      <div class="header-actions">
        <button v-if="activeTab === 'events'" class="btn btn-secondary verify-btn" @click="handleVerify" :disabled="verifying" title="验证已落地事件的方向预测">
          <Icon :name="verifying ? 'spinner' : 'check-circle'" size="14" :class="{ spinning: verifying }" />
          <span>{{ verifying ? '验证中...' : '落地验证' }}</span>
        </button>
        <button v-if="activeTab === 'events'" class="btn btn-primary scan-btn" @click="handleScan" :disabled="scanning">
          <Icon :name="scanning ? 'spinner' : 'scan-search'" size="14" :class="{ spinning: scanning }" />
          <span>{{ scanning ? '扫描中...' : '立即扫描' }}</span>
        </button>
        <button v-if="activeTab === 'watchlist'" class="btn btn-secondary verify-btn" @click="handleRefreshNavs" :disabled="refreshingNavs">
          <Icon :name="refreshingNavs ? 'spinner' : 'refresh-cw'" size="14" :class="{ spinning: refreshingNavs }" />
          <span>{{ refreshingNavs ? '刷新中...' : '刷新净值' }}</span>
        </button>
        <button v-if="activeTab === 'watchlist'" class="btn btn-primary scan-btn" @click="handleScanWatchlistSignals" :disabled="scanningWatchlist">
          <Icon :name="scanningWatchlist ? 'spinner' : 'zap'" size="14" :class="{ spinning: scanningWatchlist }" />
          <span>{{ scanningWatchlist ? '扫描中...' : '信号扫描' }}</span>
        </button>
      </div>
    </div>

    <!-- 主 Tab 切换 -->
    <div class="main-tabs">
      <button class="main-tab" :class="{ active: activeTab === 'events' }" @click="activeTab = 'events'">
        <Icon name="satellite" size="14" />
        <span>事件雷达</span>
        <span v-if="stats.total" class="tab-badge">{{ stats.total }}</span>
      </button>
      <button class="main-tab" :class="{ active: activeTab === 'watchlist' }" @click="activeTab = 'watchlist'">
        <Icon name="bookmark" size="14" />
        <span>关注机会</span>
        <span v-if="watchlistStats.total" class="tab-badge tab-badge-orange">{{ watchlistStats.total }}</span>
      </button>
      <button class="main-tab" :class="{ active: activeTab === 'verification' }" @click="activeTab = 'verification'">
        <Icon name="check-circle" size="14" />
        <span>落地验证</span>
        <span v-if="accuracy?.overall?.total" class="tab-badge tab-badge-green">{{ accuracy.overall.total }}</span>
      </button>
    </div>

    <!-- ════ Tab 1：事件雷达 ════ -->
    <template v-if="activeTab === 'events'">
      <!-- 统计卡片 -->
      <div class="stats-row">
        <div class="stat-card stat-all" @click="activeFilter = 'all'">
          <div class="stat-value">{{ stats.total }}</div>
          <div class="stat-label">全部事件</div>
        </div>
        <div class="stat-card stat-holding" @click="activeFilter = 'holding_impact'">
          <div class="stat-value">{{ stats.holding }}</div>
          <div class="stat-label">持仓影响</div>
        </div>
        <div class="stat-card stat-watchlist-hit" @click="activeFilter = 'watchlist_impact'">
          <div class="stat-value">{{ stats.watchlistHit }}</div>
          <div class="stat-label">关注机会</div>
        </div>
        <div class="stat-card stat-opportunity" @click="activeFilter = 'opportunity'">
          <div class="stat-value">{{ stats.opportunity }}</div>
          <div class="stat-label">建仓机会</div>
        </div>
        <div class="stat-card stat-watch" @click="activeFilter = 'market_watch'">
          <div class="stat-value">{{ stats.watch }}</div>
          <div class="stat-label">市场关注</div>
        </div>
      </div>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <div class="filter-tabs">
          <button
            v-for="f in FILTERS"
            :key="f.key"
            class="filter-tab"
            :class="{ active: activeFilter === f.key }"
            @click="activeFilter = f.key"
          >
            <Icon :name="f.icon" size="13" />
            <span>{{ f.label }}</span>
          </button>
        </div>
        <div class="status-tabs">
          <button
            v-for="s in STATUS_TABS"
            :key="s.key"
            class="status-tab"
            :class="{ active: activeStatus === s.key }"
            @click="activeStatus = s.key"
          >
            {{ s.label }}
          </button>
        </div>
      </div>

      <!-- 事件时间线 -->
      <div class="events-timeline">
        <div v-if="loading" class="empty-state">
          <Icon name="spinner" size="24" class="spinning" />
          <span>加载中...</span>
        </div>
        <div v-else-if="filteredEvents.length === 0" class="empty-state">
          <Icon name="check-circle" size="28" class="empty-icon" />
          <span class="empty-text">暂无相关事件</span>
          <span class="empty-hint">点击右上角「立即扫描」从今日新闻中提取前瞻事件</span>
        </div>
        <template v-else>
          <div
            v-for="(evt, idx) in filteredEvents"
            :key="evt.event_id"
            class="event-card"
            :class="[`relevance-${evt.relevance_to_user}`, `status-${evt.status}`]"
          >
            <!-- 左侧时间标记 -->
            <div class="event-time-col">
              <div class="event-date-badge">{{ formatDate(evt.expected_date) }}</div>
              <div class="event-date-sub">{{ evt.expected_date || '' }}</div>
              <div v-if="idx < filteredEvents.length - 1" class="timeline-line"></div>
            </div>

            <!-- 右侧内容 -->
            <div class="event-content-col">
              <div class="event-header">
                <div class="event-title-row">
                  <h3 class="event-title">{{ evt.title }}</h3>
                  <span class="event-type-tag">{{ {
                    policy: '政策', industry: '行业', earnings: '财报',
                    capital: '资本', macro: '宏观', theme: '主题'
                  }[evt.event_type] || evt.event_type }}</span>
                </div>
                <div class="event-meta-row">
                  <span
                    class="relevance-tag"
                    :class="`tag-${evt.relevance_to_user}`"
                  >
                    {{ relevanceLabel(evt.relevance_to_user) }}
                  </span>
                  <span class="status-tag" :class="`st-${evt.status}`">
                    {{ statusLabel(evt.status) }}
                  </span>
                  <span v-if="evt.direction" class="direction-tag" :class="directionLabel(evt.direction).class">
                    {{ directionLabel(evt.direction).text }}
                  </span>
                  <span class="confidence-tag">
                    置信度 {{ Math.round((evt.confidence || 0) * 100) }}%
                  </span>
                  <!-- 验证结果标签 -->
                  <span
                    v-if="parseVerification(evt.verification_result)"
                    class="verify-tag"
                    :class="`verify-${parseVerification(evt.verification_result).status}`"
                  >
                    <Icon :name="verificationIcon(parseVerification(evt.verification_result).status)" size="11" />
                    {{ verificationLabel(parseVerification(evt.verification_result).status) }}
                    {{ parseVerification(evt.verification_result).change_pct > 0 ? '+' : '' }}{{ parseVerification(evt.verification_result).change_pct }}%
                  </span>
                </div>
              </div>

              <p v-if="evt.summary" class="event-summary">{{ evt.summary }}</p>

              <!-- 影响板块 -->
              <div v-if="parseJsonArray(evt.affected_sectors).length" class="event-section">
                <span class="section-label">影响板块</span>
                <div class="tag-list">
                  <span v-for="s in parseJsonArray(evt.affected_sectors)" :key="s" class="chip chip-sector">{{ s }}</span>
                </div>
              </div>

              <!-- 影响主题 -->
              <div v-if="parseJsonArray(evt.affected_themes).length" class="event-section">
                <span class="section-label">主题标签</span>
                <div class="tag-list">
                  <span v-for="t in parseJsonArray(evt.affected_themes)" :key="t" class="chip chip-theme">#{{ t }}</span>
                </div>
              </div>

              <!-- 关联持仓/关注 -->
              <div v-if="parseJsonArray(evt.matched_holdings).length" class="event-section">
                <span class="section-label">关联持仓/关注</span>
                <div class="holding-list">
                  <div v-for="h in parseJsonArray(evt.matched_holdings)" :key="h.fund_code" class="holding-item" :class="{ 'holding-item-watchlist': h.match_type === 'watchlist' }">
                    <Icon :name="h.match_type === 'watchlist' ? 'bookmark' : 'briefcase'" size="13" />
                    <span class="holding-name">{{ h.fund_name }}</span>
                    <span class="holding-reason">{{ h.match_reason }}</span>
                  </div>
                </div>
              </div>

              <!-- 候选建仓基金 -->
              <div v-if="parseJsonArray(evt.candidate_funds).length" class="event-section">
                <span class="section-label">候选建仓基金</span>
                <div class="candidate-list">
                  <div v-for="c in parseJsonArray(evt.candidate_funds)" :key="c.fund_code" class="candidate-item">
                    <Icon name="trending-up" size="13" class="candidate-icon" />
                    <div class="candidate-info">
                      <span class="candidate-name">{{ c.fund_name }}</span>
                      <span class="candidate-reason">{{ c.match_reason }}</span>
                    </div>
                    <button class="btn-watch-add" @click="handleAddToWatchlist(c)" title="加入关注列表">
                      <Icon name="bookmark-plus" size="13" />
                      <span>关注</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </template>

    <!-- ════ Tab 2：关注机会 ════ -->
    <template v-if="activeTab === 'watchlist'">
      <!-- 关注列表统计 -->
      <div class="stats-row stats-row-3">
        <div class="stat-card stat-all">
          <div class="stat-value">{{ watchlistStats.total }}</div>
          <div class="stat-label">关注总数</div>
        </div>
        <div class="stat-card stat-watchlist-hit">
          <div class="stat-value">{{ watchlistStats.withTarget }}</div>
          <div class="stat-label">已设目标</div>
        </div>
        <div class="stat-card stat-holding">
          <div class="stat-value">{{ watchlistStats.lowNav }}</div>
          <div class="stat-label">目标价到位</div>
        </div>
      </div>

      <!-- 关注基金卡片 -->
      <div class="watchlist-grid">
        <div v-if="watchlist.length === 0" class="empty-state">
          <Icon name="bookmark" size="28" class="empty-icon" />
          <span class="empty-text">暂无关注基金</span>
          <span class="empty-hint">在「事件雷达」Tab 的候选基金中点击「关注」按钮加入</span>
        </div>
        <div
          v-for="item in watchlist"
          :key="item.id"
          class="wl-card"
          :class="watchlistSignalState(item).cls"
        >
          <div class="wl-card-header">
            <div class="wl-name-block">
              <Icon name="bookmark" size="14" class="wl-icon" />
              <span class="wl-fund-name">{{ item.fund_name }}</span>
            </div>
            <span class="wl-signal-tag" :class="watchlistSignalState(item).cls">
              {{ watchlistSignalState(item).label }}
            </span>
          </div>
          <div class="wl-card-body">
            <div class="wl-data-row">
              <span class="wl-data-label">当前净值</span>
              <span class="wl-data-value">{{ item.current_nav ? Number(item.current_nav).toFixed(4) : '—' }}</span>
            </div>
            <div class="wl-data-row">
              <span class="wl-data-label">目标价</span>
              <span class="wl-data-value" :class="{ 'value-hit': item.target_price && item.current_nav && parseFloat(item.current_nav) <= parseFloat(item.target_price) }">
                {{ item.target_price ? Number(item.target_price).toFixed(4) : '未设' }}
              </span>
            </div>
            <div v-if="item.target_percentile" class="wl-data-row">
              <span class="wl-data-label">目标分位</span>
              <span class="wl-data-value">{{ item.target_percentile }}%</span>
            </div>
            <div v-if="item.index_name" class="wl-data-row">
              <span class="wl-data-label">跟踪指数</span>
              <span class="wl-data-value wl-index-name">{{ item.index_name }}</span>
            </div>
            <div v-if="item.notes" class="wl-data-row">
              <span class="wl-data-label">备注</span>
              <span class="wl-data-value wl-notes">{{ item.notes }}</span>
            </div>
          </div>
          <div class="wl-card-footer">
            <span v-if="item.nav_updated_at" class="wl-updated">更新于 {{ item.nav_updated_at.slice(5, 16) }}</span>
            <span v-else class="wl-updated wl-updated-stale">未刷新</span>
            <button class="btn-wl-remove" @click="handleRemoveWatchlist(item)">
              <Icon name="trash-2" size="12" />
              <span>移除</span>
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- ════ Tab 3：落地验证 ════ -->
    <template v-if="activeTab === 'verification'">
      <!-- 准确率统计面板 -->
      <div v-if="accuracy && accuracy.overall?.total > 0" class="accuracy-panel">
        <div class="accuracy-header">
          <Icon name="target" size="14" class="accuracy-icon" />
          <span class="accuracy-title">验证准确率</span>
        </div>
        <div class="accuracy-body">
          <div class="accuracy-overall">
            <div class="accuracy-value">{{ (accuracy.overall.accuracy * 100).toFixed(0) }}%</div>
            <div class="accuracy-label">总体准确率</div>
            <div class="accuracy-detail">
              {{ accuracy.overall.correct }}正确 / {{ accuracy.overall.wrong }}偏差 / {{ accuracy.overall.flat }}平淡
              （共 {{ accuracy.overall.total }} 个已验证）
            </div>
          </div>
          <div v-if="Object.keys(accuracy.by_sector).length" class="accuracy-sectors">
            <div v-for="(s, name) in accuracy.by_sector" :key="name" class="sector-acc-item">
              <span class="sector-acc-name">{{ name }}</span>
              <div class="sector-acc-bar">
                <div class="sector-acc-fill" :style="{ width: `${s.accuracy * 100}%` }"></div>
              </div>
              <span class="sector-acc-pct">{{ (s.accuracy * 100).toFixed(0) }}%</span>
              <span class="sector-acc-samples">({{ s.total }})</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 已验证事件列表 -->
      <div class="events-timeline">
        <div v-if="verifiedEvents.length === 0" class="empty-state">
          <Icon name="check-circle" size="28" class="empty-icon" />
          <span class="empty-text">暂无已验证事件</span>
          <span class="empty-hint">事件落地后会自动进行 T+3 验证，也可点击顶部「落地验证」手动触发</span>
        </div>
        <template v-else>
          <div
            v-for="evt in verifiedEvents"
            :key="evt.event_id"
            class="event-card"
            :class="`verify-${parseVerification(evt.verification_result).status}`"
          >
            <div class="event-time-col">
              <div class="event-date-badge">{{ evt.expected_date || '' }}</div>
              <div class="event-date-sub">{{ statusLabel(evt.status) }}</div>
            </div>
            <div class="event-content-col">
              <div class="event-header">
                <div class="event-title-row">
                  <h3 class="event-title">{{ evt.title }}</h3>
                  <span
                    class="verify-tag"
                    :class="`verify-${parseVerification(evt.verification_result).status}`"
                  >
                    <Icon :name="verificationIcon(parseVerification(evt.verification_result).status)" size="11" />
                    {{ verificationLabel(parseVerification(evt.verification_result).status) }}
                    {{ parseVerification(evt.verification_result).change_pct > 0 ? '+' : '' }}{{ parseVerification(evt.verification_result).change_pct }}%
                  </span>
                </div>
                <div class="event-meta-row">
                  <span v-if="evt.direction" class="direction-tag" :class="directionLabel(evt.direction).class">
                    预测：{{ directionLabel(evt.direction).text }}
                  </span>
                  <span class="confidence-tag">
                    置信度 {{ Math.round((evt.confidence || 0) * 100) }}%
                  </span>
                </div>
              </div>
              <p v-if="evt.summary" class="event-summary">{{ evt.summary }}</p>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.event-radar-page {
  padding: 1.25rem 1.5rem;
  max-width: 960px;
  margin: 0 auto;
}

/* 顶部标题 */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.25rem;
}
.header-left { flex: 1; min-width: 0; }
.page-title-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.3rem;
}
.page-title-icon { color: var(--color-primary); }
.page-title {
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}
.page-subtitle {
  font-size: 0.82rem;
  color: var(--color-text-tertiary);
  margin: 0;
}
.header-actions { flex-shrink: 0; }

.scan-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-size: 0.82rem;
  font-weight: 500;
  border: none;
  cursor: pointer;
  transition: all 0.15s;
  background: var(--color-primary);
  color: white;
}
.scan-btn:hover:not(:disabled) { opacity: 0.9; transform: translateY(-1px); }
.scan-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* 统计卡片 */
.stats-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.stats-row-3 {
  grid-template-columns: repeat(3, 1fr);
}
.stat-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 0.9rem 1rem;
  cursor: pointer;
  transition: all 0.15s;
  border-left: 3px solid var(--color-border);
}
.stat-card:hover {
  border-color: var(--color-border);
  background: var(--color-bg-secondary);
}
.stat-all { border-left-color: var(--color-text-tertiary); }
.stat-holding { border-left-color: #dc2626; }
.stat-watchlist-hit { border-left-color: #ea580c; }
.stat-opportunity { border-left-color: #d97706; }
.stat-watch { border-left-color: #2563eb; }
.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.2;
  font-family: var(--font-jet);
}
.stat-label {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  margin-top: 0.2rem;
}

/* 筛选栏 */
.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
  gap: 1rem;
}
.filter-tabs {
  display: flex;
  gap: 0.3rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
  padding: 3px;
}
.filter-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 0.75rem;
  border: none;
  background: transparent;
  border-radius: 5px;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}
.filter-tab:hover { color: var(--color-text-primary); }
.filter-tab.active {
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}
.status-tabs {
  display: flex;
  gap: 0.2rem;
}
.status-tab {
  padding: 0.35rem 0.7rem;
  border: none;
  background: transparent;
  border-radius: 4px;
  font-size: 0.75rem;
  color: var(--color-text-tertiary);
  cursor: pointer;
  transition: all 0.15s;
}
.status-tab:hover { color: var(--color-text-secondary); }
.status-tab.active {
  color: var(--color-text-primary);
  background: var(--color-bg-secondary);
  font-weight: 500;
}

/* 时间线 */
.events-timeline {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem 1rem;
  gap: 0.6rem;
  color: var(--color-text-tertiary);
  font-size: 0.85rem;
}
.empty-icon { opacity: 0.5; }
.empty-text { font-size: 0.9rem; color: var(--color-text-secondary); }
.empty-hint { font-size: 0.78rem; }

/* 事件卡片 */
.event-card {
  display: flex;
  gap: 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 1rem 1.1rem;
  transition: all 0.15s;
}
.event-card:hover {
  border-color: var(--color-border);
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
.event-card.relevance-holding_impact { border-left: 3px solid #dc2626; }
.event-card.relevance-watchlist_impact { border-left: 3px solid #ea580c; }
.event-card.relevance-opportunity { border-left: 3px solid #d97706; }
.event-card.relevance-market_watch { border-left: 3px solid #2563eb; }

/* 左侧时间列 */
.event-time-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 70px;
  flex-shrink: 0;
  position: relative;
}
.event-date-badge {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
  background: var(--color-bg-secondary);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  white-space: nowrap;
  font-family: var(--font-jet);
}
.event-date-sub {
  font-size: 0.68rem;
  color: var(--color-text-tertiary);
  margin-top: 0.25rem;
  font-family: var(--font-jet);
}
.timeline-line {
  width: 1px;
  flex: 1;
  background: var(--color-border-light);
  margin-top: 0.5rem;
  min-height: 20px;
}

/* 右侧内容 */
.event-content-col {
  flex: 1;
  min-width: 0;
}
.event-header { margin-bottom: 0.5rem; }
.event-title-row {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.event-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
  line-height: 1.4;
  flex: 1;
}
.event-type-tag {
  flex-shrink: 0;
  font-size: 0.68rem;
  padding: 0.15rem 0.5rem;
  border-radius: 3px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-weight: 500;
}
.event-meta-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.relevance-tag, .status-tag, .direction-tag, .confidence-tag {
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
}
.relevance-tag.tag-holding_impact { background: rgba(220,38,38,0.1); color: #dc2626; }
.relevance-tag.tag-watchlist_impact { background: rgba(234,88,12,0.1); color: #ea580c; }
.relevance-tag.tag-opportunity { background: rgba(217,119,6,0.1); color: #d97706; }
.relevance-tag.tag-market_watch { background: rgba(37,99,235,0.1); color: #2563eb; }

.status-tag.st-upcoming { background: var(--color-bg-tertiary); color: var(--color-text-secondary); }
.status-tag.st-imminent { background: rgba(220,38,38,0.1); color: #dc2626; }
.status-tag.st-materialized { background: rgba(22,163,74,0.1); color: #16a34a; }
.status-tag.st-expired { background: var(--color-bg-tertiary); color: var(--color-text-tertiary); }

.direction-tag.dir-positive { background: rgba(22,163,74,0.1); color: #16a34a; }
.direction-tag.dir-negative { background: rgba(220,38,38,0.1); color: #dc2626; }
.direction-tag.dir-neutral { background: var(--color-bg-tertiary); color: var(--color-text-secondary); }

.confidence-tag {
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
}

.event-summary {
  font-size: 0.82rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0 0 0.6rem 0;
}

.event-section {
  margin-top: 0.5rem;
}
.section-label {
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  font-weight: 500;
  margin-right: 0.5rem;
  display: inline-block;
}
.tag-list {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  vertical-align: middle;
}
.chip {
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
}
.chip-sector {
  background: rgba(59,130,246,0.08);
  color: #2563eb;
}
.chip-theme {
  background: rgba(168,85,247,0.08);
  color: #7c3aed;
}

/* 持仓列表 */
.holding-list {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin-top: 0.25rem;
}
.holding-item {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  padding: 0.25rem 0.5rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
}
.holding-item .holding-name {
  color: var(--color-text-primary);
  font-weight: 500;
}
.holding-item .holding-reason {
  color: var(--color-text-tertiary);
  font-size: 0.72rem;
}

/* 候选基金 */
.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin-top: 0.25rem;
}
.candidate-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.6rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
}
.candidate-icon { color: #d97706; flex-shrink: 0; }
.candidate-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.candidate-name {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-primary);
}
.candidate-reason {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
}

/* 响应式 */
@media (max-width: 768px) {
  .event-radar-page { padding: 0.75rem; }
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .event-time-col { width: 55px; }
  .event-date-badge { font-size: 0.72rem; padding: 0.2rem 0.4rem; }
  .filter-bar { flex-direction: column; align-items: flex-start; }
}

/* ── 验证按钮 ── */
.verify-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.9rem;
  border-radius: 6px;
  font-size: 0.82rem;
  font-weight: 500;
  border: 1px solid var(--color-border);
  cursor: pointer;
  transition: all 0.15s;
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  margin-right: 0.5rem;
}
.verify-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}
.verify-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* ── 准确率统计面板 ── */
.accuracy-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 1rem 1.1rem;
  margin-bottom: 1rem;
}
.accuracy-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.75rem;
}
.accuracy-icon { color: var(--color-primary); }
.accuracy-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.accuracy-body {
  display: flex;
  gap: 1.5rem;
  align-items: flex-start;
}
.accuracy-overall {
  flex-shrink: 0;
  min-width: 140px;
}
.accuracy-value {
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--color-primary);
  font-family: var(--font-jet);
  line-height: 1.2;
}
.accuracy-label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  margin-top: 0.15rem;
}
.accuracy-detail {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  margin-top: 0.3rem;
}
.accuracy-sectors {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  min-width: 0;
}
.sector-acc-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
}
.sector-acc-name {
  width: 60px;
  flex-shrink: 0;
  color: var(--color-text-secondary);
}
.sector-acc-bar {
  flex: 1;
  height: 6px;
  background: var(--color-bg-tertiary);
  border-radius: 3px;
  overflow: hidden;
}
.sector-acc-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary), var(--color-gold));
  border-radius: 3px;
  transition: width 0.3s;
}
.sector-acc-pct {
  width: 36px;
  text-align: right;
  font-weight: 600;
  color: var(--color-text-primary);
  font-family: var(--font-jet);
}
.sector-acc-samples {
  font-size: 0.68rem;
  color: var(--color-text-tertiary);
  width: 30px;
}

/* ── 验证结果标签 ── */
.verify-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
}
.verify-tag.verify-correct {
  background: rgba(22,163,74,0.1);
  color: #16a34a;
}
.verify-tag.verify-wrong {
  background: rgba(220,38,38,0.1);
  color: #dc2626;
}
.verify-tag.verify-flat {
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
}

/* ── 主 Tab 切换 ── */
.main-tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border-light);
  margin-bottom: 1rem;
  padding-bottom: 0;
}
.main-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.55rem 0.9rem;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
  margin-bottom: -1px;
}
.main-tab:hover { color: var(--color-text-primary); }
.main-tab.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}
.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 0.68rem;
  font-weight: 600;
  font-family: var(--font-jet);
}
.tab-badge-orange { background: rgba(234,88,12,0.12); color: #ea580c; }
.tab-badge-green { background: rgba(22,163,74,0.12); color: #16a34a; }

/* ── 候选基金关注按钮 ── */
.candidate-item {
  position: relative;
}
.btn-watch-add {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.55rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-radius: 4px;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  margin-left: auto;
}
.btn-watch-add:hover {
  border-color: #ea580c;
  color: #ea580c;
  background: rgba(234,88,12,0.04);
}

/* ── 关注列表卡片 ── */
.watchlist-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 0.75rem;
}
.wl-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 0.9rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  transition: all 0.15s;
  border-left: 3px solid var(--color-border);
}
.wl-card:hover {
  border-color: var(--color-border);
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
.wl-card.sig-hit { border-left-color: #16a34a; }
.wl-card.sig-waiting { border-left-color: var(--color-text-tertiary); }
.wl-card.sig-neutral { border-left-color: var(--color-border); }

.wl-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
}
.wl-name-block {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
  flex: 1;
}
.wl-icon { color: #ea580c; flex-shrink: 0; }
.wl-fund-name {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: 1.3;
  word-break: break-all;
}
.wl-signal-tag {
  flex-shrink: 0;
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
  white-space: nowrap;
}
.wl-signal-tag.sig-hit {
  background: rgba(22,163,74,0.1);
  color: #16a34a;
}
.wl-signal-tag.sig-waiting {
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
}
.wl-signal-tag.sig-neutral {
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
}

.wl-card-body {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.wl-data-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.78rem;
}
.wl-data-label {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
.wl-data-value {
  color: var(--color-text-primary);
  font-weight: 500;
  text-align: right;
  font-family: var(--font-jet);
}
.wl-data-value.value-hit {
  color: #16a34a;
  font-weight: 600;
}
.wl-index-name {
  font-family: inherit;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}
.wl-notes {
  font-family: inherit;
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  font-style: italic;
}

.wl-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--color-border-light);
}
.wl-updated {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
}
.wl-updated-stale { color: #d97706; }
.btn-wl-remove {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.2rem 0.5rem;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 0.7rem;
  cursor: pointer;
  border-radius: 3px;
  transition: all 0.15s;
}
.btn-wl-remove:hover {
  color: #dc2626;
  background: rgba(220,38,38,0.06);
}

/* 关联持仓中的关注列表项样式 */
.holding-item.holding-item-watchlist {
  background: rgba(234,88,12,0.04);
}
.holding-item.holding-item-watchlist .holding-name {
  color: #ea580c;
}

/* 响应式补充 */
@media (max-width: 768px) {
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .stats-row-3 { grid-template-columns: repeat(3, 1fr); }
  .watchlist-grid { grid-template-columns: 1fr; }
  .main-tab { padding: 0.45rem 0.6rem; font-size: 0.78rem; }
}
</style>
