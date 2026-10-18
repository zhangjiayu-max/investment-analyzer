<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { listRecurringTemplates, createRecurringTemplate, updateRecurringTemplate, deleteRecurringTemplate, getRecurringBills, payRecurringBill } from '../../api'
import { useToast } from '../../composables/useToast'
import ConfirmDialog from '../layout/ConfirmDialog.vue'
import { shanghaiDate } from '../../utils/expenseDates'
const props = defineProps({ month: { type: String, required: true }, categories: { type: Array, default: () => [] } })
const emit = defineEmits(['updated'])
const { showToast } = useToast()
const templates = ref([])
const bills = ref([])
const loading = ref(false)
const failed = ref(false)
const saving = ref(false)
const busy = ref(false)
const editingId = ref(null)
const action = ref(null)
let sequence = 0
let disposed = false
const emptyForm = () => ({ name: '', category: props.categories[0] || '保险', amount: '', frequency: 'monthly', start_month: props.month, end_month: '', due_day: 1, active: true, note: '' })
const form = ref(emptyForm())
function billReminder(bill) {
  if (bill.status === 'paid') return '已支付'
  const today = shanghaiDate()
  if (bill.due_date < today) return '已过到期日（待确认）'
  return bill.due_date === today ? '今日到期（待支付）' : '未到期（待支付）'
}
const frequencyLabel = value => ({ monthly: '每月', quarterly: '每季度', yearly: '每年' }[value] || value)
const money = value => Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
function reset() { editingId.value = null; form.value = emptyForm() }
async function load() {
  const request = ++sequence
  const month = props.month
  loading.value = true; failed.value = false
  try {
    const [templateRes, billRes] = await Promise.all([listRecurringTemplates(), getRecurringBills(month)])
    if (disposed || request !== sequence || month !== props.month) return
    templates.value = templateRes.data.items || []
    bills.value = billRes.data.items || []
  } catch (e) {
    if (disposed || request !== sequence) return
    failed.value = true; bills.value = []; templates.value = []
    showToast('周期账单加载失败: ' + e.message, 'error')
  } finally { if (!disposed && request === sequence) loading.value = false }
}
watch(() => props.month, () => { reset(); load() })
watch(() => props.categories, values => { if (!editingId.value && !values.includes(form.value.category)) form.value.category = values[0] || '保险' })
onMounted(load)
onBeforeUnmount(() => { disposed = true; ++sequence })
function edit(template) {
  editingId.value = template.id
  form.value = { ...template, end_month: template.end_month || '' }
}
async function save() {
  if (saving.value) return
  const payload = {
    name: form.value.name.trim(), category: form.value.category,
    note: (form.value.note || '').trim(), amount: Number(form.value.amount),
    frequency: form.value.frequency, start_month: form.value.start_month,
    due_day: Number(form.value.due_day), end_month: form.value.end_month || null,
  }
  if (!payload.name || !payload.category || !Number.isFinite(payload.amount) || payload.amount <= 0 || !payload.start_month || !Number.isInteger(payload.due_day) || payload.due_day < 1 || payload.due_day > 31 || (payload.end_month && payload.end_month < payload.start_month)) {
    showToast('请填写有效账单信息，结束月份不能早于开始月份', 'warning'); return
  }
  const id = editingId.value
  const month = props.month
  saving.value = true
  try {
    if (id) await updateRecurringTemplate(id, payload)
    else await createRecurringTemplate(payload)
    if (!disposed && month === props.month) reset()
    if (!disposed) await load()
    emit('updated')
    showToast('周期账单已保存', 'success')
  } catch (e) { showToast('保存账单失败: ' + (e.response?.data?.detail || e.message), 'error') }
  finally { saving.value = false }
}
function askPay(bill) { if (!busy.value) action.value = { type: 'pay', item: { ...bill }, month: props.month } }
function askToggle(template) { if (!busy.value) action.value = { type: template.active ? 'stop' : 'restore', item: { ...template } } }
async function confirmAction() {
  if (!action.value || busy.value) return
  const target = action.value
  busy.value = true
  try {
    if (target.type === 'pay') await payRecurringBill(target.item.template_id, target.month)
    else if (target.type === 'stop') await deleteRecurringTemplate(target.item.id)
    else await updateRecurringTemplate(target.item.id, { active: true })
    action.value = null
    if (!disposed) await load()
    emit('updated')
    showToast(target.type === 'pay' ? '账单已支付并记入实际支出' : '账单状态已更新', 'success')
  } catch (e) { showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error') }
  finally { busy.value = false }
}
</script>
<template>
  <section class="recurring-bills" aria-label="周期账单">
    <header><div><h3>周期账单</h3><p>月供、保费与家庭固定开销，按周期提醒并预留。</p></div><span>{{ month }}</span></header>
    <p v-if="loading" class="muted">正在加载账单…</p>
    <p v-else-if="failed" class="muted">账单暂不可用。<button class="btn-secondary" @click="load">重试</button></p>
    <div v-else class="bills-list">
      <p v-if="!bills.length" class="muted">本月没有到期账单。未来季度和年度账单仍按月预留。</p>
      <article v-for="bill in bills" :key="bill.template_id" class="bill-row">
        <div><strong>{{ bill.name }}</strong><small>{{ bill.category }} · {{ bill.due_date }} 到期</small><small v-if="bill.status !== 'paid'" :class="bill.due_date <= shanghaiDate() ? 'bill-due' : ''">{{ billReminder(bill) }}</small></div>
        <b class="font-jet">¥{{ money(bill.amount) }}</b>
        <span v-if="bill.status === 'paid'" class="paid">已支付</span>
        <button v-else class="btn-secondary" :disabled="busy" @click="askPay(bill)">登记已支付</button>
      </article>
    </div>
    <details open class="templates-section">
      <summary>管理周期账单模板</summary>
      <div v-for="template in templates" :key="template.id" class="template-row">
        <div><strong>{{ template.name }}</strong><small>{{ frequencyLabel(template.frequency) }} ¥{{ money(template.amount) }} · {{ template.active ? '生效中' : '已停用' }}</small></div>
        <button type="button" class="btn-secondary" :disabled="busy || saving" @click="edit(template)">编辑模板</button>
        <button type="button" class="btn-secondary" :disabled="busy" @click="askToggle(template)">{{ template.active ? '停用' : '恢复' }}</button>
      </div>
      <form class="template-form" @submit.prevent="save">
        <label>账单名称<input v-model="form.name" class="input-field" aria-label="账单名称" placeholder="例如家庭保险" required /></label>
        <label>分类<select v-model="form.category" class="input-field" aria-label="账单分类"><option v-for="category in (categories.length ? categories : ['保险'])" :key="category">{{ category }}</option><option v-if="form.category && categories.length && !categories.includes(form.category)">{{ form.category }}</option></select></label>
        <label>每期金额<input v-model="form.amount" type="number" min="0.01" step="0.01" class="input-field" aria-label="账单金额" required /></label>
        <label>周期<select v-model="form.frequency" class="input-field" aria-label="账单周期"><option value="monthly">每月</option><option value="quarterly">每季度</option><option value="yearly">每年</option></select></label>
        <label>起始月份<input v-model="form.start_month" type="month" class="input-field" aria-label="账单起始月份" required /></label>
        <label>结束月份（可选）<input v-model="form.end_month" type="month" class="input-field" aria-label="账单结束月份" /></label>
        <label>到期日（1–31）<input v-model="form.due_day" type="number" min="1" max="31" class="input-field" aria-label="账单到期日" /></label>
        <label>备注<input v-model="form.note" class="input-field" aria-label="账单备注" /></label>
        <button type="submit" class="btn-primary" :disabled="saving || busy">{{ saving ? '保存中...' : editingId ? '保存模板' : '新增周期账单' }}</button>
        <button v-if="editingId" type="button" class="btn-secondary" @click="reset">取消编辑</button>
      </form>
      <p class="muted">季度、年度以起始月份为周期锚点；不足指定到期日的月份按月末处理。编辑模板只影响未支付账单和后续生效月份，不改变已支付记录。</p>
    </details>
  </section>
  <ConfirmDialog :visible="Boolean(action)" :title="action?.type === 'pay' ? '登记账单支付' : action?.type === 'stop' ? '停用周期账单' : '恢复周期账单'" :message="action?.type === 'pay' ? `确认 ${action.month} 的「${action.item.name}」¥${money(action.item.amount)} 已实际支付？仅记录您已完成的支付，不会发起转账或扣款；实际支出仅记一次。` : action?.type === 'stop' ? `停用「${action.item.name}」后将不再生成待支付账单，已支付记录保留。` : `恢复「${action?.item.name}」并按原周期继续生成账单。`" :confirm-text="action?.type === 'pay' ? '确认已支付并记账' : action?.type === 'stop' ? '确认停用' : '确认恢复'" :danger="action?.type === 'stop'" :loading="busy" @confirm="confirmAction" @cancel="action = null" />
</template>
<style scoped>
.recurring-bills { margin-bottom: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: var(--space-4); }
header, .bill-row, .template-row { display: flex; align-items: center; gap: var(--space-3); }
header { justify-content: space-between; padding-bottom: var(--space-3); }
h3, header p { margin: 0; }
header p { margin-top: .4rem; color: var(--color-text-secondary); font-size: .8rem; }
header > span { color: var(--color-text-muted); font-size: .85rem; }
.bill-row, .template-row { padding: var(--space-3) 0; border-top: 1px solid var(--color-border-light); }
.bill-row > div, .template-row > div { flex: 1; min-width: 0; }
small { display: block; color: var(--color-text-muted); font-size: .75rem; margin-top: .25rem; }
.bill-due { color: var(--color-warning-text); }
.paid { color: var(--color-success); font-size: .8rem; }
.muted { font-size: .77rem; color: var(--color-text-muted); line-height: 1.7; }
.templates-section { margin-top: var(--space-4); border-top: 1px solid var(--color-border); padding-top: var(--space-3); }
summary { cursor: pointer; font-size: .85rem; font-weight: 600; margin-bottom: var(--space-3); }
.template-form { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-3); margin-top: var(--space-3); }
.template-form label { display: grid; gap: .35rem; font-size: .75rem; color: var(--color-text-secondary); }
.input-field { width: 100%; min-width: 0; }
@media (max-width: 760px) { .template-form { grid-template-columns: repeat(2, minmax(0, 1fr)); } .bill-row, .template-row { flex-wrap: wrap; } }
</style>
