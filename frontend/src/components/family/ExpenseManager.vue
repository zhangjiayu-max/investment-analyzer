<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  copyFixedExpenses,
  createExpense,
  deleteExpense,
  getExpenseCategories,
  getExpenses,
  getExpenseSummary,
  getExpenseTrend,
  updateExpense,
} from '../../api'
import { useToast } from '../../composables/useToast'
import ConfirmDialog from '../layout/ConfirmDialog.vue'
import Icon from '../ui/Icon.vue'
import LineChart from '../charts/LineChart.vue'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['update:open', 'updated'])
const { showToast } = useToast()

const month = ref(new Date().toISOString().slice(0, 7))
const items = ref([])
const summary = ref({ total: 0, fixed_total: 0, variable_total: 0, by_category: {} })
const trend = ref([])
const categories = ref({ fixed: [], variable: [] })
const loading = ref(false)
const saving = ref(false)
const editingId = ref(null)
const deleteTarget = ref(null)
const copying = ref(false)
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
  form.value = { category: '', amount: '', is_fixed: false, note: '' }
}

async function load() {
  loading.value = true
  try {
    const [listRes, summaryRes, trendRes, categoryRes] = await Promise.all([
      getExpenses(month.value), getExpenseSummary(month.value), getExpenseTrend(6), getExpenseCategories(),
    ])
    items.value = listRes.data.items || []
    summary.value = summaryRes.data || summary.value
    trend.value = trendRes.data.items || []
    categories.value = categoryRes.data || categories.value
    if (!form.value.category) form.value.category = categories.value.variable?.[0] || ''
  } catch (e) {
    showToast('加载支出数据失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

watch(month, load)
watch(() => form.value.is_fixed, () => {
  form.value.category = allCategories.value[0] || ''
})
watch(() => props.open, open => { if (open) load() })

async function save() {
  const amount = Number(form.value.amount)
  if (!form.value.category || !Number.isFinite(amount) || amount <= 0) {
    showToast('请选择分类并填写大于零的金额', 'warning')
    return
  }
  saving.value = true
  const payload = {
    month: month.value,
    category: form.value.category,
    amount,
    is_fixed: form.value.is_fixed,
    note: form.value.note.trim(),
  }
  try {
    if (editingId.value) await updateExpense(editingId.value, payload)
    else await createExpense(payload)
    showToast(editingId.value ? '支出已更新' : '支出已记录', 'success')
    resetForm()
    await load()
    emit('updated')
  } catch (e) {
    showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    saving.value = false
  }
}

function edit(item) {
  editingId.value = item.id
  form.value = { category: item.category, amount: item.amount, is_fixed: item.is_fixed, note: item.note || '' }
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  try {
    await deleteExpense(deleteTarget.value.id)
    deleteTarget.value = null
    showToast('支出记录已删除', 'success')
    await load()
    emit('updated')
  } catch (e) {
    showToast('删除失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

async function copyFixed() {
  const [year, value] = month.value.split('-').map(Number)
  const previous = new Date(year, value - 2, 1).toISOString().slice(0, 7)
  copying.value = true
  try {
    const { data } = await copyFixedExpenses(previous, month.value)
    showToast(data.copied ? `已复制 ${data.copied} 条固定支出` : '没有需要复制的固定支出', 'success')
    await load()
    emit('updated')
  } catch (e) {
    showToast('复制失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    copying.value = false
  }
}

onMounted(() => { if (props.open) load() })
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
            <button class="btn-secondary" :disabled="copying" @click="copyFixed">
              <Icon name="copy" size="15" /> {{ copying ? '复制中...' : '复制上月固定项' }}
            </button>
          </div>

          <div class="expense-summary">
            <div><span>本月支出</span><strong>¥{{ money(summary.total) }}</strong></div>
            <div><span>固定支出</span><strong>¥{{ money(summary.fixed_total) }}</strong></div>
            <div><span>浮动支出</span><strong>¥{{ money(summary.variable_total) }}</strong></div>
          </div>

          <form class="expense-form" @submit.prevent="save">
            <select v-model="form.is_fixed" class="input-field" aria-label="支出类型">
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
              <div v-else-if="!items.length" class="empty-state">还没有支出记录，先录入每月固定支出。</div>
              <article v-for="item in items" :key="item.id" class="expense-item">
                <div><strong>{{ item.category }}</strong><span>{{ item.is_fixed ? '固定' : '浮动' }}{{ item.note ? ` · ${item.note}` : '' }}</span></div>
                <b>¥{{ money(item.amount) }}</b>
                <button class="icon-btn" title="编辑" @click="edit(item)"><Icon name="pencil" size="15" /></button>
                <button class="icon-btn danger" title="删除" @click="deleteTarget = item"><Icon name="trash" size="15" /></button>
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
    message="删除后会重新计算本月结余，确定继续？"
    confirm-text="删除"
    danger
    @confirm="confirmDelete"
    @cancel="deleteTarget = null"
  />
</template>

<style scoped>
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
