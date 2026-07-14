<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getBacktestPresets, runBacktest, saveBacktest, listBacktests, deleteBacktest, linkBacktestToDecision, listDecisions } from '../../api'
import { useToast } from '../../composables/useToast'
import Icon from '../ui/Icon.vue'

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
const equityTarget = ref(0.6)
const frequencyMonths = ref(3)
const driftThreshold = ref(0.05)

// ── 结果 ──
const loading = ref(false)
const result = ref(null)

const strategies = [
  { key: 'dca', label: '普通定投', desc: '每月固定金额买入' },
  { key: 'valuation_dca', label: '估值加权定投', desc: '低估多投、高估少投' },
  { key: 'percentile_trade', label: '估值分位买卖', desc: '低分位买入，高分位止盈' },
  { key: 'periodic_rebalance', label: '定期再平衡', desc: '每N个月恢复目标配比' },
  { key: 'threshold_rebalance', label: '偏离阈值再平衡', desc: '偏离超阈值时触发' },
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
  if (p.equity_target) equityTarget.value = p.equity_target
  if (p.frequency_months) frequencyMonths.value = p.frequency_months
  if (p.drift_threshold) driftThreshold.value = p.drift_threshold
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
      equity_target: equityTarget.value,
      frequency_months: frequencyMonths.value,
      drift_threshold: driftThreshold.value,
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

// ── 保存回测 ──
const saveName = ref('')
const saveNotes = ref('')
const saving = ref(false)

// ── 历史回测 ──
const history = ref([])
const historyLoading = ref(false)
const showHistory = ref(false)

async function loadHistory() {
  historyLoading.value = true
  try {
    const { data } = await listBacktests(20)
    history.value = data.items || []
  } catch (e) {
    console.error('加载历史回测失败:', e)
  } finally {
    historyLoading.value = false
  }
}

async function saveResult() {
  if (!result.value || result.value.status !== 'ok') return
  if (!saveName.value.trim()) {
    showToast('请输入回测名称', 'warning')
    return
  }
  saving.value = true
  try {
    await saveBacktest({
      name: saveName.value.trim(),
      target_code: result.value.target_code,
      target_type: result.value.target_type,
      strategy: result.value.strategy,
      params: result.value.params,
      result: result.value.result,
      benchmark: result.value.benchmark,
      months: result.value.months,
      notes: saveNotes.value.trim(),
    })
    showToast('回测结果已保存', 'success')
    saveName.value = ''
    saveNotes.value = ''
    await loadHistory()
  } catch (e) {
    showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    saving.value = false
  }
}

async function removeHistory(id) {
  try {
    await deleteBacktest(id)
    history.value = history.value.filter(h => h.id !== id)
    showToast('已删除', 'success')
  } catch (e) {
    showToast('删除失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// ── 关联决策 ──
const decisionsForLink = ref([])
const linkModal = ref({ visible: false, backtestId: null })
const selectedDecisionId = ref(null)
const linking = ref(false)

async function openLinkModal(backtestId) {
  linkModal.value = { visible: true, backtestId }
  selectedDecisionId.value = null
  if (!decisionsForLink.value.length) {
    try {
      const { data } = await listDecisions('', 30)
      decisionsForLink.value = (data.items || []).filter(d =>
        ['proposed', 'accepted', 'executed'].includes(d.status)
      )
    } catch { /* silent */ }
  }
}

async function doLinkDecision() {
  if (!selectedDecisionId.value || !linkModal.value.backtestId) return
  linking.value = true
  try {
    await linkBacktestToDecision(linkModal.value.backtestId, selectedDecisionId.value)
    showToast('已关联回测到决策', 'success')
    linkModal.value = { visible: false, backtestId: null }
    selectedDecisionId.value = null
  } catch (e) {
    showToast('关联失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    linking.value = false
  }
}

function toggleHistory() {
  showHistory.value = !showHistory.value
  if (showHistory.value && !history.value.length) loadHistory()
}
</script>

<template>
  <div class="sandbox-page bg-mesh">
    <header class="page-head">
      <div>
        <h2 class="page-title editorial-title-lg">策略沙盒</h2>
        <p class="page-desc editorial-subtitle">用历史数据回测投资策略，对比买入持有，避免凭感觉调整。</p>
      </div>
      <button class="btn-secondary" :class="{ active: showHistory }" @click="toggleHistory">
        <Icon name="clock" size="16" />
        历史回测
      </button>
    </header>

    <div class="sandbox-layout">
      <!-- 左侧：参数面板 -->
      <aside class="params-panel editorial-card">
        <!-- 预设 -->
        <div class="param-section" v-if="presets.length">
          <label class="param-label terminal-label">快速预设</label>
          <div class="preset-list">
            <button
              v-for="p in presets"
              :key="p.id"
              :class="['preset-btn', { active: selectedPreset === p.id }]"
              @click="applyPreset(p)"
            >
              <strong>{{ p.name }}</strong>
              <small class="terminal-label">{{ p.description }}</small>
            </button>
          </div>
        </div>

        <!-- 标的 -->
        <div class="param-section">
          <label class="param-label terminal-label">标的代码</label>
          <input v-model="targetCode" class="param-input font-jet" placeholder="如 000300（沪深300）、110011" />
          <div class="type-toggle">
            <button :class="{ active: targetType === 'index' }" @click="targetType = 'index'">指数</button>
            <button :class="{ active: targetType === 'fund' }" @click="targetType = 'fund'">基金</button>
          </div>
        </div>

        <!-- 策略 -->
        <div class="param-section">
          <label class="param-label terminal-label">策略</label>
          <div class="strategy-list">
            <button
              v-for="s in strategies"
              :key="s.key"
              :class="['strategy-btn', { active: strategy === s.key }]"
              @click="strategy = s.key"
            >
              <strong>{{ s.label }}</strong>
              <small class="terminal-label">{{ s.desc }}</small>
            </button>
          </div>
        </div>

        <!-- 资金 -->
        <div class="param-section">
          <label class="param-label terminal-label">初始资金</label>
          <input v-model.number="initialCash" type="number" class="param-input font-jet" min="0" step="1000" />
        </div>
        <div class="param-section" v-if="strategy === 'dca' || strategy === 'valuation_dca'">
          <label class="param-label terminal-label">每月投入</label>
          <input v-model.number="monthlyAmount" type="number" class="param-input font-jet" min="0" step="100" />
        </div>

        <!-- 估值加权参数 -->
        <template v-if="strategy === 'valuation_dca'">
          <div class="param-section">
            <label class="param-label terminal-label">低估分位 (%)</label>
            <input v-model.number="lowPct" type="number" class="param-input font-jet" min="0" max="100" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">高估分位 (%)</label>
            <input v-model.number="highPct" type="number" class="param-input font-jet" min="0" max="100" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">最低倍数</label>
            <input v-model.number="minMultiplier" type="number" class="param-input font-jet" min="0" max="5" step="0.1" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">最高倍数</label>
            <input v-model.number="maxMultiplier" type="number" class="param-input font-jet" min="0" max="5" step="0.1" />
          </div>
        </template>

        <!-- 估值分位买卖参数 -->
        <template v-if="strategy === 'percentile_trade'">
          <div class="param-section">
            <label class="param-label terminal-label">买入分位 (%)</label>
            <input v-model.number="buyThreshold" type="number" class="param-input font-jet" min="0" max="100" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">止盈分位 (%)</label>
            <input v-model.number="sellThreshold" type="number" class="param-input font-jet" min="0" max="100" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">单次买入金额</label>
            <input v-model.number="buyAmount" type="number" class="param-input font-jet" min="0" step="500" />
          </div>
          <div class="param-section">
            <label class="param-label terminal-label">止盈卖出比例</label>
            <input v-model.number="sellRatio" type="number" class="param-input font-jet" min="0" max="1" step="0.05" />
          </div>
        </template>

        <template v-if="strategy === 'periodic_rebalance' || strategy === 'threshold_rebalance'">
          <div class="param-section">
            <label class="param-label terminal-label">权益目标比例</label>
            <input v-model.number="equityTarget" type="number" class="param-input font-jet" min="0" max="1" step="0.05" />
          </div>
          <div class="param-section" v-if="strategy === 'periodic_rebalance'">
            <label class="param-label terminal-label">再平衡频率（月）</label>
            <input v-model.number="frequencyMonths" type="number" class="param-input font-jet" min="1" max="12" />
          </div>
          <div class="param-section" v-if="strategy === 'threshold_rebalance'">
            <label class="param-label terminal-label">偏离阈值</label>
            <input v-model.number="driftThreshold" type="number" class="param-input font-jet" min="0.01" max="0.2" step="0.01" />
          </div>
        </template>

        <button class="btn-primary run-btn" :disabled="loading" @click="doBacktest">
          <Icon :name="loading ? 'spinner' : 'play'" size="16" />
          {{ loading ? '回测中...' : '开始回测' }}
        </button>
      </aside>

      <!-- 右侧：结果面板 -->
      <main class="result-panel editorial-card">
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
            <div class="compare-head editorial-card-header">
              <span class="title"></span>
              <span class="compare-col-main title">策略结果</span>
              <span class="compare-col-bench title">买入持有</span>
            </div>
            <div class="compare-row reveal-stagger">
              <span>总投入</span>
              <span class="font-jet">¥{{ money(result.result.total_invested) }}</span>
              <span class="font-jet">¥{{ money(result.benchmark.total_invested) }}</span>
            </div>
            <div class="compare-row reveal-stagger">
              <span>期末资产</span>
              <strong class="font-jet num-gold">¥{{ money(result.result.final_value) }}</strong>
              <strong class="font-jet">¥{{ money(result.benchmark.final_value) }}</strong>
            </div>
            <div class="compare-row reveal-stagger">
              <span>累计收益</span>
              <strong class="font-jet" :class="result.result.total_return >= 0 ? 'positive' : 'negative'">{{ pct(result.result.total_return) }}</strong>
              <strong class="font-jet" :class="result.benchmark.total_return >= 0 ? 'positive' : 'negative'">{{ pct(result.benchmark.total_return) }}</strong>
            </div>
            <div class="compare-row reveal-stagger">
              <span>年化收益</span>
              <span class="font-jet" :class="result.result.ann_return >= 0 ? 'positive' : 'negative'">{{ pct(result.result.ann_return) }}</span>
              <span class="font-jet" :class="result.benchmark.ann_return >= 0 ? 'positive' : 'negative'">{{ pct(result.benchmark.ann_return) }}</span>
            </div>
            <div class="compare-row reveal-stagger">
              <span>最大回撤</span>
              <span class="font-jet negative">{{ pct(result.result.max_drawdown) }}</span>
              <span class="font-jet negative">{{ pct(result.benchmark.max_drawdown) }}</span>
            </div>
            <div class="compare-row reveal-stagger">
              <span>波动率</span>
              <span class="font-jet">{{ pct(result.result.volatility) }}</span>
              <span class="font-jet">{{ pct(result.benchmark.volatility) }}</span>
            </div>
            <div class="compare-row reveal-stagger">
              <span>交易次数</span>
              <span class="font-jet">{{ result.result.trades }}</span>
              <span class="font-jet">{{ result.benchmark.trades }}</span>
            </div>
            <div class="compare-row reveal-stagger">
              <span>回测月数</span>
              <span class="font-jet">{{ result.months }}</span>
              <span class="font-jet">{{ result.months }}</span>
            </div>
          </div>

          <!-- 净值曲线 -->
          <div v-if="chartPaths" class="chart-section editorial-card">
            <div class="chart-legend editorial-card-header">
              <span class="legend-main title">● 策略</span>
              <span class="legend-bench meta">● 买入持有</span>
            </div>
            <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="equity-chart" preserveAspectRatio="none">
              <path :d="chartPaths.bench" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4,3" />
              <path :d="chartPaths.main" fill="none" stroke="#c9a84c" stroke-width="2" />
            </svg>
          </div>

          <!-- 免责声明 -->
          <div class="disclaimer terminal-label">
            <Icon name="info" size="14" />
            {{ result.disclaimer }}
          </div>

          <!-- 保存回测 -->
          <div class="save-section editorial-card">
            <label class="param-label terminal-label">保存回测</label>
            <div class="save-row">
              <input v-model="saveName" class="param-input save-name-input font-jet" placeholder="回测名称，如：医药50估值加权3年" />
              <input v-model="saveNotes" class="param-input save-notes-input font-jet" placeholder="备注（可选）" />
              <button class="btn-primary save-btn" :disabled="saving" @click="saveResult">
                <Icon :name="saving ? 'spinner' : 'download'" size="15" />
                保存
              </button>
            </div>
          </div>
        </template>

        <!-- 历史回测 -->
        <section v-if="showHistory" class="history-section editorial-card">
          <div class="section-title-row editorial-card-header">
            <h3 class="title">历史回测</h3>
            <button class="btn-ghost btn-sm meta" @click="loadHistory" :disabled="historyLoading">
              <Icon :name="historyLoading ? 'spinner' : 'refresh'" size="14" />
              刷新
            </button>
          </div>
          <div v-if="historyLoading && !history.length" class="history-loading">
            <Icon name="spinner" size="18" />
          </div>
          <div v-else-if="!history.length" class="history-empty">
            暂无保存的回测
          </div>
          <div v-else class="history-list">
            <article v-for="h in history" :key="h.id" class="history-item reveal-stagger">
              <div class="history-main">
                <strong>{{ h.name }}</strong>
                <span class="history-meta terminal-label">
                  {{ h.strategy }} · {{ h.target_code }} · {{ h.months }}个月
                  <span v-if="h.decision_id" class="linked-badge font-jet">已关联决策 #{{ h.decision_id }}</span>
                </span>
              </div>
              <div class="history-metrics">
                <span :class="['history-return font-jet', h.total_return >= 0 ? 'positive' : 'negative']">
                  {{ (h.total_return * 100).toFixed(1) }}%
                </span>
                <span class="history-date font-jet">{{ h.created_at?.slice(0, 10) }}</span>
              </div>
              <div class="history-actions">
                <button v-if="!h.decision_id" class="history-link" @click="openLinkModal(h.id)" title="关联决策">
                  <Icon name="link" size="14" />
                </button>
                <button class="history-del" @click="removeHistory(h.id)">
                  <Icon name="trash" size="14" />
                </button>
              </div>
            </article>
          </div>
        </section>
      </main>
    </div>

    <!-- 关联决策弹窗 -->
    <div v-if="linkModal.visible" class="modal-overlay" @click.self="linkModal.visible = false">
      <div class="modal-card editorial-card">
        <header class="modal-head editorial-card-header">
          <h3 class="title">关联到决策</h3>
          <button class="icon-btn meta" @click="linkModal.visible = false"><Icon name="close" size="18" /></button>
        </header>
        <div class="modal-body">
          <p class="modal-desc terminal-label">选择一条决策，将此回测结果关联到该决策，作为决策依据。</p>
          <div v-if="!decisionsForLink.length" class="modal-empty">暂无可关联的决策</div>
          <div v-else class="decision-options">
            <label
              v-for="d in decisionsForLink"
              :key="d.id"
              :class="['decision-option', { selected: selectedDecisionId === d.id }]"
            >
              <input type="radio" :value="d.id" v-model="selectedDecisionId" />
              <div class="option-content">
                <strong>{{ d.summary }}</strong>
                <span class="terminal-label">{{ d.target_name || d.target_code || '组合' }} · {{ d.decision_type }}</span>
              </div>
            </label>
          </div>
        </div>
        <footer class="modal-foot">
          <button class="btn-ghost" @click="linkModal.visible = false">取消</button>
          <button class="btn-primary" @click="doLinkDecision" :disabled="!selectedDecisionId || linking">
            <Icon :name="linking ? 'spinner' : 'link'" size="15" />
            确认关联
          </button>
        </footer>
      </div>
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
  font-size: inherit;
  font-weight: inherit;
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
  font-size: inherit;
  margin-bottom: 8px;
}
.legend-main { color: #c9a84c; font-weight: inherit; }
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
  font-size: inherit;
}

@media (max-width: 960px) {
  .sandbox-layout { grid-template-columns: 1fr; }
  .params-panel { position: static; }
}

/* ── 保存回测 ── */
.save-section {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.save-row {
  display: flex;
  gap: 8px;
}
.save-name-input { flex: 2; }
.save-notes-input { flex: 3; }
.save-btn {
  padding: 6px 14px;
  white-space: nowrap;
  font-size: 0.82rem;
}

/* ── 历史回测 ── */
.history-section {
  margin-top: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  background: var(--color-bg-card);
}
.section-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
}
.section-title-row h3 {
  margin: 0;
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-primary);
}
.history-loading, .history-empty {
  min-height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  font-size: 0.82rem;
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
  font-size: inherit;
  font-weight: inherit;
  font-variant-numeric: inherit;
}
.history-return.positive { color: #dc2626; }
.history-return.negative { color: #059669; }
.history-date {
  font-size: inherit;
  color: var(--color-text-muted);
}
.history-del {
  width: 26px;
  height: 26px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  background: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.history-del:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.history-actions {
  display: flex;
  gap: 4px;
}

.history-link {
  width: 26px;
  height: 26px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-primary);
  background: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.history-link:hover {
  background: var(--color-primary-bg);
  border-color: var(--color-primary);
}

.linked-badge {
  display: inline-block;
  padding: 1px 6px;
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  border-radius: var(--radius-sm);
  margin-left: 4px;
}

/* ── 弹窗 ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
}
.modal-card {
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  width: 100%;
  max-width: 480px;
  max-height: 80vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}
.modal-head h3 {
  margin: 0;
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-primary);
}
.modal-body {
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.modal-desc {
  margin: 0;
  font-size: inherit;
  color: var(--color-text-muted);
}
.modal-empty {
  text-align: center;
  color: var(--color-text-muted);
  font-size: 0.85rem;
  padding: var(--space-4);
}
.decision-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.decision-option {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  background: var(--color-bg-input);
  transition: all var(--transition-fast);
}
.decision-option:hover {
  border-color: var(--color-primary);
}
.decision-option.selected {
  border-color: var(--color-primary);
  background: var(--color-primary-bg);
}
.decision-option input[type="radio"] {
  margin-top: 2px;
}
.option-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.option-content strong {
  font-size: 0.85rem;
  color: var(--color-text-primary);
}
.option-content span {
  font-size: inherit;
  color: var(--color-text-muted);
}
.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4);
  border-top: 1px solid var(--color-border);
}
.icon-btn {
  width: 30px;
  height: 30px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: none;
}
.icon-btn:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
</style>
