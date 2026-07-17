<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import {
  getHealthV2Dashboard,
  recalculateHealthV2,
  getHealthV2Profile,
  updateHealthV2Profile,
  getHealthV2TargetPotsDefaults,
  getHealthV2History,
  updateHealthV2ActionStatus,
} from '../../api'
import { useToast } from '../../composables/useToast'
import EmptyState from '../ui/EmptyState.vue'
import Icon from '../ui/Icon.vue'

const { showToast } = useToast()

// ── ECharts 动态加载 ──
let echartsModule = null
async function getEcharts() {
  if (!echartsModule) echartsModule = await import('echarts')
  return echartsModule
}

// ── 状态 ──
const loading = ref(false)
const recalculating = ref(false)
const dashboard = ref(null)
const profile = ref(null)
const history = ref([])
const targetPotsDefaults = ref(null)
const showProfileDrawer = ref(false)
const historyChartRef = ref(null)
let historyChart = null

const editingProfile = ref({
  risk_level: 'steady',
  target_date: '',
  target_pots: { cash: 10, steady: 35, long_term: 50, insurance: 5 },
  monthly_investable: 0,
  emergency_months: 6,
})

// ── 计算属性 ──
const assetOverview = computed(() => dashboard.value?.asset_overview || {})
const healthScore = computed(() => dashboard.value?.health_score || {})
const fourPots = computed(() => dashboard.value?.four_pots || {})
const actions = computed(() => dashboard.value?.actions || [])
const roadmap = computed(() => dashboard.value?.roadmap || {})

const score = computed(() => healthScore.value?.total_score || 0)
const scoreLevel = computed(() => {
  const s = score.value
  if (s >= 800) return '优秀'
  if (s >= 600) return '良好'
  if (s >= 400) return '一般'
  if (s >= 200) return '较差'
  return '危险'
})
const scoreColor = computed(() => {
  const s = score.value
  if (s >= 800) return '#10b981'
  if (s >= 600) return '#22c55e'
  if (s >= 400) return '#f59e0b'
  if (s >= 200) return '#f97316'
  return '#ef4444'
})

const dimensions = computed(() => healthScore.value?.dimensions || [])
const scoreChange = computed(() => healthScore.value?.score_change_7d || 0)

const netWorthData = computed(() => {
  const ao = assetOverview.value
  return [
    { name: '投资市值', value: ao.investment_value || 0, itemStyle: { color: '#1e40af' } },
    { name: '现金', value: ao.cash_balance || 0, itemStyle: { color: '#059669' } },
  ]
})

