<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getBacktestPresets, runBacktest } from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'

const { showToast } = useToast()

// ── 预设 ──
const presets = ref([])
const selectedPreset = ref(null)

// ── 参数 ──
const targetCode = ref('')
const targetType = ref('index')
const strategy = ref('dca')
const initialCash = ref(10000)
const monthlyAmount = ref(1000)
const lowPct = ref(30)
const highPct = ref(70)
const minMultiplier = ref(0.5)
const maxMultiplier = ref(2.0)
const buyThreshold = ref(30)
const sellThreshold = ref(70)
const buyAmount = ref(2000)
const sellRatio = ref(0.3)

// ── 结果 ──
const loading = ref(false)
const result = ref(null)

const strategies = [
  { key: 'dca', label: '普通定投', desc: '每月固定金额买入' },
  { key: 'valuation_dca', label: '估值加权定投', desc: '低估多投、高估少投' },
  { key: 'percentile_trade', label: '估值分位买卖', desc: '低分位买入，高分位止盈' },
]

async function loadPresets() {
  try {
    const { data } = await getBacktestPresets()
    presets.value = data.presets || []
  } catch (e) {
    console.error('加载预设失败:', e)
  }
}

function applyPreset(preset) {
  selectedPreset.value = preset.id
  strategy.value = preset.strategy
  const p = preset.params
  if (p.monthly_amount) monthlyAmount.value = p.monthly_amount
  if (p.initial_cash) initialCash.value = p.initial_cash
  if (p.low_pct) lowPct.value = p.low_pct
  if (p.high_pct) highPct.value = p.high_pct
  if (p.min_multiplier) minMultiplier.value = p.min_multiplier
  if (p.max_multiplier) maxMultiplier.value = p.max_multiplier
  if (p.buy_threshold) buyThreshold.value = p.buy_threshold
  if (p.sell_threshold) sellThreshold.value = p.sell_threshold
  if (p.buy_amount) buyAmount.value = p.buy_amount
  if (p.sell_ratio) sellRatio.value = p.sell_ratio
}

