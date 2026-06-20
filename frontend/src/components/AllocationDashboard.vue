<script setup>
import { computed, onMounted, ref } from 'vue'
import { getAllocationDashboard } from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'

const { showToast } = useToast()

const loading = ref(false)
const dashboard = ref(null)

const rows = computed(() => dashboard.value?.allocation_rows || [])
const suggestions = computed(() => dashboard.value?.suggestions || [])
const guardrails = computed(() => dashboard.value?.guardrails || [])
const topDrift = computed(() => dashboard.value?.top_drift || null)

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function ratio(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`
}

function driftClass(row) {
  if (row.level === 'significant') return 'danger'
  if (row.level === 'slight') return 'warning'
  return 'ok'
}

function driftText(row) {
  const value = Number(row.drift || 0)
  const prefix = value > 0 ? '+' : ''
  return `${prefix}${ratio(value)}`
}

function actionLabel(action) {
  return {
    deploy_cash: '配置现金',
    reserve_cash: '补现金',
    buy: '加仓',
    sell: '减仓',
    buy_index: '指数定投',
  }[action] || action
}

async function load() {
  loading.value = true
  try {
    const { data } = await getAllocationDashboard()
    dashboard.value = data
  } catch (e) {
    showToast('加载组合偏离失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="allocation-page">
    <header class="page-head">
      <div>
        <h2 class="page-title">目标配置 / 偏离度</h2>
        <p class="page-desc">把当前组合和目标配置放在同一张表里，先看偏离，再看是否需要行动。</p>
      </div>
      <button class="btn-secondary" :disabled="loading" @click="load">
        <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
        刷新
      </button>
    </header>

    <div v-if="loading && !dashboard" class="empty-panel">
      <Icon name="spinner" size="22" />
      <span>正在计算组合偏离...</span>
    </div>

    <div v-else-if="dashboard?.status === 'empty'" class="empty-panel">
      <Icon name="pie-chart" size="26" />
      <span>{{ dashboard.error || '暂无持仓数据' }}</span>
    </div>

    <template v-else-if="dashboard">
      <section class="metric-strip">
        <div class="metric-cell">
          <span>总资产</span>
          <strong>¥{{ money(dashboard.total_assets) }}</strong>
        </div>
        <div class="metric-cell">
          <span>现金余额</span>
          <strong>¥{{ money(dashboard.cash_balance) }}</strong>
        </div>
        <div class="metric-cell">
          <span>最大偏离</span>
          <strong>{{ ratio(dashboard.max_drift) }}</strong>
        </div>
        <div class="metric-cell">
          <span>市场估值</span>
          <strong>{{ dashboard.market_level || '-' }}</strong>
        </div>
      </section>

      <section v-if="guardrails.length" class="guardrail-list">
        <div v-for="item in guardrails" :key="item" class="guardrail-item">
          <Icon name="shield-alert" size="15" />
          {{ item }}
        </div>
      </section>

      <main class="allocation-layout">
        <section class="allocation-table-panel">
          <div class="section-title">
            <h3>配置偏离</h3>
            <span v-if="topDrift">最大偏离：{{ topDrift.label }}</span>
          </div>
          <div class="allocation-table">
            <div class="table-row table-head">
              <span>资产类别</span>
              <span>当前</span>
              <span>目标</span>
              <span>偏离</span>
              <span>金额差</span>
            </div>
            <div v-for="row in rows" :key="row.category" class="table-row">
              <div class="asset-name">
                <strong>{{ row.label }}</strong>
                <small>{{ row.category }}</small>
              </div>
              <div>
                <strong>{{ ratio(row.current_ratio) }}</strong>
                <small>¥{{ money(row.current_amount) }}</small>
              </div>
              <div>
                <strong>{{ ratio(row.target_ratio) }}</strong>
                <small>¥{{ money(row.target_amount) }}</small>
              </div>
              <div :class="['drift-pill', driftClass(row)]">{{ driftText(row) }}</div>
              <div>
                <strong>¥{{ money(row.drift_amount) }}</strong>
              </div>
            </div>
          </div>
        </section>

        <aside class="suggestion-panel">
          <div class="section-title">
            <h3>建议路径</h3>
            <span>{{ suggestions.length }} 条</span>
          </div>
          <div v-if="!suggestions.length" class="quiet-empty">当前偏离不明显，保持观察即可。</div>
          <article v-for="item in suggestions" :key="`${item.priority}-${item.action}-${item.category}`" class="suggestion-card">
            <div class="suggestion-top">
              <span class="action-badge">{{ actionLabel(item.action) }}</span>
              <span>{{ item.amount_range || '-' }}</span>
            </div>
            <p>{{ item.reason }}</p>
            <small v-if="item.fund_name">{{ item.fund_name }} {{ item.fund_code }}</small>
            <div v-if="item.guardrail_note" class="suggestion-guardrail">
              <Icon name="warning" size="13" />
              {{ item.guardrail_note }}
            </div>
          </article>
        </aside>
      </main>
    </template>
  </div>
</template>

<style scoped>
.allocation-page {
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

.metric-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.metric-cell {
  padding: var(--space-4);
  border-right: 1px solid var(--color-border-light);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.metric-cell:last-child { border-right: 0; }
.metric-cell span {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}
.metric-cell strong {
  color: var(--color-text-primary);
  font-size: 1.1rem;
}

.guardrail-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.guardrail-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  color: var(--color-warning-text);
  background: var(--color-warning-bg);
  border: 1px solid var(--color-warning-border);
  border-radius: var(--radius-md);
}

.allocation-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: var(--space-5);
  align-items: start;
}

.allocation-table-panel,
.suggestion-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
}

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.section-title h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: 1rem;
}
.section-title span {
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.allocation-table {
  display: flex;
  flex-direction: column;
  gap: 1px;
  overflow-x: auto;
}
.table-row {
  display: grid;
  grid-template-columns: 1.4fr repeat(4, minmax(96px, 1fr));
  align-items: center;
  gap: var(--space-3);
  min-width: 720px;
  padding: 12px;
  background: var(--color-bg-card);
  border-bottom: 1px solid var(--color-border-light);
}
.table-head {
  color: var(--color-text-muted);
  font-size: 0.78rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
}
.table-row strong {
  display: block;
  color: var(--color-text-primary);
}
.table-row small {
  display: block;
  margin-top: 3px;
  color: var(--color-text-muted);
}
.asset-name strong {
  font-size: 0.95rem;
}

.drift-pill {
  width: fit-content;
  min-width: 72px;
  text-align: center;
  padding: 5px 9px;
  border-radius: var(--radius-sm);
  font-weight: 700;
}
.drift-pill.ok {
  color: var(--color-success);
  background: var(--color-success-bg);
}
.drift-pill.warning {
  color: var(--color-warning-text);
  background: var(--color-warning-bg);
}
.drift-pill.danger {
  color: var(--color-danger);
  background: var(--color-danger-bg);
}

.suggestion-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.suggestion-card {
  border: 1px solid var(--color-border-light);
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  padding: var(--space-3);
}
.suggestion-top {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  align-items: center;
  color: var(--color-text-secondary);
  font-size: 0.8rem;
}
.action-badge {
  color: var(--color-primary);
  background: var(--color-primary-bg);
  border: 1px solid var(--color-primary-border);
  border-radius: var(--radius-sm);
  padding: 3px 7px;
  font-weight: 700;
}
.suggestion-card p {
  margin: 10px 0 6px;
  color: var(--color-text-primary);
  line-height: 1.6;
}
.suggestion-card small {
  color: var(--color-text-muted);
}
.suggestion-guardrail {
  margin-top: 10px;
  display: flex;
  gap: 6px;
  color: var(--color-warning-text);
  font-size: 0.78rem;
}

.empty-panel,
.quiet-empty {
  min-height: 180px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}
.quiet-empty {
  min-height: 120px;
  padding: var(--space-4);
}

@media (max-width: 1100px) {
  .allocation-layout { grid-template-columns: 1fr; }
}

@media (max-width: 760px) {
  .allocation-page { padding: var(--space-4); }
  .page-head { flex-direction: column; }
  .metric-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-cell:nth-child(2) { border-right: 0; }
  .metric-cell:nth-child(-n + 2) { border-bottom: 1px solid var(--color-border-light); }
}
</style>
