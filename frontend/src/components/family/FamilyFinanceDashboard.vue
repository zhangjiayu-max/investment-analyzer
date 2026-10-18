<script setup>
import { computed, onMounted, onActivated, onDeactivated, onBeforeUnmount, ref, watch } from 'vue'
import { getFinanceDashboard, getFinanceTrend } from '../../api'
import { useToast } from '../../composables/useToast'
import Icon from '../ui/Icon.vue'
import TermTooltip from '../ui/TermTooltip.vue'
import LineChart from '../charts/LineChart.vue'
import ExpenseManager from './ExpenseManager.vue'
import HouseholdCashflowCard from './HouseholdCashflowCard.vue'
import FixedBillsOverview from './FixedBillsOverview.vue'
import { shanghaiMonth } from '../../utils/expenseDates'

const { showToast } = useToast()

const loading = ref(false)
const data = ref(null)
const trend = ref(null)
const trendLoading = ref(false)
const expenseOpen = ref(false)
const selectedMonth = ref(shanghaiMonth())
let dashboardSequence = 0
let trendSequence = 0
let activatedBefore = false

const nw = computed(() => data.value?.net_worth || {})
const cf = computed(() => data.value?.cash_flow || null)
const debt = computed(() => data.value?.debt || {})
const goals = computed(() => data.value?.goals || {})
const alloc = computed(() => data.value?.allocation || {})
const risk = computed(() => data.value?.risk || {})
const cashAccounts = computed(() => nw.value.cash_accounts || [])
const currentMonth = computed(() => shanghaiMonth())
const historicalSelection = computed(() => selectedMonth.value && selectedMonth.value !== currentMonth.value)
const cashScopeLabel = computed(() => ({ household_accounts: '分账户现金', legacy_default_cash: '旧版家庭现金（未分账户）', user_cash: '当前用户现金' }[nw.value.cash_scope] || '现金来源待确认'))

