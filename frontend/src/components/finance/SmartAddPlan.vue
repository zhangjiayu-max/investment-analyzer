<script setup>
/**
 * SmartAddPlan — 智能补仓计划器
 *
 * 双引擎：
 *  1. 估值 z-score 加权定投（日常）— 基础月投 × 估值倍数
 *  2. 金字塔补仓（极端下跌触发）— 亏损分档释放资金池
 *
 * 数据源：/api/smart-add/plan，含 plans / portfolio_view / summary / config。
 */
import { ref, computed, onMounted } from 'vue'
import Icon from '../ui/Icon.vue'
import { smartAddAPI } from '../../api'

// ── 状态 ──────────────────────────────
const loading = ref(true)
const error = ref('')
const plan = ref(null)            // generate_smart_add_plan 返回
const cfg = ref(null)             // 配置面板数据

// 折叠面板
const showSimulator = ref(false)
const showStratSim = ref(false)
const showConfig = ref(false)
const showCounterfactual = ref(false)

// 反事实决策验证
const counterfactual = ref(null)
const cfLoading = ref(false)
const cfError = ref('')

// 穿透指数集中度（维度2）
const indexExposure = ref(null)
const exposureLoading = ref(false)
const exposureError = ref('')

// 摊薄模拟器
const simFundCode = ref('')
const simDropPct = ref(-10)
const simAmount = ref(5000)
const simResult = ref(null)
const simLoading = ref(false)
const simError = ref('')

// 策略对比模拟器（新增）
const simStratLoading = ref(false)
const simStratResult = ref(null)
const simStratCode = ref('')
const simStratDrop = ref(-5)
const simStratMonths = ref(6)
const simStratError = ref('')

// 配置表单
const cfgForm = ref({
  base_dca_pct: 4.0,
  pool_pct: 15.0,
  loss_threshold: -10.0,
  max_single_position_pct: 25.0,
  valuation_pause_pct: 60.0,
  pyramid_tiers: '10:15,20:25,30:30,40:20,50:10',
  pyramid_enabled: true,
  max_add_vs_position_mult: 2.0,
  exit_signal_enabled: false,
  va_enabled: false,
  grid_enabled: false,
  fund_health_enabled: false,
})
const cfgSaving = ref(false)
const cfgSaved = ref(false)

// ── 计算属性 ──────────────────────────────
const summary = computed(() => plan.value?.summary || {})
const portfolioView = computed(() => plan.value?.portfolio_view || {})
const priorityList = computed(() => portfolioView.value.priority_list || [])
const rebalanceItems = computed(() => {
  const items = portfolioView.value.rebalance_suggestions || []
  return items.filter(i => i.action !== 'hold')
})
const allPlans = computed(() => plan.value?.plans || [])
// 有信号的标的 = 金字塔触发 OR 趋势加仓 OR 大跌定投（多维度触发器命中任一）
const deepPlans = computed(() =>
  allPlans.value.filter(p => p.has_signal || (p.pyramid && p.pyramid.triggered_tiers > 0))
)

// 信号类型 -> 短标签
const signalShortLabel = (p) => {
  if (!p.triggered_signals || !p.triggered_signals.length) return ''
  const map = { pyramid: 'A', trend: 'B', dip: 'C' }
  return p.triggered_signals
    .filter(s => s.triggered)
    .map(s => map[s.type] || '')
    .join('+')
}

const fmtMoney = (v) => {
  if (v == null || isNaN(v)) return '--'
  const n = Number(v)
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
}
const fmtPct = (v, digits = 2) => {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(digits) + '%'
}
const fmtNum = (v, digits = 2) => {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(digits)
}
const fmtSignedPct = (v, digits = 2) => {
  if (v == null || isNaN(v)) return '--'
  const n = Number(v)
  return (n > 0 ? '+' : '') + n.toFixed(digits) + '%'
}
// 盈亏率染色：项目约定 profit(红)/loss(绿)，亏损为负数 → 绿色
const profitColor = (v) => {
  if (v == null || isNaN(v)) return ''
  return Number(v) >= 0 ? 'var(--color-profit)' : 'var(--color-loss)'
}

// 估值等级 → 颜色
const levelColor = (level) => {
  if (!level) return ''
  if (level.includes('低估')) return 'var(--color-loss)'
  if (level.includes('高估')) return 'var(--color-profit)'
  return 'var(--color-text-secondary)'
}

// ── 数据加载 ──────────────────────────────
async function loadPlan() {
  loading.value = true
  error.value = ''
  try {
    const data = await smartAddAPI.getPlan()
    plan.value = data
    if (data?.config) {
      cfg.value = data.config
      syncCfgForm(data.config)
    }
  } catch (e) {
    error.value = e?.message || '加载补仓计划失败'
  } finally {
    loading.value = false
  }
}

async function loadConfig() {
  try {
    const data = await smartAddAPI.getConfig()
    cfg.value = data
    syncCfgForm(data)
  } catch { /* 静默，plan 已带 config */ }
}

// 加载穿透指数集中度
async function loadIndexExposure() {
  exposureLoading.value = true
  exposureError.value = ''
  try {
    indexExposure.value = await smartAddAPI.getIndexExposure()
  } catch (e) {
    exposureError.value = e?.message || '加载穿透集中度失败'
  } finally {
    exposureLoading.value = false
  }
}

// 穿透条目转为数组（按占比降序已由后端完成）
const exposureList = computed(() => {
  const exp = indexExposure.value?.exposure
  if (!exp) return []
  return Object.entries(exp).map(([code, info]) => ({ code, ...info }))
})

// 多维度决策层摘要：从 plan.position_sizing 取关键字段
const positionSizingView = (p) => {
  const ps = p?.position_sizing
  if (!ps) return null
  const tp = ps.target_position || {}
  return {
    effective_base: p.effective_base,
    target_driven_monthly: p.target_driven_monthly,
    final_suggested_amount: p.final_suggested_amount,
    target_position_pct: tp.target_pct,
    adjust_months: tp.adjust_months,
    valuation_bucket: tp.bucket,
    valuation_coeff: tp.valuation_coeff,
    exposure_warning: (ps.index_exposure && ps.index_exposure.warning) || null,
    first_position: ps.first_position,
    return_elasticity: ps.elasticity,
    cash_constraint: ps.cash_constraint,
    exit_signals: ps.exit_signals,
    summary: ps.summary,
    risk_multiplier: tp.components && tp.components.risk_mult,
  }
}

// 基金类型中文标签
const fundTypeLabel = (t) => {
  const m = { broad: '宽基', industry: '行业', theme: '主题', bond: '债基', hk_overseas: '港股/海外', unknown: '未分类' }
  return m[t] || t || '--'
}

// 反事实决策验证：加载假设操作跟踪结果
async function loadCounterfactual() {
  cfLoading.value = true
  cfError.value = ''
  try {
    counterfactual.value = await smartAddAPI.trackHypothetical()
  } catch (e) {
    cfError.value = e?.message || '加载反事实验证失败'
  } finally {
    cfLoading.value = false
  }
}

// 切换反事实面板时按需加载
async function toggleCounterfactual() {
  showCounterfactual.value = !showCounterfactual.value
  if (showCounterfactual.value && !counterfactual.value) {
    await loadCounterfactual()
  }
}

// 删除假设交易
async function removeHypothetical(txId) {
  if (!confirm('确认删除这条假设交易记录？')) return
  try {
    await smartAddAPI.deleteHypothetical(txId)
    await loadCounterfactual()
  } catch (e) {
    alert('删除失败：' + (e?.message || ''))
  }
}

function syncCfgForm(c) {
  if (!c) return
  cfgForm.value = {
    base_dca_pct: Number(c.base_dca_pct ?? 4.0),
    pool_pct: Number(c.pool_pct ?? 15.0),
    loss_threshold: Number(c.loss_threshold ?? -10.0),
    max_single_position_pct: Number(c.max_single_position_pct ?? 25.0),
    valuation_pause_pct: Number(c.valuation_pause_pct ?? 60.0),
    pyramid_tiers: tiersToString(c.tiers) || '10:15,20:25,30:30,40:20,50:10',
    pyramid_enabled: !!c.pyramid_enabled,
    max_add_vs_position_mult: Number(c.max_add_vs_position_mult ?? 2.0),
    exit_signal_enabled: !!c.exit_signal_enabled,
    va_enabled: !!c.va_enabled,
    grid_enabled: !!c.grid_enabled,
    fund_health_enabled: !!c.fund_health_enabled,
  }
}

function tiersToString(tiers) {
  if (!Array.isArray(tiers) || !tiers.length) return ''
  return tiers.map(t => `${Math.abs(t.loss_pct)}:${t.release_pct}`).join(',')
}

// ── 摊薄模拟器 ──────────────────────────────
async function runSim() {
  if (!simFundCode.value.trim()) {
    simError.value = '请输入基金代码'
    return
  }
  simLoading.value = true
  simError.value = ''
  simResult.value = null
  try {
    const data = await smartAddAPI.previewScenario(
      simFundCode.value.trim(),
      Number(simDropPct.value),
      Number(simAmount.value),
    )
    if (data?.error) simError.value = data.error
    else simResult.value = data
  } catch (e) {
    simError.value = e?.message || '模拟失败'
  } finally {
    simLoading.value = false
  }
}

// 策略对比模拟器
async function runSimulate() {
  if (!simStratCode.value.trim()) {
    simStratError.value = '请输入基金代码'
    return
  }
  simStratLoading.value = true
  simStratError.value = ''
  simStratResult.value = null
  try {
    const data = await smartAdd.simulate({
      fund_code: simStratCode.value.trim(),
      monthly_drop_pct: Number(simStratDrop.value),
      months: Number(simStratMonths.value),
    })
    if (data?.error) simStratError.value = data.error
    else simStratResult.value = data
  } catch (e) {
    simStratError.value = e?.message || '模拟失败'
  } finally {
    simStratLoading.value = false
  }
}

// 用深套标的快捷填充模拟器
function fillSim(fundCode) {
  simFundCode.value = fundCode
  showSimulator.value = true
}

// ── 保存配置 ──────────────────────────────
async function saveConfig() {
  cfgSaving.value = true
  cfgSaved.value = false
  try {
    await smartAddAPI.updateConfig({
      'smart_add.base_dca_pct': cfgForm.value.base_dca_pct,
      'smart_add.pool_pct': cfgForm.value.pool_pct,
      'smart_add.loss_threshold': cfgForm.value.loss_threshold,
      'smart_add.max_single_position_pct': cfgForm.value.max_single_position_pct,
      'smart_add.valuation_pause_pct': cfgForm.value.valuation_pause_pct,
      'smart_add.pyramid_tiers': cfgForm.value.pyramid_tiers,
      'smart_add.pyramid_enabled': cfgForm.value.pyramid_enabled,
      'smart_add.max_add_vs_position_mult': cfgForm.value.max_add_vs_position_mult,
      'smart_add.exit_signal_enabled': cfgForm.value.exit_signal_enabled,
      'smart_add.va_enabled': cfgForm.value.va_enabled,
      'smart_add.grid_enabled': cfgForm.value.grid_enabled,
      'smart_add.fund_health_enabled': cfgForm.value.fund_health_enabled,
    })
    cfgSaved.value = true
    setTimeout(() => { cfgSaved.value = false }, 2000)
    // 重新拉取计划表反映新配置
    await loadPlan()
  } catch (e) {
    simError.value = e?.message || '保存失败'
  } finally {
    cfgSaving.value = false
  }
}