// ── 加载数据 ──
async function loadDashboard(forceRefresh = false) {
  loading.value = true
  try {
    const { data } = await getHealthV2Dashboard(forceRefresh)
    dashboard.value = data.result || data
  } catch (e) {
    showToast('加载诊断数据失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function loadProfile() {
  try {
    const { data } = await getHealthV2Profile()
    profile.value = data.result || data
    syncEditingProfile()
  } catch (e) {
    showToast('加载用户画像失败: ' + e.message, 'error')
  }
}

async function loadHistory() {
  try {
    const { data } = await getHealthV2History(30)
    history.value = data.result?.scores || data.scores || []
    nextTick(() => renderHistoryChart())
  } catch (e) {
    console.warn('加载历史趋势失败:', e)
  }
}

async function loadDefaults() {
  try {
    const { data } = await getHealthV2TargetPotsDefaults()
    targetPotsDefaults.value = data.result || data
  } catch (_) {}
}

async function refreshAll() {
  await Promise.all([loadDashboard(), loadProfile(), loadHistory()])
}

async function handleRecalculate() {
  recalculating.value = true
  try {
    const { data } = await recalculateHealthV2()
    dashboard.value = data.result || data
    await loadHistory()
    showToast('健康度已重新计算', 'success')
  } catch (e) {
    showToast('重新计算失败: ' + e.message, 'error')
  } finally {
    recalculating.value = false
  }
}

// ── 用户画像 ──
function syncEditingProfile() {
  if (!profile.value) return
  editingProfile.value = {
    risk_level: profile.value.risk_level || 'steady',
    target_date: profile.value.target_date || '',
    target_pots: profile.value.target_pots || { cash: 10, steady: 35, long_term: 50, insurance: 5 },
    monthly_investable: profile.value.monthly_investable || 0,
    emergency_months: profile.value.emergency_months || 6,
  }
}

function openProfileDrawer() {
  syncEditingProfile()
  showProfileDrawer.value = true
}

function applyRiskDefaults() {
  if (!targetPotsDefaults.value) return
  editingProfile.value.target_pots = { ...targetPotsDefaults.value[editingProfile.value.risk_level] }
}

async function saveProfile() {
  try {
    const { data } = await updateHealthV2Profile(editingProfile.value)
    profile.value = data.result || data
    showProfileDrawer.value = false
    showToast('用户画像已保存', 'success')
    await loadDashboard(true)
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error')
  }
}

// ── 行动项 ──
async function handleActionStatus(action, status) {
  try {
    await updateHealthV2ActionStatus(action.action_id, { status })
    action._status = status
    showToast(status === 'accepted' ? '已接受' : status === 'rejected' ? '已忽略' : '已标记执行', 'success')
  } catch (e) {
    showToast('操作失败: ' + e.message, 'error')
  }
}

// ── 图表 ──
async function renderHistoryChart() {
  if (!historyChartRef.value || !history.value.length) return
  const echarts = await getEcharts()
  if (historyChart) historyChart.dispose()
  historyChart = echarts.init(historyChartRef.value)

  const dates = history.value.map(h => h.score_date)
  const series = ['quality', 'diversification', 'valuation', 'behavior', 'risk'].map((key, idx) => ({
    name: ['选品质量', '分散配置', '估值合理', '持有行为', '风控纪律'][idx],
    type: 'line',
    smooth: true,
    symbol: 'circle',
    symbolSize: 6,
    data: history.value.map(h => h[key] || 0),
    emphasis: { focus: 'series' },
  }))
  series.push({
    name: '总分',
    type: 'line',
    smooth: true,
    symbol: 'circle',
    symbolSize: 8,
    lineStyle: { width: 3 },
    data: history.value.map(h => h.total_score || 0),
    emphasis: { focus: 'series' },
  })

  historyChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { bottom: 0, icon: 'circle' },
    grid: { left: 48, right: 24, top: 24, bottom: 40 },
    xAxis: { type: 'category', boundaryGap: false, data: dates },
    yAxis: { type: 'value', min: 0, max: 1000, splitLine: { lineStyle: { type: 'dashed' } } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 20, height: 16 }],
    series,
  })
}

function resizeCharts() {
  historyChart?.resize()
}

// ── 生命周期 ──
onMounted(() => {
  refreshAll()
  loadDefaults()
  window.addEventListener('resize', resizeCharts)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  if (historyChart) historyChart.dispose()
})

watch(showProfileDrawer, (v) => {
  if (v) syncEditingProfile()
})
</script>

