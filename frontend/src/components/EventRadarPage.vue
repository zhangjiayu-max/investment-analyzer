<script setup>
/**
 * EventRadarPage — 前瞻性事件雷达详情页
 *
 * 功能：
 * - 时间线视图：未来 1-2 周即将发生的市场事件
 * - 三级筛选：持仓影响 / 建仓机会 / 市场关注
 * - 事件详情卡片：预期日期、影响板块、候选基金、来源新闻
 * - 手动扫描按钮
 */
import { ref, computed, onMounted } from 'vue'
import { listMarketEvents, triggerEventRadarScan, triggerEventRadarVerify, getEventRadarAccuracy } from '../api'
import Icon from './ui/Icon.vue'
import { useToast } from '../composables/useToast'

const emit = defineEmits(['navigate'])

const events = ref([])
const loading = ref(false)
const scanning = ref(false)
const verifying = ref(false)
const accuracy = ref(null)
const activeFilter = ref('all') // all / holding_impact / opportunity / market_watch
const activeStatus = ref('active') // active / all / materialized / expired

const FILTERS = [
  { key: 'all', label: '全部', icon: 'satellite' },
  { key: 'holding_impact', label: '持仓影响', icon: 'alert-triangle' },
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

const stats = computed(() => {
  const total = events.value.length
  const holding = events.value.filter(e => e.relevance_to_user === 'holding_impact').length
  const opportunity = events.value.filter(e => e.relevance_to_user === 'opportunity').length
  const watch = events.value.filter(e => e.relevance_to_user === 'market_watch').length
  return { total, holding, opportunity, watch }
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
  return { holding_impact: '持仓影响', opportunity: '建仓机会', market_watch: '市场关注' }[r] || r
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

onMounted(() => {
  loadEvents()
  loadAccuracy()
})
</script>

<template>
  <div class="event-radar-page">
    <!-- 顶部标题区 -->
    <div class="page-header">
      <div class="header-left">
        <div class="page-title-row">
          <Icon name="satellite" size="22" class="page-title-icon" />
          <h2 class="page-title">前瞻事件雷达</h2>
        </div>
        <p class="page-subtitle">从每日新闻中提取未来 1-2 周即将发生的市场事件，提前布局不踏空</p>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary verify-btn" @click="handleVerify" :disabled="verifying" title="验证已落地事件的方向预测">
          <Icon :name="verifying ? 'spinner' : 'check-circle'" size="14" :class="{ spinning: verifying }" />
          <span>{{ verifying ? '验证中...' : '落地验证' }}</span>
        </button>
        <button class="btn btn-primary scan-btn" @click="handleScan" :disabled="scanning">
          <Icon :name="scanning ? 'spinner' : 'scan-search'" size="14" :class="{ spinning: scanning }" />
          <span>{{ scanning ? '扫描中...' : '立即扫描' }}</span>
        </button>
      </div>
    </div>

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

            <!-- 关联持仓 -->
            <div v-if="parseJsonArray(evt.matched_holdings).length" class="event-section">
              <span class="section-label">关联持仓</span>
              <div class="holding-list">
                <div v-for="h in parseJsonArray(evt.matched_holdings)" :key="h.fund_code" class="holding-item">
                  <Icon name="briefcase" size="13" />
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
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
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
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
  margin-bottom: 1rem;
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
</style>
