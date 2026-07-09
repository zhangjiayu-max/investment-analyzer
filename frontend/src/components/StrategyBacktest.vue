<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  listStrategies,
  runStrategyBacktest,
  listStrategyResults,
} from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'
import EmptyState from './ui/EmptyState.vue'

const { showToast } = useToast()

// ── 策略定义 ──
const STRATEGIES = [
  { key: 'dca', label: '定投', desc: '定期定额买入，平滑成本' },
  { key: 'grid', label: '网格', desc: '区间网格买卖，赚取波动' },
  { key: 'dual_momentum', label: '二八', desc: '股债轮动，动量择时' },
  { key: 'core_satellite', label: '核心卫星', desc: '核心持仓 + 卫星增强' },
]

// ── 参数 ──
const strategy = ref('dca')
const targetCode = ref('')
const initialCash = ref(10000)
const monthlyAmount = ref(1000)
const gridLow = ref(0.8)
const gridHigh = ref(1.2)
const gridSteps = ref(6)
const rebalanceMonths = ref(3)
const satelliteRatio = ref(0.3)

// ── 数据状态 ──
const loading = ref(false)
const result = ref(null)
const history = ref([])
const historyLoading = ref(false)
const strategyList = ref([])

const activeStrategy = computed(() => STRATEGIES.find(s => s.key === strategy.value) || STRATEGIES[0])

// ── 收益指标卡片 ──
const metrics = computed(() => {
  if (!result.value || result.value.status !== 'ok') return []
  const r = result.value.result || result.value
  return [
    { key: 'total_return', label: '累计收益', value: r.total_return, fmt: 'pct' },
    { key: 'ann_return', label: '年化收益', value: r.ann_return, fmt: 'pct' },
    { key: 'max_drawdown', label: '最大回撤', value: r.max_drawdown, fmt: 'pct' },
    { key: 'volatility', label: '波动率', value: r.volatility, fmt: 'pct' },
    { key: 'sharpe', label: '夏普比率', value: r.sharpe, fmt: 'num' },
    { key: 'final_value', label: '期末资产', value: r.final_value, fmt: 'money' },
  ]
})

