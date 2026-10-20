<script setup>
import { ref, onMounted, watch } from 'vue'
import { portfolioRiskAPI } from '../../api'
import ConfirmDialog from '../layout/ConfirmDialog.vue'

const props = defineProps({ refreshKey: { default: null } })
const snapshot = ref(null), error = ref(''), loading = ref(false), busy = ref(false)
const showPolicy = ref(false), draft = ref({}), pendingAction = ref('')
const selectedFund = ref(''), amount = ref(1000), decision = ref(null), reviews = ref([]), showReview = ref(false)
const transactionIds = ref({}), activeReview = ref(null), outcome = ref(null)
let loadGeneration = 0, previewGeneration = 0
const fields = [
  ['max_single_position_pct', '单基金上限（%）', 1, 100, 1],
  ['max_same_index_pct', '同指数合计上限（%）', 1, 100, 1],
  ['max_equity_pct', '权益暴露上限（%）', 1, 100, 1],
  ['max_stress_loss_pct', '压力损失上限（%）', 1, 100, 1],
  ['max_stress_loss_amount', '压力损失金额上限（元，可留空）', 1, null, 100],
  ['nav_max_age_business_days', '净值最多滞后工作日', 1, 365, 1],
  ['valuation_max_age_days', '估值有效天数', 1, 365, 1],
  ['future_cash_days', '近期用途保护天数', 1, 365, 1],
  ['estimated_fee_rate', '预估申购费率（比例）', 0, .05, .0001],
]
const money = v => v == null || !Number.isFinite(Number(v)) ? '待补齐' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const pct = v => v == null ? '待补齐' : `${Number(v).toFixed(1)}%`
const statusLabel = s => ({ allowed:'可在约束内计划', blocked:'暂停新增', wait_for_data:'等待补齐资料', planned:'已保存 · 未成交', executed:'已关联真实成交', reviewed:'已复查', pending_maturity:'期限未成熟', pending_data:'等待期限净值', needs_reconciliation:'期间有其他交易 · 请先对账', not_executed:'尚未成交' }[s] || s)
function message(e) { return e?.response?.data?.detail || e?.message || '请求失败，请重试' }
async function load() {
  const generation = ++loadGeneration
  loading.value = true; error.value = ''
  try {
    const result = await portfolioRiskAPI.snapshot()
    if (generation !== loadGeneration) return
    snapshot.value = result
    if (!selectedFund.value && snapshot.value?.funds?.length) selectedFund.value = snapshot.value.funds[0].fund_code
    // 任何新事实都使旧预览失效。
    if (decision.value?.snapshot_id !== snapshot.value?.snapshot_id) decision.value = null
  } catch(e) { if (generation === loadGeneration) error.value = message(e) }
  finally { if (generation === loadGeneration) loading.value = false }
}
async function loadReviews() {
  try { reviews.value = (await portfolioRiskAPI.reviews()).reviews || [] }
  catch(e) { error.value = message(e) }
}
function openPolicy() {
  draft.value = Object.fromEntries(fields.map(([key]) => [key, snapshot.value?.policy?.[key] ?? null]))
  showPolicy.value = true
}
async function preview() {
  const generation = ++previewGeneration
  const fund = selectedFund.value, requested = Number(amount.value)
  error.value = ''; busy.value = true; decision.value = null
  try {
    const result = await portfolioRiskAPI.preview(fund, requested)
    if (generation === previewGeneration && fund === selectedFund.value && requested === Number(amount.value)) decision.value = result
  }
  catch(e) { if (generation === previewGeneration) error.value = message(e) }
  finally { busy.value = false }
}
async function confirmAction() {
  const action = pendingAction.value
  if (!action) return
  busy.value = true; error.value = ''
  try {
    if (action === 'policy') {
      const payload = { ...draft.value }
      if (payload.max_stress_loss_amount === '') payload.max_stress_loss_amount = null
      await portfolioRiskAPI.savePolicy(payload, snapshot.value.policy.version)
      showPolicy.value = false; decision.value = null; await load()
    } else if (action === 'save' && decision.value?.status === 'allowed') {
      await portfolioRiskAPI.saveReview(decision.value.fund_code, decision.value.executable_amount, decision.value.snapshot_id)
      showReview.value = true; await loadReviews()
    } else if (action === 'link' && activeReview.value) {
      await portfolioRiskAPI.linkExecution(activeReview.value.id, Number(transactionIds.value[activeReview.value.id]))
      await loadReviews()
    }
  } catch(e) { error.value = message(e) }
  finally { pendingAction.value = ''; busy.value = false }
}
async function reviewOutcome(item) {
  busy.value = true; error.value = ''
  try { outcome.value = { id:item.id, ...await portfolioRiskAPI.reviewOutcome(item.id) }; await loadReviews() }
  catch(e) { error.value = message(e) }
  finally { busy.value = false }
}
watch([selectedFund, amount], () => { ++previewGeneration; decision.value = null })
watch(() => props.refreshKey, () => load())
onMounted(load)
defineExpose({ refresh:load })
</script>

