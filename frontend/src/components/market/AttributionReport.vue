<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  getAttributionReport,
  getAttributionByCategory,
  getAttributionContributors,
} from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'
import EmptyState from './ui/EmptyState.vue'

const { showToast } = useToast()

// ── 日期范围（默认近 90 天）──
function fmt(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
const today = new Date()
const ninetyAgo = new Date(today)
ninetyAgo.setDate(ninetyAgo.getDate() - 90)

const startDate = ref(fmt(ninetyAgo))
const endDate = ref(fmt(today))

// ── 数据状态 ──
const loading = ref(false)
const report = ref(null)
const categories = ref([])
const contributors = ref([])
const contributorsOrder = ref('desc') // desc=Top 贡献, asc=拖累

// ── 汇总数字卡片 ──
const summaryCards = computed(() => {
  const r = report.value || {}
  return [
    { key: 'total', label: '总收益', value: r.total_return, suffix: '%', positive: (r.total_return || 0) >= 0 },
    { key: 'selection', label: '选股效应', value: r.selection_effect, suffix: '%', positive: (r.selection_effect || 0) >= 0 },
    { key: 'timing', label: '择时效应', value: r.timing_effect, suffix: '%', positive: (r.timing_effect || 0) >= 0 },
    { key: 'interaction', label: '交互效应', value: r.interaction_effect, suffix: '%', positive: (r.interaction_effect || 0) >= 0 },
  ]
})

function pct(v) {
  const n = Number(v || 0)
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

function weightFmt(v) {
  const n = Number(v || 0)
  return `${(n * 100).toFixed(1)}%`
}

// ── 加载数据 ──
async function loadAll() {
  loading.value = true
  try {
    const [reportResp, catResp, contribResp] = await Promise.allSettled([
      getAttributionReport(startDate.value, endDate.value),
      getAttributionByCategory('90d'),
      getAttributionContributors(10, contributorsOrder.value),
    ])
    if (reportResp.status === 'fulfilled') {
      const d = reportResp.value.data
      report.value = d.result || d
    }
    if (catResp.status === 'fulfilled') {
      categories.value = catResp.value.data.items || catResp.value.data.categories || []
    }
    if (contribResp.status === 'fulfilled') {
      contributors.value = contribResp.value.data.items || contribResp.value.data.contributors || []
    }
  } catch (e) {
    showToast('加载归因报告失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

async function reloadContributors() {
  try {
    const { data } = await getAttributionContributors(10, contributorsOrder.value)
    contributors.value = data.items || data.contributors || []
  } catch (e) {
    showToast('加载贡献列表失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

function toggleContributorsOrder() {
  contributorsOrder.value = contributorsOrder.value === 'desc' ? 'asc' : 'desc'
  reloadContributors()
}

onMounted(loadAll)
</script>

<template>
  <div class="attribution-page bg-mesh">
    <header class="page-header">
      <div>
        <h2 class="page-title editorial-title-lg">收益归因报告</h2>
        <p class="page-desc">拆解组合收益来源：选股、择时与交互效应。</p>
      </div>
      <div class="header-actions">
        <label class="date-field">
          <span class="terminal-label">起始</span>
          <input v-model="startDate" type="date" class="input-field date-input" />
        </label>
        <label class="date-field">
          <span class="terminal-label">截止</span>
          <input v-model="endDate" type="date" class="input-field date-input" />
        </label>
        <button class="btn-primary" @click="loadAll" :disabled="loading">
          <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
          {{ loading ? '加载中...' : '查询' }}
        </button>
      </div>
    </header>

    <!-- 汇总卡片 -->
    <section v-if="report" class="summary-grid">
      <article
        v-for="card in summaryCards"
        :key="card.key"
        class="summary-card editorial-card reveal-stagger"
      >
        <span class="terminal-label">{{ card.label }}</span>
        <strong class="font-jet" :class="card.positive ? 'positive' : 'negative'">
          {{ pct(card.value) }}
        </strong>
      </article>
    </section>

    <!-- 加载中 -->
    <div v-if="loading && !report" class="loading-state editorial-card">
      <Icon name="spinner" size="22" />
      <span>正在生成归因报告...</span>
    </div>

    <!-- 空状态 -->
    <EmptyState
      v-else-if="!report && !loading"
      icon="chart"
      title="暂无归因数据"
      description="选择日期范围后点击查询，系统将拆解组合收益来源。"
    />

    <!-- 品类归因表格 -->
    <section v-if="categories.length" class="category-section editorial-card">
      <div class="section-head editorial-card-header">
        <h3 class="editorial-title">品类归因</h3>
        <span class="terminal-label">按品类拆解权重、收益率与贡献度</span>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>品类</th>
              <th class="num-col">权重</th>
              <th class="num-col">收益率</th>
              <th class="num-col">贡献度</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in categories" :key="c.category || c.name" class="reveal-stagger">
              <td>{{ c.category || c.name }}</td>
              <td class="num-col font-jet">{{ weightFmt(c.weight || c.weight_ratio) }}</td>
              <td class="num-col font-jet" :class="{ positive: (c.return || c.return_rate) >= 0, negative: (c.return || c.return_rate) < 0 }">
                {{ pct(c.return || c.return_rate) }}
              </td>
              <td class="num-col font-jet" :class="{ positive: (c.contribution || 0) >= 0, negative: (c.contribution || 0) < 0 }">
                {{ pct(c.contribution) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Top 贡献 / 拖累 -->
    <section v-if="contributors.length" class="contributors-section editorial-card">
      <div class="section-head editorial-card-header">
        <h3 class="editorial-title">
          {{ contributorsOrder === 'desc' ? 'Top 贡献标的' : '拖累标的' }}
        </h3>
        <button class="btn-ghost btn-sm" @click="toggleContributorsOrder">
          <Icon name="refresh" size="14" />
          切换为 {{ contributorsOrder === 'desc' ? '拖累榜' : '贡献榜' }}
        </button>
      </div>
      <div class="contributor-list">
        <article
          v-for="(item, idx) in contributors"
          :key="idx"
          class="contributor-item reveal-stagger"
        >
          <div class="contributor-main">
            <strong>{{ item.name || item.target_name || item.code }}</strong>
            <span class="terminal-label">{{ item.category || item.code || '' }}</span>
          </div>
          <div class="contributor-metrics">
            <span class="terminal-label">贡献度</span>
            <strong class="font-jet" :class="{ positive: (item.contribution || 0) >= 0, negative: (item.contribution || 0) < 0 }">
              {{ pct(item.contribution) }}
            </strong>
          </div>
        </article>
      </div>
    </section>

    <!-- 品类/贡献空提示 -->
    <EmptyState
      v-if="report && !categories.length && !contributors.length && !loading"
      icon="empty"
      title="所选范围内暂无明细数据"
      description="尝试调整日期范围，或先在持仓管理中补全交易记录。"
    />
  </div>
</template>

<style scoped>
.attribution-page {
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
  align-items: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.date-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.date-field .terminal-label {
  font-size: inherit;
  color: var(--color-text-muted);
}
.date-input {
  padding: 6px 10px;
  font-size: 0.85rem;
  min-width: 140px;
}

/* 汇总卡片 */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}
.summary-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.summary-card .terminal-label {
  font-size: inherit;
  color: var(--color-text-muted);
}
.summary-card strong {
  font-size: 1.4rem;
  font-weight: 700;
}

.positive { color: var(--color-success); }
.negative { color: var(--color-danger); }

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

/* 表格 */
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
  gap: var(--space-2);
}
.section-head h3 { margin: 0; }

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

/* 贡献列表 */
.contributor-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.contributor-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  gap: var(--space-2);
}
.contributor-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.contributor-main strong {
  font-size: 0.88rem;
  color: var(--color-text-primary);
}
.contributor-metrics {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
.contributor-metrics .terminal-label {
  font-size: inherit;
  color: var(--color-text-muted);
}
.contributor-metrics strong {
  font-size: 1rem;
}

@media (max-width: 768px) {
  .attribution-page { padding: var(--space-3); }
  .page-header { flex-direction: column; }
  .header-actions { width: 100%; }
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .summary-card strong { font-size: 1.2rem; }
  .data-table { font-size: 0.8rem; }
  .data-table th, .data-table td { padding: 6px 8px; }
}
</style>
