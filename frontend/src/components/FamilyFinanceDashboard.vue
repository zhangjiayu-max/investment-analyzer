<script setup>
import { computed, onMounted, ref } from 'vue'
import { getFinanceDashboard, getFinanceTrend } from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'

const { showToast } = useToast()

const loading = ref(false)
const data = ref(null)
const trend = ref(null)
const trendLoading = ref(false)

const nw = computed(() => data.value?.net_worth || {})
const cf = computed(() => data.value?.cash_flow || {})
const debt = computed(() => data.value?.debt || {})
const goals = computed(() => data.value?.goals || {})
const alloc = computed(() => data.value?.allocation || {})
const risk = computed(() => data.value?.risk || {})
const warnings = computed(() => data.value?.health_warnings || [])

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function pct(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`
}
function pnlClass(value) {
  return Number(value) >= 0 ? 'positive' : 'negative'
}

function progressColor(pct) {
  if (pct >= 100) return 'var(--color-success)'
  if (pct >= 60) return 'var(--color-primary)'
  return 'var(--color-warning-text)'
}

async function load() {
  loading.value = true
  try {
    const { data: res } = await getFinanceDashboard()
    data.value = res
  } catch (e) {
    showToast('加载财务总览失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
  // 异步加载趋势
  loadTrend()
}

async function loadTrend() {
  trendLoading.value = true
  try {
    const { data: res } = await getFinanceTrend(12)
    trend.value = res
  } catch (e) {
    console.error('加载趋势失败:', e)
  } finally {
    trendLoading.value = false
  }
}

// ── 趋势图 ──
const trendChartWidth = 700
const trendChartHeight = 160

const trendChartPaths = computed(() => {
  if (!trend.value || !trend.value.trend?.length) return null
  const items = trend.value.trend
  const maxVal = Math.max(...items.map(i => i.cumulative_invested), 1) * 1.1
  const len = items.length
  if (len < 2) return null

  const path = items.map((item, i) => {
    const x = (i / (len - 1)) * trendChartWidth
    const y = trendChartHeight - (item.cumulative_invested / maxVal) * trendChartHeight
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  // 填充区域路径
  const areaPath = path + ` L${trendChartWidth},${trendChartHeight} L0,${trendChartHeight} Z`

  return { line: path, area: areaPath, labels: items }
})

onMounted(load)
</script>

<template>
  <div class="finance-page">
    <header class="page-head bg-mesh">
      <div>
        <h2 class="page-title editorial-title-lg">财务总览</h2>
        <p class="page-desc">家庭资产负债全景，一眼看清财务健康状态。</p>
      </div>
      <button class="btn-secondary" :disabled="loading" @click="load">
        <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
        刷新
      </button>
    </header>

    <div v-if="loading && !data" class="empty-panel">
      <Icon name="spinner" size="22" />
      <span>正在加载财务数据...</span>
    </div>

    <template v-else-if="data">
      <!-- 健康状态提示 -->
      <section v-if="warnings.length" class="health-warnings">
        <div v-for="w in warnings" :key="w" class="warning-item">
          <Icon name="alert-triangle" size="15" />
          {{ w }}
        </div>
      </section>

      <!-- 六大模块 -->
      <div class="dashboard-grid">
        <!-- 1. 净值总览 -->
        <section class="card net-worth-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="wallet" size="18" />
            <h3 class="title">净值总览</h3>
          </div>
          <div class="card-body">
            <div class="hero-metric">
              <span class="terminal-label">总资产</span>
              <strong class="font-jet-lg">¥{{ money(nw.total_assets) }}</strong>
            </div>
            <div class="metric-grid">
              <div class="metric-item">
                <span class="terminal-label">现金</span>
                <strong class="font-jet">¥{{ money(nw.cash_balance) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">持仓市值</span>
                <strong class="font-jet">¥{{ money(nw.holding_value) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">总成本</span>
                <strong class="font-jet">¥{{ money(nw.total_cost) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">浮盈亏</span>
                <strong class="font-jet-lg" :class="pnlClass(nw.float_pnl)">¥{{ money(nw.float_pnl) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">总收益率</span>
                <strong class="font-jet-lg" :class="pnlClass(nw.total_return_pct)">{{ pct(nw.total_return_pct) }}</strong>
              </div>
            </div>
          </div>
        </section>

        <!-- 2. 现金流 -->
        <section class="card cashflow-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="trending-up" size="18" />
            <h3 class="title">现金流</h3>
          </div>
          <div class="card-body">
            <div class="metric-grid three-col">
              <div class="metric-item">
                <span class="terminal-label">月收入</span>
                <strong class="font-jet">¥{{ money(cf.monthly_income) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">月支出</span>
                <strong class="font-jet">¥{{ money(cf.monthly_expense) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">月结余</span>
                <strong class="font-jet" :class="pnlClass(cf.monthly_surplus)">¥{{ money(cf.monthly_surplus) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">结余率</span>
                <strong class="font-jet">{{ pct(cf.surplus_rate) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">备用金覆盖</span>
                <strong class="font-jet">{{ cf.emergency_fund_months || '-' }} 个月</strong>
              </div>
            </div>
          </div>
        </section>

        <!-- 3. 负债 -->
        <section class="card debt-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="credit-card" size="18" />
            <h3 class="title">负债</h3>
          </div>
          <div class="card-body">
            <div v-if="debt.debt_summary" class="debt-summary">{{ debt.debt_summary }}</div>
            <div v-else class="no-data">暂无负债信息</div>
            <div class="metric-grid two-col">
              <div class="metric-item">
                <span class="terminal-label">月供</span>
                <strong class="font-jet">¥{{ money(debt.monthly_debt_payment) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">负债收入比</span>
                <strong class="font-jet">{{ pct(debt.debt_to_income) }}</strong>
              </div>
            </div>
          </div>
        </section>

        <!-- 4. 目标进度 -->
        <section class="card goals-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="target" size="18" />
            <h3 class="title">目标进度</h3>
            <span class="card-meta meta">
              共 {{ goals.buckets?.length || 0 }} 个目标，
              目标 ¥{{ money(goals.total_target) }}
            </span>
          </div>
          <div class="card-body">
            <div v-if="!goals.buckets?.length" class="no-data">暂无资金桶目标</div>
            <div v-else class="goal-list">
              <div v-for="b in goals.buckets" :key="b.id" class="goal-item">
                <div class="goal-top">
                  <span class="goal-name">{{ b.name }}</span>
                  <span class="goal-pct font-jet" :style="{ color: progressColor(b.progress_pct) }">
                    {{ b.progress_pct.toFixed(1) }}%
                  </span>
                </div>
                <div class="goal-bar-bg">
                  <div
                    class="goal-bar-fill"
                    :style="{
                      width: Math.min(b.progress_pct, 100) + '%',
                      background: progressColor(b.progress_pct),
                    }"
                  />
                </div>
                <div class="goal-detail font-jet">
                  ¥{{ money(b.current_amount) }} / ¥{{ money(b.target_amount) }}
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- 5. 投资状态 -->
        <section class="card alloc-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="pie-chart" size="18" />
            <h3 class="title">投资配置</h3>
          </div>
          <div class="card-body">
            <div class="metric-grid three-col">
              <div class="metric-item">
                <span class="terminal-label">最大偏离</span>
                <strong class="font-jet">{{ pct(alloc.max_drift) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">市场估值</span>
                <strong class="font-jet">{{ alloc.market_level || '-' }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">护栏提示</span>
                <strong class="font-jet">{{ alloc.guardrails_count || 0 }} 条</strong>
              </div>
            </div>
            <div v-if="alloc.top_drift" class="top-drift">
              最大偏离资产：<strong class="font-jet">{{ alloc.top_drift.label }}</strong>
            </div>
          </div>
        </section>

        <!-- 6. 风险视图 -->
        <section class="card risk-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="shield" size="18" />
            <h3 class="title">风险视图</h3>
          </div>
          <div class="card-body">
            <div class="metric-grid three-col">
              <div class="metric-item">
                <span class="terminal-label">压力测试损失</span>
                <strong class="font-jet negative">¥{{ money(risk.stress_loss_amount) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">损失比例</span>
                <strong class="font-jet negative">{{ pct(risk.stress_loss_ratio) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">风险等级</span>
                <span :class="['risk-badge', risk.risk_level]">
                  {{ { high: '高', medium: '中', low: '低' }[risk.risk_level] || risk.risk_level }}
                </span>
              </div>
            </div>
            <div v-if="risk.warnings?.length" class="risk-warnings">
              <div v-for="w in risk.warnings" :key="w" class="risk-warning-item">
                <Icon name="alert-triangle" size="13" />
                {{ w }}
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- 投资趋势 -->
      <section class="card trend-card reveal-stagger editorial-card">
        <div class="card-head editorial-card-header">
          <Icon name="bar-chart" size="18" />
          <h3 class="title">投资趋势</h3>
          <span class="card-meta meta">近 12 个月累计投入</span>
        </div>
        <div class="card-body">
          <div v-if="trendLoading && !trend" class="trend-loading">
            <Icon name="spinner" size="20" />
            <span>正在加载趋势数据...</span>
          </div>
          <div v-else-if="!trend?.trend?.length" class="no-data">
            暂无交易记录，无法展示趋势
          </div>
          <template v-else>
            <div class="trend-summary">
              <div class="metric-item">
                <span class="terminal-label">累计投入</span>
                <strong class="font-jet">¥{{ money(trend.trend[trend.trend.length - 1]?.cumulative_invested || 0) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">当前净值</span>
                <strong class="font-jet">¥{{ money(trend.current_value) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">累计收益</span>
                <strong class="font-jet" :class="pnlClass(trend.current_value - trend.total_cost)">
                  {{ money(trend.current_value - trend.total_cost) }}
                  ({{ pct(trend.total_return) }})
                </strong>
              </div>
            </div>
            <div v-if="trendChartPaths" class="trend-chart-wrap">
              <svg :viewBox="`0 0 ${trendChartWidth} ${trendChartHeight}`" class="trend-chart" preserveAspectRatio="none">
                <defs>
                  <linearGradient :id="'trend-grad'" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#c9a84c" stop-opacity="0.3" />
                    <stop offset="100%" stop-color="#c9a84c" stop-opacity="0" />
                  </linearGradient>
                </defs>
                <path :d="trendChartPaths.area" :fill="`url(#trend-grad)`" />
                <path :d="trendChartPaths.line" fill="none" stroke="#c9a84c" stroke-width="2" />
              </svg>
              <div class="trend-labels">
                <span v-for="item in trendChartPaths.labels" :key="item.month" class="trend-label">
                  {{ item.month.slice(5) }}
                </span>
              </div>
            </div>
            <div v-else class="no-data">数据不足，至少需要 2 个月记录</div>
          </template>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.finance-page {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.empty-panel {
  min-height: 200px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}

/* ── 健康提示 ── */
.health-warnings {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.warning-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  color: var(--color-warning-text);
  background: var(--color-warning-bg);
  border: 1px solid var(--color-warning-border);
  border-radius: var(--radius-md);
  font-size: 0.88rem;
}

/* ── 六宫格布局 ── */
.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-5);
}

.card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text-primary);
}
.card-head h3 {
  margin: 0;
  font-size: 1rem;
  flex: 1;
}
.card-meta {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}