const warnings = computed(() => data.value?.health_warnings || [])

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function assetMoney(value) {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function pct(value) {
  return value == null ? '待确认' : `${(Number(value) * 100).toFixed(1)}%`
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
  const month = selectedMonth.value
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) return
  const sequence = ++dashboardSequence
  loading.value = true
  try {
    const { data: res } = await getFinanceDashboard(month)
    if (sequence !== dashboardSequence) return
    data.value = res
  } catch (e) {
    if (sequence !== dashboardSequence) return
    data.value = null
    showToast('加载财务总览失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    if (sequence === dashboardSequence) loading.value = false
  }
  // 异步加载趋势
  if (sequence === dashboardSequence) loadTrend()
}

async function loadTrend() {
  const sequence = ++trendSequence
  trendLoading.value = true
  try {
    const { data: res } = await getFinanceTrend(12)
    if (sequence === trendSequence) trend.value = res
  } catch (e) {
    console.error('加载趋势失败:', e)
  } finally {
    if (sequence === trendSequence) trendLoading.value = false
  }
}

// ── 趋势图（ECharts LineChart 数据） ──
const trendChartReady = computed(() => {
  if (!trend.value || !trend.value.trend?.length) return false
  return trend.value.trend.length >= 2
})

const trendDates = computed(() => {
  if (!trend.value?.trend) return []
  return trend.value.trend.map(i => i.month)
})

const trendSeries = computed(() => {
  if (!trend.value?.trend) return []
  return [{
    name: '累计投入',
    data: trend.value.trend.map(i => i.cumulative_invested),
    color: 'rgb(201,168,76)',
  }]
})

onMounted(load)
onActivated(() => { if (activatedBefore) load(); activatedBefore = true })
watch(selectedMonth, month => { if (/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) load(); else { ++dashboardSequence; loading.value = false } })
onDeactivated(() => { ++dashboardSequence; ++trendSequence })
onBeforeUnmount(() => { ++dashboardSequence; ++trendSequence })
</script>

<template>
  <div class="finance-page">
    <header class="page-head bg-mesh">
      <div>
        <h2 class="page-title editorial-title-lg">财务总览</h2>
        <p class="page-desc">家庭资产负债全景，一眼看清财务健康状态。</p>
      </div>
      <label class="month-selector">收支月份<input v-model="selectedMonth" type="month" aria-label="收支月份" /></label>
      <button class="btn-secondary" :disabled="loading" @click="load">
        <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
        刷新
      </button>
      <button class="btn-primary" @click="expenseOpen = true">
        <Icon name="wallet" size="16" />
        记录支出
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
        <!-- 1. 资产总览 -->
        <section class="card net-worth-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="wallet" size="18" />
            <h3 class="title"><TermTooltip label="资产总览" content="已登记现金与基金资产汇总；负债余额未完整登记，不能据此确认家庭净资产" /></h3>
          </div>
          <div class="card-body">
            <div class="hero-metric">
              <span class="terminal-label">{{ historicalSelection ? '总资产（当前）' : '总资产' }}</span>
              <strong class="font-jet-lg">¥{{ assetMoney(nw.total_assets) }}</strong>
              <span class="asset-scope">{{ historicalSelection ? '当前资产快照；收支按所选月份展示' : '与持仓管理「全部账户」口径一致' }}</span>
            </div>
            <div class="asset-composition">
              <p>持仓市值 ¥{{ assetMoney(nw.holding_value) }} + 现金 ¥{{ assetMoney(nw.cash_balance) }} = 总资产 ¥{{ assetMoney(nw.total_assets) }}</p>
              <div class="cash-breakdown"><span>{{ cashScopeLabel }}</span><span v-for="account in cashAccounts" :key="account.account">{{ account.account === 'default' ? '默认账户' : account.account }} ¥{{ assetMoney(account.balance) }}</span></div>
              <p class="asset-scope-note">仅汇总已登记投资资产；未涵盖未登记资产，负债余额尚未完整录入，不能视为完整家庭净资产。</p>
            </div>
            <div class="metric-grid">
              <div class="metric-item">
                <span class="terminal-label">现金</span>
                <strong class="font-jet">¥{{ assetMoney(nw.cash_balance) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">持仓市值</span>
                <strong class="font-jet">¥{{ assetMoney(nw.holding_value) }}</strong>
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

        <!-- 2. 现金流：与资金桶、健康诊断共用相同口径 -->
        <HouseholdCashflowCard :cashflow="cf" @edit-budget="expenseOpen = true" />

        <!-- 3. 负债 -->
        <section class="card debt-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="credit-card" size="18" />
            <h3 class="title"><TermTooltip label="贷款还款" content="仅统计贷款还款，不包含保险等非贷款支出；月供不能推算贷款本金，贷款余额待录入" /></h3>
          </div>
          <div class="card-body">
            <div v-if="debt.liabilities_status === 'unknown' || data.household_diagnosis?.liabilities_status === 'unknown'" class="debt-summary">负债余额未录入，当前未知；月供仅反映现金支出。</div>
            <div v-else-if="debt.debt_summary" class="debt-summary">{{ debt.debt_summary }}</div>
            <div v-else class="no-data">负债余额未录入，当前未知</div>
            <div class="metric-grid two-col">
              <div class="metric-item">
                <span class="terminal-label">已还贷款</span>
                <strong class="font-jet">¥{{ assetMoney(debt.recorded_debt_payment ?? debt.monthly_debt_payment) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">待还贷款</span>
                <strong class="font-jet">¥{{ assetMoney(debt.pending_debt_payment) }}</strong>
              </div>
              <div class="metric-item">
                <span class="terminal-label">本月贷款还款</span>
                <strong class="font-jet">¥{{ assetMoney(debt.monthly_debt_payment ?? ((debt.recorded_debt_payment || 0) + (debt.pending_debt_payment || 0))) }}</strong>
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
            <h3 class="title"><TermTooltip label="目标进度" content="各财务目标（如备用金、购房、教育）的资金桶达成进度" /></h3>
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

        <!-- 5. 投资配置 -->
        <section class="card alloc-card reveal-stagger editorial-card">
          <div class="card-head editorial-card-header">
            <Icon name="pie-chart" size="18" />
            <h3 class="title"><TermTooltip label="投资配置" content="当前资产配置比例与目标配比的偏离度，含市场估值水平与护栏触发数" /></h3>
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
                <TermTooltip label="护栏提示" content="基于您的风险偏好和持仓上限自动触发的风险提示规则数量" />
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
            <h3 class="title"><TermTooltip label="风险视图" content="压力测试下的最大损失估算与风险等级，含在险金额与回撤比例" /></h3>
          </div>
          <div class="card-body">
            <div class="metric-grid three-col">
              <div class="metric-item">
                <TermTooltip label="压力测试损失" content="模拟极端市场环境（如2008/2015级下跌）下组合的预期最大损失" />
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

      <FixedBillsOverview :month="selectedMonth" :cashflow="cf" />

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
            <div v-if="trendChartReady" class="trend-chart-wrap">
              <LineChart
                :dates="trendDates"
                :series="trendSeries"
                :y-names="['累计投入']"
                :area="true"
                :smooth="true"
                :zoomable="false"
                height="180px"
              />
            </div>
            <div v-else class="no-data">数据不足，至少需要 2 个月记录</div>
          </template>
        </div>
      </section>
    </template>
    <HouseholdCashflowCard v-if="!loading && !data" :cashflow="null" />
    <ExpenseManager v-model:open="expenseOpen" :initial-month="selectedMonth" @updated="load" />
  </div>
</template>

<style scoped>
.asset-composition { padding: var(--space-3); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); }
.asset-composition p { margin: 0; font-size: .78rem; line-height: 1.7; }
.cash-breakdown { display: flex; flex-wrap: wrap; gap: .35rem .8rem; margin-top: .4rem; font-size: .75rem; color: var(--color-text-secondary); }
.asset-composition .asset-scope-note { margin-top: .5rem; font-size: .72rem; color: var(--color-text-muted); }
.hero-metric .asset-scope { margin-top: .45rem; color: var(--color-text-secondary); font-size: .72rem; }
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
.month-selector { display: inline-flex; align-items: center; gap: .5rem; margin-left: auto; font-size: .8rem; color: var(--color-text-secondary); }
.month-selector input { width: 150px; }

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
.cashflow-source { color: var(--color-text-muted); font-size: 0.72rem; }

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
