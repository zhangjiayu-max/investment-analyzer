<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  getAccuracyStats,
  autoVerifyAccuracy,
  getAccuracyTrend,
  getRecentVerified,
  getAdoptionStats,
} from '../../api'
import { useToast } from '../../composables/useToast'
import Icon from '../ui/Icon.vue'
import EmptyState from '../ui/EmptyState.vue'

const { showToast } = useToast()

// ── 分组与周期 ──
const GROUP_OPTIONS = [
  { value: 'agent', label: '按 Agent' },
  { value: 'action', label: '按操作类型' },
]
const PERIOD_OPTIONS = [
  { value: 30, label: '近 30 天' },
  { value: 90, label: '近 90 天' },
  { value: 180, label: '近 180 天' },
  { value: 365, label: '近 1 年' },
]
const groupBy = ref('agent')
const periodDays = ref(90)

// ── 数据状态 ──
const loading = ref(false)
const verifying = ref(false)
const stats = ref(null)
const trend = ref([])
// P0-A 决策闭环：最近验证结果 + 采纳率统计
const recentVerified = ref([])
const adoptionStats = ref(null)

// ── 总体准确率 ──
const overall = computed(() => {
  const s = stats.value || {}
  const total = s.total ?? s.total_count ?? 0
  const correct = s.correct ?? s.correct_count ?? 0
  const rate = total > 0 ? (correct / total) * 100 : 0
  return { total, correct, rate }
})

const rateColor = computed(() => {
  const r = overall.value.rate
  if (r >= 70) return 'var(--color-success)'
  if (r >= 50) return 'var(--color-warning)'
  return 'var(--color-danger)'
})

// ── 分组统计 ──
const groups = computed(() => {
  const s = stats.value || {}
  return s.groups || s.by_group || []
})

// ── 趋势图（SVG 简易柱状）──
const chartWidth = 700
const chartHeight = 180
const trendChart = computed(() => {
  if (!trend.value.length) return null
  const data = trend.value
  const max = Math.max(...data.map(d => Number(d.rate ?? d.accuracy ?? 0)), 100)
  const barW = chartWidth / data.length - 6
  const bars = data.map((d, i) => {
    const r = Number(d.rate ?? d.accuracy ?? 0)
    const h = (r / max) * (chartHeight - 30)
    return {
      x: i * (barW + 6) + 3,
      y: chartHeight - h - 20,
      w: barW,
      h,
      label: d.week || d.label || d.period || '',
      rate: r,
    }
  })
  return { bars, max }
})

function pct(v) {
  return `${Number(v || 0).toFixed(1)}%`
}

// ── P0-A：验证结果列表辅助 ──
const DIRECTION_LABELS = { up: '加仓', down: '减仓', hold: '持有', watch: '观察' }
const DIRECTION_ICONS = { up: '↑', down: '↓', hold: '→', watch: '·' }
const STATUS_LABELS = { correct: '正确', wrong: '错误', flat: '持平' }
const STATUS_ICONS = { correct: '✓', wrong: '✗', flat: '—' }
const ADOPTED_LABELS = { 1: '已采纳', [-1]: '未采纳', 0: '未标记' }

function directionClass(d) {
  return { up: 'dir-up', down: 'dir-down', hold: 'dir-hold', watch: 'dir-hold' }[d] || 'dir-hold'
}

function statusClass(s) {
  return { correct: 'st-correct', wrong: 'st-wrong', flat: 'st-flat' }[s] || 'st-flat'
}

function adoptedClass(a) {
  return { 1: 'ad-adopted', [-1]: 'ad-rejected' }[a] || 'ad-unmarked'
}