onMounted(() => {
  loadPlan()
  loadIndexExposure()
})
</script>

<template>
  <div class="smart-add-page">
    <!-- 页头 -->
    <header class="page-head">
      <div class="page-head-left">
        <h2 class="page-title">智能补仓计划器</h2>
        <p class="page-desc">估值 z-score 加权定投 + 金字塔补仓双引擎，前瞻规划深套标的补仓路径</p>
      </div>
      <button class="btn-ghost" @click="loadPlan" :disabled="loading">
        <Icon name="refresh" size="15" :class="{ spinning: loading }" />
        <span>刷新</span>
      </button>
    </header>

    <!-- 加载中 -->
    <div v-if="loading" class="state-block">
      <Icon name="spinner" size="20" class="spinning" />
      <span>生成补仓计划中…</span>
    </div>

    <!-- 错误 -->
    <div v-else-if="error" class="state-block state-error">
      <Icon name="warning" size="18" />
      <span>{{ error }}</span>
      <button class="btn-ghost" @click="loadPlan">重试</button>
    </div>

    <!-- 未开启 / 无数据 -->
    <div v-else-if="plan && plan.enabled === false" class="state-block">
      <Icon name="info" size="18" />
      <span>{{ plan.message || '智能补仓计划器未开启' }}</span>
    </div>
    <div v-else-if="plan && !allPlans.length" class="state-block">
      <Icon name="info" size="18" />
      <span>{{ plan.message || '暂无持仓数据' }}</span>
    </div>

    <template v-else>
      <!-- ① 顶部总览卡 -->
      <section class="overview-grid">
        <div class="ov-card">
          <span class="ov-label">总资产</span>
          <span class="ov-value font-jet">¥{{ fmtMoney(summary.total_assets) }}</span>
        </div>
        <div class="ov-card">
          <span class="ov-label">资金池总额</span>
          <span class="ov-value font-jet">¥{{ fmtMoney(summary.pool_total) }}</span>
        </div>
        <div class="ov-card">
          <span class="ov-label">已释放</span>
          <span class="ov-value font-jet ov-used">¥{{ fmtMoney(summary.pool_used) }}</span>
        </div>
        <div class="ov-card">
          <span class="ov-label">剩余</span>
          <span class="ov-value font-jet ov-remain">¥{{ fmtMoney(summary.pool_remaining) }}</span>
        </div>
        <div class="ov-card">
          <span class="ov-label">深套标的数</span>
          <span class="ov-value font-jet ov-deep">{{ summary.deep_loss_count ?? 0 }}</span>
        </div>
        <div class="ov-card">
          <span class="ov-label">基础月投</span>
          <span class="ov-value font-jet">¥{{ fmtMoney(summary.base_monthly) }}</span>
        </div>
      </section>

      <!-- 资金池耗尽警告 -->
      <div v-if="summary.pool_exit_signals && summary.pool_exit_signals.length" class="pool-warn-banner">
        <Icon name="alert-triangle" size="16" />
        <span class="pool-warn-text">{{ summary.pool_exit_signals[0].suggested_action }}</span>
      </div>

      <!-- ①.5 穿透指数集中度（维度2 软提示） -->
      <section v-if="exposureList.length" class="block">
        <h3 class="block-title">
          <Icon name="layers" size="16" />
          <span>穿透指数集中度</span>
          <span class="block-subtitle">基于原始投入 max(total_cost, current_value)，超限仅警告不拦截</span>
        </h3>
        <div class="exposure-table-wrap">
          <table class="exposure-table">
            <thead>
              <tr>
                <th>指数代码</th>
                <th>类型</th>
                <th class="num">穿透占比</th>
                <th class="num">上限</th>
                <th class="num">余量</th>
                <th>状态</th>
                <th>涉及基金</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in exposureList"
                :key="item.code"
                :class="{ 'exposure-warn-row': item.warning?.exceeded }"
              >
                <td class="font-jet">{{ item.code }}</td>
                <td>
                  <span :class="['exposure-type-tag', `type-${item.fund_type}`]">
                    {{ fundTypeLabel(item.fund_type) }}
                  </span>
                </td>
                <td class="num font-jet">{{ fmtPct(item.total_pct) }}</td>
                <td class="num font-jet">{{ fmtPct(item.limit_pct, 0) }}</td>
                <td class="num font-jet" :class="item.warning?.exceeded ? 'text-loss' : ''">
                  {{ item.warning?.room_pct != null ? fmtPct(item.warning.room_pct) : '--' }}
                </td>
                <td>
                  <span v-if="item.warning?.exceeded" class="exposure-flag flag-warn">超限</span>
                  <span v-else class="exposure-flag flag-ok">正常</span>
                </td>
                <td class="exposure-funds">
                  <span v-for="(name, i) in item.fund_names" :key="i" class="exposure-fund-chip">{{ name }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="exposureError" class="exposure-error">{{ exposureError }}</div>
      </section>

      <!-- ② 组合视角：深套标的优先级排序表 -->
      <section v-if="priorityList.length" class="block">
        <h3 class="block-title">
          <Icon name="target" size="16" />
          <span>组合视角 · 深套标的优先级</span>
        </h3>
        <div class="table-wrap">
          <table class="priority-table">
            <thead>
              <tr>
                <th>基金名称</th>
                <th class="num">亏损率</th>
                <th class="num">z-score</th>
                <th>估值等级</th>
                <th class="num">已释放</th>
                <th class="num">剩余档位</th>
                <th class="num">下次触发</th>
                <th class="num">优先级</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in priorityList" :key="item.fund_code">
                <td class="fund-cell">
                  <span class="fund-name">{{ item.fund_name || '--' }}</span>
                  <span class="fund-code font-jet">{{ item.fund_code }}</span>
                </td>
                <td class="num" :style="{ color: profitColor((item.profit_rate || 0) * 100) }">
                  {{ fmtSignedPct((item.profit_rate || 0) * 100) }}
                </td>
                <td class="num font-jet">{{ item.zscore != null ? fmtNum(item.zscore) : '--' }}</td>
                <td :style="{ color: levelColor(item.valuation_level) }">{{ item.valuation_level || '--' }}</td>
                <td class="num font-jet">¥{{ fmtMoney(item.released_amount) }}</td>
                <td class="num">{{ item.remaining_tiers ?? 0 }}</td>
                <td class="num">
                  <template v-if="item.next_trigger">
                    {{ fmtPct(item.next_trigger.loss_pct) }} / ¥{{ fmtMoney(item.next_trigger.release_amount) }}
                  </template>
                  <span v-else class="muted">已全部触发</span>
                </td>
                <td class="num priority-cell">{{ item.priority || '★☆☆' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- ②.5 组合再平衡视角 -->
      <section v-if="rebalanceItems.length" class="block">
        <h3 class="block-title">
          <Icon name="refresh-cw" size="16" />
          <span>组合再平衡建议</span>
          <span class="block-subtitle">偏离等权配置>5%的标的</span>
        </h3>
        <div class="rebalance-grid">
          <div
            v-for="item in rebalanceItems"
            :key="item.fund_code"
            :class="['rebalance-card', `rebalance-${item.action}`]"
          >
            <div class="rebalance-head">
              <span class="rebalance-name">{{ item.fund_name }}</span>
              <span :class="['rebalance-tag', `tag-${item.action}`]">
                {{ { add: '加仓', reduce: '减仓', hold: '维持' }[item.action] }}
              </span>
            </div>
            <div class="rebalance-body">
              <div class="rebalance-row">
                <span>当前仓位</span>
                <span class="font-jet">{{ fmtSignedPct(item.current_pct) }}</span>
              </div>
              <div class="rebalance-row">
                <span>目标仓位</span>
                <span class="font-jet">{{ fmtSignedPct(item.target_pct) }}</span>
              </div>
              <div class="rebalance-row">
                <span>偏离</span>
                <span :class="item.deviation > 0 ? 'text-profit' : 'text-safe'" class="font-jet">
                  {{ fmtSignedPct(item.deviation) }}
                </span>
              </div>
              <div v-if="item.suggested_amount > 0" class="rebalance-row rebalance-amount">
                <span>建议调整</span>
                <span class="font-jet">¥{{ fmtMoney(item.suggested_amount) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ③ 计划表列表 -->
      <section v-if="deepPlans.length" class="block">
        <h3 class="block-title">
          <Icon name="trending-down" size="16" />
          <span>补仓计划表 · {{ deepPlans.length }} 个深套标的</span>
        </h3>
        <div class="plan-cards">
          <article
            v-for="p in deepPlans"
            :key="p.fund_code"
            class="plan-card"
            :class="{ 'pool-warn': p.pyramid?.pool_warning }"
          >
            <!-- 标的头部 -->
            <div class="plan-header">
              <div class="plan-header-left">
                <span class="plan-fund-name">{{ p.fund_name || '--' }}</span>
                <span class="plan-fund-code font-jet">{{ p.fund_code }}</span>
              </div>
              <div class="plan-header-right">
                <span class="badge badge-loss">
                  亏损 {{ fmtPct(p.profit_rate_pct) }}
                </span>
                <span class="badge badge-pos">占比 {{ fmtPct(p.position_pct) }}</span>
              </div>
            </div>

            <!-- 估值信息 -->
            <div v-if="p.valuation" class="plan-row plan-valuation">
              <div class="val-item">
                <span class="val-label">{{ p.valuation.metric_type || '估值' }}</span>
                <span class="val-value font-jet">{{ fmtNum(p.valuation.current_value) }}</span>
              </div>
              <div class="val-item">
                <span class="val-label">分位</span>
                <span class="val-value font-jet">{{ fmtPct(p.valuation.percentile) }}</span>
              </div>
              <div class="val-item">
                <span class="val-label">z-score</span>
                <span class="val-value font-jet">{{ fmtNum(p.valuation.zscore) }}</span>
              </div>
              <div class="val-item">
                <span class="term-with-tip val-level" :style="{ color: levelColor(p.valuation.level) }">
                  {{ p.valuation.level || '未知' }}
                  <span class="term-tip">
                    <b>估值等级说明</b><br/>
                    基于 z-score 划分：≤-2.5 极度低估，≤-1.5 深度低估，≤-0.5 低估，±0.5 合理区间，≤1.5 高估，&gt;1.5 深度高估。<br/>
                    <span class="muted">数据日期：{{ p.valuation.snapshot_date || '--' }}{{ p.valuation.is_expired ? '（已过期）' : '' }}</span>
                  </span>
                </span>
              </div>
            </div>
            <div v-else class="plan-row muted">该标的未关联指数估值数据</div>

            <!-- L2-L5 智能指标 -->
            <div class="smart-metrics" v-if="p.fund_type || p.kelly || p.recovery || p.win_rate">
              <div class="metrics-grid">
                <!-- L2 基金类型 -->
                <div class="metric-item" v-if="p.fund_type">
                  <span class="metric-label">基金类型</span>
                  <span class="metric-value font-jet">{{ p.fund_type }}</span>
                  <span class="metric-sub" v-if="p.type_strategy">定投倍数 ×{{ p.type_strategy.dca_mult }}</span>
                </div>
                <!-- L3 半凯利 -->
                <div class="metric-item" v-if="p.kelly && p.kelly.data_source !== 'default'">
                  <span class="metric-label">凯利上限</span>
                  <span class="metric-value font-jet">{{ fmtPct(p.kelly.limit_pct, 1) }}</span>
                  <span class="metric-sub">μ={{ fmtPct(p.kelly.mu * 100, 1) }} σ={{ fmtPct(p.kelly.sigma * 100, 1) }}</span>
                </div>
                <!-- L4 修复时间 -->
                <div class="metric-item" v-if="p.recovery && p.recovery.sample_count > 0">
                  <span class="metric-label">修复时间</span>
                  <span class="metric-value font-jet" v-if="p.recovery.median_recovery_months">{{ p.recovery.median_recovery_months }}月</span>
                  <span class="metric-value font-jet" v-else>未修复</span>
                  <span class="metric-sub">{{ p.recovery.sample_count }}个历史场景</span>
                </div>
                <!-- L5 胜率 -->
                <div class="metric-item" v-if="p.win_rate && p.win_rate.sample_count > 0">
                  <span class="metric-label">12月胜率</span>
                  <span class="metric-value font-jet" v-if="p.win_rate.win_rate_12m != null">{{ fmtPct(p.win_rate.win_rate_12m * 100, 0) }}</span>
                  <span class="metric-value font-jet" v-else>-</span>
                  <span class="metric-sub">{{ p.win_rate.sample_count }}个样本</span>
                </div>
              </div>
            </div>

            <!-- 引擎1：估值 z-score 加权定投 -->
            <div class="plan-row engine1-row">
              <span class="engine-tag engine1-tag">引擎1 · 加权定投</span>
              <span class="engine-formula">
                基础月投 ¥{{ fmtMoney(p.engine1?.base_monthly) }}
                <span class="x">×</span>
                <span class="multiplier-badge">{{ fmtNum(p.engine1?.multiplier) }}</span>
                = <span class="engine-result font-jet">¥{{ fmtMoney(p.engine1?.monthly_dca) }}</span>
              </span>
            </div>

            <!-- 引擎2：金字塔档位 -->
            <div v-if="p.pyramid" class="plan-row engine2-row">
              <div class="engine2-head">
                <span class="engine-tag engine2-tag">引擎2 · 金字塔补仓</span>
                <span class="engine2-meta">
                  已释放 ¥{{ fmtMoney(p.pyramid.released_amount) }} ·
                  剩余 {{ p.pyramid.remaining_tiers }} 档
                  <span v-if="p.pyramid.pool_warning" class="pool-warn-tag">{{ p.pyramid.pool_warning }}</span>
                </span>
              </div>

              <!-- 拦截/缩减提示（修复3/5/6） -->
              <div v-if="p.pyramid.blocked_reason || p.pyramid.capped_reason || p.pyramid.scale_reason" class="cap-warn-box">
                <Icon name="alert" size="13" />
                <span v-if="p.pyramid.blocked_reason" class="cap-blocked">{{ p.pyramid.blocked_reason }}</span>
                <span v-if="p.pyramid.capped_reason" class="cap-capped">
                  {{ p.pyramid.capped_reason }}（原 {{ fmtMoney(p.pyramid.scaled_from_position_cap) }} → {{ fmtMoney(p.pyramid.released_amount) }}）
                </span>
                <span v-if="p.pyramid.scale_reason" class="cap-scaled">
                  {{ p.pyramid.scale_reason }}（原 {{ fmtMoney(p.pyramid.scaled_from_pool) }} → {{ fmtMoney(p.pyramid.released_amount) }}）
                </span>
              </div>
              <table class="pyramid-table">
                <thead>
                  <tr>
                    <th class="num">档位</th>
                    <th class="num">亏损触发点</th>
                    <th class="num">释放金额</th>
                    <th class="num">累计</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="t in p.pyramid.tiers"
                    :key="t.tier"
                    :class="{ 'tier-triggered': t.triggered }"
                  >
                    <td class="num font-jet">{{ t.tier }}</td>
                    <td class="num">{{ fmtPct(t.loss_pct) }}</td>
                    <td class="num font-jet">¥{{ fmtMoney(t.release_amount) }}</td>
                    <td class="num font-jet">¥{{ fmtMoney(t.cumulative) }}</td>
                    <td>
                      <span :class="['tier-status', t.triggered ? 'st-triggered' : 'st-pending']">
                        {{ t.triggered ? '已触发' : '待触发' }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>

              <!-- 预估摊薄效果 -->
              <div v-if="p.pyramid.improvement_pct != null" class="improvement-box">
                <Icon name="chart" size="14" />
                <span>全档触发后预估摊薄效果：</span>
                <span class="improvement-val font-jet" :style="{ color: profitColor(p.pyramid.improvement_pct) }">
                  {{ fmtSignedPct(p.pyramid.improvement_pct) }}
                </span>
                <span class="muted">（成本 {{ fmtNum(p.pyramid.current_avg_cost, 4) }} → {{ fmtNum(p.pyramid.avg_cost_after_full_add, 4) }}）</span>
              </div>

              <!-- 下次触发提示 -->
              <div v-if="p.pyramid.next_trigger" class="next-trigger">
                <Icon name="info" size="13" />
                <span>下一档触发：亏损达 {{ fmtPct(p.pyramid.next_trigger.loss_pct) }}，释放 ¥{{ fmtMoney(p.pyramid.next_trigger.release_amount) }}</span>
              </div>
            </div>

            <!-- 安全阀 -->
            <div class="plan-row safety-row">
              <Icon name="shield-check" size="14" class="safety-icon" />
              <span class="safety-label">安全阀</span>
              <span class="safety-meta">
                仓位占比 {{ fmtPct(p.safety?.current_position_pct) }} / 上限 {{ fmtPct(p.safety?.max_position_pct) }}
              </span>
              <span :class="['safety-flag', p.safety?.can_add ? 'flag-ok' : 'flag-stop']">
                {{ p.safety?.can_add ? '可补仓' : '已达上限' }}
              </span>
              <button class="btn-link" @click="fillSim(p.fund_code)">模拟摊薄</button>
            </div>

            <!-- 多维度决策层（六维金额计算体系） -->
            <div v-if="positionSizingView(p)" class="plan-row ps-row">
              <div class="ps-head">
                <Icon name="compass" size="14" />
                <span class="ps-title">多维度决策层</span>
                <span v-if="p.final_suggested_amount != null" class="ps-final">
                  最终建议 <b class="font-jet">¥{{ fmtMoney(p.final_suggested_amount) }}</b>
                </span>
                <span v-if="positionSizingView(p).risk_multiplier != null" class="ps-risk">
                  风险偏好 ×{{ fmtNum(positionSizingView(p).risk_multiplier) }}
                </span>
              </div>
              <div class="ps-summary" v-if="positionSizingView(p).summary">
                <Icon name="info" size="12" />
                <span>{{ positionSizingView(p).summary }}</span>
              </div>
              <div class="ps-grid">
                <!-- 维度1：目标仓位锚定 -->
                <div class="ps-item ps-d1">
                  <span class="ps-label">维度1 · 目标仓位</span>
                  <span class="ps-value font-jet" v-if="positionSizingView(p).target_position_pct != null">
                    {{ fmtPct(positionSizingView(p).target_position_pct) }}
                  </span>
                  <span class="ps-value font-jet" v-else>--</span>
                  <span class="ps-sub">
                    <span v-if="positionSizingView(p).valuation_bucket">{{ positionSizingView(p).valuation_bucket }}</span>
                    <span v-if="positionSizingView(p).valuation_coeff != null"> ×{{ fmtNum(positionSizingView(p).valuation_coeff) }}</span>
                    <span v-if="positionSizingView(p).adjust_months"> / {{ positionSizingView(p).adjust_months }}月</span>
                  </span>
                  <span class="ps-sub" v-if="p.target_driven_monthly != null">
                    目标驱动月投 ¥{{ fmtMoney(p.target_driven_monthly) }}
                  </span>
                </div>
                <!-- 维度2：穿透集中度软提示 -->
                <div
                  class="ps-item ps-d2"
                  :class="{ 'ps-warn': positionSizingView(p).exposure_warning?.exceeded }"
                >
                  <span class="ps-label">维度2 · 穿透集中度</span>
                  <span class="ps-value font-jet" v-if="positionSizingView(p).exposure_warning?.current_pct != null">
                    {{ fmtPct(positionSizingView(p).exposure_warning.current_pct) }}
                    / 上限 {{ fmtPct(positionSizingView(p).exposure_warning.limit_pct, 0) }}
                  </span>
                  <span class="ps-value font-jet" v-else>未关联指数</span>
                  <span class="ps-sub" v-if="positionSizingView(p).exposure_warning?.message">
                    {{ positionSizingView(p).exposure_warning.message }}
                  </span>
                </div>
                <!-- 维度3：首次建仓标准仓 -->
                <div class="ps-item ps-d3" v-if="positionSizingView(p).first_position">
                  <span class="ps-label">维度3 · 首次建仓</span>
                  <span class="ps-value font-jet" v-if="positionSizingView(p).first_position.first_pct != null">
                    标准 {{ fmtPct(positionSizingView(p).first_position.first_pct, 0) }}
                  </span>
                  <span class="ps-value font-jet" v-else>--</span>
                  <span
                    class="ps-sub"
                    v-if="positionSizingView(p).first_position.needed && positionSizingView(p).first_position.target_add_total != null"
                  >
                    缺口 ¥{{ fmtMoney(positionSizingView(p).first_position.target_add_total) }} · 月补 ¥{{ fmtMoney(positionSizingView(p).first_position.monthly_add) }}
                  </span>
                  <span class="ps-sub" v-else>
                    {{ positionSizingView(p).first_position.reason || '原投入已达标' }}
                  </span>
                </div>
                <!-- 维度4：收益弹性 -->
                <div
                  class="ps-item ps-d4"
                  :class="{ 'ps-warn': positionSizingView(p).return_elasticity && positionSizingView(p).return_elasticity.level !== 'normal' }"
                  v-if="positionSizingView(p).return_elasticity"
                >
                  <span class="ps-label">维度4 · 收益弹性</span>
                  <span class="ps-value font-jet" v-if="positionSizingView(p).return_elasticity.elasticity != null">
                    {{ fmtSignedPct(positionSizingView(p).return_elasticity.elasticity) }}
                  </span>
                  <span class="ps-value font-jet" v-else>--</span>
                  <span class="ps-sub" v-if="positionSizingView(p).return_elasticity.message">
                    {{ positionSizingView(p).return_elasticity.message }}
                  </span>
                  <span
                    class="ps-sub"
                    v-else-if="positionSizingView(p).return_elasticity.expected_return_pct != null"
                  >
                    预期{{ fmtSignedPct(positionSizingView(p).return_elasticity.expected_return_pct) }} · 仓位{{ fmtPct(positionSizingView(p).return_elasticity.position_pct) }}
                  </span>
                </div>
                <!-- 维度6：资金约束 -->
                <div class="ps-item ps-d6" v-if="positionSizingView(p).cash_constraint">
                  <span class="ps-label">维度6 · 资金约束</span>
                  <span class="ps-value font-jet" v-if="positionSizingView(p).cash_constraint.total_available_3m != null">
                    ¥{{ fmtMoney(positionSizingView(p).cash_constraint.total_available_3m) }}
                  </span>
                  <span class="ps-value font-jet" v-else>--</span>
                  <span class="ps-sub" v-if="positionSizingView(p).cash_constraint.position_room_pct != null">
                    可加仓位 {{ fmtPct(positionSizingView(p).cash_constraint.position_room_pct) }}
                  </span>
                </div>
                <!-- 有效基准（核心：原始投入驱动） -->
                <div class="ps-item ps-base">
                  <span class="ps-label">有效基准（原始投入驱动）</span>
                  <span class="ps-value font-jet" v-if="p.effective_base != null">
                    ¥{{ fmtMoney(p.effective_base) }}
                  </span>
                  <span class="ps-value font-jet" v-else>--</span>
                  <span class="ps-sub">max(总成本, 当前市值)</span>
                </div>
              </div>
              <!-- 维度5：减仓信号（如有触发） -->
              <div
                v-if="positionSizingView(p).exit_signals && positionSizingView(p).exit_signals.filter(s => s.triggered).length"
                class="ps-exit-list"
              >
                <div
                  v-for="(sig, idx) in positionSizingView(p).exit_signals.filter(s => s.triggered)"
                  :key="idx"
                  :class="['ps-exit-item', `ps-exit-${sig.severity || 'info'}`]"
                >
                  <span class="ps-exit-tag">{{ sig.label || '减仓信号' }}</span>
                  <span class="ps-exit-action">{{ sig.suggested_action }}</span>
                  <span class="ps-exit-reason">{{ sig.reason }}</span>
                </div>
              </div>
            </div>

            <!-- 多维度触发器：命中的信号列表 -->
            <div v-if="p.triggered_signals && p.triggered_signals.length" class="plan-row signals-row">
              <div class="signals-head">
                <Icon name="zap" size="14" />
                <span class="signals-title">触发信号（多维度）</span>
                <span v-if="p.total_suggested > 0" class="signals-total">
                  合计建议 <b class="font-jet">¥{{ fmtMoney(p.total_suggested) }}</b>
                </span>
              </div>
              <div class="signals-list">
                <div
                  v-for="(sig, idx) in p.triggered_signals"
                  :key="idx"
                  :class="['signal-item', `sig-${sig.type}`, sig.triggered ? 'sig-on' : 'sig-blocked']"
                >
                  <div class="signal-head">
                    <span class="signal-tag">{{ { pyramid: 'A', trend: 'B', dip: 'C' }[sig.type] || '?' }}</span>
                    <span class="signal-label">{{ sig.label }}</span>
                    <span v-if="sig.triggered" class="signal-amount font-jet">¥{{ fmtMoney(sig.amount) }}</span>
                    <span v-else class="signal-blocked-tag">已拦截</span>
                    <span v-if="sig.tag" class="signal-sub-tag">{{ sig.tag }}</span>
                  </div>
                  <div v-if="sig.reason" class="signal-reason">{{ sig.reason }}</div>
                  <div v-if="sig.blocked_reason" class="signal-reason blocked">{{ sig.blocked_reason }}</div>
                  <div v-if="sig.conditions_met && sig.conditions_met.length" class="signal-conditions">
                    <span v-for="(c, ci) in sig.conditions_met" :key="ci" class="cond-chip">{{ c }}</span>
                  </div>
                  <!-- 信号B风险提示 -->
                  <div v-if="sig.type === 'trend' && sig.risk_note && sig.triggered" class="risk-warn-box">
                    <Icon name="alert-triangle" size="12" />
                    <span>{{ sig.risk_note }}</span>
                  </div>
                  <!-- 金额计算依据（动态化公式） -->
                  <div v-if="sig.triggered && sig.amount_formula" class="signal-formula">
                    <span class="formula-label">本次建议补仓</span>
                    <b class="formula-amount font-jet">¥{{ fmtMoney(sig.amount) }}</b>
                    <span class="formula-detail font-jet">{{ sig.amount_formula.formula }}</span>
                    <span class="formula-breakdown">
                      <span v-if="sig.amount_formula.base_amount">基数¥{{ fmtMoney(sig.amount_formula.base_amount) }}</span>
                      <span v-if="sig.amount_formula.trend_strength_mult">趋势{{ sig.amount_formula.trend_strength_mult }}×</span>
                      <span v-if="sig.amount_formula.valuation_mult">估值{{ sig.amount_formula.valuation_mult }}×</span>
                      <span v-if="sig.amount_formula.drop_mult">跌幅{{ sig.amount_formula.drop_mult }}×</span>
                      <span v-if="sig.amount_formula.loss_mult">亏损{{ sig.amount_formula.loss_mult }}×</span>
                      <span v-if="sig.amount_formula.room_mult">仓位余量{{ sig.amount_formula.room_mult }}×</span>
                      <span v-if="sig.amount_formula.recent_buy_deducted > 0" class="deduct-info">已补扣减¥{{ fmtMoney(sig.amount_formula.recent_buy_deducted) }}</span>
                      <span class="pos-info">当前仓位{{ sig.amount_formula.current_position_pct }}%</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 退出信号（止盈/止损/暂停） -->
            <div v-if="p.exit_signals && p.exit_signals.length" class="plan-row exit-signals-row">
              <div class="exit-signals-head">
                <Icon name="alert-triangle" size="14" />
                <span class="exit-signals-title">退出信号</span>
              </div>
              <div class="exit-signals-list">
                <div
                  v-for="(sig, idx) in p.exit_signals"
                  :key="idx"
                  :class="['exit-item', `exit-${sig.severity}`]"
                >
                  <div class="exit-head">
                    <span class="exit-tag">{{ sig.label }}</span>
                    <span class="exit-action">{{ sig.suggested_action }}</span>
                  </div>
                  <div class="exit-reason">{{ sig.reason }}</div>
                </div>
              </div>
            </div>

            <!-- 价值平均法（VA）结果 -->
            <div v-if="p.va_result" class="plan-row va-row">
              <div class="va-head">
                <Icon name="trending-up" size="14" />
                <span class="va-title">价值平均法（VA）</span>
                <span :class="['va-action-tag', `va-${p.va_result.action}`]">
                  {{ { buy: '建议买入', sell: '建议卖出', hold: '维持不变' }[p.va_result.action] || p.va_result.action }}
                </span>
              </div>
              <div class="va-body">
                <div class="va-reason">{{ p.va_result.reason }}</div>
                <div class="va-meta">
                  <span>目标市值 ¥{{ fmtMoney(p.va_result.target_value) }}</span>
                  <span>实际市值 ¥{{ fmtMoney(p.va_result.actual_value) }}</span>
                  <span :class="p.va_result.gap_pct > 0 ? 'text-profit' : 'text-loss'">偏离 {{ fmtSignedPct(p.va_result.gap_pct) }}</span>
                  <span v-if="p.va_result.action === 'buy' && p.va_result.amount > 0" class="va-amount font-jet">建议 ¥{{ fmtMoney(p.va_result.amount) }}</span>
                  <span class="va-max">上限 ¥{{ fmtMoney(p.va_result.max_monthly) }}</span>
                </div>
              </div>
            </div>

            <!-- 网格交易结果 -->
            <div v-if="p.grid_result" class="plan-row grid-row">
              <div class="grid-head">
                <Icon name="grid" size="14" />
                <span class="grid-title">网格交易</span>
                <span :class="['grid-action-tag', `grid-${p.grid_result.action}`]">
                  {{ { buy: '建议买入', sell: '建议卖出', wait: '等待触发' }[p.grid_result.action] || p.grid_result.action }}
                </span>
              </div>
              <div class="grid-body">
                <div class="grid-reason">{{ p.grid_result.reason }}</div>
                <div class="grid-meta">
                  <span>区间 ¥{{ fmtNum(p.grid_result.price_low, 4) }} ~ ¥{{ fmtNum(p.grid_result.price_high, 4) }}</span>
                  <span>步长 ¥{{ fmtNum(p.grid_result.grid_step, 4) }}</span>
                  <span v-if="p.grid_result.action !== 'wait'" class="grid-amount font-jet">每格 ¥{{ fmtMoney(p.grid_result.suggested_amount) }}</span>
                </div>
              </div>
            </div>

            <!-- 基本面健康检查 -->
            <div v-if="p.fund_health && p.fund_health.data_available" class="plan-row health-row">
              <div class="health-head">
                <Icon name="activity" size="14" />
                <span class="health-title">基本面健康</span>
                <span :class="['health-tag', p.fund_health.healthy ? 'health-ok' : 'health-risk']">
                  {{ p.fund_health.healthy ? '健康' : '有风险' }}
                </span>
              </div>
              <div class="health-body">
                <div v-if="p.fund_health.warnings && p.fund_health.warnings.length" class="health-warnings">
                  <span v-for="(w, wi) in p.fund_health.warnings" :key="wi" class="health-item health-warn">{{ w }}</span>
                </div>
                <div v-if="p.fund_health.risks && p.fund_health.risks.length" class="health-risks">
                  <span v-for="(r, ri) in p.fund_health.risks" :key="ri" class="health-item health-danger">{{ r }}</span>
                </div>
                <div v-if="!p.fund_health.warnings?.length && !p.fund_health.risks?.length" class="health-clean">未发现明显问题</div>
              </div>
            </div>
          </article>
        </div>
      </section>

      <!-- ④ 摊薄模拟器（折叠） -->
      <section class="block">
        <button class="collapse-head" @click="showSimulator = !showSimulator">
          <Icon name="chevron-right" size="16" class="chevron" :class="{ expanded: showSimulator }" />
          <Icon name="chart" size="16" />
          <span>摊薄模拟器</span>
          <span class="muted collapse-hint">输入下跌幅度与补仓金额，预估摊薄效果</span>
        </button>
        <div v-show="showSimulator" class="collapse-body">
          <div class="sim-form">
            <div class="sim-field">
              <label>基金代码</label>
              <input v-model="simFundCode" type="text" placeholder="如 110022" class="sim-input font-jet" />
            </div>
            <div class="sim-field">
              <label>额外下跌 (%)</label>
              <input v-model.number="simDropPct" type="number" step="1" class="sim-input font-jet" />
            </div>
            <div class="sim-field">
              <label>补仓金额 (元)</label>
              <input v-model.number="simAmount" type="number" step="100" class="sim-input font-jet" />
            </div>
            <button class="btn-primary" @click="runSim" :disabled="simLoading">
              <Icon name="spinner" v-if="simLoading" size="14" class="spinning" />
              <span>{{ simLoading ? '计算中…' : '计算' }}</span>
            </button>
          </div>
          <p v-if="simError" class="sim-error">
            <Icon name="warning" size="14" />{{ simError }}
          </p>
          <div v-if="simResult" class="sim-result">
            <div class="sim-result-head">
              <span>{{ simResult.fund_name || simResult.fund_code }}</span>
              <span class="muted">现价 {{ fmtNum(simResult.current_price, 4) }} → 模拟价 {{ fmtNum(simResult.simulated_price, 4) }}</span>
            </div>
            <div class="sim-result-grid">
              <div class="sr-item">
                <span class="sr-label">当前成本</span>
                <span class="sr-value font-jet">{{ fmtNum(simResult.current_avg_cost, 4) }}</span>
              </div>
              <div class="sr-item">
                <span class="sr-label">补仓后成本</span>
                <span class="sr-value font-jet sr-new">{{ fmtNum(simResult.new_avg_cost, 4) }}</span>
              </div>
              <div class="sr-item">
                <span class="sr-label">当前盈亏</span>
                <span class="sr-value font-jet" :style="{ color: profitColor(simResult.current_profit_rate * 100) }">
                  {{ fmtSignedPct(simResult.current_profit_rate * 100) }}
                </span>
              </div>
              <div class="sr-item">
                <span class="sr-label">补仓后盈亏</span>
                <span class="sr-value font-jet" :style="{ color: profitColor(simResult.new_profit_rate * 100) }">
                  {{ fmtSignedPct(simResult.new_profit_rate * 100) }}
                </span>
              </div>
              <div class="sr-item sr-improve">
                <span class="sr-label">改善百分点</span>
                <span class="sr-value font-jet" :style="{ color: profitColor(simResult.improvement_pct) }">
                  {{ fmtSignedPct(simResult.improvement_pct) }}
                </span>
              </div>
              <div class="sr-item">
                <span class="sr-label">补仓后份额</span>
                <span class="sr-value font-jet">{{ fmtNum(simResult.new_shares) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ④.5 策略对比模拟器（折叠） -->
      <section class="block">
        <button class="collapse-head" @click="showStratSim = !showStratSim">
          <Icon name="bar-chart" size="16" />
          <span>策略对比模拟器</span>
          <span class="collapse-hint">{{ showStratSim ? '收起' : '展开' }}</span>
        </button>
        <div v-if="showStratSim" class="sim-panel">
          <div class="sim-form">
            <input v-model="simStratCode" placeholder="基金代码" class="sim-input" style="width:100px" />
            <label class="sim-label">
              月跌
              <input v-model.number="simStratDrop" type="number" step="0.5" class="sim-input" style="width:70px" />%
            </label>
            <label class="sim-label">
              月数
              <input v-model.number="simStratMonths" type="number" min="1" max="24" class="sim-input" style="width:60px" />
            </label>
            <button class="btn btn-sm btn-primary" :disabled="simStratLoading" @click="runSimulate">
              {{ simStratLoading ? '模拟中...' : '对比' }}
            </button>
          </div>
          <div v-if="simStratError" class="sim-error">{{ simStratError }}</div>
          <div v-if="simStratResult" class="sim-strategies">
            <div
              v-for="(s, idx) in simStratResult.strategies"
              :key="idx"
              :class="['sim-card', s.is_best ? 'sim-best' : '']"
            >
              <div class="sim-card-head">
                <Icon :name="s.icon" size="14" />
                <span>{{ s.name }}</span>
                <span v-if="s.is_best" class="sim-best-badge">最优</span>
              </div>
              <div class="sim-card-body">
                <div class="sim-row">
                  <span>最终成本</span>
                  <span class="font-jet">{{ fmtNum(s.final_cost, 4) }}</span>
                </div>
                <div class="sim-row">
                  <span>最终市值</span>
                  <span class="font-jet">¥{{ fmtMoney(s.final_value) }}</span>
                </div>
                <div class="sim-row">
                  <span>总投入</span>
                  <span class="font-jet">¥{{ fmtMoney(s.total_invested) }}</span>
                </div>
                <div class="sim-row">
                  <span>盈亏率</span>
                  <span class="font-jet" :style="{ color: profitColor(s.profit_rate) }">
                    {{ fmtSignedPct(s.profit_rate) }}
                  </span>
                </div>
                <div class="sim-desc">{{ s.description }}</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ⑤ 配置面板（折叠） -->
      <section class="block">
        <button class="collapse-head" @click="showConfig = !showConfig">
          <Icon name="chevron-right" size="16" class="chevron" :class="{ expanded: showConfig }" />
          <Icon name="config" size="16" />
          <span>配置面板</span>
          <span class="muted collapse-hint">倍数公式 / 资金池比例 / 金字塔档位 / 安全阀</span>
        </button>
        <div v-show="showConfig" class="collapse-body">
          <div class="cfg-formula">
            <Icon name="info" size="14" />
            <span>估值倍数公式：<code class="font-jet">clamp(1.0 + (-z_score) × 0.5, 0, 3.0)</code>　z=-2 → ×2.0，z=0 → ×1.0，z=+2 → ×0</span>
          </div>
          <div class="cfg-grid">
            <div class="cfg-field">
              <label>基础定投年化 (%)</label>
              <input v-model.number="cfgForm.base_dca_pct" type="number" step="0.5" class="cfg-input font-jet" />
            </div>
            <div class="cfg-field">
              <label>资金池比例 (%)</label>
              <input v-model.number="cfgForm.pool_pct" type="number" step="1" class="cfg-input font-jet" />
            </div>
            <div class="cfg-field">
              <label>金字塔触发阈值 (%)</label>
              <input v-model.number="cfgForm.loss_threshold" type="number" step="1" class="cfg-input font-jet" />
            </div>
            <div class="cfg-field">
              <label>单标的仓位上限 (%)</label>
              <input v-model.number="cfgForm.max_single_position_pct" type="number" step="1" class="cfg-input font-jet" />
            </div>
            <div class="cfg-field">
              <label>补仓额≤原市值×倍数</label>
              <input v-model.number="cfgForm.max_add_vs_position_mult" type="number" step="0.5" min="0.5" class="cfg-input font-jet" />
            </div>
            <div class="cfg-field">
              <label>估值暂停阈值 (分位%)</label>
              <input v-model.number="cfgForm.valuation_pause_pct" type="number" step="5" class="cfg-input font-jet" />
            </div>
            <div class="cfg-field cfg-field-wide">
              <label>金字塔档位 (亏损%:释放%)</label>
              <input v-model="cfgForm.pyramid_tiers" type="text" class="cfg-input font-jet" placeholder="10:15,20:25,30:30,40:20,50:10" />
            </div>
            <div class="cfg-field cfg-field-check">
              <label class="check-label">
                <input type="checkbox" v-model="cfgForm.pyramid_enabled" />
                <span>启用金字塔补仓引擎</span>
              </label>
            </div>
            <div class="cfg-field cfg-field-check">
              <label class="check-label">
                <input type="checkbox" v-model="cfgForm.exit_signal_enabled" />
                <span>启用退出信号（止盈/止损/暂停）</span>
              </label>
            </div>
            <div class="cfg-field cfg-field-check">
              <label class="check-label">
                <input type="checkbox" v-model="cfgForm.va_enabled" />
                <span>启用价值平均法（VA）</span>
              </label>
            </div>
            <div class="cfg-field cfg-field-check">
              <label class="check-label">
                <input type="checkbox" v-model="cfgForm.grid_enabled" />
                <span>启用网格交易</span>
              </label>
            </div>
            <div class="cfg-field cfg-field-check">
              <label class="check-label">
                <input type="checkbox" v-model="cfgForm.fund_health_enabled" />
                <span>启基本面健康检查</span>
              </label>
            </div>
          </div>
          <div class="cfg-actions">
            <button class="btn-primary" @click="saveConfig" :disabled="cfgSaving">
              <Icon name="spinner" v-if="cfgSaving" size="14" class="spinning" />
              <span>{{ cfgSaving ? '保存中…' : '保存配置' }}</span>
            </button>
            <span v-if="cfgSaved" class="cfg-saved">
              <Icon name="check" size="14" /> 已保存
            </span>
          </div>
        </div>
      </section>

      <!-- ⑤ 反事实决策验证 -->
      <section class="cfg-section">
        <header class="cfg-head" @click="toggleCounterfactual">
          <h3 class="cfg-title">
            <Icon name="check-circle" size="16" />
            <span>反事实决策验证</span>
            <span class="cf-badge" v-if="counterfactual?.summary?.total_count > 0">
              {{ counterfactual.summary.total_count }}条
            </span>
          </h3>
          <Icon name="chevron-down" size="16" :class="{ rotated: showCounterfactual }" />
        </header>

        <div v-if="showCounterfactual" class="cfg-body">
          <p class="cf-intro">
            系统每次生成补仓建议时自动创建"假设交易"。此处跟踪：如果过去都按建议补仓了，现在赚/亏多少。
            <button class="btn-link" @click="loadCounterfactual" :disabled="cfLoading">
              {{ cfLoading ? '验证中…' : '刷新验证' }}
            </button>
          </p>

          <div v-if="cfError" class="cf-error">{{ cfError }}</div>

          <div v-else-if="counterfactual" class="cf-content">
            <!-- 汇总卡 -->
            <div class="cf-summary-grid" v-if="counterfactual.summary">
              <div class="cf-sum-card">
                <span class="cf-sum-label">假设累计投入</span>
                <span class="cf-sum-value font-jet">¥{{ fmtMoney(counterfactual.summary.total_hypothetical_invested) }}</span>
              </div>
              <div class="cf-sum-card">
                <span class="cf-sum-label">假设当前市值</span>
                <span class="cf-sum-value font-jet">¥{{ fmtMoney(counterfactual.summary.total_hypothetical_value) }}</span>
              </div>
              <div class="cf-sum-card">
                <span class="cf-sum-label">假设累计盈亏</span>
                <span class="cf-sum-value font-jet" :style="{ color: profitColor(counterfactual.summary.total_profit_loss) }">
                  {{ counterfactual.summary.total_profit_loss >= 0 ? '+' : '' }}¥{{ fmtMoney(counterfactual.summary.total_profit_loss) }}
                </span>
              </div>
              <div class="cf-sum-card">
                <span class="cf-sum-label">假设收益率</span>
                <span class="cf-sum-value font-jet" :style="{ color: profitColor(counterfactual.summary.total_profit_rate) }">
                  {{ fmtSignedPct(counterfactual.summary.total_profit_rate * 100) }}
                </span>
              </div>
              <div class="cf-sum-card">
                <span class="cf-sum-label">已回本数</span>
                <span class="cf-sum-value font-jet">{{ counterfactual.summary.breakeven_count }} / {{ counterfactual.summary.total_count }}</span>
              </div>
              <div class="cf-sum-card" v-if="counterfactual.comparison?.real_portfolio_profit_rate != null">
                <span class="cf-sum-label">真实组合收益率</span>
                <span class="cf-sum-value font-jet" :style="{ color: profitColor(counterfactual.comparison.real_portfolio_profit_rate) }">
                  {{ fmtSignedPct(counterfactual.comparison.real_portfolio_profit_rate * 100) }}
                </span>
              </div>
            </div>

            <!-- 改善幅度提示 -->
            <div class="cf-improvement" v-if="counterfactual.comparison?.improvement != null">
              <Icon name="trending-up" size="14" />
              <span>
                如果当时按建议补仓，收益率改善
                <strong :style="{ color: profitColor(counterfactual.comparison.improvement) }">
                  {{ fmtSignedPct(counterfactual.comparison.improvement * 100) }}
                </strong>
              </span>
            </div>

            <!-- 假设交易明细 -->
            <div class="cf-txs" v-if="counterfactual.hypothetical_txs?.length">
              <h4 class="cf-sub-title">假设交易明细</h4>
              <table class="cf-table">
                <thead>
                  <tr>
                    <th>基金</th>
                    <th>买入日</th>
                    <th>买入价</th>
                    <th>现价</th>
                    <th>投入</th>
                    <th>市值</th>
                    <th>盈亏</th>
                    <th>收益率</th>
                    <th>持有天数</th>
                    <th>状态</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="tx in counterfactual.hypothetical_txs" :key="tx.tx_id">
                    <td>{{ tx.fund_name || tx.fund_code }}</td>
                    <td>{{ tx.buy_date }}</td>
                    <td class="font-jet">{{ tx.buy_price ?? '--' }}</td>
                    <td class="font-jet">{{ tx.current_price ?? '--' }}</td>
                    <td class="font-jet">¥{{ fmtMoney(tx.buy_amount) }}</td>
                    <td class="font-jet">{{ tx.current_value != null ? '¥' + fmtMoney(tx.current_value) : '--' }}</td>
                    <td class="font-jet" :style="{ color: profitColor(tx.profit_loss) }">
                      {{ tx.profit_loss != null ? (tx.profit_loss >= 0 ? '+' : '') + '¥' + fmtMoney(tx.profit_loss) : '--' }}
                    </td>
                    <td class="font-jet" :style="{ color: profitColor(tx.profit_rate) }">
                      {{ tx.profit_rate != null ? fmtSignedPct(tx.profit_rate * 100) : '--' }}
                    </td>
                    <td class="font-jet">{{ tx.holding_days ?? '--' }}</td>
                    <td>
                      <span v-if="tx.status === 'verified'" class="cf-tag" :class="tx.is_breakeven ? 'cf-tag-win' : 'cf-tag-loss'">
                        {{ tx.is_breakeven ? '已回本' : '仍亏损' }}
                      </span>
                      <span v-else class="cf-tag cf-tag-muted">{{ tx.status === 'no_nav_data' ? '无净值' : tx.status }}</span>
                    </td>
                    <td>
                      <button class="btn-link-danger" @click="removeHypothetical(tx.tx_id)">删除</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-else class="cf-empty">
              <Icon name="info" size="16" />
              <span>暂无假设交易记录。每次访问补仓计划页面时，系统会自动为建议创建假设交易。</span>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.smart-add-page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

/* 页头 */
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
.page-title {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
  letter-spacing: -0.01em;
}
.page-desc {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin: 0.25rem 0 0;
}

/* 状态块 */
.state-block {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 2rem 1.25rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  color: var(--color-text-secondary);
  font-size: 0.88rem;
}
.state-error { color: var(--color-profit); border-color: var(--color-profit-weak); }

/* 总览卡 */
.overview-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 0.75rem;
}
.ov-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: 0.85rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.ov-label {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}
.ov-value {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--color-text-primary);
}
.ov-used { color: var(--color-warning); }
.ov-remain { color: var(--color-loss); }
.ov-deep { color: var(--color-profit); }

/* 区块 */
.block {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.block-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

/* 优先级表 */
.table-wrap {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  overflow-x: auto;
}
.priority-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}
.priority-table th,
.priority-table td {
  padding: 0.6rem 0.8rem;
  text-align: left;
  border-bottom: 1px solid var(--color-border-light);
  white-space: nowrap;
}
.priority-table th {
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-bg-input);
  font-size: 0.74rem;
}
.priority-table tbody tr:last-child td { border-bottom: none; }
.priority-table tbody tr:hover { background: var(--color-bg-hover); }
.priority-table .num { text-align: right; }
.fund-cell { display: flex; flex-direction: column; gap: 0.1rem; }
.fund-name { color: var(--color-text-primary); font-weight: 500; }
.fund-code { font-size: 0.7rem; color: var(--color-text-muted); }
.priority-cell {
  font-size: 0.85rem;
  letter-spacing: 0.05em;
  color: var(--color-gold);
}
.muted { color: var(--color-text-muted); font-size: 0.78rem; }

/* 计划卡 */
.plan-cards {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.plan-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-left: 3px solid var(--color-profit);
  border-radius: var(--radius-md);
  padding: 1rem 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}
.plan-card.pool-warn { border-left-color: var(--color-warning); }

.plan-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.plan-header-left { display: flex; align-items: baseline; gap: 0.5rem; }
.plan-fund-name { font-weight: 600; color: var(--color-text-primary); font-size: 0.95rem; }
.plan-fund-code { font-size: 0.72rem; color: var(--color-text-muted); }
.plan-header-right { display: flex; gap: 0.4rem; }
.badge {
  font-size: 0.7rem;
  padding: 0.18rem 0.5rem;
  border-radius: 999px;
  font-weight: 600;
}
.badge-loss { background: var(--color-profit-bg); color: var(--color-profit); }
.badge-pos { background: var(--color-bg-input); color: var(--color-text-secondary); }

/* 估值信息行 */
.plan-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
  font-size: 0.82rem;
}
.plan-valuation {
  padding: 0.55rem 0.7rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
}
.val-item { display: flex; flex-direction: column; gap: 0.1rem; min-width: 70px; }
.val-label { font-size: 0.68rem; color: var(--color-text-muted); }
.val-value { font-weight: 600; color: var(--color-text-primary); font-size: 0.85rem; }
.val-level { font-weight: 600; font-size: 0.82rem; }

/* L2-L5 智能指标 */
.smart-metrics {
  margin: 0.5rem 0;
  padding: 0.7rem 0.8rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.75rem;
}
.metric-item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.metric-label {
  font-size: 0.68rem;
  color: var(--color-text-muted);
}
.metric-value {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-text-primary);
}
.metric-sub {
  font-size: 0.66rem;
  color: var(--color-text-muted);
}

/* 引擎1 */
.engine1-row { gap: 0.5rem; }
.engine-tag {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}
.engine1-tag { background: var(--color-primary-bg); color: var(--color-primary); }
.engine2-tag { background: var(--color-profit-bg); color: var(--color-profit); }
.engine-formula {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--color-text-secondary);
  font-size: 0.82rem;
}
.engine-formula .x { color: var(--color-text-muted); }
.multiplier-badge {
  font-weight: 700;
  font-size: 0.85rem;
  padding: 0.1rem 0.45rem;
  border-radius: var(--radius-sm);
  background: var(--color-gold-light);
  color: #1a1a1a;
}
.dark .multiplier-badge { color: #1a1a1a; }
.engine-result { font-weight: 700; color: var(--color-text-primary); }

/* 引擎2 */
.engine2-row { flex-direction: column; align-items: stretch; gap: 0.5rem; }
.engine2-head { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.engine2-meta { font-size: 0.78rem; color: var(--color-text-secondary); }
.pool-warn-tag {
  margin-left: 0.4rem;
  font-size: 0.68rem;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius-sm);
  background: var(--color-warning-bg, rgba(217, 119, 6, 0.12));
  color: var(--color-warning);
  font-weight: 600;
}

/* 拦截/缩减提示（修复3/5/6） */
.cap-warn-box {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin: 0.4rem 0;
  padding: 0.35rem 0.6rem;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  background: var(--color-warning-bg, rgba(217, 119, 6, 0.08));
  border-left: 3px solid var(--color-warning);
}
.cap-blocked { color: var(--color-loss); font-weight: 600; }
.cap-capped { color: var(--color-warning); }
.cap-scaled { color: var(--color-info, #2563eb); }

.pyramid-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.pyramid-table th,
.pyramid-table td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--color-border-light);
  white-space: nowrap;
}
.pyramid-table th {
  color: var(--color-text-muted);
  font-weight: 500;
  font-size: 0.7rem;
  background: var(--color-bg-input);
}
.pyramid-table .num { text-align: right; }
.pyramid-table tbody tr:last-child td { border-bottom: none; }
.tier-triggered { background: var(--color-loss-bg); }
.tier-status {
  font-size: 0.7rem;
  padding: 0.12rem 0.45rem;
  border-radius: 999px;
  font-weight: 600;
}
.st-triggered { background: var(--color-loss-bg); color: var(--color-loss); }
.st-pending { background: var(--color-bg-input); color: var(--color-text-muted); }

.improvement-box {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  padding: 0.45rem 0.6rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
}
.improvement-val { font-weight: 700; font-size: 0.88rem; }
.next-trigger {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.76rem;
  color: var(--color-text-muted);
}

/* 安全阀 */
.safety-row {
  padding-top: 0.4rem;
  border-top: 1px dashed var(--color-border-light);
  font-size: 0.78rem;
}
.safety-icon { color: var(--color-text-muted); flex-shrink: 0; }
.safety-label { font-weight: 600; color: var(--color-text-secondary); }
.safety-meta { color: var(--color-text-muted); }
.safety-flag {
  font-size: 0.7rem;
  padding: 0.12rem 0.45rem;
  border-radius: 999px;
  font-weight: 600;
}
.flag-ok { background: var(--color-loss-bg); color: var(--color-loss); }
.flag-stop { background: var(--color-profit-bg); color: var(--color-profit); }

/* ── 多维度触发器：信号展示 ── */
.signals-row {
  margin-top: 0.5rem;
  padding: 0.7rem 0.85rem;
  background: linear-gradient(135deg, rgba(99,102,241,0.05), rgba(168,85,247,0.05));
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
}
.signals-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.5rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.signals-title { margin-right: auto; }
.signals-total { color: var(--color-text-secondary); font-size: 0.8rem; }
.signals-total b { color: var(--color-loss); font-weight: 600; }
.signals-list { display: flex; flex-direction: column; gap: 0.4rem; }
.signal-item {
  padding: 0.5rem 0.65rem;
  border-radius: var(--radius-sm);
  border-left: 3px solid var(--color-border-light);
  background: var(--color-bg-card);
  font-size: 0.82rem;
}
.signal-item.sig-on { border-left-color: var(--color-loss); }
.signal-item.sig-blocked { border-left-color: var(--color-warning, #f59e0b); opacity: 0.78; }
.signal-item.sig-pyramid { border-left-color: #ef4444; }
.signal-item.sig-trend { border-left-color: #10b981; }
.signal-item.sig-dip { border-left-color: #f59e0b; }
.signal-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.2rem;
}
.signal-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 700;
  color: #fff;
  background: #6366f1;
}
.sig-pyramid .signal-tag { background: #ef4444; }
.sig-trend .signal-tag { background: #10b981; }
.sig-dip .signal-tag { background: #f59e0b; }
.signal-label { font-weight: 600; color: var(--color-text-primary); }
.signal-amount { margin-left: auto; color: var(--color-loss); font-weight: 600; }
.signal-blocked-tag {
  margin-left: auto;
  padding: 0 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  background: rgba(245,158,11,0.15);
  color: #b45309;
}
.signal-sub-tag {
  padding: 0 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  background: var(--color-bg-muted, #f3f4f6);
  color: var(--color-text-secondary);
}
.signal-reason { color: var(--color-text-secondary); font-size: 0.78rem; }
.signal-reason.blocked { color: #b45309; }
.signal-conditions { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.3rem; }
.cond-chip {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  background: rgba(99,102,241,0.08);
  color: #4f46e5;
}

/* 金额计算依据（动态化公式展示） */
.signal-formula {
  margin-top: 0.35rem;
  padding: 0.4rem 0.55rem;
  background: rgba(16,185,129,0.06);
  border-radius: var(--radius-sm);
  border: 1px dashed rgba(16,185,129,0.3);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.76rem;
}
.formula-label { color: var(--color-text-secondary); font-weight: 500; }
.formula-amount {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-loss);
}
.formula-detail {
  color: var(--color-text-secondary);
  font-size: 0.72rem;
  opacity: 0.85;
}
.formula-breakdown {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  width: 100%;
  margin-top: 0.2rem;
}
.formula-breakdown span {
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.68rem;
  background: rgba(99,102,241,0.06);
  color: #6366f1;
}
.formula-breakdown .pos-info {
  margin-left: auto;
  background: rgba(100,116,139,0.08);
  color: var(--color-text-secondary);
}
.formula-breakdown .deduct-info {
  background: rgba(239,68,68,0.08);
  color: var(--color-loss);
}

/* 折叠头 */
.collapse-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.7rem 0.9rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
  text-align: left;
  transition: background var(--transition-fast);
}
.collapse-head:hover { background: var(--color-bg-hover); }
.collapse-head .chevron {
  transition: transform var(--transition-fast);
  color: var(--color-text-muted);
}
.collapse-head .chevron.expanded { transform: rotate(90deg); }
.collapse-hint { font-weight: 400; font-size: 0.76rem; }
.collapse-body {
  padding: 1rem 1.1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-top: none;
  border-radius: 0 0 var(--radius-md) var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

/* 模拟器 */
.sim-form {
  display: flex;
  align-items: flex-end;
  gap: 0.85rem;
  flex-wrap: wrap;
}
.sim-field { display: flex; flex-direction: column; gap: 0.3rem; }
.sim-field label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.sim-input {
  padding: 0.5rem 0.7rem;
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  font-size: 0.85rem;
  color: var(--color-text-primary);
  width: 150px;
  outline: none;
  transition: border-color var(--transition-fast);
}
.sim-input:focus { border-color: var(--color-primary-border); }
.sim-error {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--color-profit);
  font-size: 0.8rem;
}
.sim-result {
  border-top: 1px solid var(--color-border-light);
  padding-top: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.sim-result-head {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  font-weight: 600;
  color: var(--color-text-primary);
  font-size: 0.88rem;
}
.sim-result-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 0.6rem;
}
.sr-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.55rem 0.7rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
}
.sr-label { font-size: 0.68rem; color: var(--color-text-muted); }
.sr-value { font-weight: 700; color: var(--color-text-primary); font-size: 0.88rem; }
.sr-new { color: var(--color-primary); }
.sr-improve { background: var(--color-gold-light); }
.sr-improve .sr-value { color: #1a1a1a; }

/* 配置面板 */
.cfg-formula {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.76rem;
  color: var(--color-text-secondary);
  padding: 0.55rem 0.7rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
}
.cfg-formula code {
  background: var(--color-bg-card);
  padding: 0.1rem 0.35rem;
  border-radius: var(--radius-sm);
  font-size: 0.74rem;
  color: var(--color-primary);
}
.cfg-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.75rem;
}
.cfg-field { display: flex; flex-direction: column; gap: 0.3rem; }
.cfg-field-wide { grid-column: span 2; }
.cfg-field-check { grid-column: span 3; }
.cfg-field label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.cfg-input {
  padding: 0.5rem 0.7rem;
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  font-size: 0.85rem;
  color: var(--color-text-primary);
  outline: none;
  transition: border-color var(--transition-fast);
}
.cfg-input:focus { border-color: var(--color-primary-border); }
.check-label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.cfg-actions {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.cfg-saved {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.8rem;
  color: var(--color-loss);
  font-weight: 600;
}

/* 按钮 */
.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  background: var(--color-primary);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--transition-fast);
}
.btn-primary:hover:not(:disabled) { background: var(--color-primary-600, var(--color-primary)); }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.4rem 0.75rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.btn-ghost:hover:not(:disabled) { background: var(--color-bg-hover); color: var(--color-text-primary); }
.btn-ghost:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-link {
  background: none;
  border: none;
  color: var(--color-primary);
  font-size: 0.76rem;
  cursor: pointer;
  padding: 0.1rem 0.3rem;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.btn-link:hover { color: var(--color-primary-400); }

.spinning { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* 响应式 */
@media (max-width: 1024px) {
  .overview-grid { grid-template-columns: repeat(3, 1fr); }
  .sim-result-grid { grid-template-columns: repeat(3, 1fr); }
  .cfg-grid { grid-template-columns: repeat(2, 1fr); }
  .cfg-field-wide { grid-column: span 2; }
  .cfg-field-check { grid-column: span 2; }
}
@media (max-width: 640px) {
  .overview-grid { grid-template-columns: repeat(2, 1fr); }
  .sim-result-grid { grid-template-columns: repeat(2, 1fr); }
  .cfg-grid { grid-template-columns: 1fr; }
  .cfg-field-wide, .cfg-field-check { grid-column: span 1; }
  .sim-input { width: 100%; }
  .sim-form { flex-direction: column; align-items: stretch; }
}

/* ── 反事实决策验证 ── */
.cf-badge {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  margin-left: 6px;
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--color-profit);
  background: rgba(220, 38, 38, 0.1);
  border-radius: 8px;
  line-height: 18px;
}
.cf-intro {
  margin: 0 0 1rem;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
}
.cf-intro .btn-link {
  margin-left: 8px;
  color: var(--color-primary);
  background: none;
  border: none;
  cursor: pointer;
  font-size: inherit;
  text-decoration: underline;
}
.cf-error {
  color: var(--color-profit);
  font-size: 0.85rem;
  padding: 0.5rem 0;
}
.cf-content { display: flex; flex-direction: column; gap: 1rem; }

.cf-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.75rem;
}
.cf-sum-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0.75rem;
  background: var(--color-bg-secondary);
  border-radius: 8px;
}
.cf-sum-label {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
}
.cf-sum-value {
  font-size: 1.05rem;
  font-weight: 600;
}

.cf-improvement {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0.6rem 0.8rem;
  background: rgba(34, 197, 94, 0.06);
  border-radius: 8px;
  font-size: 0.85rem;
  color: var(--color-text-primary);
}
.cf-improvement strong { font-weight: 700; }

.cf-sub-title {
  margin: 0 0 0.5rem;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.cf-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}
.cf-table th {
  padding: 8px 6px;
  text-align: left;
  font-weight: 500;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}
.cf-table td {
  padding: 8px 6px;
  border-bottom: 1px solid var(--color-border-light);
  white-space: nowrap;
}
.cf-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 8px;
  font-size: 0.72rem;
  font-weight: 600;
}
.cf-tag-win {
  color: var(--color-loss);
  background: rgba(34, 197, 94, 0.12);
}
.cf-tag-loss {
  color: var(--color-profit);
  background: rgba(220, 38, 38, 0.1);
}
.cf-tag-muted {
  color: var(--color-text-secondary);
  background: var(--color-bg-secondary);
}
.cf-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 1rem;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}
.btn-link-danger {
  color: var(--color-profit);
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.78rem;
  text-decoration: underline;
  padding: 0;
}
.btn-link-danger:hover { opacity: 0.7; }

/* —— 退出信号（止盈/止损/暂停） —— */
.exit-signals-row {
  border-left: 3px solid var(--color-profit, #dc2626);
  background: rgba(220, 38, 38, 0.03);
  padding: 0.6rem 0.8rem;
  border-radius: var(--radius-sm);
}
.exit-signals-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.4rem;
  color: var(--color-profit, #dc2626);
  font-weight: 600;
  font-size: 0.82rem;
}
.exit-signals-title { color: var(--color-profit, #dc2626); }
.exit-signals-list { display: flex; flex-direction: column; gap: 0.35rem; }
.exit-item {
  padding: 0.35rem 0.5rem;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  font-size: 0.78rem;
}
.exit-item.exit-danger {
  background: rgba(220, 38, 38, 0.08);
  border-color: rgba(220, 38, 38, 0.2);
}
.exit-item.exit-warning {
  background: rgba(245, 158, 11, 0.08);
  border-color: rgba(245, 158, 11, 0.2);
}
.exit-item.exit-info {
  background: rgba(59, 130, 246, 0.06);
  border-color: rgba(59, 130, 246, 0.15);
}
.exit-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.15rem;
}
.exit-tag {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  font-weight: 700;
  white-space: nowrap;
}
.exit-danger .exit-tag { background: rgba(220, 38, 38, 0.2); color: #dc2626; }
.exit-warning .exit-tag { background: rgba(245, 158, 11, 0.2); color: #b45309; }
.exit-info .exit-tag { background: rgba(59, 130, 246, 0.15); color: #2563eb; }
.exit-action {
  font-weight: 600;
  color: var(--color-text-primary);
}
.exit-reason {
  color: var(--color-text-secondary);
  font-size: 0.74rem;
  padding-left: 0.3rem;
}

/* —— 信号B风险提示条 —— */
.risk-warn-box {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
  margin-top: 0.4rem;
  padding: 0.35rem 0.5rem;
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: var(--radius-sm);
  font-size: 0.74rem;
  color: #b45309;
  line-height: 1.4;
}

/* —— 资金池耗尽警告横幅 —— */
.pool-warn-banner {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.75rem 0;
  padding: 0.6rem 0.9rem;
  background: rgba(220, 38, 38, 0.08);
  border: 1px solid rgba(220, 38, 38, 0.25);
  border-radius: var(--radius-sm);
  color: var(--color-profit, #dc2626);
  font-size: 0.82rem;
  font-weight: 500;
}
.pool-warn-text { flex: 1; }

/* —— 价值平均法（VA） —— */
.va-row {
  background: rgba(16, 185, 129, 0.03);
  border-left: 3px solid rgba(16, 185, 129, 0.4);
  padding: 0.5rem 0.7rem;
  border-radius: var(--radius-sm);
}
.va-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.3rem;
  font-size: 0.82rem;
  font-weight: 600;
}
.va-title { color: var(--color-text-primary); }
.va-action-tag {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  font-weight: 700;
}
.va-buy { background: rgba(16, 185, 129, 0.15); color: #059669; }
.va-sell { background: rgba(220, 38, 38, 0.12); color: #dc2626; }
.va-hold { background: var(--color-bg-muted, #f3f4f6); color: var(--color-text-secondary); }
.va-body { font-size: 0.76rem; }
.va-reason { color: var(--color-text-secondary); margin-bottom: 0.25rem; }
.va-meta { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.va-meta span {
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.7rem;
  background: rgba(99, 102, 241, 0.06);
  color: var(--color-text-secondary);
}
.va-amount { font-weight: 700; color: var(--color-loss); background: rgba(16, 185, 129, 0.1); }
.va-max { background: rgba(100, 116, 139, 0.08); }

/* —— 网格交易 —— */
.grid-row {
  background: rgba(99, 102, 241, 0.03);
  border-left: 3px solid rgba(99, 102, 241, 0.4);
  padding: 0.5rem 0.7rem;
  border-radius: var(--radius-sm);
}
.grid-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.3rem;
  font-size: 0.82rem;
  font-weight: 600;
}
.grid-title { color: var(--color-text-primary); }
.grid-action-tag {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  font-weight: 700;
}
.grid-buy { background: rgba(16, 185, 129, 0.15); color: #059669; }
.grid-sell { background: rgba(220, 38, 38, 0.12); color: #dc2626; }
.grid-wait { background: var(--color-bg-muted, #f3f4f6); color: var(--color-text-secondary); }
.grid-body { font-size: 0.76rem; }
.grid-reason { color: var(--color-text-secondary); margin-bottom: 0.25rem; }
.grid-meta { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.grid-meta span {
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.7rem;
  background: rgba(99, 102, 241, 0.06);
  color: var(--color-text-secondary);
}
.grid-amount { font-weight: 700; color: #6366f1; background: rgba(99, 102, 241, 0.1); }

/* —— 基本面健康检查 —— */
.health-row {
  background: rgba(100, 116, 139, 0.03);
  border-left: 3px solid rgba(100, 116, 139, 0.3);
  padding: 0.5rem 0.7rem;
  border-radius: var(--radius-sm);
}
.health-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.3rem;
  font-size: 0.82rem;
  font-weight: 600;
}
.health-title { color: var(--color-text-primary); }
.health-tag {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  font-weight: 700;
}
.health-ok { background: rgba(16, 185, 129, 0.15); color: #059669; }
.health-risk { background: rgba(220, 38, 38, 0.12); color: #dc2626; }
.health-body { font-size: 0.76rem; }
.health-warnings, .health-risks { display: flex; flex-direction: column; gap: 0.2rem; }
.health-item {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.72rem;
}
.health-warn { background: rgba(245, 158, 11, 0.1); color: #b45309; }
.health-danger { background: rgba(220, 38, 38, 0.08); color: #dc2626; }
.health-clean { color: #059669; font-size: 0.74rem; }

/* —— 组合再平衡 —— */
.rebalance-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 0.5rem;
}
.rebalance-card {
  padding: 0.5rem 0.6rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-bg-white);
}
.rebalance-card.rebalance-add { border-left: 3px solid #059669; }
.rebalance-card.rebalance-reduce { border-left: 3px solid #dc2626; }
.rebalance-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.35rem;
}
.rebalance-name { font-weight: 600; font-size: 0.8rem; }
.rebalance-tag {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.68rem;
  font-weight: 700;
}
.tag-add { background: rgba(16, 185, 129, 0.15); color: #059669; }
.tag-reduce { background: rgba(220, 38, 38, 0.12); color: #dc2626; }
.tag-hold { background: var(--color-bg-muted); color: var(--color-text-secondary); }
.rebalance-body { font-size: 0.74rem; }
.rebalance-row {
  display: flex;
  justify-content: space-between;
  padding: 1px 0;
  color: var(--color-text-secondary);
}
.rebalance-amount { font-weight: 600; color: var(--color-text-primary); }

/* —— 策略对比模拟器 —— */
.sim-result {
  margin-top: 0.4rem;
  font-size: 0.78rem;
}
.sim-strategies {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.sim-card {
  padding: 0.5rem 0.6rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-bg-white);
}
.sim-card.sim-best {
  border-color: #059669;
  border-width: 2px;
  background: rgba(16, 185, 129, 0.03);
}
.sim-card-head {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-bottom: 0.35rem;
  font-weight: 600;
  font-size: 0.8rem;
}
.sim-best-badge {
  font-size: 0.65rem;
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(16, 185, 129, 0.15);
  color: #059669;
  font-weight: 700;
}
.sim-card-body { font-size: 0.74rem; display: flex; flex-direction: column; gap: 0.2rem; }
.sim-row { display: flex; justify-content: space-between; color: var(--color-text-secondary); }
.sim-desc { font-size: 0.68rem; color: var(--color-text-tertiary); margin-top: 0.2rem; }

@media (max-width: 768px) {
  .cf-summary-grid { grid-template-columns: repeat(2, 1fr); }
  .cf-table { font-size: 0.75rem; }
  .cf-table th, .cf-table td { padding: 6px 4px; }
}

/* ── 穿透指数集中度 ── */
.exposure-table-wrap {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  overflow-x: auto;
}
.exposure-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}
.exposure-table th,
.exposure-table td {
  padding: 0.55rem 0.8rem;
  text-align: left;
  border-bottom: 1px solid var(--color-border-light);
  white-space: nowrap;
}
.exposure-table th {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-bg-input);
}
.exposure-table tbody tr:last-child td { border-bottom: none; }
.exposure-table tbody tr:hover { background: var(--color-bg-hover); }
.exposure-table .num { text-align: right; }
.exposure-warn-row { background: rgba(245, 158, 11, 0.06); }
.exposure-warn-row:hover { background: rgba(245, 158, 11, 0.1) !important; }

.exposure-type-tag {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  font-size: 0.68rem;
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
}
.exposure-type-tag.type-broad { background: rgba(59, 130, 246, 0.12); color: #1d4ed8; }
.exposure-type-tag.type-industry { background: rgba(16, 185, 129, 0.12); color: #047857; }
.exposure-type-tag.type-theme { background: rgba(168, 85, 247, 0.12); color: #7c3aed; }
.exposure-type-tag.type-bond { background: rgba(100, 116, 139, 0.14); color: #334155; }
.exposure-type-tag.type-hk_overseas { background: rgba(245, 158, 11, 0.12); color: #b45309; }

.exposure-flag {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  font-size: 0.7rem;
  font-weight: 500;
}
.exposure-flag.flag-ok { background: rgba(16, 185, 129, 0.12); color: #047857; }
.exposure-flag.flag-warn { background: rgba(245, 158, 11, 0.18); color: #b45309; }

.exposure-funds { max-width: 320px; }
.exposure-fund-chip {
  display: inline-block;
  margin-right: 0.3rem;
  margin-bottom: 0.2rem;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  font-size: 0.66rem;
  background: var(--color-bg-input);
  color: var(--color-text-tertiary);
  white-space: nowrap;
}
.exposure-error { color: var(--color-loss); font-size: 0.78rem; padding: 0.4rem 0.6rem; }

/* ── 多维度决策层 ── */
.ps-row {
  margin: 0.5rem 0;
  padding: 0.7rem 0.8rem;
  background: linear-gradient(180deg, rgba(99, 102, 241, 0.04), rgba(99, 102, 241, 0.01));
  border: 1px solid rgba(99, 102, 241, 0.15);
  border-radius: var(--radius-sm);
}
.ps-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.ps-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.ps-final {
  margin-left: auto;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
}
.ps-final b {
  color: var(--color-profit);
  font-size: 0.92rem;
  margin-left: 0.3rem;
}
.ps-risk {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  background: var(--color-bg-input);
  color: var(--color-text-muted);
}
.ps-summary {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.74rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.55rem;
  padding: 0.4rem 0.55rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
  line-height: 1.5;
}
.ps-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.55rem;
}
.ps-item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: 0.5rem 0.6rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
}
.ps-item.ps-warn {
  background: rgba(245, 158, 11, 0.06);
  border-color: rgba(245, 158, 11, 0.3);
}
.ps-label {
  font-size: 0.66rem;
  color: var(--color-text-muted);
  font-weight: 500;
}
.ps-value {
  font-size: 0.86rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.ps-sub {
  font-size: 0.66rem;
  color: var(--color-text-tertiary);
  line-height: 1.4;
}
.ps-base {
  background: rgba(99, 102, 241, 0.05);
  border-color: rgba(99, 102, 241, 0.2);
}
.ps-base .ps-value { color: #4338ca; }

.ps-exit-list {
  margin-top: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.ps-exit-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.6rem;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  border-left: 3px solid;
}
.ps-exit-info { background: rgba(59, 130, 246, 0.08); border-color: #3b82f6; }
.ps-exit-warning { background: rgba(245, 158, 11, 0.08); border-color: #f59e0b; }
.ps-exit-danger { background: rgba(220, 38, 38, 0.08); border-color: #dc2626; }
.ps-exit-tag {
  font-weight: 600;
  color: var(--color-text-primary);
}
.ps-exit-action {
  color: var(--color-text-secondary);
}
.ps-exit-reason {
  color: var(--color-text-muted);
  font-size: 0.7rem;
  margin-left: auto;
}

@media (max-width: 768px) {
  .ps-grid { grid-template-columns: repeat(2, 1fr); }
  .exposure-funds { max-width: 200px; }
}
</style>
