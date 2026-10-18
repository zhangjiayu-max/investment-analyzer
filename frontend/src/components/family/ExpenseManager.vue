<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import {
  copyFixedExpenses,
  createExpense,
  deleteExpense,
  getExpenseCategories,
  getExpenses,
  getExpenseSummary,
  getExpenseTrend,
  updateExpense, getExpenseCashflow, setExpenseMonthComplete, getProfile, updateProfile,
} from '../../api'
import { useToast } from '../../composables/useToast'
import ConfirmDialog from '../layout/ConfirmDialog.vue'
import Icon from '../ui/Icon.vue'
import LineChart from '../charts/LineChart.vue'
import RecurringBills from './RecurringBills.vue'
import HouseholdCashflowCard from './HouseholdCashflowCard.vue'
import { shanghaiMonth, previousMonth } from '../../utils/expenseDates'

const props = defineProps({ open: Boolean, initialMonth: { type: String, default: '' } })
const emit = defineEmits(['update:open', 'updated'])
const { showToast } = useToast()

const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(props.initialMonth) ? props.initialMonth : shanghaiMonth())
const items = ref([])
const summary = ref({ total: 0, fixed_total: 0, variable_total: 0, by_category: {} })
const trend = ref([])
const categories = ref({ fixed: [], variable: [] })
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const editingId = ref(null)
const deleteTarget = ref(null)
const copying = ref(false)
const copyTarget = ref(null)
const completionTarget = ref(null)
const operationBusy = ref(false)
const cashflow = ref(null)
const billsVersion = ref(0)
const budget = ref({ monthly_income: '', monthly_expense: '' })
const budgetReady = ref(false)
const budgetSaving = ref(false)
let requestSequence = 0
let budgetSequence = 0
let editingMonth = null

const form = ref({ category: '', amount: '', is_fixed: false, note: '' })

const allCategories = computed(() => form.value.is_fixed ? categories.value.fixed : categories.value.variable)
const trendDates = computed(() => trend.value.map(item => item.month))
const trendSeries = computed(() => [{
  name: '月支出',
  data: trend.value.map(item => item.total),
  color: 'rgb(217, 119, 6)',
}])

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function resetForm() {
  editingId.value = null
  editingMonth = null
  form.value = { category: '', amount: '', is_fixed: false, note: '' }
}

