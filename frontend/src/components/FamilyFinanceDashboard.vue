<script setup>
import { computed, onMounted, ref } from 'vue'
import { getFinanceDashboard } from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'

const { showToast } = useToast()

const loading = ref(false)
const data = ref(null)

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
}

onMounted(load)
</script>

<template>
  <div class="finance-page">
    <header class="page-head">
      <div>
        <h2 class="page-title">财务总览</h2>
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
        <section class="card net-worth-card">
          <div class="card-head">
            <Icon name="wallet" size="18" />
            <h3>净值总览</h3>
          </div>
          <div class="card-body">
            <div class="hero-metric">
              <span>总资产</span>
              <strong>¥{{ money(nw.total_assets) }}</strong>
            </div>
            <div class="metric-grid">
              <div class="metric-item">
                <span>现金</span>
                <strong>¥{{ money(nw.cash_balance) }}</strong>
              </div>
              <div class="metric-item">
                <span>持仓市值</span>
                <strong>¥{{ money(nw.holding_value) }}</strong>
              </div>
              <div class="metric-item">
                <span>总成本</span>
                <strong>¥{{ money(nw.total_cost) }}</strong>
              </div>
              <div class="metric-item">
                <span>浮盈亏</span>
                <strong :class="pnlClass(nw.float_pnl)">¥{{ money(nw.float_pnl) }}</strong>
              </div>
              <div class="metric-item">
                <span>总收益率</span>
                <strong :class="pnlClass(nw.total_return_pct)">{{ pct(nw.total_return_pct) }}</strong>
              </div>
            </div>
          </div>
        </section>

        <!-- 2. 现金流 -->
        <section class="card cashflow-card">
          <div class="card-head">
            <Icon name="trending-up" size="18" />
            <h3>现金流</h3>
          </div>
          <div class="card-body">
            <div class="metric-grid three-col">
              <div class="metric-item">
                <span>月收入</span>
                <strong>¥{{ money(cf.monthly_income) }}</strong>
              </div>
              <div class="metric-item">
                <span>月支出</span>
                <strong>¥{{ money(cf.monthly_expense) }}</strong>
              </div>
              <div class="metric-item">
                <span>月结余</span>
                <strong :class="pnlClass(cf.monthly_surplus)">¥{{ money(cf.monthly_surplus) }}</strong>
              </div>
              <div class="metric-item">
                <span>结余率</span>
                <strong>{{ pct(cf.surplus_rate) }}</strong>
              </div>
              <div class="metric-item">
                <span>备用金覆盖</span>
                <strong>{{ cf.emergency_fund_months || '-' }} 个月</strong>
              </div>
            </div>
          </div>
        </section>

        <!-- 3. 负债 -->
        <section class="card debt-card">
          <div class="card-head">
            <Icon name="credit-card" size="18" />
            <h3>负债</h3>
          </div>
          <div class="card-body">
            <div v-if="debt.debt_summary" class="debt-summary">{{ debt.debt_summary }}</div>
            <div v-else class="no-data">暂无负债信息</div>
            <div class="metric-grid two-col">
              <div class="metric-item">
                <span>月供</span>
                <strong>¥{{ money(debt.monthly_debt_payment) }}</strong>
              </div>
              <div class="metric-item">
                <span>负债收入比</span>
                <strong>{{ pct(debt.debt_to_income) }}</strong>
              </div>
            </div>
          </div>
        </section>

        <!-- 4. 目标进度 -->
        <section class="card goals-card">
          <div class="card-head">
            <Icon name="target" size="18" />
            <h3>目标进度</h3>
            <span class="card-meta">
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
                  <span class="goal-pct" :style="{ color: progressColor(b.progress_pct) }">
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
                <div class="goal-detail">
                  ¥{{ money(b.current_amount) }} / ¥{{ money(b.target_amount) }}
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- 5. 投资状态 -->
        <section class="card alloc-card">
          <div class="card-head">
            <Icon name="pie-chart" size="18" />
            <h3>投资配置</h3>
          </div>
          <div class="card-body">
            <div class="metric-grid three-col">
              <div class="metric-item">
                <span>最大偏离</span>
                <strong>{{ pct(alloc.max_drift) }}</strong>
              </div>
              <div class="metric-item">
                <span>市场估值</span>
                <strong>{{ alloc.market_level || '-' }}</strong>
              </div>
              <div class="metric-item">
                <span>护栏提示</span>
                <strong>{{ alloc.guardrails_count || 0 }} 条</strong>
              </div>
            </div>
            <div v-if="alloc.top_drift" class="top-drift">
              最大偏离资产：<strong>{{ alloc.top_drift.label }}</strong>
            </div>
          </div>
        </section>

        <!-- 6. 风险视图 -->
        <section class="card risk-card">
          <div class="card-head">
            <Icon name="shield" size="18" />
            <h3>风险视图</h3>
          </div>
          <div class="card-body">
            <div class="metric-grid three-col">
              <div class="metric-item">
                <span>压力测试损失</span>
                <strong class="negative">¥{{ money(risk.stress_loss_amount) }}</strong>
              </div>
              <div class="metric-item">
                <span>损失比例</span>
                <strong class="negative">{{ pct(risk.stress_loss_ratio) }}</strong>
              </div>
              <div class="metric-item">
                <span>风险等级</span>
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

@media (max-width: 760px) {
  .finance-page { padding: var(--space-4); }
  .page-head { flex-direction: column; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .hero-metric strong { font-size: 1.4rem; }
}
</style>
