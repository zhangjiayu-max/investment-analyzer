<script setup>
import { computed, onMounted, ref } from 'vue'
import { decisionAPI } from '../../api'
import { useToast } from '../../composables/useToast'
import Icon from '../ui/Icon.vue'
import EmptyState from '../ui/EmptyState.vue'
import ConfirmDialog from '../layout/ConfirmDialog.vue'

const { showToast } = useToast()

// ── 数据状态 ──
const loading = ref(false)
const accuracy = ref(null)
const ledger = ref([])
const selected = ref(null)
const detailLoading = ref(false)

// ── 运行决策表单 ──
const fundCode = ref('')

// ── 二次确认弹窗（所有操作类按钮，遵循项目规范）──
const confirm = ref({ visible: false, type: '', title: '', message: '', loading: false })

const VERDICT_META = {
  can_buy: { label: '可小仓', cls: 'vd-buy' },
  watch: { label: '观察', cls: 'vd-watch' },
  avoid: { label: '回避', cls: 'vd-avoid' },
  reduce: { label: '减仓', cls: 'vd-reduce' },
}
const STATUS_LABELS = {
  suggested: '待执行', confirmed: '已确认', executed: '已执行', closed: '已回测', expired: '已过期',
}
const TYPE_LABELS = {
  opportunity_buy: '机会买入', smart_add: '智能补仓', loss_recovery: '补仓回本',
  valuation_channel: '估值驱动', watch: '观察',
}

const overall = computed(() => accuracy.value?.overall || { reviewed: 0, hits: 0, hit_rate_pct: 0, avg_excess_pct: null })
const byModule = computed(() => Object.entries(accuracy.value?.by_module || {}))
const byTheme = computed(() => Object.entries(accuracy.value?.by_theme || {}))

function verdictMeta(v) { return VERDICT_META[v] || { label: v || '—', cls: 'vd-watch' } }
function pct(v) { return v === null || v === undefined ? '—' : `${Number(v).toFixed(1)}%` }
function money(v) { return v ? `¥${Number(v).toLocaleString()}` : '—' }
function fmtDate(s) { return s ? String(s).slice(0, 16).replace('T', ' ') : '—' }

// ── 数据加载 ──
async function loadAccuracy() {
  try {
    const res = await decisionAPI.getAccuracy()
    accuracy.value = res.data || null
  } catch (e) { showToast('准确率统计加载失败', 'error') }
}
async function loadLedger() {
  loading.value = true
  try {
    const res = await decisionAPI.getLedger({ limit: 100 })
    ledger.value = res.data || []
  } catch (e) { showToast('决策账本加载失败', 'error') }
  finally { loading.value = false }
}
async function refresh() { await Promise.all([loadAccuracy(), loadLedger()]) }

// ── 查看详情 ──
async function openDetail(row) {
  detailLoading.value = true
  selected.value = row
  try {
    const res = await decisionAPI.getDetail(row.id)
    if (res.data) selected.value = res.data
  } catch (e) { /* 用列表行兜底 */ }
  finally { detailLoading.value = false }
}
function closeDetail() { selected.value = null }

// ── 操作（均经二次确认）──
function askRunDecision() {
  if (!fundCode.value.trim()) { showToast('请输入基金代码', 'warning'); return }
  confirm.value = {
    visible: true, type: 'run', title: '运行投资决策流水线',
    message: `将对基金 ${fundCode.value} 运行完整决策流水线（发现→sizing→风控→融合），并写入决策账本。确定继续？`,
    loading: false,
  }
}
function askBacktest() {
  confirm.value = {
    visible: true, type: 'backtest', title: '运行决策回测',
    message: '将对所有到期决策计算超额收益、判定命中并反哺信号权重（"越来越准"引擎）。确定继续？',
    loading: false,
  }
}
function askExitScan() {
  confirm.value = {
    visible: true, type: 'exit', title: '运行止盈闭环扫描',
    message: '将扫描有止盈计划的在途决策，按状态机检查回本/分批止盈并生成提醒。确定继续？',
    loading: false,
  }
}
async function onConfirm() {
  const { type } = confirm.value
  confirm.value.loading = true
  try {
    if (type === 'run') {
      const res = await decisionAPI.run(fundCode.value.trim())
      if (res.code === 0) { showToast(`决策已生成：${res.data?.verdict || ''}（置信度 ${res.data?.confidence ?? '—'}）`, 'success'); await refresh() }
      else showToast(res.message || '决策生成失败', 'error')
    } else if (type === 'backtest') {
      const res = await decisionAPI.runBacktest()
      const d = res.data || {}
      showToast(`回测完成：处理 ${d.processed || 0}，命中 ${d.hits || 0}，权重调整 ${(d.weight_changes || []).length}`, 'success')
      await refresh()
    } else if (type === 'exit') {
      const res = await decisionAPI.runExitScan()
      const d = res.data || {}
      showToast(`止盈扫描完成：扫描 ${d.scanned || 0}，新增提醒 ${d.alerts_created || 0}`, 'success')
    }
    confirm.value.visible = false
  } catch (e) {
    showToast('操作失败：' + (e.message || e), 'error')
  } finally { confirm.value.loading = false }
}
function onCancel() { confirm.value.visible = false }

