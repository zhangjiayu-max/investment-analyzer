<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  getAccuracyStats,
  autoVerifyAccuracy,
  getAccuracyTrend,
} from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'
import EmptyState from './ui/EmptyState.vue'

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

// ── 加载数据 ──
async function loadAll() {
  loading.value = true
  try {
    const [statsResp, trendResp] = await Promise.allSettled([
      getAccuracyStats(periodDays.value, groupBy.value),
      getAccuracyTrend(12),
    ])
    if (statsResp.status === 'fulfilled') {
      const d = statsResp.value.data
      stats.value = d.result || d
    }
    if (trendResp.status === 'fulfilled') {
      const d = trendResp.value.data
      trend.value = d.items || d.trend || []
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

@media (max-width: 768px) {
  .accuracy-page { padding: var(--space-3); }
  .page-header { flex-direction: column; }
  .header-actions { width: 100%; }
  .overall-hero { flex-direction: column; text-align: center; }
  .rate-circle { width: 90px; height: 90px; }
  .rate-number { font-size: 1.3em; }
  .data-table { font-size: 0.8rem; }
  .data-table th, .data-table td { padding: 6px 8px; }
}
</style>