<template>
  <div class="health-v2-page bg-mesh">
    <div class="page-header">
      <div>
        <h2 class="editorial-title-lg">全账户资产健康度诊断</h2>
        <p class="page-desc">资产全景 · 健康评分 · 四笔钱偏离 · 今日行动</p>
      </div>
      <div class="flex items-center gap-3">
        <button class="btn-secondary" @click="openProfileDrawer">
          <Icon name="config" class="w-4 h-4 mr-1" /> 投资画像
        </button>
        <button class="btn-primary" @click="handleRecalculate" :disabled="recalculating || loading">
          {{ recalculating ? '计算中...' : '重新诊断' }}
        </button>
      </div>
    </div>

    <div v-if="loading && !dashboard" class="flex justify-center py-20">
      <div class="spinner spinner-lg"></div>
    </div>

    <template v-else-if="dashboard">
      <!-- 资产全景 -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <div class="card metric-card">
          <div class="metric-label">净资产</div>
          <div class="metric-value font-jet-lg">¥{{ (assetOverview.net_worth || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</div>
          <div class="metric-delta" :class="assetOverview.profit_rate >= 0 ? 'text-profit' : 'text-loss'">
            盈亏 {{ (assetOverview.profit_rate || 0) >= 0 ? '+' : '' }}{{ ((assetOverview.profit_rate || 0) * 100).toFixed(2) }}%
          </div>
        </div>
        <div class="card metric-card">
          <div class="metric-label">投资市值</div>
          <div class="metric-value font-jet-lg">¥{{ (assetOverview.investment_value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</div>
          <div class="metric-delta text-secondary">占比 {{ ((assetOverview.investment_ratio || 0) * 100).toFixed(1) }}%</div>
        </div>
        <div class="card metric-card">
          <div class="metric-label">现金余额</div>
          <div class="metric-value font-jet-lg">¥{{ (assetOverview.cash_balance || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</div>
          <div class="metric-delta text-secondary">占比 {{ ((assetOverview.cash_ratio || 0) * 100).toFixed(1) }}%</div>
        </div>
        <div class="card metric-card">
          <div class="metric-label">持仓数量</div>
          <div class="metric-value font-jet-lg">{{ assetOverview.holding_count || 0 }}</div>
          <div class="metric-delta text-secondary">只基金</div>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-3 gap-5 mb-5">
        <!-- 健康分 -->
        <div class="card editorial-card lg:col-span-1">
          <div class="flex items-center justify-between mb-4">
            <h3 class="section-title">健康分 2.0</h3>
            <span v-if="scoreChange !== 0" class="change-badge" :class="scoreChange >= 0 ? 'up' : 'down'">
              {{ scoreChange >= 0 ? '↑' : '↓' }} {{ Math.abs(scoreChange) }}
            </span>
          </div>
          <div class="score-hero-v2">
            <div class="score-ring" :style="{ borderColor: scoreColor, color: scoreColor }">
              <span class="score-ring-number">{{ score }}</span>
              <span class="score-ring-level">{{ scoreLevel }}</span>
            </div>
          </div>
          <div class="mt-5 space-y-3">
            <div v-for="dim in dimensions" :key="dim.key" class="dimension-row">
              <div class="flex justify-between text-sm mb-1">
                <span class="text-secondary">{{ dim.label }}</span>
                <span class="font-medium">{{ dim.score }}/{{ dim.max }}</span>
              </div>
              <div class="dimension-bar-bg">
                <div class="dimension-bar-fill" :class="dim.rating"
                  :style="{ width: `${(dim.score / dim.max) * 100}%` }"></div>
              </div>
              <div v-if="dim.top_issue" class="text-xs text-warning mt-1">{{ dim.top_issue }}</div>
            </div>
          </div>
        </div>

        <!-- 四笔钱偏离 -->
        <div class="card editorial-card lg:col-span-2">
          <div class="flex items-center justify-between mb-4">
            <h3 class="section-title">四笔钱配置诊断</h3>
            <span class="risk-tag" :class="fourPots.max_drift > 10 ? 'alert' : fourPots.max_drift > 5 ? 'warning' : 'ok'">
              最大偏离 {{ fourPots.max_drift || 0 }}%
            </span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div v-for="pot in fourPots.pots || []" :key="pot.key" class="pot-card">
              <div class="flex items-center justify-between mb-2">
                <span class="font-medium">{{ pot.label }}</span>
                <span class="text-xs" :class="pot.status === 'ok' ? 'text-success' : pot.status === 'warning' ? 'text-warning' : 'text-danger'">
                  {{ pot.status === 'ok' ? '正常' : pot.status === 'warning' ? '轻度偏离' : '偏离较大' }}
                </span>
              </div>
              <div class="flex items-center gap-3 mb-2">
                <div class="flex-1">
                  <div class="text-xs text-secondary mb-1">实际 {{ pot.actual_pct }}%</div>
                  <div class="pot-bar-bg">
                    <div class="pot-bar-actual" :style="{ width: `${Math.min(pot.actual_pct, 100)}%` }"></div>
                  </div>
                </div>
                <div class="flex-1">
                  <div class="text-xs text-secondary mb-1">目标 {{ pot.target_pct }}%</div>
                  <div class="pot-bar-bg">
                    <div class="pot-bar-target" :style="{ width: `${Math.min(pot.target_pct, 100)}%` }"></div>
                  </div>
                </div>
              </div>
              <div class="text-xs" :class="pot.drift_pct > 0 ? 'text-profit' : 'text-loss'">
                {{ pot.drift_pct >= 0 ? '+' : '' }}{{ pot.drift_pct }}%
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 行动清单 -->
      <div class="card editorial-card mb-5">
        <div class="flex items-center justify-between mb-4">
          <h3 class="section-title">今日行动清单</h3>
          <span class="terminal-label">{{ actions.length }} 项待办</span>
        </div>
        <div v-if="actions.length" class="space-y-3">
          <div v-for="(action, idx) in actions" :key="action.action_id" class="action-item"
            :class="{ accepted: action._status === 'accepted', rejected: action._status === 'rejected', executed: action._status === 'executed' }">
            <div class="action-rank">{{ idx + 1 }}</div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="action-title">{{ action.title }}</span>
                <span v-if="action.amount" class="action-amount">¥{{ action.amount.toLocaleString('zh-CN') }}</span>
              </div>
              <div class="action-subtitle">{{ action.subtitle }}</div>
              <div class="flex items-center gap-2 mt-1">
                <span class="action-tag" :class="action.category">{{ action.cta }}</span>
                <span class="text-xs text-muted">影响 {{ action.impact }} · 紧迫 {{ action.urgency }}</span>
              </div>
            </div>
            <div class="action-btns">
              <button v-if="!action._status" class="btn-xs btn-success" @click="handleActionStatus(action, 'accepted')">接受</button>
              <button v-if="!action._status" class="btn-xs btn-ghost" @click="handleActionStatus(action, 'rejected')">忽略</button>
              <button v-if="action._status === 'accepted'" class="btn-xs btn-primary" @click="handleActionStatus(action, 'executed')">执行</button>
              <span v-else class="status-label">{{ action._status === 'rejected' ? '已忽略' : action._status === 'executed' ? '已执行' : '' }}</span>
            </div>
          </div>
        </div>
        <EmptyState v-else icon="check-circle" title="暂无待办行动" desc="当前资产状态良好，无需立即操作" />
      </div>

      <!-- 历史趋势 -->
      <div class="card editorial-card">
        <div class="flex items-center justify-between mb-4">
          <h3 class="section-title">健康分历史趋势</h3>
        </div>
        <div ref="historyChartRef" class="history-chart"></div>
        <EmptyState v-if="!history.length" icon="chart" title="暂无历史数据" desc="保存更多快照后将显示趋势图" />
      </div>
    </template>

    <EmptyState v-else icon="heart-pulse" title="暂无诊断数据" desc="点击上方按钮开始全账户资产健康度诊断" />

    <!-- 投资画像抽屉 -->
    <Teleport to="body">
      <Transition name="slide-right">
        <div v-if="showProfileDrawer" class="profile-drawer-overlay" @click.self="showProfileDrawer = false">
          <div class="profile-drawer">
            <div class="drawer-header">
              <h3 class="editorial-title">投资画像</h3>
              <button class="btn-icon" @click="showProfileDrawer = false"><Icon name="x" class="w-5 h-5" /></button>
            </div>
            <div class="drawer-body">
              <div class="form-group">
                <label>风险偏好</label>
                <select v-model="editingProfile.risk_level" class="form-select" @change="applyRiskDefaults">
                  <option value="conservative">保守型</option>
                  <option value="steady">稳健型</option>
                  <option value="aggressive">进取型</option>
                </select>
              </div>
              <div class="form-group">
                <label>目标日期</label>
                <input v-model="editingProfile.target_date" type="date" class="form-input" />
              </div>
              <div class="form-group">
                <label>每月可投资金额（元）</label>
                <input v-model.number="editingProfile.monthly_investable" type="number" min="0" class="form-input" />
              </div>
              <div class="form-group">
                <label>备用金月数</label>
                <input v-model.number="editingProfile.emergency_months" type="number" min="1" max="24" class="form-input" />
              </div>
              <div class="form-group">
                <label>四笔钱目标配比（%）</label>
                <div class="grid grid-cols-2 gap-3">
                  <div v-for="key in ['cash', 'steady', 'long_term', 'insurance']" :key="key">
                    <label class="text-xs text-secondary">{{ { cash: '活钱', steady: '稳健', long_term: '长期', insurance: '保险' }[key] }}</label>
                    <input v-model.number="editingProfile.target_pots[key]" type="number" min="0" max="100" class="form-input" />
                  </div>
                </div>
                <div class="text-xs mt-2" :class="Object.values(editingProfile.target_pots).reduce((a, b) => a + b, 0) === 100 ? 'text-success' : 'text-warning'">
                  合计 {{ Object.values(editingProfile.target_pots).reduce((a, b) => a + b, 0) }}%
                </div>
              </div>
            </div>
            <div class="drawer-footer">
              <button class="btn-secondary flex-1" @click="showProfileDrawer = false">取消</button>
              <button class="btn-primary flex-1" @click="saveProfile">保存</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.health-v2-page {
  padding: var(--space-5);
  min-height: 100%;
}

.metric-card {
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.metric-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.metric-value {
  font-size: 24px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.metric-delta {
  font-size: 12px;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.score-hero-v2 {
  display: flex;
  justify-content: center;
  padding: var(--space-4) 0;
}
.score-ring {
  width: 140px;
  height: 140px;
  border-radius: 50%;
  border: 8px solid;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--color-bg-card);
  box-shadow: var(--shadow-md);
}
.score-ring-number {
  font-size: 38px;
  font-weight: 700;
  line-height: 1;
}
.score-ring-level {
  font-size: 14px;
  margin-top: 4px;
}

.dimension-row {
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border-light);
}
.dimension-row:last-child {
  border-bottom: none;
}
.dimension-bar-bg {
  height: 8px;
  background: var(--color-bg-secondary);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.dimension-bar-fill {
  height: 100%;
  border-radius: var(--radius-sm);
  transition: width 0.5s ease;
}
.dimension-bar-fill.excellent { background: #10b981; }
.dimension-bar-fill.good { background: #3b82f6; }
.dimension-bar-fill.fair { background: #f59e0b; }
.dimension-bar-fill.poor { background: #ef4444; }

.risk-tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}
.risk-tag.ok { background: var(--color-success-bg); color: var(--color-success); }
.risk-tag.warning { background: var(--color-warning-bg); color: var(--color-warning); }
.risk-tag.alert { background: var(--color-danger-bg); color: var(--color-danger); }

.pot-card {
  padding: var(--space-3);
  background: var(--color-bg-secondary);
  border-radius: var(--radius-md);
}
.pot-bar-bg {
  height: 6px;
  background: var(--color-bg);
  border-radius: var(--radius-xs);
  overflow: hidden;
}
.pot-bar-actual {
  height: 100%;
  background: var(--color-primary);
  border-radius: var(--radius-xs);
}
.pot-bar-target {
  height: 100%;
  background: var(--color-success);
  border-radius: var(--radius-xs);
}

.action-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
}
.action-item:hover {
  border-color: var(--color-primary-border);
  box-shadow: var(--shadow-sm);
}
.action-item.accepted {
  background: var(--color-success-bg);
  border-color: var(--color-success-border);
}
.action-item.rejected {
  opacity: 0.6;
}
.action-rank {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.action-title {
  font-weight: 500;
  color: var(--color-text-primary);
}
.action-amount {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-danger);
  background: var(--color-danger-bg);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}
.action-subtitle {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.action-tag {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: var(--radius-xs);
  background: var(--color-primary-bg);
  color: var(--color-primary);
}
.action-btns {
  display: flex;
  gap: var(--space-2);
  flex-shrink: 0;
}
.status-label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.history-chart {
  width: 100%;
  height: 320px;
}

.profile-drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: var(--z-modal);
  display: flex;
  justify-content: flex-end;
}
.profile-drawer {
  width: 420px;
  max-width: 90vw;
  height: 100%;
  background: var(--color-bg-card);
  box-shadow: var(--shadow-xl);
  display: flex;
  flex-direction: column;
}
.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-4);
}
.drawer-footer {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.slide-right-enter-active,
.slide-right-leave-active {
  transition: opacity 0.25s ease;
}
.slide-right-enter-from,
.slide-right-leave-to {
  opacity: 0;
}
.slide-right-enter-active .profile-drawer,
.slide-right-leave-active .profile-drawer {
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
.slide-right-enter-from .profile-drawer,
.slide-right-leave-to .profile-drawer {
  transform: translateX(100%);
}

.change-badge {
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}
.change-badge.up { background: var(--color-profit-bg); color: var(--color-profit); }
.change-badge.down { background: var(--color-loss-bg); color: var(--color-loss); }

.btn-xs {
  padding: 4px 10px;
  font-size: 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  border: 1px solid transparent;
  transition: all var(--transition-fast);
}
.btn-success {
  background: var(--color-success);
  color: white;
}
.btn-ghost {
  background: transparent;
  border-color: var(--color-border);
  color: var(--color-text-secondary);
}
</style>