onMounted(refresh)
</script>

<template>
  <div class="decision-center">
    <!-- 页头 + 操作 -->
    <div class="page-header card">
      <div>
        <h2 class="editorial-title-lg">决策中心</h2>
        <p class="page-sub">三模块联动：机会雷达发现 → 智能补仓 sizing → 组合风控 → 决策账本回测反哺</p>
      </div>
      <div class="header-actions">
        <div class="run-form">
          <input v-model="fundCode" class="fund-input" placeholder="基金代码 如 510300" @keyup.enter="askRunDecision" />
          <button class="btn-primary" @click="askRunDecision"><Icon name="target" /> 运行决策</button>
        </div>
        <button class="btn-secondary" @click="askBacktest"><Icon name="refresh" /> 回测</button>
        <button class="btn-secondary" @click="askExitScan"><Icon name="warning" /> 止盈扫描</button>
      </div>
    </div>

    <!-- 命中率看板 -->
    <div class="stat-grid">
      <div class="card stat-card">
        <div class="stat-label">累计命中率</div>
        <div class="stat-value" :style="{ color: overall.hit_rate_pct >= 60 ? 'var(--color-success)' : overall.hit_rate_pct >= 40 ? 'var(--color-warning)' : 'var(--color-danger)' }">
          {{ pct(overall.hit_rate_pct) }}
        </div>
        <div class="stat-foot">命中 {{ overall.hits }} / 回测 {{ overall.reviewed }}</div>
      </div>
      <div class="card stat-card">
        <div class="stat-label">平均超额收益</div>
        <div class="stat-value">{{ overall.avg_excess_pct === null || overall.avg_excess_pct === undefined ? '—' : pct(overall.avg_excess_pct) }}</div>
        <div class="stat-foot">相对沪深300（近90天）</div>
      </div>
      <div class="card stat-card stat-wide">
        <div class="stat-label">按来源模块</div>
        <div v-if="byModule.length" class="chip-row">
          <span v-for="[mod, s] in byModule" :key="mod" class="module-chip">
            <b>{{ mod }}</b> {{ pct(s.hit_rate_pct) }} <i>({{ s.hits }}/{{ s.reviewed }})</i>
          </span>
        </div>
        <div v-else class="stat-foot">暂无回测数据</div>
      </div>
    </div>

    <!-- 主题命中率 -->
    <div v-if="byTheme.length" class="card section">
      <h3 class="section-title">主题命中率（信号权重反哺依据）</h3>
      <div class="chip-row">
        <span v-for="[theme, s] in byTheme" :key="theme" class="theme-chip"
          :class="s.hit_rate_pct >= 60 ? 'tc-good' : s.hit_rate_pct >= 40 ? 'tc-mid' : 'tc-bad'">
          {{ theme }} {{ pct(s.hit_rate_pct) }} <i>({{ s.hits }}/{{ s.reviewed }})</i>
        </span>
      </div>
    </div>

    <!-- 决策账本 -->
    <div class="card section">
      <div class="section-head">
        <h3 class="section-title">决策账本</h3>
        <button class="btn-secondary btn-sm" @click="refresh"><Icon name="refresh" /> 刷新</button>
      </div>
      <EmptyState v-if="!loading && !ledger.length" text="暂无决策记录，运行决策流水线后将写入账本" />
      <div v-else class="table-wrap">
        <table class="ledger-table">
          <thead>
            <tr>
              <th>时间</th><th>基金</th><th>类型</th><th>来源</th><th>结论</th>
              <th>置信度</th><th>建议金额</th><th>状态</th><th>回测</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in ledger" :key="row.id" @click="openDetail(row)" class="ledger-row">
              <td class="td-date">{{ fmtDate(row.created_at) }}</td>
              <td>{{ row.fund_name || row.fund_code }}</td>
              <td>{{ TYPE_LABELS[row.decision_type] || row.decision_type }}</td>
              <td class="td-muted">{{ row.source_module }}</td>
              <td><span :class="['verdict-badge', verdictMeta(row.verdict).cls]">{{ verdictMeta(row.verdict).label }}</span></td>
              <td>{{ row.confidence !== null && row.confidence !== undefined ? row.confidence : '—' }}</td>
              <td>{{ money(row.suggested_amount) }}</td>
              <td class="td-muted">{{ STATUS_LABELS[row.status] || row.status }}</td>
              <td>
                <span v-if="row.is_hit === true" class="hit-yes">✓ 命中</span>
                <span v-else-if="row.is_hit === false" class="hit-no">✗ 未中</span>
                <span v-else class="td-muted">待回测</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 决策详情抽屉 -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="selected" class="detail-backdrop" @click.self="closeDetail">
          <div class="detail-panel card">
            <div class="detail-head">
              <div>
                <h3 class="editorial-title-lg">{{ selected.fund_name || selected.fund_code }}</h3>
                <div class="detail-tags">
                  <span :class="['verdict-badge', verdictMeta(selected.verdict).cls]">{{ verdictMeta(selected.verdict).label }}</span>
                  <span class="conf-tag">置信度 {{ selected.confidence ?? '—' }}</span>
                  <span class="conf-tag">建议 {{ money(selected.suggested_amount) }}</span>
                </div>
              </div>
              <button class="btn-secondary btn-sm" @click="closeDetail">关闭</button>
            </div>

            <div class="detail-body">
              <div class="detail-block">
                <h4>估值科学信号</h4>
                <div v-if="selected.valuation_signal && selected.valuation_signal.zscore !== null && selected.valuation_signal.zscore !== undefined">
                  z-score ≈ <b>{{ selected.valuation_signal.zscore }}</b>（{{ selected.valuation_signal.level }}），
                  回看 {{ selected.valuation_signal.window_years }} 年，样本 {{ selected.valuation_signal.sample_size }}
                  <span v-if="selected.valuation_signal.half_life_months">，均值回归半衰期约 {{ selected.valuation_signal.half_life_months }} 月</span>
                </div>
                <div v-else class="td-muted">估值分位 {{ selected.valuation_percentile ?? '—' }}%（z-score 数据不足）</div>
              </div>

              <div class="detail-block">
                <h4>组合风控</h4>
                <div v-if="selected.risk_check && Object.keys(selected.risk_check).length">
                  <div>最大相关系数：{{ selected.risk_check.max_correlation ?? '—' }}｜降权因子：{{ selected.risk_check.corr_downweight ?? 1.0 }}</div>
                  <div v-if="selected.risk_check.portfolio_beta_proxy">组合 β ≈ {{ selected.risk_check.portfolio_beta_proxy }}</div>
                  <ul v-if="(selected.risk_check.flags || []).length" class="risk-flags">
                    <li v-for="(f, i) in selected.risk_check.flags" :key="i">⚠️ {{ f }}</li>
                  </ul>
                </div>
                <div v-else class="td-muted">无风控数据</div>
              </div>

              <div v-if="selected.exit_plan && selected.exit_plan.cost_price" class="detail-block">
                <h4>止盈计划</h4>
                <div>成本 {{ selected.exit_plan.cost_price }} → 第一批 +{{ selected.exit_plan.tp_first_pct }}%（{{ selected.exit_plan.tp1_price }}）减 {{ Math.round((selected.exit_plan.tp_first_ratio || 0) * 100) }}%；
                  第二批 +{{ selected.exit_plan.tp_second_pct }}%（{{ selected.exit_plan.tp2_price }}）减 {{ Math.round((selected.exit_plan.tp_second_ratio || 0) * 100) }}%</div>
              </div>

              <div v-if="(selected.evidence || []).length" class="detail-block">
                <h4>决策依据</h4>
                <ul class="evidence-list">
                  <li v-for="(ev, i) in selected.evidence" :key="i"><b>[{{ ev.type }}]</b> {{ ev.summary }}</li>
                </ul>
              </div>

              <div v-if="(selected.data_sources || []).length" class="detail-block">
                <h4>数据来源</h4>
                <div class="chip-row">
                  <span v-for="ds in selected.data_sources" :key="ds.name" class="source-chip">{{ ds.name }}（{{ ds.update_time }}）</span>
                </div>
              </div>

              <div class="disclaimer">{{ selected.risk_disclaimer }}</div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :loading="confirm.loading"
      confirm-text="确定执行"
      @confirm="onConfirm"
      @cancel="onCancel"
    />
  </div>
