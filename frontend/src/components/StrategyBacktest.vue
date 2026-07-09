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

// ── 策略定义（P3 Step2：key 对齐后端 STRATEGY_REGISTRY）──
const STRATEGIES = [
  { key: 'dca', label: '定投', desc: '定期定额买入，平滑成本' },
  { key: 'grid', label: '网格', desc: '区间网格买卖，赚取波动' },
  { key: 'two_eight', label: '二八', desc: '股债轮动，动量择时' },
  { key: 'core_satellite', label: '核心卫星', desc: '核心持仓 + 卫星增强' },
]

// ── 参数 ──
const strategy = ref('dca')
const targetCode = ref('')
const targetType = ref('index')  // P3 Step2：index | fund
const initialCash = ref(10000)
// DCA
const dcaIntervalDays = ref(7)
const dcaAmount = ref(1000)
const dcaTrigger = ref('fixed')
// Grid
const gridSteps = ref(5)
const gridPct = ref(0.05)
const gridBaseAmount = ref(5000)
// TwoEight
const equityRatio = ref(0.8)
const rebalanceDay = ref(1)
const bondCode = ref('511010')  // P3 Step4：真实债基代码
// CoreSatellite
const coreRatio = ref(0.6)
const maShort = ref(20)
const maLong = ref(60)

// ── 数据状态 ──
const loading = ref(false)
const result = ref(null)
const history = ref([])
const historyLoading = ref(false)
const strategyList = ref([])

const activeStrategy = computed(() => STRATEGIES.find(s => s.key === strategy.value) || STRATEGIES[0])