function formatChange(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function formatValue(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(4)
}

function formatDate(ts) {
  if (!ts) return ''
  const s = String(ts).replace(' ', 'T')
  const d = new Date(s)
  if (isNaN(d.getTime())) return ts
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  if (isToday) return `${hh}:${mm}`
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`
}

// ── P0-A：采纳率统计辅助 ──
const adoptionRatePct = computed(() => {
  const s = adoptionStats.value || {}
  return Number((s.adoption_rate ?? 0) * 100).toFixed(1)
})

const adoptedReturn = computed(() => {
  const v = adoptionStats.value?.adopted_avg_return ?? 0
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
})

const rejectedReturn = computed(() => {
  const v = adoptionStats.value?.rejected_avg_return ?? 0
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
})

const returnDelta = computed(() => {
  const a = adoptionStats.value?.adopted_avg_return ?? 0
  const r = adoptionStats.value?.rejected_avg_return ?? 0
  const d = a - r
  const sign = d > 0 ? '+' : ''
  return `${sign}${Number(d).toFixed(2)}%`
})

const returnDeltaClass = computed(() => {
  const d = (adoptionStats.value?.adopted_avg_return ?? 0) - (adoptionStats.value?.rejected_avg_return ?? 0)
  if (d > 0.01) return 'delta-positive'
  if (d < -0.01) return 'delta-negative'
  return 'delta-neutral'
})

// ── 加载数据 ──
async function loadAll() {
  loading.value = true
  try {
    const [statsResp, trendResp, recentResp, adoptionResp] = await Promise.allSettled([
      getAccuracyStats(periodDays.value, groupBy.value),
      getAccuracyTrend(12),
      getRecentVerified(20),
      getAdoptionStats(180),
    ])
    if (statsResp.status === 'fulfilled') {
      const d = statsResp.value.data
      stats.value = d.result || d
    }
    if (trendResp.status === 'fulfilled') {
      const d = trendResp.value.data
      trend.value = d.items || d.trend || []
    }
    if (recentResp.status === 'fulfilled') {
      recentVerified.value = recentResp.value.data?.items || []
    }
    if (adoptionResp.status === 'fulfilled') {
      adoptionStats.value = adoptionResp.value.data || null
    }
  } catch (e) {
    showToast('加载准确率数据失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

async function doAutoVerify() {
  verifying.value = true
  try {
    const { data } = await autoVerifyAccuracy()
    const verified = data.verified ?? data.count ?? 0
    showToast(`已自动验证 ${verified} 条决策`, 'success')
    await loadAll()
  } catch (e) {
    showToast('自动验证失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    verifying.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="accuracy-page bg-mesh">
    <header class="page-header">
      <div>
        <h2 class="page-title editorial-title-lg">决策准确率追踪</h2>
        <p class="page-desc">回测 AI 决策与人工操作的实际命中情况，持续校准。</p>
      </div>
      <div class="header-actions">
        <select v-model="periodDays" class="input-field ctrl-select" @change="loadAll">
          <option v-for="opt in PERIOD_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <select v-model="groupBy" class="input-field ctrl-select" @change="loadAll">
          <option v-for="opt in GROUP_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <button class="btn-secondary" @click="doAutoVerify" :disabled="verifying">
          <Icon :name="verifying ? 'spinner' : 'check'" size="16" />
          {{ verifying ? '验证中...' : '自动验证' }}
        </button>
        <button class="btn-primary" @click="loadAll" :disabled="loading">
          <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </header>

    <!-- 总体准确率 -->
    <section v-if="stats" class="overall-hero editorial-card reveal-stagger">
      <div class="rate-circle" :style="{ borderColor: rateColor }">
        <span class="rate-number font-jet-lg" :style="{ color: rateColor }">{{ pct(overall.rate) }}</span>
      </div>
      <div class="overall-info">
        <span class="overall-title editorial-title">总体准确率</span>
        <div class="overall-meta">
          <span class="terminal-label">已验证 <strong class="font-jet">{{ overall.total }}</strong> 条</span>
          <span class="terminal-label">正确 <strong class="font-jet positive">{{ overall.correct }}</strong> 条</span>
        </div>
        <span v-if="stats?.period" class="overall-period terminal-label">统计周期：{{ stats.period }}</span>
      </div>
    </section>

    <!-- 加载中 -->
    <div v-if="loading && !stats" class="loading-state editorial-card">
      <Icon name="spinner" size="22" />
      <span>正在加载准确率数据...</span>
    </div>

    <!-- 空状态 -->
    <EmptyState
      v-else-if="!stats && !loading"
      icon="chart"
      title="暂无准确率数据"
      description="系统会在决策到期后自动验证，也可点击「自动验证」手动触发。"
    />

    <!-- 趋势图 -->
    <section v-if="trendChart" class="trend-section editorial-card">
      <div class="section-head editorial-card-header">
        <h3 class="editorial-title">准确率趋势（近 12 周）</h3>
      </div>
      <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="trend-chart" preserveAspectRatio="none">
        <g v-for="(b, i) in trendChart.bars" :key="i">
          <rect
            :x="b.x" :y="b.y" :width="b.w" :height="b.h"
            :fill="b.rate >= 70 ? '#10b981' : b.rate >= 50 ? '#f59e0b' : '#ef4444'"
            rx="2"
          />
          <text :x="b.x + b.w / 2" :y="b.y - 4" text-anchor="middle" class="bar-rate">{{ b.rate.toFixed(0) }}%</text>
          <text :x="b.x + b.w / 2" :y="chartHeight - 6" text-anchor="middle" class="bar-label">{{ b.label }}</text>
        </g>
      </svg>
    </section>

    <!-- 分组统计表格 -->
    <section v-if="groups.length" class="groups-section editorial-card">
      <div class="section-head editorial-card-header">
        <h3 class="editorial-title">分组统计</h3>
        <span class="terminal-label">分组维度：{{ GROUP_OPTIONS.find(g => g.value === groupBy)?.label }}</span>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>分组</th>
              <th class="num-col">总数</th>
              <th class="num-col">正确</th>
              <th class="num-col">准确率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="g in groups" :key="g.group || g.name || g.key" class="reveal-stagger">
              <td>{{ g.group || g.name || g.key }}</td>
              <td class="num-col font-jet">{{ g.total ?? g.count ?? 0 }}</td>
              <td class="num-col font-jet positive">{{ g.correct ?? 0 }}</td>
              <td class="num-col font-jet" :style="{ color: (g.rate ?? g.accuracy ?? 0) >= 70 ? 'var(--color-success)' : (g.rate ?? g.accuracy ?? 0) >= 50 ? 'var(--color-warning)' : 'var(--color-danger)' }">
                {{ pct(g.rate ?? g.accuracy) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 分组空提示 -->
    <EmptyState
      v-if="stats && !groups.length && !trendChart && !loading"
      icon="empty"
      title="所选范围内暂无分组数据"
      description="尝试切换分组维度或延长统计周期。"
    />

    <!-- P0-A 决策闭环：采纳率 + 采纳 vs 未采纳收益对比 -->
    <section v-if="adoptionStats" class="adoption-section editorial-card">
      <div class="section-head editorial-card-header">
        <h3 class="editorial-title">采纳率与收益对比</h3>
        <span class="terminal-label">证明采纳建议的收益更高</span>
      </div>
      <div class="adoption-grid">
        <!-- 左：采纳率 -->
        <div class="adoption-block">
          <div class="adoption-ring" :style="{ '--ring-pct': adoptionRatePct + '%' }">
            <div class="ring-track"></div>
            <div class="ring-fill"></div>
            <div class="ring-center">
              <span class="ring-num font-jet-lg">{{ adoptionRatePct }}%</span>
              <span class="ring-label">采纳率</span>
            </div>
          </div>
          <div class="adoption-meta">
            <div class="meta-row">
              <span class="terminal-label">已采纳</span>
              <strong class="font-jet positive">{{ adoptionStats.adopted ?? 0 }}</strong>
            </div>
            <div class="meta-row">
              <span class="terminal-label">未采纳</span>
              <strong class="font-jet">{{ adoptionStats.rejected ?? 0 }}</strong>
            </div>
            <div class="meta-row">
              <span class="terminal-label">已验证总数</span>
              <strong class="font-jet">{{ adoptionStats.verified_count ?? 0 }}</strong>
            </div>
          </div>
        </div>

        <!-- 右：采纳 vs 未采纳收益对比 -->
        <div class="adoption-block compare-block">
          <div class="compare-row">
            <div class="compare-side adopted">
              <span class="compare-label">采纳组平均收益</span>
              <span class="compare-value font-jet-lg" :class="{ positive: (adoptionStats.adopted_avg_return ?? 0) > 0, negative: (adoptionStats.adopted_avg_return ?? 0) < 0 }">
                {{ adoptedReturn }}
              </span>
              <span class="compare-sub terminal-label">预测正确率 {{ pct((adoptionStats.adopted_correct_rate ?? 0) * 100) }}</span>
            </div>
            <div class="compare-vs">VS</div>
            <div class="compare-side rejected">
              <span class="compare-label">未采纳组平均收益</span>
              <span class="compare-value font-jet-lg" :class="{ positive: (adoptionStats.rejected_avg_return ?? 0) > 0, negative: (adoptionStats.rejected_avg_return ?? 0) < 0 }">
                {{ rejectedReturn }}
              </span>
              <span class="compare-sub terminal-label">预测正确率 {{ pct((adoptionStats.rejected_correct_rate ?? 0) * 100) }}</span>
            </div>
          </div>
          <div class="compare-delta" :class="returnDeltaClass">
            <Icon name="trending-up" size="14" v-if="returnDeltaClass === 'delta-positive'" />
            <Icon name="trending-down" size="14" v-else-if="returnDeltaClass === 'delta-negative'" />
            <Icon name="minus" size="14" v-else />
            <span class="font-jet">收益差 {{ returnDelta }}</span>
            <span class="delta-hint terminal-label" v-if="returnDeltaClass === 'delta-positive'">采纳建议收益更高</span>
            <span class="delta-hint terminal-label" v-else-if="returnDeltaClass === 'delta-negative'">未采纳反而更高</span>
            <span class="delta-hint terminal-label" v-else>两者持平</span>
          </div>
        </div>
      </div>
    </section>

    <!-- P0-A 决策闭环：最近验证结果列表 -->
    <section v-if="recentVerified.length" class="recent-section editorial-card">
      <div class="section-head editorial-card-header">
        <h3 class="editorial-title">最近验证结果</h3>
        <span class="terminal-label">共 {{ recentVerified.length }} 条已验证</span>
      </div>
      <div class="table-wrap">
        <table class="data-table recent-table">
          <thead>
            <tr>
              <th>标的</th>
              <th>方向</th>
              <th class="num-col">基线值</th>
              <th class="num-col">当前值</th>
              <th class="num-col">涨跌幅</th>
              <th>结果</th>
              <th>采纳</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in recentVerified" :key="r.id" class="reveal-stagger">
              <td>
                <div class="cell-target">
                  <span class="target-name">{{ r.index_name || '—' }}</span>
                  <span class="target-code font-jet" v-if="r.index_code">{{ r.index_code }}</span>
                </div>
              </td>
              <td>
                <span class="dir-badge" :class="directionClass(r.direction)">
                  {{ DIRECTION_ICONS[r.direction] || '·' }} {{ DIRECTION_LABELS[r.direction] || r.direction }}
                </span>
              </td>
              <td class="num-col font-jet">{{ formatValue(r.baseline_value) }}</td>
              <td class="num-col font-jet">{{ formatValue(r.current_value) }}</td>
              <td class="num-col font-jet" :class="{ positive: (r.change_pct ?? 0) > 0, negative: (r.change_pct ?? 0) < 0 }">
                {{ formatChange(r.change_pct) }}
              </td>
              <td>
                <span class="status-badge" :class="statusClass(r.status)">
                  {{ STATUS_ICONS[r.status] || '—' }} {{ STATUS_LABELS[r.status] || r.status }}
                </span>
              </td>
              <td>
                <span class="adopted-badge" :class="adoptedClass(r.adopted)">
                  {{ ADOPTED_LABELS[r.adopted] || '未标记' }}
                </span>
              </td>
              <td class="num-col font-jet cell-time">{{ formatDate(r.verified_at || r.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <EmptyState
      v-if="!loading && !recentVerified.length && !adoptionStats"
      icon="empty"
      title="暂无验证结果"
      description="AI 建议到期后会自动验证，请耐心等待。"
    />
  </div>
</template>

<style scoped>
.accuracy-page {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.page-desc {
  color: var(--color-text-muted);
  font-size: 0.85rem;
  margin-top: 4px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.ctrl-select {
  padding: 6px 10px;
  font-size: 0.85rem;
  min-width: 120px;
}

/* 总体卡片 */
.overall-hero {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-5);
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}
.rate-circle {
  width: 110px;
  height: 110px;
  border-radius: 50%;
  border: 4px solid;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.rate-number {
  font-size: 1.6em;
  font-weight: 700;
  line-height: 1;
}
.overall-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.overall-title {
  font-size: 1.2rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.overall-meta {
  display: flex;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.overall-meta .terminal-label {
  font-size: inherit;
  color: var(--color-text-muted);
}
.overall-meta strong { font-size: 1rem; }
.overall-period {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-6);
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
}

/* 趋势图 */
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
  gap: var(--space-2);
}
.section-head h3 { margin: 0; }
.trend-chart {
  width: 100%;
  height: 200px;
}
.bar-rate {
  font-size: 10px;
  fill: var(--color-text-secondary);
}
.bar-label {
  font-size: 9px;
  fill: var(--color-text-muted);
}

/* 表格 */
.table-wrap {
  overflow-x: auto;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}
.data-table th {
  text-align: left;
  padding: 8px 12px;
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  font-weight: 600;
  font-size: 0.82rem;
  border-bottom: 1px solid var(--color-border);
}
.data-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text-primary);
}
.data-table tr:last-child td { border-bottom: 0; }
.num-col {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.positive { color: var(--color-success); }
.negative { color: var(--color-danger); }

/* P0-A 决策闭环：采纳率 + 收益对比 */
.adoption-section { padding: var(--space-5); }
.adoption-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 2fr;
  gap: var(--space-5);
  align-items: stretch;
}
.adoption-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
}

/* 采纳率环形进度 */
.adoption-ring {
  position: relative;
  width: 140px;
  height: 140px;
  flex-shrink: 0;
}
.ring-track,
.ring-fill {
  position: absolute;
  inset: 0;
  border-radius: 50%;
}
.ring-track {
  background: conic-gradient(var(--color-border) 0deg 360deg);
  -webkit-mask: radial-gradient(circle, transparent 56px, #000 58px);
  mask: radial-gradient(circle, transparent 56px, #000 58px);
}
.ring-fill {
  background: conic-gradient(var(--color-primary) 0deg calc(var(--ring-pct) * 3.6deg), transparent 0deg);
  -webkit-mask: radial-gradient(circle, transparent 56px, #000 58px);
  mask: radial-gradient(circle, transparent 56px, #000 58px);
  transition: background 0.6s ease;
}
.ring-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
}
.ring-num {
  font-size: 1.6em;
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1;
}
.ring-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.adoption-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}
.meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.82rem;
}
.meta-row strong { font-size: 0.95rem; }

/* 采纳 vs 未采纳收益对比 */
.compare-block { gap: var(--space-4); }
.compare-row {
  display: flex;
  align-items: stretch;
  gap: var(--space-3);
  width: 100%;
}
.compare-side {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
}
.compare-side.adopted { border-left: 3px solid var(--color-success); }
.compare-side.rejected { border-left: 3px solid var(--color-text-muted); }
.compare-label {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
.compare-value {
  font-size: 1.5em;
  font-weight: 700;
  line-height: 1.1;
}
.compare-sub {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.compare-vs {
  display: flex;
  align-items: center;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--color-text-muted);
  letter-spacing: 1px;
}
.compare-delta {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  font-size: 0.9rem;
  font-weight: 600;
  width: 100%;
}
.compare-delta.delta-positive {
  background: rgba(16, 185, 129, 0.08);
  color: var(--color-success);
}
.compare-delta.delta-negative {
  background: rgba(239, 68, 68, 0.08);
  color: var(--color-danger);
}
.compare-delta.delta-neutral {
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
}
.delta-hint { font-weight: 400; font-size: 0.78rem; }

/* P0-A 决策闭环：最近验证结果列表 */
.recent-section { padding: var(--space-5); }
.recent-table { font-size: 0.85rem; }
.recent-table th { white-space: nowrap; }
.cell-target {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.target-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.target-code {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.cell-time {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

/* 方向徽章 */
.dir-badge {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  font-weight: 600;
  white-space: nowrap;
}
.dir-badge.dir-up {
  background: rgba(16, 185, 129, 0.12);
  color: var(--color-success);
}
.dir-badge.dir-down {
  background: rgba(239, 68, 68, 0.12);
  color: var(--color-danger);
}
.dir-badge.dir-hold {
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
}

/* 结果徽章 */
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  font-weight: 600;
  white-space: nowrap;
}
.status-badge.st-correct {
  background: rgba(16, 185, 129, 0.12);
  color: var(--color-success);
}
.status-badge.st-wrong {
  background: rgba(239, 68, 68, 0.12);
  color: var(--color-danger);
}
.status-badge.st-flat {
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
}

/* 采纳徽章 */
.adopted-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  font-weight: 600;
  white-space: nowrap;
}
.adopted-badge.ad-adopted {
  background: rgba(16, 185, 129, 0.12);
  color: var(--color-success);
}
.adopted-badge.ad-rejected {
  background: rgba(239, 68, 68, 0.1);
  color: var(--color-danger);
}
.adopted-badge.ad-unmarked {
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
}

@media (max-width: 768px) {
  .accuracy-page { padding: var(--space-3); }
  .page-header { flex-direction: column; }
  .header-actions { width: 100%; }
  .overall-hero { flex-direction: column; text-align: center; }
  .rate-circle { width: 90px; height: 90px; }
  .rate-number { font-size: 1.3em; }
  .data-table { font-size: 0.8rem; }
  .data-table th, .data-table td { padding: 6px 8px; }
  .adoption-grid { grid-template-columns: 1fr; }
  .compare-row { flex-direction: column; }
  .compare-vs { justify-content: center; padding: 4px 0; }
  .recent-table { font-size: 0.78rem; }
}
</style>