</template>

<style scoped>
.decision-center { display: flex; flex-direction: column; gap: 1rem; }

.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; padding: 1.25rem 1.5rem; }
.page-sub { margin: 0.3rem 0 0; font-size: 0.82rem; color: var(--color-text-secondary); }
.header-actions { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.run-form { display: flex; gap: 0.4rem; }
.fund-input { padding: 0.5rem 0.7rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-bg-card); color: var(--color-text-primary); font-size: 0.85rem; width: 160px; }

.stat-grid { display: grid; grid-template-columns: repeat(2, 1fr) 2fr; gap: 1rem; }
.stat-card { padding: 1rem 1.25rem; }
.stat-wide { grid-column: span 1; }
.stat-label { font-size: 0.8rem; color: var(--color-text-secondary); }
.stat-value { font-size: 1.8rem; font-weight: 700; margin: 0.3rem 0; }
.stat-foot { font-size: 0.75rem; color: var(--color-text-secondary); }
.chip-row { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.3rem; }
.module-chip, .source-chip { font-size: 0.75rem; padding: 0.25rem 0.6rem; border-radius: var(--radius-full); background: var(--color-primary-bg); color: var(--color-primary-600); }
.module-chip i, .theme-chip i { font-style: normal; opacity: 0.7; }

.section { padding: 1.25rem 1.5rem; }
.section-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.8rem; }
.section-title { margin: 0; font-size: 1rem; font-weight: 600; color: var(--color-text-primary); }
.theme-chip { font-size: 0.78rem; padding: 0.3rem 0.7rem; border-radius: var(--radius-full); }
.tc-good { background: var(--color-success-bg, rgba(34,197,94,.12)); color: var(--color-success); }
.tc-mid { background: var(--color-warning-bg, rgba(234,179,8,.12)); color: var(--color-warning); }
.tc-bad { background: var(--color-danger-bg); color: var(--color-danger); }