async function doBacktest() {
  if (!targetCode.value.trim()) {
    showToast('请输入标的代码', 'warning')
    return
  }
  loading.value = true
  result.value = null
  try {
    const { data } = await runBacktest({
      target_code: targetCode.value.trim(),
      target_type: targetType.value,
      strategy: strategy.value,
      initial_cash: initialCash.value,
      monthly_amount: monthlyAmount.value,
      low_pct: lowPct.value,
      high_pct: highPct.value,
      min_multiplier: minMultiplier.value,
      max_multiplier: maxMultiplier.value,
      buy_threshold: buyThreshold.value,
      sell_threshold: sellThreshold.value,
      buy_amount: buyAmount.value,
      sell_ratio: sellRatio.value,
    })
    result.value = data
    if (data.status === 'error') {
      showToast(data.error, 'error')
    }
  } catch (e) {
    showToast('回测失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

// ── 图表 ──
const chartWidth = 700
const chartHeight = 240

function pct(v) {
  return `${(Number(v || 0) * 100).toFixed(2)}%`
}
function money(v) {
  return Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

const chartPaths = computed(() => {
  if (!result.value || result.value.status !== 'ok') return null
  const main = result.value.result?.equity_curve || []
  const bench = result.value.benchmark?.equity_curve || []
  if (!main.length) return null

  const all = [...main, ...bench]
  const minVal = Math.min(...all) * 0.95
  const maxVal = Math.max(...all) * 1.05
  const range = maxVal - minVal || 1
  const len = Math.max(main.length, bench.length)

  function toPath(curve) {
    if (!curve.length) return ''
    return curve.map((v, i) => {
      const x = (i / (len - 1)) * chartWidth
      const y = chartHeight - ((v - minVal) / range) * chartHeight
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
  }

  return {
    main: toPath(main),
    bench: toPath(bench),
    labels: result.value.result?.months || 0,
  }
})

onMounted(loadPresets)
</script>

<template>
  <div class="sandbox-page">
    <header class="page-head">
      <div>
        <h2 class="page-title">策略沙盒</h2>
        <p class="page-desc">用历史数据回测投资策略，对比买入持有，避免凭感觉调整。</p>
      </div>
    </header>

    <div class="sandbox-layout">
      <!-- 左侧：参数面板 -->
      <aside class="params-panel">
        <!-- 预设 -->
        <div class="param-section" v-if="presets.length">
          <label class="param-label">快速预设</label>
          <div class="preset-list">
            <button
              v-for="p in presets"
              :key="p.id"
              :class="['preset-btn', { active: selectedPreset === p.id }]"
              @click="applyPreset(p)"
            >
              <strong>{{ p.name }}</strong>
              <small>{{ p.description }}</small>
            </button>
          </div>
        </div>

        <!-- 标的 -->
        <div class="param-section">
          <label class="param-label">标的代码</label>
          <input v-model="targetCode" class="param-input" placeholder="如 000300（沪深300）、110011" />
          <div class="type-toggle">
            <button :class="{ active: targetType === 'index' }" @click="targetType = 'index'">指数</button>
            <button :class="{ active: targetType === 'fund' }" @click="targetType = 'fund'">基金</button>
          </div>
        </div>

        <!-- 策略 -->
        <div class="param-section">
          <label class="param-label">策略</label>
          <div class="strategy-list">
            <button
              v-for="s in strategies"
              :key="s.key"
              :class="['strategy-btn', { active: strategy === s.key }]"
              @click="strategy = s.key"
            >
              <strong>{{ s.label }}</strong>
              <small>{{ s.desc }}</small>
            </button>
          </div>
        </div>

        <!-- 资金 -->
        <div class="param-section">
          <label class="param-label">初始资金</label>
          <input v-model.number="initialCash" type="number" class="param-input" min="0" step="1000" />
        </div>
        <div class="param-section" v-if="strategy === 'dca' || strategy === 'valuation_dca'">
          <label class="param-label">每月投入</label>
          <input v-model.number="monthlyAmount" type="number" class="param-input" min="0" step="100" />
        </div>

        <!-- 估值加权参数 -->
        <template v-if="strategy === 'valuation_dca'">
          <div class="param-section">
            <label class="param-label">低估分位 (%)</label>
            <input v-model.number="lowPct" type="number" class="param-input" min="0" max="100" />
          </div>
          <div class="param-section">
            <label class="param-label">高估分位 (%)</label>
            <input v-model.number="highPct" type="number" class="param-input" min="0" max="100" />
          </div>
          <div class="param-section">
            <label class="param-label">最低倍数</label>
            <input v-model.number="minMultiplier" type="number" class="param-input" min="0" max="5" step="0.1" />
          </div>
          <div class="param-section">
            <label class="param-label">最高倍数</label>
            <input v-model.number="maxMultiplier" type="number" class="param-input" min="0" max="5" step="0.1" />
          </div>
        </template>

        <!-- 估值分位买卖参数 -->
        <template v-if="strategy === 'percentile_trade'">
          <div class="param-section">
            <label class="param-label">买入分位 (%)</label>
            <input v-model.number="buyThreshold" type="number" class="param-input" min="0" max="100" />
          </div>
          <div class="param-section">
            <label class="param-label">止盈分位 (%)</label>
            <input v-model.number="sellThreshold" type="number" class="param-input" min="0" max="100" />
          </div>
          <div class="param-section">
            <label class="param-label">单次买入金额</label>
            <input v-model.number="buyAmount" type="number" class="param-input" min="0" step="500" />
          </div>
          <div class="param-section">
            <label class="param-label">止盈卖出比例</label>
            <input v-model.number="sellRatio" type="number" class="param-input" min="0" max="1" step="0.05" />
          </div>
        </template>

        <button class="btn-primary run-btn" :disabled="loading" @click="doBacktest">
          <Icon :name="loading ? 'spinner' : 'play'" size="16" />
          {{ loading ? '回测中...' : '开始回测' }}
        </button>
      </aside>

      <!-- 右侧：结果面板 -->
      <main class="result-panel">
        <!-- 空状态 -->
        <div v-if="!result && !loading" class="empty-result">
          <Icon name="bar-chart" size="36" />
          <p>设置参数后点击"开始回测"</p>
        </div>

        <div v-else-if="loading" class="empty-result">
          <Icon name="spinner" size="24" />
          <p>正在获取历史数据并回测...</p>
        </div>

        <div v-else-if="result?.status === 'error'" class="error-result">
          <Icon name="alert-circle" size="24" />
          <p>{{ result.error }}</p>
        </div>

        <template v-else-if="result?.status === 'ok'">
          <!-- 关键指标对比 -->
          <div class="compare-table">
            <div class="compare-head">
              <span></span>
              <span class="compare-col-main">策略结果</span>
              <span class="compare-col-bench">买入持有</span>
            </div>
            <div class="compare-row">
              <span>总投入</span>
              <span>¥{{ money(result.result.total_invested) }}</span>
              <span>¥{{ money(result.benchmark.total_invested) }}</span>
            </div>
            <div class="compare-row">
              <span>期末资产</span>
              <strong>¥{{ money(result.result.final_value) }}</strong>
              <strong>¥{{ money(result.benchmark.final_value) }}</strong>
            </div>
            <div class="compare-row">
              <span>累计收益</span>
              <strong :class="result.result.total_return >= 0 ? 'positive' : 'negative'">{{ pct(result.result.total_return) }}</strong>
              <strong :class="result.benchmark.total_return >= 0 ? 'positive' : 'negative'">{{ pct(result.benchmark.total_return) }}</strong>
            </div>
            <div class="compare-row">
              <span>年化收益</span>
              <span :class="result.result.ann_return >= 0 ? 'positive' : 'negative'">{{ pct(result.result.ann_return) }}</span>
              <span :class="result.benchmark.ann_return >= 0 ? 'positive' : 'negative'">{{ pct(result.benchmark.ann_return) }}</span>
            </div>
            <div class="compare-row">
              <span>最大回撤</span>
              <span class="negative">{{ pct(result.result.max_drawdown) }}</span>
              <span class="negative">{{ pct(result.benchmark.max_drawdown) }}</span>
            </div>
            <div class="compare-row">
              <span>波动率</span>
              <span>{{ pct(result.result.volatility) }}</span>
              <span>{{ pct(result.benchmark.volatility) }}</span>
            </div>
            <div class="compare-row">
              <span>交易次数</span>
              <span>{{ result.result.trades }}</span>
              <span>{{ result.benchmark.trades }}</span>
            </div>
            <div class="compare-row">
              <span>回测月数</span>
              <span>{{ result.months }}</span>
              <span>{{ result.months }}</span>
            </div>
          </div>

          <!-- 净值曲线 -->
          <div v-if="chartPaths" class="chart-section">
            <div class="chart-legend">
              <span class="legend-main">● 策略</span>
              <span class="legend-bench">● 买入持有</span>
            </div>
            <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="equity-chart" preserveAspectRatio="none">
              <path :d="chartPaths.bench" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4,3" />
              <path :d="chartPaths.main" fill="none" stroke="#c9a84c" stroke-width="2" />
            </svg>
          </div>

          <!-- 免责声明 -->
          <div class="disclaimer">
            <Icon name="info" size="14" />
            {{ result.disclaimer }}
          </div>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.sandbox-page {
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

.sandbox-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: var(--space-5);
  align-items: start;
}

/* ── 参数面板 ── */
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
  font-size: 0.78rem;
  font-weight: 600;
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

.type-toggle {
  display: flex;
  gap: 4px;
}
.type-toggle button {
  flex: 1;
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
  font-size: 0.82rem;
  cursor: pointer;
}
.type-toggle button.active {
  background: var(--color-primary-bg);
  border-color: var(--color-primary);
  color: var(--color-primary);
  font-weight: 600;
}

.preset-list, .strategy-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preset-btn, .strategy-btn {
  text-align: left;
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  cursor: pointer;
  transition: all 0.15s;
}
.preset-btn:hover, .strategy-btn:hover {
  border-color: var(--color-primary);
}
.preset-btn.active, .strategy-btn.active {
  background: var(--color-primary-bg);
  border-color: var(--color-primary);
}
.preset-btn strong, .strategy-btn strong {
  display: block;
  font-size: 0.85rem;
  color: var(--color-text-primary);
}
.preset-btn small, .strategy-btn small {
  display: block;
  font-size: 0.72rem;
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

/* ── 结果面板 ── */
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

/* 对比表 */
.compare-table {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.compare-head {
  display: grid;
  grid-template-columns: 1.2fr 1fr 1fr;
  gap: var(--space-3);
  padding: 8px 12px;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--color-text-muted);
}
.compare-col-main { color: var(--color-primary); }
.compare-col-bench { color: var(--color-text-muted); }
.compare-row {
  display: grid;
  grid-template-columns: 1.2fr 1fr 1fr;
  gap: var(--space-3);
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
  font-size: 0.88rem;
  color: var(--color-text-primary);
}
.compare-row span:first-child {
  color: var(--color-text-muted);
  font-size: 0.82rem;
}
.positive { color: var(--color-success) !important; }
.negative { color: var(--color-danger) !important; }

/* 图表 */
.chart-section {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: var(--space-3);
}
.chart-legend {
  display: flex;
  gap: var(--space-4);
  font-size: 0.75rem;
  margin-bottom: 8px;
}
.legend-main { color: #c9a84c; font-weight: 600; }
.legend-bench { color: #94a3b8; }
.equity-chart {
  width: 100%;
  height: 200px;
}

/* 免责声明 */
.disclaimer {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  background: var(--color-warning-bg);
  border: 1px solid var(--color-warning-border);
  border-radius: var(--radius-md);
  color: var(--color-warning-text);
  font-size: 0.78rem;
}

@media (max-width: 960px) {
  .sandbox-layout { grid-template-columns: 1fr; }
  .params-panel { position: static; }
}
</style>
