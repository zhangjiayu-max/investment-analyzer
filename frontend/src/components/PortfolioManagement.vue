<script setup>
import { ref, computed, onMounted } from 'vue'

const maxIndustryPct = computed(() => {
  if (!detailData.value?.industry_allocation?.length) return 100
  return Math.max(...detailData.value.industry_allocation.map(i => i.pct_nav), 1)
})
import {
  getPortfolioSummary, createPortfolio, updatePortfolio, deletePortfolio,
  listPortfolioTransactions, createPortfolioTransaction,
  confirmTransaction, settleTransaction,
  refreshAllPortfolioPrices, refreshPortfolioPrice,
  lookupFundInfo, getFundHoldings,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'

// State
const holdings = ref([])
const summary = ref({ holding_count: 0, total_cost: 0, total_value: 0, total_profit: 0, profit_rate: 0 })
const loading = ref(false)
const refreshing = ref(false)
const showForm = ref(false)
const editingId = ref(null)
const showTxForm = ref(false)
const txHoldingId = ref(null)
const txFundCode = ref('')
const transactions = ref([])
const showTxHistory = ref(false)

// Fund lookup
const lookupLoading = ref(false)
const lookupResult = ref(null)

// Fund detail panel
const showDetail = ref(false)
const detailLoading = ref(false)
const detailData = ref(null)
const detailFundName = ref('')

// Add purchase (追加买入) panel — amount-based
const showAddPurchase = ref(false)
const addPurchaseHolding = ref(null)
const addPurchaseForm = ref({
  amount: 0,
  transaction_date: new Date().toISOString().slice(0, 10),
  notes: '',
})
const addPurchaseEstShares = computed(() => {
  const price = addPurchaseHolding.value?.current_price
  if (!price || price <= 0) return 0
  return (addPurchaseForm.value.amount / price).toFixed(2)
})

// Sell (卖出) panel — shares-based
const showSell = ref(false)
const sellHolding = ref(null)
const sellForm = ref({
  shares: 0,
  transaction_date: new Date().toISOString().slice(0, 10),
  notes: '',
})
const sellEstAmount = computed(() => {
  const price = sellHolding.value?.current_price
  if (!price || price <= 0) return 0
  return (sellForm.value.shares * price).toFixed(2)
})

// Confirm transaction modal
const showConfirmTx = ref(false)
const confirmTxData = ref(null)
const confirmTxPrice = ref(0)

// Pending transactions reminder
const pendingTxs = ref([])

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
    // 加载所有待确认交易
    await loadPendingTxs()
  } catch (e) {
    showToast('加载失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function loadPendingTxs() {
  const allPending = []
  for (const h of holdings.value) {
    try {
      const { data } = await listPortfolioTransactions(h.id)
      const pending = (data.transactions || []).filter(tx => tx.status === 'pending')
      for (const tx of pending) {
        tx._fund_name = h.fund_name
        tx._fund_code = h.fund_code
      }
      allPending.push(...pending)
    } catch (e) { /* ignore */ }
  }
  pendingTxs.value = allPending
}

onMounted(loadData)

// Refresh prices
async function refreshAll() {
  refreshing.value = true
  try {
    const { data } = await refreshAllPortfolioPrices()
    showToast(`已刷新 ${data.total} 只基金净值`)
    loadData()
  } catch (e) {
    showToast('刷新失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    refreshing.value = false
  }
}

async function refreshSingle(h) {
  try {
    await refreshPortfolioPrice(h.id)
    showToast(`${h.fund_name} 净值已更新`)
    loadData()
  } catch (e) {
    showToast('刷新失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

function freshnessHint(dateStr) {
  if (!dateStr) return { text: '未更新', cls: 'fresh-stale' }
  const today = new Date().toISOString().slice(0, 10)
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10)
  if (dateStr >= today) return { text: '今日', cls: 'fresh-today' }
  if (dateStr >= yesterday) return { text: '昨日', cls: 'fresh-yesterday' }
  return { text: dateStr, cls: 'fresh-stale' }
}

// Fund lookup
async function doLookup() {
  const code = form.value.fund_code.trim()
  if (!code) { showToast('请输入基金代码', 'error'); return }
  lookupLoading.value = true
  lookupResult.value = null
  try {
    const { data } = await lookupFundInfo(code)
    lookupResult.value = data
    form.value.fund_name = data.fund_name || ''
    form.value.index_name = data.tracking_index || ''
    showToast('已自动填充基金信息')
  } catch (e) {
    showToast('未找到该基金信息，请手动填写', 'error')
  } finally {
    lookupLoading.value = false
  }
}

// Fund detail
async function openDetail(h) {
  detailFundName.value = h.fund_name
  showDetail.value = true
  detailLoading.value = true
  detailData.value = null
  try {
    const { data } = await getFundHoldings(h.fund_code)
    detailData.value = data
  } catch (e) {
    showToast('获取基金详情失败', 'error')
  } finally {
    detailLoading.value = false
  }
}

// Form operations
function openAddForm() {
  editingId.value = null
  lookupResult.value = null
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

// Add purchase (追加买入) — amount-based, pending status
function openAddPurchase(h) {
  addPurchaseHolding.value = h
  addPurchaseForm.value = {
    amount: 0,
    transaction_date: new Date().toISOString().slice(0, 10),
    notes: '',
  }
  showAddPurchase.value = true
}

async function submitAddPurchase() {
  const f = addPurchaseForm.value
  if (f.amount <= 0) {
    showToast('买入金额必须大于 0', 'error')
    return
  }
  const h = addPurchaseHolding.value
  try {
    await createPortfolioTransaction({
      fund_code: h.fund_code,
      holding_id: h.id,
      transaction_type: 'buy',
      amount: 0,
      transaction_date: f.transaction_date,
      notes: f.notes,
      status: 'pending',
      submitted_amount: f.amount,
    })
    showToast(`已提交买入 ¥${f.amount}，待 T+1 确认`)
    showAddPurchase.value = false
    loadData()
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// Sell (卖出) — shares-based, pending status
function openSell(h) {
  sellHolding.value = h
  sellForm.value = {
    shares: 0,
    transaction_date: new Date().toISOString().slice(0, 10),
    notes: '',
  }
  showSell.value = true
}

async function submitSell() {
  const f = sellForm.value
  if (f.shares <= 0) {
    showToast('卖出份额必须大于 0', 'error')
    return
  }
  const h = sellHolding.value
  if (f.shares > (h.shares || 0)) {
    showToast(`卖出份额不能超过持有份额 ${h.shares}`, 'error')
    return
  }
  try {
    await createPortfolioTransaction({
      fund_code: h.fund_code,
      holding_id: h.id,
      transaction_type: 'sell',
      amount: 0,
      shares: null,
      transaction_date: f.transaction_date,
      notes: f.notes,
      status: 'pending',
      submitted_shares: f.shares,
    })
    showToast(`已提交卖出 ${f.shares} 份，待 T+1 确认`)
    showSell.value = false
    loadData()
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// Confirm transaction (确认交易)
function openConfirmTx(tx) {
  confirmTxData.value = tx
  confirmTxPrice.value = 0
  showConfirmTx.value = true
}

async function submitConfirmTx() {
  if (confirmTxPrice.value <= 0) {
    showToast('确认净值必须大于 0', 'error')
    return
  }
  try {
    await confirmTransaction(confirmTxData.value.id, {
      confirmed_price: confirmTxPrice.value,
    })
    showToast('交易已确认')
    showConfirmTx.value = false
    loadData()
    // Refresh transaction list
    if (txHoldingId.value) {
      const { data } = await listPortfolioTransactions(txHoldingId.value)
      transactions.value = data.transactions || []
    }
  } catch (e) {
    showToast('确认失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// Settle transaction (标记到账)
async function handleSettle(tx) {
  try {
    await settleTransaction(tx.id)
    showToast('已标记到账')
    loadData()
    if (txHoldingId.value) {
      const { data } = await listPortfolioTransactions(txHoldingId.value)
      transactions.value = data.transactions || []
    }
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
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

function txStatusLabel(s) {
  return { pending: '待确认', confirmed: '已确认', settled: '已到账' }[s] || '已确认'
}

function txStatusBadge(s) {
  return { pending: 'badge-warning', confirmed: 'badge-success', settled: 'badge-info' }[s] || 'badge-success'
}

function txDisplayAmount(tx) {
  if (tx.status === 'pending') {
    if (tx.transaction_type === 'buy') return '¥' + (tx.submitted_amount || 0).toLocaleString()
    if (tx.transaction_type === 'sell') return (tx.submitted_shares || 0).toLocaleString() + ' 份'
  }
  return formatMoney(tx.amount)
}
</script>

<template>
  <div class="portfolio-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">持仓管理</h2>
        <p class="page-desc">管理基金持仓，AI 对话时可结合持仓数据给出加减仓建议</p>
      </div>
      <div class="header-actions">
        <button class="btn-secondary" @click="refreshAll" :disabled="refreshing">
          <svg :class="['icon-spin', { 'spinning': refreshing }]" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h5M20 20v-5h-5M4.93 9a8 8 0 0113.14 0M19.07 15a8 8 0 01-13.14 0"/>
          </svg>
          {{ refreshing ? '刷新中...' : '刷新净值' }}
        </button>
        <button class="btn-primary" @click="openAddForm">
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
          </svg>
          新增持仓
        </button>
      </div>
    </div>

    <!-- Pending Transactions Reminder -->
    <div v-if="pendingTxs.length > 0" class="pending-banner">
      <div class="pending-banner-header">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <strong>{{ pendingTxs.length }} 笔交易待确认</strong>
        <span class="pending-hint">（提交后 T+1 才能确认净值，请在确认后点击「确认」填入实际净值）</span>
      </div>
      <div class="pending-list">
        <div v-for="tx in pendingTxs" :key="tx.id" class="pending-item">
          <span :class="['badge', txTypeBadge(tx.transaction_type)]">{{ txTypeLabel(tx.transaction_type) }}</span>
          <span class="pending-fund">{{ tx._fund_name }}</span>
          <span class="pending-detail">
            {{ tx.transaction_type === 'buy' ? '¥' + (tx.submitted_amount || 0).toLocaleString() : (tx.submitted_shares || 0).toLocaleString() + ' 份' }}
          </span>
          <span class="pending-date">{{ tx.transaction_date }}</span>
          <button class="btn-ghost btn-sm btn-primary-text" @click="openConfirmTx(tx)">确认</button>
        </div>
      </div>
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
            <th>净值更新</th>
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
            <td>
              <span :class="['freshness-tag', freshnessHint(h.price_updated_at).cls]">
                {{ freshnessHint(h.price_updated_at).text }}
              </span>
            </td>
            <td class="actions-cell">
              <button class="btn-ghost btn-sm" @click="openDetail(h)" title="查看详情">详情</button>
              <button class="btn-ghost btn-sm" @click="refreshSingle(h)" title="刷新净值">刷新</button>
              <button class="btn-ghost btn-sm btn-primary-text" @click="openAddPurchase(h)" title="买入">买入</button>
              <button class="btn-ghost btn-sm btn-sell-text" @click="openSell(h)" title="卖出">卖出</button>
              <button class="btn-ghost btn-sm" @click="openTxForm(h)" title="记账（分红等）">记账</button>
              <button class="btn-ghost btn-sm" @click="viewTransactions(h)" title="交易记录">记录</button>
              <button class="btn-ghost btn-sm" @click="openEditForm(h)" title="编辑">编辑</button>
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
              <!-- 基金代码 + 自动查询 -->
              <div class="form-group">
                <label>基金代码 *</label>
                <div class="lookup-row">
                  <input v-model="form.fund_code" class="input-field" placeholder="输入基金代码，如 161725" required :disabled="!!editingId" />
                  <button v-if="!editingId" type="button" class="btn-secondary btn-sm" @click="doLookup" :disabled="lookupLoading">
                    {{ lookupLoading ? '查询中...' : '查询' }}
                  </button>
                </div>
              </div>
              <!-- 查询结果预览 -->
              <div v-if="lookupResult" class="lookup-preview">
                <span class="lookup-tag">{{ lookupResult.fund_type }}</span>
                <span>{{ lookupResult.fund_name }}</span>
                <span v-if="lookupResult.tracking_index" class="lookup-index">跟踪：{{ lookupResult.tracking_index }}</span>
                <span v-if="lookupResult.scale" class="lookup-scale">规模：{{ lookupResult.scale }}</span>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>基金名称 *</label>
                  <input v-model="form.fund_name" class="input-field" placeholder="自动填充或手动输入" required />
                </div>
                <div class="form-group">
                  <label>关联指数名称</label>
                  <input v-model="form.index_name" class="input-field" placeholder="自动填充或手动输入" />
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
                  <th>状态</th>
                  <th class="text-right">金额</th>
                  <th class="text-right">份额</th>
                  <th class="text-right">净值</th>
                  <th>备注</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="tx in transactions" :key="tx.id">
                  <td>{{ tx.transaction_date }}</td>
                  <td><span :class="['badge', txTypeBadge(tx.transaction_type)]">{{ txTypeLabel(tx.transaction_type) }}</span></td>
                  <td><span :class="['badge', txStatusBadge(tx.status || 'confirmed')]">{{ txStatusLabel(tx.status || 'confirmed') }}</span></td>
                  <td class="text-right">{{ txDisplayAmount(tx) }}</td>
                  <td class="text-right">{{ tx.status === 'pending' ? (tx.submitted_shares ? tx.submitted_shares.toLocaleString() + ' (预估)' : '--') : (tx.shares?.toLocaleString() || '--') }}</td>
                  <td class="text-right">{{ tx.price?.toFixed(4) || '--' }}</td>
                  <td>{{ tx.notes || '--' }}</td>
                  <td>
                    <button v-if="tx.status === 'pending'" class="btn-ghost btn-sm btn-primary-text" @click="openConfirmTx(tx)">确认</button>
                    <button v-if="(tx.status || 'confirmed') === 'confirmed' && tx.transaction_type === 'sell'" class="btn-ghost btn-sm btn-info-text" @click="handleSettle(tx)">已到账</button>
                    <span v-if="tx.status === 'settled'" class="text-muted">已完成</span>
                  </td>
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

    <!-- Add Purchase Modal (追加买入) — amount-based -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showAddPurchase" class="modal-overlay" @click.self="showAddPurchase = false">
          <div class="modal-box">
            <h3 class="modal-title">买入 — {{ addPurchaseHolding?.fund_name }}</h3>
            <p class="modal-subtitle">{{ addPurchaseHolding?.fund_code }} · 当前持有 {{ addPurchaseHolding?.shares?.toLocaleString() }} 份 · 净值 {{ addPurchaseHolding?.current_price || '--' }}</p>
            <form @submit.prevent="submitAddPurchase" class="modal-form">
              <div class="form-group">
                <label>买入金额（元）*</label>
                <input v-model.number="addPurchaseForm.amount" type="number" step="0.01" class="input-field" placeholder="如 10000" required />
              </div>
              <div v-if="addPurchaseForm.amount > 0 && addPurchaseHolding?.current_price" class="add-purchase-preview">
                <span>预估可买入约 <strong>{{ addPurchaseEstShares }}</strong> 份</span>
                <span class="preview-note">（按当前净值 {{ addPurchaseHolding.current_price }} 估算，实际以 T+1 确认为准）</span>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>买入日期</label>
                  <input v-model="addPurchaseForm.transaction_date" type="date" class="input-field" />
                </div>
                <div class="form-group">
                  <label>备注</label>
                  <input v-model="addPurchaseForm.notes" class="input-field" placeholder="可选" />
                </div>
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showAddPurchase = false">取消</button>
                <button type="submit" class="btn-primary">提交买入</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Sell Modal (卖出) — shares-based -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showSell" class="modal-overlay" @click.self="showSell = false">
          <div class="modal-box">
            <h3 class="modal-title">卖出 — {{ sellHolding?.fund_name }}</h3>
            <p class="modal-subtitle">{{ sellHolding?.fund_code }} · 当前持有 {{ sellHolding?.shares?.toLocaleString() }} 份 · 净值 {{ sellHolding?.current_price || '--' }}</p>
            <form @submit.prevent="submitSell" class="modal-form">
              <div class="form-group">
                <label>卖出份额 *</label>
                <input v-model.number="sellForm.shares" type="number" step="0.01" class="input-field" placeholder="如 5000" required />
              </div>
              <div v-if="sellForm.shares > 0 && sellHolding?.current_price" class="add-purchase-preview sell-preview">
                <span>预估可赎回约 <strong>¥{{ Number(sellEstAmount).toLocaleString() }}</strong></span>
                <span class="preview-note">（按当前净值 {{ sellHolding.current_price }} 估算，实际以 T+1 确认为准）</span>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>卖出日期</label>
                  <input v-model="sellForm.transaction_date" type="date" class="input-field" />
                </div>
                <div class="form-group">
                  <label>备注</label>
                  <input v-model="sellForm.notes" class="input-field" placeholder="可选" />
                </div>
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showSell = false">取消</button>
                <button type="submit" class="btn-primary">提交卖出</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Confirm Transaction Modal (确认交易) -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showConfirmTx" class="modal-overlay" @click.self="showConfirmTx = false">
          <div class="modal-box">
            <h3 class="modal-title">确认交易</h3>
            <p class="modal-subtitle">
              {{ txTypeLabel(confirmTxData?.transaction_type) }}
              · {{ confirmTxData?.transaction_type === 'buy' ? '金额' : '份额' }}
              {{ confirmTxData?.transaction_type === 'buy' ? '¥' + (confirmTxData?.submitted_amount || 0).toLocaleString() : (confirmTxData?.submitted_shares || 0).toLocaleString() + ' 份' }}
            </p>
            <form @submit.prevent="submitConfirmTx" class="modal-form">
              <div class="form-group">
                <label>T+1 确认净值 *</label>
                <input v-model.number="confirmTxPrice" type="number" step="0.0001" class="input-field" placeholder="输入确认日的实际净值" required />
              </div>
              <div v-if="confirmTxPrice > 0" class="add-purchase-preview">
                <span v-if="confirmTxData?.transaction_type === 'buy'">
                  实际买入约 <strong>{{ ((confirmTxData?.submitted_amount || 0) / confirmTxPrice).toFixed(2) }}</strong> 份
                </span>
                <span v-else>
                  实际赎回约 <strong>¥{{ ((confirmTxData?.submitted_shares || 0) * confirmTxPrice).toFixed(2) }}</strong>
                </span>
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showConfirmTx = false">取消</button>
                <button type="submit" class="btn-primary">确认</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Fund Detail Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showDetail" class="modal-overlay" @click.self="showDetail = false">
          <div class="modal-box modal-wide">
            <h3 class="modal-title">{{ detailFundName }} — 持仓分析</h3>
            <div v-if="detailLoading" class="loading-state">
              <div class="spinner"></div>
              <span>正在查询基金持仓数据...</span>
            </div>
            <div v-else-if="detailData" class="fund-detail">
              <!-- Asset Allocation -->
              <div v-if="detailData.asset_allocation?.length" class="detail-section">
                <h4 class="detail-heading">资产配置</h4>
                <div class="alloc-bars">
                  <div v-for="a in detailData.asset_allocation" :key="a.type" class="alloc-item">
                    <span class="alloc-label">{{ a.type }}</span>
                    <div class="alloc-bar-bg">
                      <div class="alloc-bar" :style="{ width: a.pct + '%' }"></div>
                    </div>
                    <span class="alloc-pct">{{ a.pct }}%</span>
                  </div>
                </div>
              </div>
              <!-- Top Stocks -->
              <div v-if="detailData.top_stocks?.length" class="detail-section">
                <h4 class="detail-heading">重仓股票 Top {{ detailData.top_stocks.length }}</h4>
                <table class="data-table mini-table">
                  <thead>
                    <tr>
                      <th>股票名称</th>
                      <th>代码</th>
                      <th class="text-right">占净值比</th>
                      <th class="text-right">持仓市值(万)</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="s in detailData.top_stocks" :key="s.stock_code">
                      <td class="fund-name">{{ s.stock_name }}</td>
                      <td><code>{{ s.stock_code }}</code></td>
                      <td class="text-right">{{ s.pct_nav }}%</td>
                      <td class="text-right">{{ (s.market_value / 10000).toFixed(0) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <!-- Bond Holdings -->
              <div v-if="detailData.bond_holdings?.length" class="detail-section">
                <h4 class="detail-heading">债券持仓</h4>
                <div v-if="detailData.bond_type_summary && Object.keys(detailData.bond_type_summary).length" class="bond-summary">
                  <span v-for="(v, k) in detailData.bond_type_summary" :key="k" :class="['bond-tag', `bond-${k}`]">{{ k }} {{ v }}%</span>
                </div>
                <table class="data-table mini-table">
                  <thead>
                    <tr>
                      <th>债券名称</th>
                      <th>类型</th>
                      <th class="text-right">占净值比</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="b in detailData.bond_holdings" :key="b.bond_code">
                      <td>{{ b.bond_name }}</td>
                      <td><span :class="['bond-tag', `bond-${b.bond_type}`]">{{ b.bond_type }}</span></td>
                      <td class="text-right">{{ b.pct_nav }}%</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <!-- Industry -->
              <div v-if="detailData.industry_allocation?.length" class="detail-section">
                <h4 class="detail-heading">行业配置</h4>
                <div class="industry-bars">
                  <div v-for="i in detailData.industry_allocation" :key="i.industry" class="alloc-item">
                    <span class="alloc-label">{{ i.industry }}</span>
                    <div class="alloc-bar-bg">
                      <div class="alloc-bar" :style="{ width: (i.pct_nav / maxIndustryPct * 100) + '%' }"></div>
                    </div>
                    <span class="alloc-pct">{{ i.pct_nav }}%</span>
                  </div>
                </div>
              </div>
            </div>
            <div class="modal-actions" style="margin-top: 1rem">
              <button class="btn-secondary" @click="showDetail = false">关闭</button>
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

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.header-actions .btn-secondary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 1.2rem;
  white-space: nowrap;
}

.header-actions .btn-primary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 1.2rem;
  white-space: nowrap;
}

.icon-spin {
  transition: transform 0.3s;
}
.icon-spin.spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Freshness tags */
.freshness-tag {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 9999px;
  font-size: 0.7rem;
  font-weight: 600;
  white-space: nowrap;
}
.fresh-today {
  background: #dcfce7;
  color: #16a34a;
}
.fresh-yesterday {
  background: #fef9c3;
  color: #a16207;
}
.fresh-stale {
  background: #fee2e2;
  color: #dc2626;
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

/* Pending transactions banner */
.pending-banner {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: var(--radius-lg);
  padding: 1rem 1.25rem;
  margin-bottom: 1.5rem;
}

.pending-banner-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #92400e;
  font-size: 0.9rem;
  margin-bottom: 0.75rem;
}

.pending-hint {
  font-size: 0.75rem;
  color: #a16207;
  font-weight: 400;
}

.pending-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.pending-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
  background: rgba(255, 255, 255, 0.6);
  border-radius: var(--radius-md);
  font-size: 0.82rem;
}

.pending-fund {
  font-weight: 600;
  color: var(--color-text-primary);
}

.pending-detail {
  color: var(--color-text-secondary);
}

.pending-date {
  color: var(--color-text-muted);
  font-size: 0.75rem;
  margin-left: auto;
}

.dark .pending-banner {
  background: #422006;
  border-color: #92400e;
}

.dark .pending-banner-header {
  color: #fbbf24;
}

.dark .pending-hint {
  color: #d97706;
}

.dark .pending-item {
  background: rgba(0, 0, 0, 0.2);
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

.btn-primary-text {
  color: var(--color-primary-600) !important;
}

.btn-primary-text:hover {
  background: var(--color-primary-bg) !important;
}

.btn-sell-text {
  color: #d97706 !important;
}

.btn-sell-text:hover {
  background: var(--color-warning-bg) !important;
}

.btn-info-text {
  color: var(--color-primary-600) !important;
}

.btn-info-text:hover {
  background: var(--color-primary-bg) !important;
}

.text-muted {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}

.preview-note {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  display: block;
  margin-top: 0.25rem;
}

.sell-preview {
  background: var(--color-warning-bg, rgba(245, 158, 11, 0.08));
  color: #d97706;
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
  margin: 0 0 0.5rem 0;
}

.modal-subtitle {
  font-size: 0.82rem;
  color: var(--color-text-muted);
  margin: 0 0 1rem 0;
}

.add-purchase-preview {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 0.8rem;
  background: var(--color-primary-bg, rgba(99, 102, 241, 0.08));
  border-radius: var(--radius-md);
  font-size: 0.9rem;
  color: var(--color-primary-600);
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

/* Lookup row */
.lookup-row {
  display: flex;
  gap: 0.5rem;
}
.lookup-row .input-field {
  flex: 1;
}
.lookup-row .btn-sm {
  padding: 0.5rem 1rem;
  white-space: nowrap;
}

.lookup-preview {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 0.8rem;
  background: var(--color-bg-hover, #f3f4f6);
  border-radius: var(--radius-md);
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}
.lookup-tag {
  background: var(--color-primary, #3b82f6);
  color: white;
  padding: 0.1rem 0.5rem;
  border-radius: 9999px;
  font-size: 0.7rem;
  font-weight: 600;
}
.lookup-index {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}
.lookup-scale {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}

/* Fund detail modal */
.fund-detail {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}
.detail-section {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1rem;
}
.detail-heading {
  font-size: 0.9rem;
  font-weight: 700;
  margin: 0 0 0.75rem 0;
  color: var(--color-text-primary);
}

/* Allocation bars */
.alloc-bars, .industry-bars {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.alloc-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.alloc-label {
  width: 100px;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  text-align: right;
  flex-shrink: 0;
}
.alloc-bar-bg {
  flex: 1;
  height: 14px;
  background: var(--color-border-light, #e5e7eb);
  border-radius: 7px;
  overflow: hidden;
}
.alloc-bar {
  height: 100%;
  background: var(--color-primary, #3b82f6);
  border-radius: 7px;
  transition: width 0.4s ease;
  min-width: 2px;
}
.alloc-pct {
  width: 50px;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  text-align: right;
  flex-shrink: 0;
}

/* Bond tags */
.bond-summary {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.bond-tag {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 9999px;
  font-size: 0.7rem;
  font-weight: 600;
}
.bond-利率债 {
  background: #dbeafe;
  color: #1d4ed8;
}
.bond-信用债 {
  background: #fef3c7;
  color: #92400e;
}
.bond-可转债 {
  background: #ede9fe;
  color: #6d28d9;
}

.mini-table {
  font-size: 0.8rem;
}
.mini-table th {
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
}
.mini-table td {
  padding: 0.45rem 0.75rem;
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