function fmtVal(v, type) {
  const n = Number(v || 0)
  if (type === 'pct') return `${n > 0 ? '+' : ''}${(n * 100).toFixed(2)}%`
  if (type === 'money') return `¥${n.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
  return n.toFixed(2)
}

function metricClass(v, type) {
  if (type === 'pct') return v >= 0 ? 'positive' : 'negative'
  if (type === 'money') return 'positive'
  return ''
}

// ── 净值曲线 SVG ──
const chartWidth = 700
const chartHeight = 200
const chartPath = computed(() => {
  if (!result.value || result.value.status !== 'ok') return null
  const curve = result.value.result?.equity_curve || result.value.equity_curve || []
  const bench = result.value.benchmark?.equity_curve || []
  if (!curve.length) return null

  const all = [...curve, ...bench]
  const minVal = Math.min(...all) * 0.95
  const maxVal = Math.max(...all) * 1.05
  const range = maxVal - minVal || 1
  const len = Math.max(curve.length, bench.length)

  function toPath(c) {
    if (!c.length) return ''
    return c.map((v, i) => {
      const x = (i / (len - 1)) * chartWidth
      const y = chartHeight - ((v - minVal) / range) * chartHeight
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
  }
  return { main: toPath(curve), bench: toPath(bench) }
})

// ── 加载策略列表 ──
async function loadStrategies() {
  try {
    const { data } = await listStrategies()
    strategyList.value = data.items || data.strategies || []
  } catch {
    // 静默：使用本地 STRATEGIES 兜底
  }
}

// ── 执行回测 ──
async function doBacktest() {
  if (!targetCode.value.trim()) {
    showToast('请输入标的指数代码', 'warning')
    return
  }
  loading.value = true
  result.value = null
  try {
    const payload = {
      strategy: strategy.value,
      target_code: targetCode.value.trim(),
      initial_cash: initialCash.value,
      monthly_amount: monthlyAmount.value,
      grid_low: gridLow.value,
      grid_high: gridHigh.value,
      grid_steps: gridSteps.value,
      rebalance_months: rebalanceMonths.value,
      satellite_ratio: satelliteRatio.value,
    }
    const { data } = await runStrategyBacktest(payload)
    result.value = data
    if (data.status === 'error') {
      showToast(data.error || '回测失败', 'error')
    } else {
      showToast('回测完成', 'success')
    }
  } catch (e) {
    showToast('回测失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

// ── 历史回测 ──
async function loadHistory() {
  historyLoading.value = true
  try {
    const { data } = await listStrategyResults(20)
    history.value = data.items || data.results || []
  } catch (e) {
    console.error('加载历史回测失败:', e)
  } finally {
    historyLoading.value = false
  }
}

function fmtHistoryReturn(v) {
  const n = Number(v || 0)
  return `${n > 0 ? '+' : ''}${(n * 100).toFixed(1)}%`
}

onMounted(() => {
  loadStrategies()
  loadHistory()
})
</script>

<template>
  <div class="strategy-page bg-mesh">
    <header class="page-header">
      <div>
        <h2 class="page-title editorial-title-lg">策略回测</h2>
        <p class="page-desc">选择策略与标的，用历史数据验证收益与风险特征。</p>
      </div>
    </header>

    <div class="strategy-layout">
      <!-- 左侧参数面板 -->
      <aside class="params-panel editorial-card">
        <!-- 策略选择 -->
        <div class="param-section">
          <label class="param-label terminal-label">策略</label>
          <div class="strategy-list">
            <button
              v-for="s in STRATEGIES"
              :key="s.key"
              :class="['strategy-btn', { active: strategy === s.key }]"
              @click="strategy = s.key"
            >
              <strong>{{ s.label }}</strong>
              <small class="terminal-label">{{ s.desc }}</small>
            </button>
          </div>
        </div>

        <!-- 标的 -->
        <div class="param-section">
          <label class="param-label terminal-label">标的指数代码</label>
          <input v-model="targetCode" class="param-input font-jet" placeholder="如 000300（沪深300）" />
        </div>

        <!-- 通用参数 -->
        <div class="param-section">
          <label class="param-label terminal-label">初始资金</label>
          <input v-model.number="initialCash" type="number" min="0" step="1000" class="param-input font-jet" />
        </div>

        <!-- 定投参数 -->
        <template v-if="strategy === 'dca'">
          <div class="param-section">
            <label class="param-label terminal-label">每月投入</label>
            <input v-model.number="monthlyAmount" type="number" min="0" step="100" class="param-input font-jet" />
          </div>
        </template>

        <!-- 网格参数 -->
        <template v-if="strategy === 'grid'">
          <div class="param-section">
            <label class="param-label terminal-label">网格下限（倍）</label>
            <input v-model.number="gridLow" type="number" min="0.1" max="1" step="0.05" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">网格上限（倍）</label>
            <input v-model.number="gridHigh" type="number" min="1" max="3" step="0.05" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">网格档数</label>
            <input v-model.number="gridSteps" type="number" min="2" max="20" class="param-input font-jet" />
          </div>
        </template>

        <!-- 二八参数 -->
        <template v-if="strategy === 'dual_momentum'">
          <div class="param-section">
            <label class="param-label terminal-label">再平衡频率（月）</label>
            <input v-model.number="rebalanceMonths" type="number" min="1" max="12" class="param-input font-jet" />
          </div>
        </template>

        <!-- 核心卫星参数 -->
        <template v-if="strategy === 'core_satellite'">
          <div class="param-section">
            <label class="param-label terminal-label">卫星仓位比例</label>
            <input v-model.number="satelliteRatio" type="number" min="0" max="1" step="0.05" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">再平衡频率（月）</label>
            <input v-model.number="rebalanceMonths" type="number" min="1" max="12" class="param-input font-jet" />
          </div>
        </template>

        <button class="btn-primary run-btn" :disabled="loading" @click="doBacktest">
          <Icon :name="loading ? 'spinner' : 'play'" size="16" />
          {{ loading ? '回测中...' : '开始回测' }}
        </button>
      </aside>

      <!-- 右侧结果面板 -->
      <main class="result-panel editorial-card">
        <!-- 空状态 -->
        <div v-if="!result && !loading" class="empty-result">
          <Icon name="line-chart" size="36" />
          <p>选择策略与标的，点击"开始回测"</p>
        </div>

        <!-- 加载中 -->
        <div v-else-if="loading" class="empty-result">
          <Icon name="spinner" size="24" />
          <p>正在获取历史数据并回测...</p>
        </div>

        <!-- 错误 -->
        <div v-else-if="result?.status === 'error'" class="error-result">
          <Icon name="warning" size="24" />
          <p>{{ result.error || '回测失败' }}</p>
        </div>

        <!-- 结果 -->
        <template v-else-if="result?.status === 'ok'">
          <!-- 收益指标卡片 -->
          <div class="metrics-grid">
            <article
              v-for="m in metrics"
              :key="m.key"
              class="metric-card reveal-stagger"
            >
              <span class="terminal-label">{{ m.label }}</span>
              <strong class="font-jet" :class="metricClass(m.value, m.fmt)">
                {{ fmtVal(m.value, m.fmt) }}
              </strong>
            </article>
          </div>

          <!-- 净值曲线 -->
          <div v-if="chartPath" class="chart-section">
            <div class="chart-legend">
              <span class="legend-main">● 策略</span>
              <span class="legend-bench">● 基准</span>
            </div>
            <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="equity-chart" preserveAspectRatio="none">
              <path :d="chartPath.bench" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4,3" />
              <path :d="chartPath.main" fill="none" stroke="#c9a84c" stroke-width="2" />
            </svg>
          </div>

          <!-- 策略信息 -->
          <div class="strategy-info terminal-label">
            <Icon name="info" size="14" />
            策略：{{ activeStrategy.label }} · 标的：{{ result.target_code || targetCode }}
          </div>
        </template>
      </main>
    </div>

    <!-- 历史回测列表 -->
    <section class="history-section editorial-card">
      <div class="section-head editorial-card-header">
        <h3 class="editorial-title">历史回测</h3>
        <button class="btn-ghost btn-sm" @click="loadHistory" :disabled="historyLoading">
          <Icon :name="historyLoading ? 'spinner' : 'refresh'" size="14" />
          刷新
        </button>
      </div>
      <div v-if="historyLoading && !history.length" class="history-loading">
        <Icon name="spinner" size="18" />
      </div>
      <EmptyState
        v-else-if="!history.length"
        icon="empty"
        title="暂无历史回测"
        description="完成一次回测后，结果会自动保存到历史列表。"
      />
      <div v-else class="history-list">
        <article v-for="h in history" :key="h.id" class="history-item reveal-stagger">
          <div class="history-main">
            <strong>{{ h.strategy_name || h.strategy }}</strong>
            <span class="history-meta terminal-label">
              {{ h.target_code }} · {{ h.months || 0 }}个月
            </span>
          </div>
          <div class="history-metrics">
            <span :class="['history-return font-jet', (h.total_return || 0) >= 0 ? 'positive' : 'negative']">
              {{ fmtHistoryReturn(h.total_return) }}
            </span>
            <span class="history-date font-jet">{{ (h.created_at || '').slice(0, 10) }}</span>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.strategy-page {
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

.strategy-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: var(--space-5);
  align-items: start;
}

/* 参数面板 */
.params-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  position: sticky;
  top: var(--space-4);
}
.param-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.param-label {
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-muted);
}
.param-input {
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  font-size: 0.88rem;
}
.param-input:focus {
  outline: none;
  border-color: var(--color-primary);
}

.strategy-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.strategy-btn {
  text-align: left;
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  cursor: pointer;
  transition: all 0.15s;
}
.strategy-btn:hover {
  border-color: var(--color-primary);
}
.strategy-btn.active {
  background: var(--color-primary-bg);
  border-color: var(--color-primary);
}
.strategy-btn strong {
  display: block;
  font-size: 0.85rem;
  color: var(--color-text-primary);
}
.strategy-btn small {
  display: block;
  font-size: inherit;
  color: var(--color-text-muted);
  margin-top: 2px;
}

.run-btn {
  width: 100%;
  padding: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-weight: 700;
}

/* 结果面板 */
.result-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-height: 400px;
}
.empty-result, .error-result {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
}
.error-result { color: var(--color-danger); }

/* 指标卡片 */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}
.metric-card {
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.metric-card .terminal-label {
  font-size: inherit;
  color: var(--color-text-muted);
}
.metric-card strong {
  font-size: 1.1rem;
  font-weight: 700;
}
.positive { color: var(--color-success); }
.negative { color: var(--color-danger); }

/* 图表 */
.chart-section {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: var(--space-3);
}
.chart-legend {
  display: flex;
  gap: var(--space-4);
  font-size: 0.8rem;
  margin-bottom: 8px;
}
.legend-main { color: #c9a84c; }
.legend-bench { color: #94a3b8; }
.equity-chart {
  width: 100%;
  height: 200px;
}

.strategy-info {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  font-size: inherit;
  color: var(--color-text-muted);
}

/* 历史回测 */
.history-section {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  background: var(--color-bg-card);
}
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
}
.section-head h3 { margin: 0; }
.history-loading {
  min-height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
}
.history-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.history-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 8px 10px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
}
.history-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.history-main strong {
  font-size: 0.84rem;
  color: var(--color-text-primary);
}
.history-meta {
  font-size: inherit;
  color: var(--color-text-muted);
}
.history-metrics {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
.history-return {
  font-size: 0.88rem;
  font-weight: 600;
}
.history-return.positive { color: var(--color-success); }
.history-return.negative { color: var(--color-danger); }
.history-date {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

@media (max-width: 960px) {
  .strategy-layout { grid-template-columns: 1fr; }
  .params-panel { position: static; }
}

@media (max-width: 768px) {
  .strategy-page { padding: var(--space-3); }
  .metrics-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-card strong { font-size: 1rem; }
}
</style>