<template>
  <section class="risk-overview" aria-label="家庭稳健决策概览">
    <header class="risk-head">
      <div><span class="risk-kicker">家庭风险底线</span><h3>先看承受力，再决定加多少</h3></div>
      <div class="risk-actions"><button class="btn-secondary" :disabled="loading || busy" @click="load">{{ loading ? '核对中…' : '刷新风险快照' }}</button><button class="btn-secondary" :disabled="!snapshot || busy" @click="openPolicy">设置风险上限</button></div>
    </header>
    <p v-if="error" class="risk-error" role="alert">风险资料读取或操作失败：{{ error }}。请刷新核对。</p>
    <template v-if="snapshot">
      <p class="risk-stamp">家庭全部账户 · 核对于 {{ snapshot.as_of?.replace('T', ' ').slice(0,19) }} · 政策 v{{ snapshot.policy_version ?? snapshot.policy?.version }} · {{ snapshot.policy?.confirmed ? '用户已确认' : '默认值尚未确认，暂不执行新增' }}</p>
      <div v-if="snapshot.issues?.length" class="risk-issues"><strong>资料或约束待核对</strong><ul><li v-for="issue in snapshot.issues" :key="issue">{{ issue }}</li></ul></div>
      <div class="risk-ledger">
        <div class="cash-primary"><span>今日可执行现金</span><strong>¥{{ money(snapshot.cash?.available_now) }}</strong><small>已扣冻结、家庭保护和近期用途资金</small></div>
        <div><span>家庭保护资金</span><strong>¥{{ money(snapshot.cash?.protected) }}</strong></div>
        <div><span>近期用途预留</span><strong>¥{{ money(snapshot.cash?.near_term_reserved) }}</strong></div>
        <div><span>冻结 / 应收</span><strong>¥{{ money(snapshot.cash?.frozen) }} / {{ money(snapshot.cash?.receivable) }}</strong></div>
        <div><span>未来月结余 · 仅作计划</span><strong>¥{{ money(snapshot.cash?.future_monthly_surplus) }}</strong></div>
      </div>
      <div class="risk-pressure"><div><span>已知家庭资产</span><b>¥{{ money(snapshot.total_assets) }}</b><small>权益估算 {{ pct(snapshot.overview?.equity_pct) }}{{ snapshot.issues?.length ? ' · 资料不完整，金额仅供核对' : '' }}</small></div><div v-for="s in snapshot.overview?.scenarios" :key="s.key"><span>{{ s.label }}</span><b class="loss">−¥{{ money(s.loss_amount) }}</b></div></div>
      <p class="risk-note">情景损失为固定冲击估算，可能低估实际风险。{{ snapshot.unassessed_factors?.join('；') }}</p>
      <details v-if="snapshot.active_risks?.length" class="risk-details"><summary>尚未解除的风险 · {{ snapshot.active_risks.length }} 项</summary><ul class="risk-active-list"><li v-for="risk in snapshot.active_risks" :key="risk.id"><strong>{{ risk.severity === 'danger' ? '高危 · ' : '' }}{{ risk.title }}</strong><small> {{ risk.fact_date || risk.last_confirmed_at || risk.created_at }} · {{ risk.risk_status === 'in_progress' ? '处理中' : '持续跟踪' }}{{ risk.is_read ? ' · 通知已读，风险仍在' : '' }} · 解除条件：{{ risk.recovery_condition || '成功完整巡检确认不再触发' }}</small></li></ul></details>
      <details class="risk-details"><summary>投前比较与实际执行复查</summary>
        <form class="risk-preview" @submit.prevent="preview"><label>持仓基金<select v-model="selectedFund"><option v-for="f in snapshot.funds" :key="f.fund_code" :value="f.fund_code">{{ f.fund_name }} · {{ f.fund_code }}</option></select></label><label>本次申请金额（含费用）<input v-model.number="amount" type="number" min="0" step=".01" required /></label><button class="btn-primary" :disabled="busy || !selectedFund || loading">重新检查并比较</button></form>
        <div v-if="decision" class="risk-decision"><strong>{{ statusLabel(decision.status) }} · 最终上限 ¥{{ money(decision.executable_amount) }}</strong><ul v-if="decision.reasons?.length"><li v-for="reason in decision.reasons" :key="reason">{{ reason }}</li></ul>
          <div class="risk-table-scroll"><table><thead><tr><th>方案</th><th>投入</th><th>投后可用现金</th><th>单基金</th><th>同指数</th><th>权益</th><th>最大情景损失</th></tr></thead><tbody><tr v-for="s in decision.comparisons" :key="s.key"><td>{{ {no_add:'不补',small_add:'少补',planned_add:'批准计划'}[s.key] }}</td><td>¥{{ money(s.amount) }}</td><td>¥{{ money(s.available_cash) }}</td><td>{{ pct(s.single_position_pct) }}</td><td>{{ pct(s.same_index_pct) }}</td><td>{{ pct(s.equity_pct) }}</td><td>¥{{ money(s.worst_loss_amount) }}</td></tr></tbody></table></div>
          <p class="risk-note">{{ decision.review_conditions?.join('；') }} · 有效至 {{ decision.expires_at?.slice(0,19).replace('T',' ') }}</p><button v-if="decision.status === 'allowed'" class="btn-secondary" :disabled="busy || !!error" @click="pendingAction = 'save'">保存 30 天复查计划</button>
        </div>
        <button class="review-toggle" @click="showReview = !showReview; showReview && loadReviews()">{{ showReview ? '收起复查档案' : '查看已保存复查档案' }}</button>
        <div v-if="showReview" class="review-list"><p v-if="!reviews.length">还没有显式保存的计划。</p><article v-for="r in reviews" :key="r.id"><header><strong>#{{ r.id }} · {{ r.fund_code }} · ¥{{ money(r.approved_amount) }}</strong><span>{{ statusLabel(r.status) }}</span></header><p class="risk-note">保存于 {{ r.created_at }} · 期限 {{ r.horizon_days }} 天 · {{ r.execution?.deviations?.join('；') }}</p><div v-if="!r.transaction_id" class="risk-link"><label>真实已确认买入流水 ID <input v-model.number="transactionIds[r.id]" type="number" min="1" /></label><button class="btn-secondary" :disabled="busy || !transactionIds[r.id]" @click="activeReview = r; pendingAction = 'link'">关联真实成交</button></div><button v-else class="btn-secondary" :disabled="busy" @click="reviewOutcome(r)">按固定期限复查成交 #{{ r.transaction_id }}</button><div v-if="outcome?.id === r.id" class="review-outcome"><strong>{{ statusLabel(outcome.status) }}</strong><p>{{ outcome.message }}</p><p v-if="outcome.profit_loss != null">期限市值 ¥{{ money(outcome.terminal_wealth) }} · 盈亏 ¥{{ money(outcome.profit_loss) }} · 最大损失 ¥{{ money(outcome.max_loss_amount) }} · {{ outcome.unrecovered ? '尚未回本' : '已覆盖本笔成本' }}</p><small>{{ outcome.limitation }} {{ outcome.cash_shortfall_note }}</small></div></article></div>
      </details>
    </template>
    <Teleport to="body"><Transition name="fade"><div v-if="showPolicy" class="risk-modal-backdrop" @click.self="showPolicy = false"><form class="risk-policy-modal" role="dialog" aria-modal="true" aria-labelledby="risk-policy-title" @submit.prevent="pendingAction = 'policy'"><h3 id="risk-policy-title">家庭风险上限</h3><p>按家庭承受能力填写。保存后所有新增建议共用此政策；若资料不足，仍会等待补齐。</p><div class="risk-policy-grid"><label v-for="[key,label,min,max,step] in fields" :key="key">{{ label }}<input v-model.number="draft[key]" type="number" :min="min" :max="max" :step="step" :required="key !== 'max_stress_loss_amount'" /></label></div><div class="risk-actions"><button type="button" class="btn-secondary" @click="showPolicy = false">取消</button><button class="btn-primary" :disabled="busy">核对并保存</button></div></form></div></Transition></Teleport>
    <ConfirmDialog :visible="!!pendingAction" :loading="busy" title="确认本次操作" :message="pendingAction === 'policy' ? '确认这些风险上限符合家庭承受能力？更新后需重新检查所有计划。' : pendingAction === 'save' ? '保存当前批准金额与事实快照，30 天复查。保存不会下单或记为成交。' : '将此计划关联到既有真实成交，保留金额和有效期偏差。此操作不会创建交易。'" @cancel="pendingAction = ''" @confirm="confirmAction" />
  </section>