.card-body {
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* ── 指标 ── */
.hero-metric {
  text-align: center;
  padding: var(--space-3) 0;
}
.hero-metric span {
  color: var(--color-text-muted);
  font-size: 0.82rem;
  display: block;
  margin-bottom: 4px;
}
.hero-metric strong {
  color: var(--color-text-primary);
  font-size: 1.8rem;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}
.metric-grid.three-col { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.metric-grid.two-col { grid-template-columns: repeat(2, minmax(0, 1fr)); }

.metric-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.metric-item span {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}
.metric-item strong {
  color: var(--color-text-primary);
  font-size: 0.95rem;
}

.positive { color: var(--color-success) !important; }
.negative { color: var(--color-danger) !important; }

.no-data {
  color: var(--color-text-muted);
  font-size: 0.85rem;
  text-align: center;
  padding: var(--space-3);
}

/* ── 负债 ── */
.debt-summary {
  color: var(--color-text-primary);
  line-height: 1.6;
  font-size: 0.9rem;
}

/* ── 目标进度 ── */
.goal-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.goal-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.goal-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.goal-name {
  color: var(--color-text-primary);
  font-weight: 600;
  font-size: 0.88rem;
}
.goal-pct {
  font-weight: 700;
  font-size: 0.85rem;
}
.goal-bar-bg {
  height: 6px;
  background: var(--color-border-light);
  border-radius: 3px;
  overflow: hidden;
}
.goal-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.4s ease;
}
.goal-detail {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

/* ── 配置偏离 ── */
.top-drift {
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border-light);
}

/* ── 风险 ── */
.risk-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: var(--radius-sm);
  font-weight: 700;
  font-size: 0.82rem;
}
.risk-badge.ok, .risk-badge.low {
  color: var(--color-success);
  background: var(--color-success-bg);
}
.risk-badge.medium, .risk-badge.warning {
  color: var(--color-warning-text);
  background: var(--color-warning-bg);
}
.risk-badge.high, .risk-badge.danger {
  color: var(--color-danger);
  background: var(--color-danger-bg);
}

