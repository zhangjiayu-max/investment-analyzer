<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  getPortfolioSummary, createPortfolio, updatePortfolio, deletePortfolio,
  listPortfolioTransactions, createPortfolioTransaction,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'

// State
const holdings = ref([])
const summary = ref({ holding_count: 0, total_cost: 0, total_value: 0, total_profit: 0, profit_rate: 0 })
const loading = ref(false)
const showForm = ref(false)
const editingId = ref(null)
const showTxForm = ref(false)
const txHoldingId = ref(null)
const txFundCode = ref('')
const transactions = ref([])
const showTxHistory = ref(false)

// Toast
const toast = ref({ show: false, message: '', type: 'success' })
function showToast(message, type = 'success') {
  toast.value = { show: true, message, type }
  setTimeout(() => { toast.value.show = false }, 3000)
}

// Confirm dialog
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

// Form
const form = ref({
  fund_code: '',
  fund_name: '',
  shares: 0,
  cost_price: 0,
  index_code: '',
  index_name: '',
  buy_date: '',
  notes: '',
})

// Transaction form
const txForm = ref({
  transaction_type: 'buy',
  amount: 0,
  shares: 0,
  price: 0,
  transaction_date: new Date().toISOString().slice(0, 10),
  notes: '',
})

// Load data
async function loadData() {
  loading.value = true
  try {
    const { data } = await getPortfolioSummary()
    summary.value = data
    holdings.value = data.holdings || []
  } catch (e) {
    showToast('加载失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

// Form operations
function openAddForm() {
  editingId.value = null
  form.value = {
    fund_code: '', fund_name: '', shares: 0, cost_price: 0,
    index_code: '', index_name: '', buy_date: '', notes: '',
  }
  showForm.value = true
}

function openEditForm(h) {
  editingId.value = h.id
  form.value = {
    fund_code: h.fund_code || '',
    fund_name: h.fund_name || '',
    shares: h.shares || 0,
    cost_price: h.cost_price || 0,
    index_code: h.index_code || '',
    index_name: h.index_name || '',
    buy_date: h.buy_date || '',
    notes: h.notes || '',
  }
  showForm.value = true
}

async function submitForm() {
  if (!form.value.fund_code.trim() || !form.value.fund_name.trim()) {
    showToast('基金代码和名称不能为空', 'error')
    return
  }
  try {
    if (editingId.value) {
      await updatePortfolio(editingId.value, form.value)
      showToast('更新成功')
    } else {
      await createPortfolio(form.value)
      showToast('新增成功')
    }
    showForm.value = false
    loadData()
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

function handleDelete(h) {
  confirm.value = {
    visible: true,
    title: '删除持仓',
    message: `确定要删除「${h.fund_name}」吗？关联的交易记录也会一并删除。`,
    danger: true,
    onConfirm: async () => {
      try {
        await deletePortfolio(h.id)
        showToast('已删除')
        loadData()
      } catch (e) {
        showToast('删除失败', 'error')
      }
      confirm.value.visible = false
    },
  }
}

// Transaction operations
function openTxForm(h) {
  txHoldingId.value = h.id
  txFundCode.value = h.fund_code
  txForm.value = {
    transaction_type: 'buy',
    amount: 0,
    shares: 0,
    price: 0,
    transaction_date: new Date().toISOString().slice(0, 10),
    notes: '',
  }
  showTxForm.value = true
}

async function submitTx() {
  if (txForm.value.amount <= 0) {
    showToast('交易金额必须大于 0', 'error')
    return
  }
  try {
    await createPortfolioTransaction({
      fund_code: txFundCode.value,
      holding_id: txHoldingId.value,
      ...txForm.value,
    })
    showToast('交易记录已添加')
    showTxForm.value = false
    loadData()
  } catch (e) {
    showToast('添加失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

async function viewTransactions(h) {
  txHoldingId.value = h.id
  try {
    const { data } = await listPortfolioTransactions(h.id)
    transactions.value = data.transactions || []
    showTxHistory.value = true
  } catch (e) {
    showToast('加载交易记录失败', 'error')
  }
}

// Helpers
function formatMoney(v) {
  if (v == null) return '--'
  return '¥' + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatRate(v) {
  if (v == null) return '--'
  return (v * 100).toFixed(2) + '%'
}

function profitClass(v) {
  if (v > 0) return 'profit-positive'
  if (v < 0) return 'profit-negative'
  return ''
}

function txTypeLabel(t) {
  return { buy: '买入', sell: '卖出', dividend: '分红' }[t] || t
}

function txTypeBadge(t) {
  return { buy: 'badge-success', sell: 'badge-danger', dividend: 'badge-info' }[t] || 'badge-neutral'
}
</script>

<template>
  <div class="portfolio-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">持仓管理</h2>
        <p class="page-desc">管理基金持仓，AI 对话时可结合持仓数据给出加减仓建议</p>
      </div>
      <button class="btn-primary" @click="openAddForm">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
        </svg>
        新增持仓
      </button>
    </div>

    <!-- Summary Cards -->
    <div class="summary-cards">
      <div class="summary-card">
        <div class="summary-label">持仓数量</div>
        <div class="summary-value">{{ summary.holding_count }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">总成本</div>
        <div class="summary-value">{{ formatMoney(summary.total_cost) }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">总市值</div>
        <div class="summary-value">{{ formatMoney(summary.total_value) }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">总盈亏</div>
        <div :class="['summary-value', profitClass(summary.total_profit)]">
          {{ formatMoney(summary.total_profit) }}
        </div>
      </div>
      <div class="summary-card">
        <div class="summary-label">收益率</div>
        <div :class="['summary-value', profitClass(summary.profit_rate)]">
          {{ formatRate(summary.profit_rate) }}
        </div>
      </div>
    </div>

    <!-- Holdings Table -->
    <div class="card holdings-card">
      <div v-if="loading" class="loading-state">
        <div class="spinner"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="holdings.length === 0" class="empty-state">
        <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24" opacity="0.3">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"/>
        </svg>
        <p>暂无持仓数据</p>
        <button class="btn-secondary" @click="openAddForm">添加第一笔持仓</button>
      </div>

      <table v-else class="data-table">
        <thead>
          <tr>
            <th>基金名称</th>
            <th>基金代码</th>
            <th>关联指数</th>
            <th class="text-right">持有份额</th>
            <th class="text-right">成本价</th>
            <th class="text-right">总成本</th>
            <th class="text-right">当前净值</th>
            <th class="text-right">当前市值</th>
            <th class="text-right">盈亏</th>
            <th class="text-right">收益率</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in holdings" :key="h.id">
            <td class="fund-name">{{ h.fund_name }}</td>
            <td><code>{{ h.fund_code }}</code></td>
            <td>{{ h.index_name || '--' }}</td>
            <td class="text-right">{{ h.shares?.toLocaleString() }}</td>
            <td class="text-right">{{ h.cost_price?.toFixed(4) }}</td>
            <td class="text-right">{{ formatMoney(h.total_cost) }}</td>
            <td class="text-right">{{ h.current_price?.toFixed(4) || '--' }}</td>
            <td class="text-right">{{ formatMoney(h.current_value) }}</td>
            <td :class="['text-right', profitClass(h.profit_loss)]">{{ formatMoney(h.profit_loss) }}</td>
            <td :class="['text-right', profitClass(h.profit_rate)]">{{ formatRate(h.profit_rate) }}</td>
            <td class="actions-cell">
              <button class="btn-ghost btn-sm" @click="openEditForm(h)" title="编辑">编辑</button>
              <button class="btn-ghost btn-sm" @click="openTxForm(h)" title="记账">记账</button>
              <button class="btn-ghost btn-sm" @click="viewTransactions(h)" title="交易记录">记录</button>
              <button class="btn-ghost btn-sm btn-danger-text" @click="handleDelete(h)" title="删除">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Add/Edit Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showForm" class="modal-overlay" @click.self="showForm = false">
          <div class="modal-box">
            <h3 class="modal-title">{{ editingId ? '编辑持仓' : '新增持仓' }}</h3>
            <form @submit.prevent="submitForm" class="modal-form">
              <div class="form-row">
                <div class="form-group">
                  <label>基金代码 *</label>
                  <input v-model="form.fund_code" class="input-field" placeholder="如 161725" required />
                </div>
                <div class="form-group">
                  <label>基金名称 *</label>
                  <input v-model="form.fund_name" class="input-field" placeholder="如 招商中证白酒指数" required />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>关联指数代码</label>
                  <input v-model="form.index_code" class="input-field" placeholder="如 399997" />
                </div>
                <div class="form-group">
                  <label>关联指数名称</label>
                  <input v-model="form.index_name" class="input-field" placeholder="如 中证白酒" />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>持有份额</label>
                  <input v-model.number="form.shares" type="number" step="0.01" class="input-field" />
                </div>
                <div class="form-group">
                  <label>成本价（每份）</label>
                  <input v-model.number="form.cost_price" type="number" step="0.0001" class="input-field" />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>买入日期</label>
                  <input v-model="form.buy_date" type="date" class="input-field" />
                </div>
                <div class="form-group">
                  <label>备注</label>
                  <input v-model="form.notes" class="input-field" placeholder="可选" />
                </div>
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showForm = false">取消</button>
                <button type="submit" class="btn-primary">{{ editingId ? '保存' : '新增' }}</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Transaction Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showTxForm" class="modal-overlay" @click.self="showTxForm = false">
          <div class="modal-box">
            <h3 class="modal-title">记账 - {{ txFundCode }}</h3>
            <form @submit.prevent="submitTx" class="modal-form">
              <div class="form-row">
                <div class="form-group">
                  <label>交易类型</label>
                  <select v-model="txForm.transaction_type" class="input-field">
                    <option value="buy">买入</option>
                    <option value="sell">卖出</option>
                    <option value="dividend">分红</option>
                  </select>
                </div>
                <div class="form-group">
                  <label>交易日期</label>
                  <input v-model="txForm.transaction_date" type="date" class="input-field" required />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>交易金额</label>
                  <input v-model.number="txForm.amount" type="number" step="0.01" class="input-field" required />
                </div>
                <div class="form-group">
                  <label>交易份额</label>
                  <input v-model.number="txForm.shares" type="number" step="0.01" class="input-field" />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>交易价格</label>
                  <input v-model.number="txForm.price" type="number" step="0.0001" class="input-field" />
                </div>
                <div class="form-group">
                  <label>备注</label>
                  <input v-model="txForm.notes" class="input-field" placeholder="可选" />
                </div>
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showTxForm = false">取消</button>
                <button type="submit" class="btn-primary">确认</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Transaction History Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showTxHistory" class="modal-overlay" @click.self="showTxHistory = false">
          <div class="modal-box modal-wide">
            <h3 class="modal-title">交易记录</h3>
            <div v-if="transactions.length === 0" class="empty-state" style="padding: 2rem">
              <p>暂无交易记录</p>
            </div>
            <table v-else class="data-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>类型</th>
                  <th class="text-right">金额</th>
                  <th class="text-right">份额</th>
                  <th class="text-right">价格</th>
                  <th>备注</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="tx in transactions" :key="tx.id">
                  <td>{{ tx.transaction_date }}</td>
                  <td><span :class="['badge', txTypeBadge(tx.transaction_type)]">{{ txTypeLabel(tx.transaction_type) }}</span></td>
                  <td class="text-right">{{ formatMoney(tx.amount) }}</td>
                  <td class="text-right">{{ tx.shares?.toLocaleString() || '--' }}</td>
                  <td class="text-right">{{ tx.price?.toFixed(4) || '--' }}</td>
                  <td>{{ tx.notes || '--' }}</td>
                </tr>
              </tbody>
            </table>
            <div class="modal-actions" style="margin-top: 1rem">
              <button class="btn-secondary" @click="showTxHistory = false">关闭</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Confirm Dialog -->
    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :danger="confirm.danger"
      @confirm="confirm.onConfirm"
      @cancel="confirm.visible = false"
    />

    <!-- Toast -->
    <Transition name="fade">
      <div v-if="toast.show" :class="['toast', `toast-${toast.type}`]">
        {{ toast.message }}
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.portfolio-page {
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  gap: 1rem;
}

.page-header .btn-primary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 1.2rem;
  white-space: nowrap;
}

/* Summary Cards */
.summary-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.summary-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1rem 1.25rem;
  transition: all var(--transition-fast);
}

.summary-card:hover {
  box-shadow: var(--shadow-md);
}

.summary-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-bottom: 0.375rem;
}

.summary-value {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

/* Profit colors */
.profit-positive {
  color: #dc2626 !important;
}

.profit-negative {
  color: #16a34a !important;
}

/* Table */
.holdings-card {
  overflow-x: auto;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.data-table th {
  text-align: left;
  padding: 0.75rem 1rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
  font-size: 0.8rem;
}

.data-table td {
  padding: 0.65rem 1rem;
  border-bottom: 1px solid var(--color-border-light, var(--color-border));
  color: var(--color-text-primary);
  white-space: nowrap;
}

.data-table tbody tr:hover {
  background: var(--color-bg-hover);
}

.text-right {
  text-align: right;
}

.fund-name {
  font-weight: 600;
}

.actions-cell {
  display: flex;
  gap: 0.25rem;
}

.btn-sm {
  padding: 0.3rem 0.6rem;
  font-size: 0.75rem;
}

.btn-danger-text {
  color: var(--color-danger) !important;
}

.btn-danger-text:hover {
  background: var(--color-danger-bg) !important;
}

/* Empty & Loading */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 100);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
}

.modal-box {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 520px;
  max-height: 90vh;
  overflow-y: auto;
  margin: 0 1rem;
  padding: 1.5rem;
}

.modal-wide {
  max-width: 700px;
}

.modal-title {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0 0 1.25rem 0;
}

.modal-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.form-group label {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.modal-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
  margin-top: 0.5rem;
}

.modal-actions button {
  padding: 0.6rem 1.25rem;
}

select.input-field {
  cursor: pointer;
}

/* Toast */
.toast {
  position: fixed;
  bottom: 2rem;
  right: 2rem;
  padding: 0.75rem 1.25rem;
  border-radius: var(--radius-md);
  font-size: 0.85rem;
  font-weight: 500;
  z-index: 9999;
  box-shadow: var(--shadow-lg);
}

.toast-success {
  background: #16a34a;
  color: white;
}

.toast-error {
  background: #dc2626;
  color: white;
}

/* Responsive */
@media (max-width: 768px) {
  .summary-cards {
    grid-template-columns: repeat(2, 1fr);
  }

  .summary-cards .summary-card:last-child {
    grid-column: span 2;
  }

  .form-row {
    grid-template-columns: 1fr;
  }

  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