async function load() {
  const selectedMonth = month.value
  const sequence = ++requestSequence
  loading.value = true
  items.value = []
  loadFailed.value = false
  cashflow.value = null
  try {
    const [listRes, summaryRes, trendRes, categoryRes, cashRes] = await Promise.all([
      getExpenses(selectedMonth), getExpenseSummary(selectedMonth), getExpenseTrend(6), getExpenseCategories(),
      getExpenseCashflow(selectedMonth).catch(() => ({ data: null })),
    ])
    if (sequence !== requestSequence || !props.open || selectedMonth !== month.value) return
    items.value = listRes.data.items || []
    summary.value = summaryRes.data || summary.value
    trend.value = trendRes.data.items || []
    categories.value = categoryRes.data || categories.value
    cashflow.value = cashRes.data
    if (!form.value.category) form.value.category = allCategories.value[0] || ''
  } catch (e) {
    if (sequence !== requestSequence || !props.open) return
    items.value = []
    loadFailed.value = true
    showToast('加载支出数据失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}
async function loadBudget() {
  const sequence = ++budgetSequence
  budgetReady.value = false
  try {
    const { data } = await getProfile()
    if (sequence !== budgetSequence || !props.open) return
    const profile = data?.profile || data || {}
    budget.value = { monthly_income: profile.monthly_income ?? '', monthly_expense: profile.monthly_expense ?? '' }
    budgetReady.value = true
  } catch (e) { if (sequence === budgetSequence && props.open) showToast('收支预算加载失败，请重试', 'error') }
}
async function saveBudget() {
  if (budgetSaving.value || !budgetReady.value) return
  const payload = { monthly_income: Number(budget.value.monthly_income), monthly_expense: Number(budget.value.monthly_expense) }
  if (Object.values(budget.value).some(v => v === '') || Object.values(payload).some(v => !Number.isFinite(v) || v < 0)) {
    showToast('请填写有效的收入和预算，金额不能小于零', 'warning'); return
  }
  budgetSaving.value = true
  try {
    await updateProfile(payload)
    showToast('收支预算已保存', 'success')
    if (props.open) await load()
    emit('updated')
  } catch (e) { showToast('保存预算失败: ' + e.message, 'error') }
  finally { budgetSaving.value = false }
}
watch(month, () => { resetForm(); if (props.open) load() })
watch(() => props.initialMonth, value => {
  if (!props.open || !/^\d{4}-(0[1-9]|1[0-2])$/.test(value) || value === month.value) return
  month.value = value
})
watch(() => props.open, open => {
  if (open) {
    if (/^\d{4}-(0[1-9]|1[0-2])$/.test(props.initialMonth) && props.initialMonth !== month.value) month.value = props.initialMonth
    load(); loadBudget()
  }
  else { ++requestSequence; ++budgetSequence; resetForm(); if (!operationBusy.value) { deleteTarget.value = null; completionTarget.value = null; copyTarget.value = null } }
})
onBeforeUnmount(() => { ++requestSequence; ++budgetSequence })
function changeType() { form.value.category = allCategories.value[0] || '' }

async function save() {
  if (saving.value) return
  const selectedMonth = editingMonth || month.value
  const savedId = editingId.value
  const amount = Number(form.value.amount)
  if (!form.value.category || !Number.isFinite(amount) || amount <= 0) {
    showToast('请选择分类并填写大于零的金额', 'warning')
    return
  }
  saving.value = true
  const payload = {
    month: selectedMonth,
    category: form.value.category,
    amount,
    is_fixed: form.value.is_fixed,
    note: form.value.note.trim(),
  }
  try {
    if (savedId) await updateExpense(savedId, payload)
    else await createExpense(payload)
    showToast(savedId ? '支出已更新' : '支出已记录', 'success')
    if (selectedMonth === month.value) resetForm()
    if (props.open) await load()
    emit('updated')
  } catch (e) {
    showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    saving.value = false
  }
}

function edit(item) {
  if (loading.value || item.recurring_template_id) return
  editingMonth = item.month || month.value
  editingId.value = item.id
  form.value = { category: item.category, amount: item.amount, is_fixed: item.is_fixed, note: item.note || '' }
}

async function confirmDelete() {
  if (!deleteTarget.value || operationBusy.value) return
  const target = deleteTarget.value
  operationBusy.value = true
  try {
    await deleteExpense(target.id)
    if (target.recurring_template_id) ++billsVersion.value
    deleteTarget.value = null
    showToast('支出记录已删除', 'success')
    await load()
    emit('updated')
  } catch (e) {
    showToast('删除失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally { operationBusy.value = false }
}

async function copyFixed() {
  if (!copyTarget.value || copying.value) return
  const targetMonth = copyTarget.value
  const previous = previousMonth(targetMonth)
  copying.value = true
  try {
    const { data } = await copyFixedExpenses(previous, targetMonth)
    showToast(data.copied ? `已复制 ${data.copied} 条固定支出` : '没有需要复制的固定支出', 'success')
    await load()
    emit('updated')
  } catch (e) {
    showToast('复制失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    copying.value = false
    copyTarget.value = null
  }
}

async function confirmComplete() {
  if (!completionTarget.value || operationBusy.value) return
  const { month: targetMonth, complete } = completionTarget.value
  operationBusy.value = true
  try {
    await setExpenseMonthComplete(targetMonth, complete)
    completionTarget.value = null
    if (props.open) await load()
    emit('updated')
  } catch (e) { showToast('更新记账状态失败: ' + e.message, 'error') }
  finally { operationBusy.value = false }
}
function recurringUpdated() { load(); emit('updated') }
onMounted(() => { if (props.open) { load(); loadBudget() } })
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="expense-backdrop" @click.self="emit('update:open', false)">
        <section class="expense-panel" aria-label="本月支出">
          <header class="expense-head">
            <div>
              <h2 class="editorial-title-lg">本月支出</h2>
              <p>实际消费将参与月结余与投资额度计算。</p>
            </div>
            <button class="icon-btn" title="关闭" @click="emit('update:open', false)"><Icon name="x" size="18" /></button>
          </header>

          <div class="expense-controls">
            <input v-model="month" type="month" class="input-field" aria-label="月份" />
            <button class="btn-secondary" :disabled="copying" @click="copyTarget = month">
              <Icon name="copy" size="15" /> {{ copying ? '复制中...' : '复制上月固定项' }}
            </button>
          </div>

          <details class="budget-settings" open>
            <summary>家庭收支预算设置</summary>
            <p>填写全月总支出预算（包含固定账单）。尚未记全时，预测取预算与「实际 + 待支付 + 未来预留」中的较高值。</p>
            <form class="budget-form" @submit.prevent="saveBudget">
              <label>月收入（元）<input v-model="budget.monthly_income" type="number" min="0" step="0.01" class="input-field" aria-label="月收入" :disabled="!budgetReady" /></label>
              <label>月支出预算（全月总额 / 元）<input v-model="budget.monthly_expense" type="number" min="0" step="0.01" class="input-field" aria-label="月支出预算" :disabled="!budgetReady" /></label>
              <button class="btn-secondary" :disabled="budgetSaving || !budgetReady">{{ budgetSaving ? '保存中...' : '保存收支预算' }}</button>
              <button v-if="!budgetReady" type="button" class="btn-secondary" @click="loadBudget">重试加载预算</button>
            </form>
          </details>
          <HouseholdCashflowCard :cashflow="cashflow" />
          <div class="completion-row">
            <span>{{ !cashflow ? '记账状态暂不可用' : cashflow.expense_complete ? '本月已记全' : '本月尚未记全，预测保留预算兜底' }}</span>
            <button class="btn-secondary" :disabled="!cashflow || operationBusy || loading" @click="completionTarget = { month, complete: !cashflow.expense_complete }">{{ cashflow?.expense_complete ? '改为未记全' : '确认本月已记全' }}</button>
          </div>
          <RecurringBills v-if="open" :key="billsVersion" :month="month" :categories="categories.fixed" @updated="recurringUpdated" />
          <div v-if="!loading && !loadFailed" class="expense-summary">
            <div><span>本月支出</span><strong>¥{{ money(summary.total) }}</strong></div>
            <div><span>固定支出</span><strong>¥{{ money(summary.fixed_total) }}</strong></div>
            <div><span>浮动支出</span><strong>¥{{ money(summary.variable_total) }}</strong></div>
          </div>

          <form class="expense-form" @submit.prevent="save">
            <select v-model="form.is_fixed" class="input-field" aria-label="支出类型" @change="changeType">
              <option :value="false">浮动支出</option>
              <option :value="true">固定支出</option>
            </select>
            <select v-model="form.category" class="input-field" aria-label="支出分类">
              <option v-for="category in allCategories" :key="category" :value="category">{{ category }}</option>
            </select>
            <input v-model="form.amount" type="number" min="0.01" step="0.01" class="input-field" placeholder="金额" aria-label="支出金额" />
            <input v-model="form.note" type="text" class="input-field" placeholder="备注（可选）" />
            <button type="submit" class="btn-primary" :disabled="saving">{{ saving ? '保存中...' : editingId ? '更新支出' : '记录支出' }}</button>
            <button v-if="editingId" type="button" class="btn-secondary" @click="resetForm">取消编辑</button>
          </form>

          <div class="expense-content">
            <div class="expense-list">
              <div class="list-head"><h3>记录明细</h3><span>{{ items.length }} 条</span></div>
              <div v-if="loading" class="empty-state">正在加载...</div>
              <div v-else-if="loadFailed" class="empty-state">支出记录暂不可用，请稍后重试。<button class="btn-secondary" @click="load">重试</button></div>
              <div v-else-if="!items.length" class="empty-state">还没有支出记录，先录入每月固定支出。</div>
              <article v-for="item in items" :key="item.id" class="expense-item">
                <div><strong>{{ item.category }}</strong><span v-if="item.recurring_template_id">周期账单已支付 · 修改金额请先删除后重新支付</span><span>{{ item.is_fixed ? '固定' : '浮动' }}{{ item.note ? ` · ${item.note}` : '' }}</span></div>
                <b>¥{{ money(item.amount) }}</b>
                <button class="icon-btn" title="编辑" :disabled="loading || Boolean(item.recurring_template_id) || saving" @click="edit(item)"><Icon name="pencil" size="15" /></button>
                <button class="icon-btn danger" title="删除" :disabled="loading || operationBusy" @click="deleteTarget = item"><Icon name="trash" size="15" /></button>
              </article>
            </div>
            <div class="expense-chart">
              <h3>近六个月趋势</h3>
              <LineChart v-if="trend.length > 1" :dates="trendDates" :series="trendSeries" :y-names="['月支出']" :area="true" :smooth="true" :zoomable="false" height="180px" />
              <div v-else class="empty-state">记录两个月以上后显示趋势。</div>
            </div>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>

  <ConfirmDialog
    :visible="Boolean(deleteTarget)"
    title="删除支出记录"
    :message="deleteTarget?.recurring_template_id ? '删除后该周期账单恢复待支付，并重新计算本月结余，确定继续？' : '删除后会重新计算本月结余，确定继续？'"
    :loading="operationBusy"
    confirm-text="删除"
    danger
    @confirm="confirmDelete"
    @cancel="deleteTarget = null"
  />
  <ConfirmDialog :visible="Boolean(copyTarget)" title="复制上月实际支出" :message="`将 ${copyTarget} 的上月手工固定项复制为已支付实际记录。周期账单支付记录不会复制。请确认这些款项本月确实已支付。`" confirm-text="确认复制" :loading="copying" @confirm="copyFixed" @cancel="copyTarget = null" />
  <ConfirmDialog :visible="Boolean(completionTarget)" title="更新月度记账状态" :message="completionTarget?.complete ? `确认 ${completionTarget.month} 所有实际支出均已录入？确认后不再使用日常预算兜底，待支付账单仍保留。` : `将 ${completionTarget?.month} 改为未记全，恢复日常预算兜底。`" :loading="operationBusy" @confirm="confirmComplete" @cancel="completionTarget = null" />
</template>

<style scoped>
.budget-settings { margin-bottom: var(--space-4); padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.budget-settings summary { cursor: pointer; font-weight: 600; }
.budget-settings p { font-size: .8rem; color: var(--color-text-secondary); }
.budget-form { display: flex; align-items: flex-end; gap: var(--space-3); flex-wrap: wrap; }
.budget-form label { font-size: .8rem; display: grid; gap: .4rem; flex: 1; min-width: 150px; }
.completion-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin: var(--space-3) 0 var(--space-5); font-size: .8rem; color: var(--color-text-secondary); }
.expense-backdrop { position: fixed; inset: 0; z-index: var(--z-modal); display: flex; justify-content: flex-end; background: rgba(15, 23, 42, .42); }
.expense-panel { width: min(960px, 100%); height: 100%; overflow-y: auto; background: var(--color-bg-card); padding: var(--space-6); box-shadow: var(--shadow-lg); }
.expense-head, .expense-controls, .list-head, .expense-item { display: flex; align-items: center; gap: var(--space-3); }
.expense-head { justify-content: space-between; border-bottom: 1px solid var(--color-border); padding-bottom: var(--space-4); }
.expense-head h2, .expense-head p, h3 { margin: 0; }
.expense-head p { margin-top: .35rem; color: var(--color-text-secondary); font-size: .86rem; }
.expense-controls { margin: var(--space-4) 0; }
.expense-controls .input-field { width: 160px; }
.expense-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-3); }
.expense-summary > div { padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.expense-summary span, .expense-item span, .list-head span { display: block; color: var(--color-text-muted); font-size: .78rem; }
.expense-summary strong { display: block; margin-top: .25rem; font-size: 1.1rem; }
.expense-form { display: grid; grid-template-columns: 120px 130px 120px minmax(150px, 1fr) auto auto; gap: var(--space-2); margin: var(--space-5) 0; }
.expense-content { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-5); }
.expense-list, .expense-chart { min-width: 0; border-top: 1px solid var(--color-border); padding-top: var(--space-3); }
.list-head { justify-content: space-between; margin-bottom: var(--space-2); }
.expense-item { padding: .75rem 0; border-bottom: 1px solid var(--color-border-light); }
.expense-item > div { flex: 1; min-width: 0; }
.expense-item b { white-space: nowrap; }
.icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text-secondary); }
.icon-btn:hover { background: var(--color-bg-hover); }
.icon-btn.danger { color: var(--color-danger); }
.empty-state { padding: var(--space-5); text-align: center; color: var(--color-text-muted); font-size: .86rem; }
@media (max-width: 760px) { .expense-panel { padding: var(--space-4); } .expense-summary, .expense-content { grid-template-columns: 1fr; } .expense-form { grid-template-columns: 1fr 1fr; } .expense-form input:nth-of-type(2), .expense-form button { grid-column: span 2; } }
</style>