// ── 收益指标卡片（P3 Step2：字段名对齐后端 annual_return/sharpe_ratio）──
const metrics = computed(() => {
  if (!result.value || result.value.status !== 'ok') return []
  const r = result.value.result || result.value
  return [
    { key: 'total_return', label: '累计收益', value: r.total_return, fmt: 'pct' },
    { key: 'annual_return', label: '年化收益', value: r.annual_return, fmt: 'pct' },
    { key: 'max_drawdown', label: '最大回撤', value: r.max_drawdown, fmt: 'pct' },
    { key: 'volatility', label: '波动率', value: r.volatility, fmt: 'pct' },
    { key: 'sharpe_ratio', label: '夏普比率', value: r.sharpe_ratio, fmt: 'num' },
    { key: 'benchmark_return', label: '基准收益', value: r.benchmark_return, fmt: 'pct' },
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

// ── 净值曲线 SVG（P3 Step2：用 nav_curve/benchmark_nav_curve，提取 value 字段）──
const chartWidth = 700
const chartHeight = 200
const chartPath = computed(() => {
  if (!result.value || result.value.status !== 'ok') return null
  const rawCurve = result.value.result?.nav_curve || result.value.nav_curve || []
  const rawBench = result.value.result?.benchmark_nav_curve || result.value.benchmark_nav_curve || []
  // 提取 value 字段（后端返回 [{date, value}]）
  const curve = rawCurve.map(p => (typeof p === 'number' ? p : p.value)).filter(v => v != null)
  const bench = rawBench.map(p => (typeof p === 'number' ? p : p.value)).filter(v => v != null)
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

// ── 按策略类型构造 params（P3 Step2：字段名对齐后端 STRATEGY_TEMPLATES）──
function buildParams() {
  switch (strategy.value) {
    case 'dca':
      return {
        interval_days: dcaIntervalDays.value,
        amount: dcaAmount.value,
        trigger: dcaTrigger.value,
      }
    case 'grid':
      return {
        grid_steps: gridSteps.value,
        grid_pct: gridPct.value,
        base_amount: gridBaseAmount.value,
      }
    case 'two_eight':
      return {
        equity_ratio: equityRatio.value,
        rebalance_day: rebalanceDay.value,
        bond_code: bondCode.value.trim(),
      }
    case 'core_satellite':
      return {
        core_ratio: coreRatio.value,
        ma_short: maShort.value,
        ma_long: maLong.value,
      }
    default:
      return {}
  }
}

// ── 执行回测 ──
async function doBacktest() {
  if (!targetCode.value.trim()) {
    showToast('请输入标的代码', 'warning')
    return
  }
  loading.value = true
  result.value = null
  try {
    const payload = {
      strategy: strategy.value,
      target_code: targetCode.value.trim(),
      target_type: targetType.value,  // P3 Step2：新增 target_type
      initial_cash: initialCash.value,
      params: buildParams(),
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

        <!-- 标的（P3 Step2：支持指数/基金切换）-->
        <div class="param-section">
          <label class="param-label terminal-label">标的类型</label>
          <div class="target-type-tabs">
            <button
              type="button"
              :class="['type-btn', { active: targetType === 'index' }]"
              @click="targetType = 'index'"
            >指数</button>
            <button
              type="button"
              :class="['type-btn', { active: targetType === 'fund' }]"
              @click="targetType = 'fund'"
            >基金</button>
          </div>
        </div>

        <div class="param-section">
          <label class="param-label terminal-label">
            {{ targetType === 'index' ? '标的指数代码' : '标的基金代码' }}
          </label>
          <input
            v-model="targetCode"
            class="param-input font-jet"
            :placeholder="targetType === 'index' ? '如 000300（沪深300）' : '如 510300（沪深300ETF）'"
          />
        </div>

        <!-- 通用参数 -->
        <div class="param-section">
          <label class="param-label terminal-label">初始资金</label>
          <input v-model.number="initialCash" type="number" min="0" step="1000" class="param-input font-jet" />
        </div>

        <!-- 定投参数（P3 Step2：字段名对齐后端）-->
        <template v-if="strategy === 'dca'">
          <div class="param-section">
            <label class="param-label terminal-label">定投间隔（天）</label>
            <input v-model.number="dcaIntervalDays" type="number" min="1" max="90" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">每次定投金额</label>
            <input v-model.number="dcaAmount" type="number" min="0" step="100" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">触发方式</label>
            <select v-model="dcaTrigger" class="param-input font-jet">
              <option value="fixed">固定定投</option>
              <option value="ma">均线交叉</option>
              <option value="valuation" :disabled="targetType === 'fund'">估值分位（仅指数）</option>
            </select>
          </div>
        </template>

        <!-- 网格参数（P3 Step2：字段名对齐后端）-->
        <template v-if="strategy === 'grid'">
          <div class="param-section">
            <label class="param-label terminal-label">网格层数</label>
            <input v-model.number="gridSteps" type="number" min="2" max="20" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">每格涨跌幅</label>
            <input v-model.number="gridPct" type="number" min="0.01" max="0.2" step="0.01" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">基础仓位金额</label>
            <input v-model.number="gridBaseAmount" type="number" min="0" step="500" class="param-input font-jet" />
          </div>
        </template>

        <!-- 二八参数（P3 Step2：字段名对齐后端 + P3 Step4 加 bond_code）-->
        <template v-if="strategy === 'two_eight'">
          <div class="param-section">
            <label class="param-label terminal-label">股票目标比例</label>
            <input v-model.number="equityRatio" type="number" min="0" max="1" step="0.05" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">每月再平衡日</label>
            <input v-model.number="rebalanceDay" type="number" min="1" max="28" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">债券基金代码（P3）</label>
            <input v-model="bondCode" class="param-input font-jet" placeholder="如 511010（国债ETF），留空用固定年化" />
          </div>
        </template>

        <!-- 核心卫星参数（P3 Step2：字段名对齐后端）-->
        <template v-if="strategy === 'core_satellite'">
          <div class="param-section">
            <label class="param-label terminal-label">核心仓位比例</label>
            <input v-model.number="coreRatio" type="number" min="0" max="1" step="0.05" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">短期均线窗口</label>
            <input v-model.number="maShort" type="number" min="5" max="60" class="param-input font-jet" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">长期均线窗口</label>
            <input v-model.number="maLong" type="number" min="20" max="250" class="param-input font-jet" />
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

          <!-- 策略信息（P3 Step2：显示 target_type + 区间）-->
          <div class="strategy-info terminal-label">
            <Icon name="info" size="14" />
            策略：{{ activeStrategy.label }} · 标的：{{ result.target_code || targetCode }}
            （{{ result.target_type === 'fund' ? '基金' : '指数' }}）
            · 区间：{{ result.start_date || '-' }} ~ {{ result.end_date || '-' }} · {{ result.data_points || 0 }} 个数据点
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

/* 指标卡片（P3 Step2：7 个指标，4 列布局以便整齐展示）*/
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

/* P3 Step2：标的类型切换 */
.target-type-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.type-btn {
  padding: 6px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.15s;
}
.type-btn:hover {
  border-color: var(--color-primary);
}
.type-btn.active {
  background: var(--color-primary-bg);
  border-color: var(--color-primary);
  color: var(--color-primary);
  font-weight: 600;
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

@media (max-width: 1024px) and (min-width: 769px) {
  .metrics-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
