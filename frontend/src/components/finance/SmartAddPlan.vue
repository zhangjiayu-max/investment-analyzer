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
const showConfig = ref(false)
const showCounterfactual = ref(false)

// 反事实决策验证
const counterfactual = ref(null)
const cfLoading = ref(false)
const cfError = ref('')

// 摊薄模拟器
const simFundCode = ref('')
const simDropPct = ref(-10)
const simAmount = ref(5000)
const simResult = ref(null)
const simLoading = ref(false)
const simError = ref('')

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
})
const cfgSaving = ref(false)
const cfgSaved = ref(false)

// ── 计算属性 ──────────────────────────────
const summary = computed(() => plan.value?.summary || {})
const portfolioView = computed(() => plan.value?.portfolio_view || {})
const priorityList = computed(() => portfolioView.value.priority_list || [])
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

@media (max-width: 768px) {
  .cf-summary-grid { grid-template-columns: repeat(2, 1fr); }
  .cf-table { font-size: 0.75rem; }
  .cf-table th, .cf-table td { padding: 6px 4px; }
}
</style>
