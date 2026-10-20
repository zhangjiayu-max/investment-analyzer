<script setup>
import { computed } from 'vue'

const props = defineProps({
  month: { type: String, default: '' },
  cashflow: { type: Object, default: null },
})

const bills = computed(() => props.cashflow?.recurring_bills || [])
const categories = computed(() => props.cashflow?.recurring_by_category || [])
const count = computed(() => Number(props.cashflow?.recurring_count || 0))

function amount(value) {
  if (value == null || !Number.isFinite(Number(value))) return '待确认'
  return `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function statusLabel(status) { return status === 'paid' ? '已付' : '待付' }
function plannedChanged(bill) {
  return bill.status === 'paid' && bill.current_template_amount != null && Number(bill.current_template_amount) !== Number(bill.amount)
}
</script>

<template>
  <section class="fixed-bills-overview editorial-card" aria-label="本月固定负担">
    <header class="fixed-bills-head">
      <div>
        <h3>固定负担 · {{ month }}</h3>
        <p>实际支付保留当月快照；修改模板只影响未支付账单与后续月份。</p>
      </div>
      <span v-if="count" class="bill-count">{{ count }} 项生效中</span>
    </header>
    <div class="fixed-summary">
      <div><span>月均固定负担</span><strong>{{ amount(cashflow?.monthly_provision) }}</strong></div>
      <div><span>本月应付</span><strong>{{ amount(cashflow?.recurring_due_total) }}</strong></div>
      <div><span>已付</span><strong class="paid">{{ amount(cashflow?.recurring_paid) }}</strong></div>
      <div><span>待付</span><strong class="pending">{{ amount(cashflow?.recurring_pending) }}</strong></div>
    </div>
    <div v-if="categories.length" class="category-breakdown">
      <div v-for="item in categories" :key="item.category" class="category-row">
        <span>{{ item.category }}</span><span>已付 {{ amount(item.paid) }} · 待付 {{ amount(item.pending) }} · 合计 {{ amount(item.total) }}</span>
      </div>
    </div>
    <div v-if="bills.length" class="bill-items">
      <article v-for="bill in bills" :key="`${bill.template_id}-${bill.month}`" class="fixed-bill-row">
        <div class="bill-main"><strong>{{ bill.name }}</strong><small>{{ bill.category }} · {{ bill.due_date }} 到期</small><small v-if="plannedChanged(bill)" class="planned-change">实际 {{ amount(bill.amount) }} · 计划 {{ amount(bill.current_template_amount) }}</small></div>
        <span class="bill-amount">{{ amount(bill.amount) }}</span>
        <span :class="['bill-status', bill.status]">{{ statusLabel(bill.status) }}</span>
      </article>
    </div>
    <p v-else-if="count" class="fixed-empty">本月没有应付账单，已启用的固定账单将在其生效月份生成。</p>
    <p v-else class="fixed-empty">暂无启用的固定账单。</p>
  </section>
</template>

<style scoped>
.fixed-bills-overview { border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-bg-card); padding: var(--space-4); }
.fixed-bills-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-4); }
.fixed-bills-head h3, .fixed-bills-head p { margin: 0; }
.fixed-bills-head p { margin-top: .35rem; color: var(--color-text-secondary); font-size: .78rem; }
.bill-count { color: var(--color-text-muted); font-size: .78rem; }
.fixed-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-3); }
.fixed-summary > div { padding: var(--space-3); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); }
.fixed-summary span, .category-row, small { color: var(--color-text-secondary); font-size: .75rem; }
.fixed-summary strong { display: block; margin-top: .35rem; font-size: 1.1rem; }
.paid, .bill-status.paid { color: var(--color-success); }
.pending, .bill-status.pending { color: var(--color-warning-text); }
.category-breakdown { display: grid; gap: .35rem; margin-top: var(--space-4); }
.category-row { display: flex; justify-content: space-between; gap: var(--space-3); padding-bottom: .35rem; border-bottom: 1px solid var(--color-border-light); }
.bill-items { margin-top: var(--space-4); }
.fixed-bill-row { display: flex; align-items: center; gap: var(--space-3); padding: .7rem 0; border-top: 1px solid var(--color-border-light); }
.bill-main { flex: 1; min-width: 0; }
.bill-main strong { display: block; }
.bill-main small { display: block; margin-top: .2rem; }
.bill-amount { white-space: nowrap; font-family: var(--font-mono-jet); }
.bill-status { font-size: .78rem; min-width: 2.5rem; text-align: right; }
.planned-change { color: var(--color-warning-text); }
.fixed-empty { margin: var(--space-4) 0 0; color: var(--color-text-muted); font-size: .8rem; }
@media (max-width: 760px) { .fixed-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); } .category-row { flex-direction: column; gap: .2rem; } }
</style>