</template>

<style scoped>
.risk-overview{margin:16px 0 24px;padding:22px 24px;border:1px solid var(--color-border);border-left:3px solid var(--color-text-secondary);border-radius:var(--radius-lg,12px);background:var(--color-bg-card);color:var(--color-text-primary)}
.risk-head,.risk-actions,.review-list article header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.risk-kicker{font-size:10px;letter-spacing:.16em;color:var(--color-text-secondary)}h3{margin:6px 0;font-size:18px}.risk-stamp,.risk-note,small{font-size:11px;line-height:1.7;color:var(--color-text-secondary)}.risk-ledger{display:grid;grid-template-columns:1.8fr repeat(4,1fr);gap:20px;margin:20px 0}.risk-ledger>div,.risk-pressure>div{display:flex;flex-direction:column;gap:6px}.risk-ledger span,.risk-pressure span{font-size:11px;color:var(--color-text-secondary)}.risk-ledger strong{font-variant-numeric:tabular-nums;font-weight:600;font-size:15px}.cash-primary strong{font-size:27px;letter-spacing:-.04em}.risk-pressure{display:grid;grid-template-columns:1.5fr repeat(5,1fr);padding:16px 0;border-top:1px solid var(--color-border);border-bottom:1px solid var(--color-border);gap:16px;font-variant-numeric:tabular-nums}.risk-pressure b{font-size:14px}.loss,.risk-error{color:var(--color-danger,#c34d46)}.risk-issues{border-left:2px solid var(--color-warning,#ba8730);padding:9px 14px;font-size:12px;line-height:1.8;background:color-mix(in srgb,var(--color-warning,#ba8730) 6%,transparent)}ul{padding-left:18px;margin:6px 0}.risk-details{margin-top:16px}.risk-details summary{cursor:pointer;font-size:12px;font-weight:600}.risk-preview{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin:18px 0}.risk-overview label{display:flex;flex-direction:column;gap:7px;font-size:12px}.risk-overview input,.risk-overview select,.risk-policy-modal input{padding:8px 10px;border:1px solid var(--color-border);border-radius:6px;background:var(--color-bg-card);color:var(--color-text-primary);max-width:100%}.risk-decision{border-top:1px solid var(--color-border);padding:16px 0;font-size:12px}.risk-table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;margin:12px 0;font-size:12px;white-space:nowrap}th,td{text-align:right;padding:9px;border-bottom:1px solid var(--color-border);font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}.review-toggle{padding:10px 0;border:0;background:none;color:var(--color-text-secondary);cursor:pointer;text-decoration:underline}.review-list article{padding:16px 0;border-top:1px solid var(--color-border);font-size:12px}.risk-link{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}.review-outcome{margin-top:12px}.risk-modal-backdrop{position:fixed;inset:0;z-index:calc(var(--z-modal,1000) - 1);display:flex;align-items:center;justify-content:center;background:#0008;padding:20px}.risk-policy-modal{width:620px;max-width:100%;max-height:85vh;overflow:auto;padding:28px;border-radius:14px;background:var(--color-bg-card);color:var(--color-text-primary)}.risk-policy-modal p{font-size:12px;line-height:1.7}.risk-policy-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:22px 0}.risk-policy-grid label{display:flex;flex-direction:column;gap:8px;font-size:12px}@media(max-width:1000px){.risk-ledger{grid-template-columns:repeat(3,1fr)}.risk-pressure{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.risk-overview{padding:16px}.risk-ledger,.risk-pressure,.risk-policy-grid{grid-template-columns:1fr 1fr}.cash-primary{grid-column:1/-1}.risk-head{align-items:flex-start}.risk-actions button{font-size:11px}.risk-modal-backdrop{padding:10px}.risk-policy-modal{padding:20px}}
</style>
