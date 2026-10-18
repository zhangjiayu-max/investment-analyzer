<script setup>
import { computed } from 'vue'
const props = defineProps({ cashflow: { type: Object, default: null }, title: { type: String, default: '家庭现金流' } })
const available = computed(() => props.cashflow != null)
function money(value) {
  return value == null || !Number.isFinite(Number(value)) ? '待确认' : `¥${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
}
const metrics = computed(() => [
  ['月收入', props.cashflow?.monthly_income > 0 ? props.cashflow.monthly_income : null], ['月支出预算', props.cashflow?.budget_expense > 0 || props.cashflow?.expense_complete ? props.cashflow?.budget_expense : null],
  ['已记实际', props.cashflow?.recorded_expense], ['本月待支付', props.cashflow?.pending_bills],
  ['未来账单月预留', props.cashflow?.future_provision], ['预测总支出', props.cashflow?.data_available === false ? null : props.cashflow?.monthly_expense],
  ['预测月结余', props.cashflow?.data_available === false ? null : props.cashflow?.monthly_surplus], ['可投资结余', props.cashflow?.data_available === false ? null : props.cashflow?.investable_surplus],
])
</script>
<template>
  <section class="household-cashflow editorial-card" aria-label="家庭现金流">
    <header><h3>{{ title }}</h3><span v-if="cashflow" class="flow-state">{{ cashflow.expense_complete ? '本月已记全' : '尚未记全' }}</span></header>
    <p v-if="!available" class="flow-unavailable">现金流暂不可用，请检查收支资料或稍后刷新。</p>
    <template v-else>
      <p v-if="cashflow.data_available === false" class="flow-alert">可投资额度暂不计算，请补充月收入和全月预算，或明确确认本月支出已记全。</p>
      <div class="flow-grid">
        <div v-for="[label, value] in metrics" :key="label" :class="{ 'flow-highlight': label === '可投资结余', 'flow-negative': value < 0 }"><span>{{ label }}</span><strong class="font-jet">{{ money(value) }}</strong></div>
      </div>
      <p class="flow-explanation">{{ cashflow.expense_complete ? '实际支出已记全，仍保留本月待支付与未来账单预留。' : '尚未记全：预算兜底，预测取全月预算与「已记实际 + 本月待支付 + 未来预留」中的较高值；零记录不代表零支出。' }}</p>
      <div class="flow-emergency">
        <div><span>本月还贷</span><strong>{{ money(cashflow.debt_payment) }}</strong></div>
        <div><span>还贷占收入</span><strong>{{ cashflow.monthly_income > 0 && cashflow.debt_payment != null ? `${(cashflow.debt_payment / cashflow.monthly_income * 100).toFixed(1)}%` : '待确认' }}</strong></div>
        <div><span>资金桶月预留</span><strong>{{ money(cashflow.bucket_reserve) }}</strong></div>
        <div><span>备用金目标</span><strong>{{ money(cashflow.emergency_target) }}</strong></div>
        <div><span>已备 / 缺口</span><strong>{{ money(cashflow.emergency_available) }} / {{ money(cashflow.emergency_gap) }}</strong></div>
        <div><span>备用金覆盖</span><strong>{{ cashflow.emergency_months == null ? '待确认' : `${cashflow.emergency_months} 个月` }}</strong></div>
      </div>
      <p v-if="cashflow.overdue_bills || cashflow.upcoming_bills" class="flow-alert">逾期待支付 {{ cashflow.overdue_bills ?? '待确认' }} 项 · 近期到期 {{ cashflow.upcoming_bills ?? '待确认' }} 项</p>
      <p class="flow-footnote">可投资结余已考虑月供与资金桶预留。月供不代表贷款余额；保费记账仅代表现金支出，不能替代家庭保障评估。投资健康分单独评估基金持仓。</p>
    </template>
  </section>
</template>
<style scoped>
.household-cashflow { border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-bg-card); padding: var(--space-4); }
header { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-4); }
h3 { margin: 0; font-size: 1rem; }
.flow-state { color: var(--color-warning-text); background: var(--color-warning-bg); padding: .2rem .55rem; border-radius: var(--radius-sm); font-size: .75rem; }
.flow-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-4) var(--space-3); }
.flow-grid > div, .flow-emergency > div { display: flex; flex-direction: column; gap: .35rem; }
.flow-grid span, .flow-emergency span { font-size: .75rem; color: var(--color-text-secondary); }
.flow-grid strong { font-size: 1rem; }
.flow-highlight strong { color: var(--color-success); }
.flow-negative strong { color: var(--color-danger); }
.flow-explanation, .flow-unavailable { line-height: 1.7; color: var(--color-text-secondary); font-size: .8rem; margin: var(--space-3) 0; }
.flow-emergency { display: grid; grid-template-columns: 1fr 1.4fr 1fr; gap: var(--space-3); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
.flow-emergency strong { font-size: .85rem; font-weight: 500; }
.flow-alert { color: var(--color-warning-text); font-size: .8rem; }
.flow-footnote { margin: var(--space-3) 0 0; font-size: .72rem; line-height: 1.6; color: var(--color-text-muted); }
@media (max-width: 760px) { .flow-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .flow-emergency { grid-template-columns: 1fr; } }
</style>