.table-wrap { overflow-x: auto; }
.ledger-table { width: 100%; border-collapse: collapse; font-size: 0.83rem; }
.ledger-table th { text-align: left; padding: 0.6rem 0.7rem; color: var(--color-text-secondary); font-weight: 500; border-bottom: 1px solid var(--color-border); white-space: nowrap; }
.ledger-table td { padding: 0.65rem 0.7rem; border-bottom: 1px solid var(--color-border); color: var(--color-text-primary); }
.ledger-row { cursor: pointer; }
.ledger-row:hover { background: var(--color-bg-hover, rgba(0,0,0,.03)); }
.td-date, .td-muted { color: var(--color-text-secondary); white-space: nowrap; }

.verdict-badge { display: inline-block; padding: 0.15rem 0.55rem; border-radius: var(--radius-full); font-size: 0.75rem; font-weight: 600; }
.vd-buy { background: var(--color-success-bg, rgba(34,197,94,.14)); color: var(--color-success); }
.vd-watch { background: var(--color-warning-bg, rgba(234,179,8,.14)); color: var(--color-warning); }
.vd-avoid { background: var(--color-danger-bg); color: var(--color-danger); }
.vd-reduce { background: var(--color-danger-bg); color: var(--color-danger); }
.hit-yes { color: var(--color-success); font-weight: 600; }
.hit-no { color: var(--color-danger); }

.detail-backdrop { position: fixed; inset: 0; z-index: var(--z-modal); background: rgba(0,0,0,.4); backdrop-filter: blur(4px); display: flex; justify-content: flex-end; }
.detail-panel { width: 100%; max-width: 560px; height: 100%; border-radius: 0; overflow-y: auto; padding: 1.5rem; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: 1rem; }
.detail-tags { display: flex; gap: 0.4rem; margin-top: 0.5rem; flex-wrap: wrap; }
.conf-tag { font-size: 0.75rem; padding: 0.15rem 0.55rem; border-radius: var(--radius-full); background: var(--color-primary-bg); color: var(--color-primary-600); }
.detail-body { display: flex; flex-direction: column; gap: 1rem; }
.detail-block { padding: 0.9rem 1rem; background: var(--color-bg-soft, rgba(0,0,0,.02)); border-radius: var(--radius-md); font-size: 0.85rem; line-height: 1.7; }
.detail-block h4 { margin: 0 0 0.4rem; font-size: 0.85rem; color: var(--color-text-primary); }
.risk-flags, .evidence-list { margin: 0.3rem 0 0; padding-left: 1.1rem; }
.risk-flags li { color: var(--color-warning); }
.disclaimer { font-size: 0.75rem; color: var(--color-text-secondary); line-height: 1.6; padding: 0.8rem 1rem; border: 1px dashed var(--color-border); border-radius: var(--radius-md); }

.btn-sm { padding: 0.35rem 0.7rem; font-size: 0.8rem; }

@media (max-width: 768px) {
  .stat-grid { grid-template-columns: 1fr; }
  .detail-panel { max-width: 100%; }
  .fund-input { width: 120px; }
}
</style>