.risk-warnings {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.risk-warning-item {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-warning-text);
  font-size: 0.8rem;
}

@media (max-width: 960px) {
  .dashboard-grid { grid-template-columns: 1fr; }
}

/* ── 趋势 ── */
.trend-card {
  grid-column: 1 / -1;
}
.trend-loading {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}
.trend-summary {
  display: flex;
  gap: var(--space-5);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border-light);
  margin-bottom: var(--space-3);
}
.trend-chart-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.trend-chart {
  width: 100%;
  height: 160px;
}
.trend-labels {
  display: flex;
  justify-content: space-between;
  font-size: 0.68rem;
  color: var(--color-text-muted);
  overflow-x: auto;
}
.trend-label {
  white-space: nowrap;
}

@media (max-width: 760px) {
  .finance-page { padding: var(--space-4); }
  .page-head { flex-direction: column; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .hero-metric strong { font-size: 1.4rem; }
  .trend-summary { flex-wrap: wrap; gap: var(--space-3); }
  .trend-summary .metric-item { min-width: 45%; }
}

/* ── 移动端响应式 (<768px) ── */
@media (max-width: 768px) {
  .finance-page {
    padding: var(--space-3);
  }

  /* 页头 */
  .page-head {
    flex-direction: column;
    gap: var(--space-2);
  }
  .page-title { font-size: 1.1rem; }
  .page-desc { font-size: 0.78rem; }

  /* 六宫格 → 单列 */
  .dashboard-grid {
    grid-template-columns: 1fr;
    gap: var(--space-3);
  }

  /* 关键指标大字体卡片 */
  .hero-metric strong {
    font-size: 1.6rem;
  }
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--space-2);
  }
  .metric-grid.three-col {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .metric-item strong {
    font-size: 1.05rem;
  }
  .metric-item span {
    font-size: 0.8rem;
  }

  /* 趋势图横向滚动 */
  .trend-chart-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
  }
  .trend-chart {
    min-width: 600px;
  }
  .trend-labels {
    min-width: 600px;
  }
  .trend-summary {
    flex-wrap: wrap;
    gap: var(--space-3);
  }

  /* 健康提示 */
  .warning-item {
    font-size: 0.82rem;
    padding: 8px 12px;
  }

  /* 卡片 */
  .card-head {
    padding: var(--space-3);
  }
  .card-body {
    padding: var(--space-3);
  }

  /* 目标进度 */
  .goal-bar-bg {
    height: 8px;
  }
}

/* ══ 编辑式金融终端叠加适配 ══ */
/* page-head 氛围背景面板 */
.page-head.bg-mesh {
  padding: var(--space-5) var(--space-6);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
}
/* editorial-card-header 叠加到 card-head：消除重复下外边距，避免与 card-body 内边距叠加 */
.card-head.editorial-card-header {
  margin-bottom: 0;
}
/* 趋势卡汇总数字字号 */
.trend-card .trend-summary .metric-item strong {
  font-size: 1.05rem;
}

/* ── 移动端 ≤480px 字号适配 ── */
@media (max-width: 480px) {
  .page-head.bg-mesh {
    padding: var(--space-4);
  }
  .hero-metric strong.font-jet-lg {
    font-size: 1.3rem;
  }
  .metric-item strong.font-jet,
  .metric-item strong.font-jet-lg {
    font-size: 0.88rem;
  }
  .trend-card .trend-summary .metric-item strong {
    font-size: 0.95rem;
  }
  .terminal-label {
    font-size: 0.58rem;
  }
  .editorial-card-header .title {
    font-size: 0.92rem;
  }
  .goal-pct.font-jet {
    font-size: 0.8rem;
  }
}
</style>
