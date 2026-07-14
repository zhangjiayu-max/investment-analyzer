<script setup>
import { computed, onMounted, ref } from 'vue'
import { getAllocationDashboard, runPortfolioStressTest, createDecision } from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'

const { showToast } = useToast()

const loading = ref(false)
const dashboard = ref(null)
const creatingDecision = ref(false)

const rows = computed(() => dashboard.value?.allocation_rows || [])
const suggestions = computed(() => dashboard.value?.suggestions || [])
const guardrails = computed(() => dashboard.value?.guardrails || [])
const topDrift = computed(() => dashboard.value?.top_drift || null)

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

// ── 创建决策 ──
const actionToDecisionType = {
  buy: 'add',
  buy_index: 'add',
  sell: 'reduce',
  deploy_cash: 'add',
  hold_cash: 'hold',
}

async function createDecisionFromSuggestion(item) {
  if (creatingDecision.value) return
  creatingDecision.value = true
  try {
    const decisionType = actionToDecisionType[item.action] || 'watch'
    const targetName = item.fund_name || item.category || ''
    const targetCode = item.fund_code || ''
    const summary = `${item.action === 'sell' ? '减仓' : '配置'} ${targetName || item.category}`.trim()
    const rationale = [item.reason, item.amount_range ? `建议金额: ${item.amount_range}` : ''].filter(Boolean).join('；')
    await createDecision({
      decision_type: decisionType,
      target_type: targetCode ? 'fund' : 'portfolio',
      target_code: targetCode,
      target_name: targetName,
      summary,
      rationale,
      source_type: 'allocation',
    })
    showToast('决策已创建，可在「理财决策 > 决策档案」中查看', 'success')
  } catch (e) {
    showToast('创建决策失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    creatingDecision.value = false
  }
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

// ── 压力测试 ──
const stressScenarios = [
  { key: 'market_drop_20', label: '市场下跌 20%', icon: 'trending-down' },
  { key: 'rate_up', label: '利率上行', icon: 'percent' },
  { key: 'liquidity_crunch', label: '流动性冲击', icon: 'droplets' },
]
const activeScenario = ref('market_drop_20')
const stressLoading = ref(false)
const stressResult = ref(null)

const maxLoss = computed(() => {
  if (!stressResult.value?.asset_impacts?.length) return 1
  return Math.max(...stressResult.value.asset_impacts.map(a => Math.abs(a.loss_amount || 0)), 1)
})

function riskLevelClass(level) {
  return { high: 'danger', medium: 'warning', low: 'ok' }[level] || 'ok'
}

function riskLevelText(level) {
  return { high: '高风险', medium: '中风险', low: '低风险' }[level] || level
}

async function runStress() {
  stressLoading.value = true
  stressResult.value = null
  try {
    const { data } = await runPortfolioStressTest(activeScenario.value)
    stressResult.value = data
  } catch (e) {
    showToast('压力测试失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    stressLoading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="allocation-page bg-mesh">
    <header class="page-head">
      <div>
        <h2 class="page-title editorial-title-lg">目标配置 / 偏离度</h2>
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
          <span class="terminal-label">总资产</span>
          <strong class="font-jet">¥{{ money(dashboard.total_assets) }}</strong>
        </div>
        <div class="metric-cell">
          <span class="terminal-label">现金余额</span>
          <strong class="font-jet">¥{{ money(dashboard.cash_balance) }}</strong>
        </div>
        <div class="metric-cell">
          <span class="terminal-label">最大偏离</span>
          <strong class="font-jet">{{ ratio(dashboard.max_drift) }}</strong>
        </div>
        <div class="metric-cell">
          <span class="terminal-label">市场估值</span>
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
        <section class="allocation-table-panel editorial-card">
          <div class="section-title">
            <h3 class="editorial-title">配置偏离</h3>
            <span v-if="topDrift" class="terminal-label">最大偏离：{{ topDrift.label }}</span>
          </div>
          <!-- 桌面端表格 -->
          <div class="allocation-table">
            <div class="table-row table-head">
              <span class="terminal-label">资产类别</span>
              <span class="terminal-label">当前</span>
              <span class="terminal-label">目标</span>
              <span class="terminal-label">偏离</span>
              <span class="terminal-label">金额差</span>
            </div>
            <div v-for="row in rows" :key="row.category" class="table-row">
              <div class="asset-name">
                <strong>{{ row.label }}</strong>
                <small>{{ row.category }}</small>
              </div>
              <div>
                <strong class="font-jet">{{ ratio(row.current_ratio) }}</strong>
                <small class="font-jet">¥{{ money(row.current_amount) }}</small>
              </div>
              <div>
                <strong class="font-jet">{{ ratio(row.target_ratio) }}</strong>
                <small class="font-jet">¥{{ money(row.target_amount) }}</small>
              </div>
              <div :class="['drift-pill', 'font-jet', driftClass(row)]">{{ driftText(row) }}</div>
              <div>
                <strong class="font-jet">¥{{ money(row.drift_amount) }}</strong>
              </div>
            </div>
          </div>

          <!-- 移动端卡片式布局 -->
          <div class="allocation-cards">
            <div v-for="row in rows" :key="row.category + '-card'" class="alloc-card-item reveal-stagger">
              <div class="alloc-card-head">
                <strong>{{ row.label }}</strong>
                <span :class="['drift-pill', 'font-jet', driftClass(row)]">{{ driftText(row) }}</span>
              </div>
              <div class="alloc-card-grid">
                <div class="alloc-card-metric">
                  <span>当前</span>
                  <strong class="font-jet">{{ ratio(row.current_ratio) }}</strong>
                  <small class="font-jet">¥{{ money(row.current_amount) }}</small>
                </div>
                <div class="alloc-card-metric">
                  <span>目标</span>
                  <strong class="font-jet">{{ ratio(row.target_ratio) }}</strong>
                  <small class="font-jet">¥{{ money(row.target_amount) }}</small>
                </div>
                <div class="alloc-card-metric">
                  <span>金额差</span>
                  <strong class="font-jet">¥{{ money(row.drift_amount) }}</strong>
                </div>
              </div>
            </div>
          </div>
        </section>

        <aside class="suggestion-panel editorial-card">
          <div class="section-title">
            <h3 class="editorial-title">建议路径</h3>
            <span class="terminal-label">{{ suggestions.length }} 条</span>
          </div>
          <div v-if="!suggestions.length" class="quiet-empty">当前偏离不明显，保持观察即可。</div>
          <article v-for="item in suggestions" :key="`${item.priority}-${item.action}-${item.category}`" class="suggestion-card reveal-stagger">
            <div class="suggestion-top">
              <span class="action-badge">{{ actionLabel(item.action) }}</span>
              <span class="font-jet">{{ item.amount_range || '-' }}</span>
            </div>
            <p>{{ item.reason }}</p>
            <small v-if="item.fund_name">{{ item.fund_name }} {{ item.fund_code }}</small>
            <div v-if="item.guardrail_note" class="suggestion-guardrail">
              <Icon name="warning" size="13" />
              {{ item.guardrail_note }}
            </div>
            <div class="suggestion-actions">
              <button
                class="suggestion-decision-btn"
                :disabled="creatingDecision"
                @click="createDecisionFromSuggestion(item)"
              >
                <Icon name="clipboard-list" size="13" />
                创建决策
              </button>
            </div>
          </article>
        </aside>
      </main>

      <!-- 压力测试面板 -->
      <section class="stress-panel editorial-card">
        <div class="section-title">
          <h3 class="editorial-title">压力测试</h3>
          <span class="terminal-label">无需 LLM，点击场景即时计算</span>
        </div>

        <div class="scenario-tabs">
          <button
            v-for="s in stressScenarios"
            :key="s.key"
            :class="['scenario-tab', { active: activeScenario === s.key }]"
            :disabled="stressLoading"
            @click="activeScenario = s.key; runStress()"
          >
            <Icon :name="s.icon" size="14" />
            {{ s.label }}
          </button>
        </div>

        <div v-if="stressLoading" class="stress-loading">
          <Icon name="spinner" size="18" />
          <span>正在计算压力冲击...</span>
        </div>

        <div v-else-if="stressResult && stressResult.status === 'ok'" class="stress-content">
          <!-- 核心指标 -->
          <div class="stress-metrics">
            <div class="stress-metric">
              <span class="terminal-label">当前总资产</span>
              <strong class="font-jet">¥{{ money(stressResult.total_assets) }}</strong>
            </div>
            <div class="stress-arrow">
              <Icon name="arrow-right" size="18" />
            </div>
            <div class="stress-metric">
              <span class="terminal-label">压力后资产</span>
              <strong class="font-jet">¥{{ money(stressResult.projected_total_assets) }}</strong>
            </div>
            <div class="stress-metric">
              <span class="terminal-label">预计损失</span>
              <strong class="text-danger font-jet">¥{{ money(stressResult.loss_amount) }}</strong>
            </div>
            <div class="stress-metric">
              <span class="terminal-label">损失比例</span>
              <strong class="text-danger font-jet">{{ ratio(stressResult.loss_ratio) }}</strong>
            </div>
            <div class="stress-metric">
              <span class="terminal-label">风险等级</span>
              <span :class="['risk-badge', riskLevelClass(stressResult.risk_level)]">
                {{ riskLevelText(stressResult.risk_level) }}
              </span>
            </div>
          </div>

          <!-- 资产类别损失条形图 -->
          <div class="impact-bars">
            <div class="impact-head">
              <span class="terminal-label">资产类别</span>
              <span class="terminal-label">当前金额</span>
              <span class="terminal-label">冲击比例</span>
              <span class="terminal-label">损失金额</span>
            </div>
            <div v-for="item in stressResult.asset_impacts" :key="item.category" class="impact-row">
              <div class="impact-label">{{ item.category }}</div>
              <div class="impact-amount font-jet">¥{{ money(item.current_amount) }}</div>
              <div class="impact-shock font-jet">{{ (item.shock_pct * 100).toFixed(1) }}%</div>
              <div class="impact-bar-cell">
                <div
                  class="impact-bar"
                  :style="{ width: (Math.abs(item.loss_amount) / maxLoss * 100) + '%' }"
                />
                <span class="impact-loss font-jet">¥{{ money(item.loss_amount) }}</span>
              </div>
            </div>
          </div>

          <!-- 备用金 / 风险提示 -->
          <div v-if="stressResult.warnings?.length" class="stress-warnings">
            <div v-for="w in stressResult.warnings" :key="w" class="stress-warning-item">
              <Icon name="alert-triangle" size="14" />
              {{ w }}
            </div>
          </div>
        </div>

        <div v-else-if="stressResult && stressResult.status === 'empty'" class="stress-empty">
          <Icon name="inbox" size="20" />
          <span>{{ stressResult.warnings?.[0] || '暂无持仓数据' }}</span>
        </div>

        <div v-else class="stress-empty">
          <Icon name="shield" size="20" />
          <span>选择一个压力场景，查看组合在极端情况下的表现</span>
        </div>
      </section>
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
  font-size: inherit;
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
  font-size: inherit;
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

.suggestion-actions {
  margin-top: 10px;
  display: flex;
  gap: 6px;
}

.suggestion-decision-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  font-size: 0.76rem;
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  border: 1px solid var(--color-primary-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.suggestion-decision-btn:hover:not(:disabled) {
  background: var(--color-primary);
  color: #fff;
}

.suggestion-decision-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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

/* 移动端卡片式配置表 */
.allocation-cards {
  display: none;
  flex-direction: column;
  gap: 8px;
}
.alloc-card-item {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: 12px;
  background: var(--color-bg-input);
}
.alloc-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.alloc-card-head strong {
  color: var(--color-text-primary);
  font-size: 0.95rem;
}
.alloc-card-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.alloc-card-metric {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.alloc-card-metric span {
  color: var(--color-text-muted);
  font-size: 0.72rem;
}
.alloc-card-metric strong {
  color: var(--color-text-primary);
  font-size: 0.88rem;
}
.alloc-card-metric small {
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

@media (max-width: 760px) {
  .allocation-page { padding: var(--space-4); }
  .page-head { flex-direction: column; }
  .metric-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-cell:nth-child(2) { border-right: 0; }
  .metric-cell:nth-child(-n + 2) { border-bottom: 1px solid var(--color-border-light); }
  .allocation-table { display: none !important; }
  .allocation-cards { display: flex; }
}

/* ── 压力测试面板 ── */
.stress-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
}

.scenario-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: var(--space-4);
}
.scenario-tab {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 500;
  transition: all 0.15s;
}
.scenario-tab:hover { border-color: var(--color-primary); color: var(--color-primary); }
.scenario-tab.active {
  background: var(--color-primary-bg);
  border-color: var(--color-primary);
  color: var(--color-primary);
  font-weight: 700;
}
.scenario-tab:disabled { opacity: 0.5; cursor: not-allowed; }

.stress-loading,
.stress-empty {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-text-muted);
}

.stress-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.stress-metrics {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3);
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  flex-wrap: wrap;
}
.stress-metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.stress-metric span {
  color: var(--color-text-muted);
  font-size: inherit;
}
.stress-metric strong {
  color: var(--color-text-primary);
  font-size: 1rem;
}
.stress-arrow {
  color: var(--color-text-muted);
}
.text-danger { color: var(--color-danger) !important; }

.risk-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: var(--radius-sm);
  font-weight: 700;
  font-size: 0.82rem;
}
.risk-badge.ok {
  color: var(--color-success);
  background: var(--color-success-bg);
}
.risk-badge.warning {
  color: var(--color-warning-text);
  background: var(--color-warning-bg);
}
.risk-badge.danger {
  color: var(--color-danger);
  background: var(--color-danger-bg);
}

.impact-bars {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.impact-head {
  display: grid;
  grid-template-columns: 100px 120px 80px 1fr;
  gap: var(--space-3);
  padding: 8px 12px;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
}
.impact-row {
  display: grid;
  grid-template-columns: 100px 120px 80px 1fr;
  gap: var(--space-3);
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
}
.impact-label {
  font-weight: 600;
  color: var(--color-text-primary);
  font-size: 0.85rem;
}
.impact-amount {
  color: var(--color-text-secondary);
  font-size: 0.82rem;
}
.impact-shock {
  color: var(--color-danger);
  font-weight: 600;
  font-size: 0.82rem;
}
.impact-bar-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.impact-bar {
  height: 8px;
  border-radius: 4px;
  background: var(--color-danger);
  opacity: 0.7;
  min-width: 4px;
  transition: width 0.3s;
}
.impact-loss {
  color: var(--color-text-primary);
  font-size: 0.82rem;
  font-weight: 600;
  white-space: nowrap;
}

.stress-warnings {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.stress-warning-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  color: var(--color-warning-text);
  background: var(--color-warning-bg);
  border: 1px solid var(--color-warning-border);
  border-radius: var(--radius-md);
  font-size: 0.85rem;
}

@media (max-width: 760px) {
  .scenario-tabs { flex-direction: column; }
  .stress-metrics { flex-direction: column; align-items: flex-start; }
  .stress-arrow { transform: rotate(90deg); }
  .impact-head,
  .impact-row { grid-template-columns: 1fr 1fr; font-size: 0.78rem; gap: 6px; }
  .impact-head span:nth-child(3),
  .impact-head span:nth-child(4),
  .impact-row .impact-shock,
  .impact-row .impact-bar-cell { display: none; }
}
</style>
