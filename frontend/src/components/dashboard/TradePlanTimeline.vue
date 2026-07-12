<script setup>
import { ref, onMounted, computed } from 'vue'
import { listTradePlans, deleteTradePlan } from '../../api'
import Icon from '../ui/Icon.vue'

const loading = ref(true)
const plans = ref([])

async function loadTradePlans() {
  loading.value = true
  try {
    const { data } = await listTradePlans('pending')
    plans.value = data || []
  } catch (e) {
    console.error('加载交易计划失败:', e)
    plans.value = []
  } finally {
    loading.value = false
  }
}

async function handleDeletePlan(planId) {
  if (!confirm('确定删除此交易计划？')) return
  try {
    await deleteTradePlan(planId)
    plans.value = plans.value.filter(p => p.id !== planId)
  } catch (e) {
    console.error('删除失败:', e)
  }
}

function actionLabel(action) {
  return { BUY: '买入', SELL: '卖出', HOLD: '持有' }[action?.toUpperCase()] || action
}

function actionClass(action) {
  return { BUY: 'action-buy', SELL: 'action-sell', HOLD: 'action-hold' }[action?.toUpperCase()] || 'action-hold'
}

function formatAmount(amount) {
  return amount ? amount.toFixed(2) : '0.00'
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  const date = new Date(dateStr.replace(' ', 'T'))
  return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
}

const groupedPlans = computed(() => {
  const groups = []
  plans.value.forEach(plan => {
    const action = plan.action?.toUpperCase() || 'BUY'
    let group = groups.find(g => g.action === action)
    if (!group) {
      group = { action, label: actionLabel(action), plans: [] }
      groups.push(group)
    }
    group.plans.push(plan)
  })
  return groups.sort((a, b) => {
    const order = { BUY: 0, SELL: 1, HOLD: 2 }
    return (order[a.action] || 99) - (order[b.action] || 99)
  })
})
</script>

<template>
  <div class="trade-plan-timeline card editorial-card">
    <div class="trade-plan-header editorial-card-header">
      <h3 class="trade-plan-title title">📋 待执行交易计划</h3>
      <button class="btn-secondary btn-sm" @click="loadTradePlans" :disabled="loading">
        <Icon name="refresh" size="14" />
        刷新
      </button>
    </div>

    <div v-if="loading" class="trade-plan-loading">
      <div class="trade-plan-skeleton">
        <div class="skeleton-item"></div>
        <div class="skeleton-item"></div>
        <div class="skeleton-item"></div>
      </div>
    </div>

    <div v-else-if="plans.length === 0" class="trade-plan-empty">
      <Icon name="calendar" size="32" />
      <p>暂无待执行的交易计划</p>
      <p class="trade-plan-empty-hint">在对话中点击「生成交易计划」创建</p>
    </div>

    <div v-else class="trade-plan-groups">
      <div v-for="group in groupedPlans" :key="group.action" class="trade-plan-group">
        <div class="trade-plan-group-header" :class="actionClass(group.action)">
          <span class="trade-plan-group-label">{{ group.label }}</span>
          <span class="trade-plan-group-count">{{ group.plans.length }} 笔</span>
        </div>
        <div class="trade-plan-list">
          <div v-for="plan in group.plans" :key="plan.id" class="trade-plan-item">
            <div class="trade-plan-item-header">
              <span class="trade-plan-fund-name">{{ plan.fund_name || plan.fund_code }}</span>
              <span v-if="plan.fund_code" class="trade-plan-fund-code font-jet">{{ plan.fund_code }}</span>
            </div>
            <div class="trade-plan-item-body">
              <div class="trade-plan-amount">
                <span class="trade-plan-amount-label">金额</span>
                <span class="trade-plan-amount-value">¥{{ formatAmount(plan.amount) }}</span>
              </div>
              <div v-if="plan.batch_count > 1" class="trade-plan-batch">
                <span class="trade-plan-batch-label">分批</span>
                <span class="trade-plan-batch-value">{{ plan.batch_count }} 次 / 每{{ plan.batch_interval_days }}天</span>
              </div>
              <div v-if="plan.stop_loss_pct" class="trade-plan-stop-loss">
                <span class="trade-plan-stop-loss-label">止损</span>
                <span class="trade-plan-stop-loss-value">-{{ (plan.stop_loss_pct * 100).toFixed(1) }}%</span>
              </div>
              <div v-if="plan.take_profit_pct" class="trade-plan-take-profit">
                <span class="trade-plan-take-profit-label">止盈</span>
                <span class="trade-plan-take-profit-value">+{{ (plan.take_profit_pct * 100).toFixed(1) }}%</span>
              </div>
            </div>
            <div class="trade-plan-item-footer">
              <span class="trade-plan-create-time meta font-jet">{{ formatDate(plan.created_at) }}</span>
              <button class="btn-trash" @click="handleDeletePlan(plan.id)" title="删除计划">
                <Icon name="trash" size="14" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trade-plan-timeline {
  margin-bottom: 1rem;
}

.trade-plan-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.trade-plan-title {
  font-size: 1rem;
  font-weight: 600;
}

.trade-plan-loading {
  padding: 1.5rem;
}

.trade-plan-skeleton {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.skeleton-item {
  height: 48px;
  background: linear-gradient(90deg, #f3f4f6 25%, #e5e7eb 50%, #f3f4f6 75%);
  background-size: 200% 100%;
  animation: skeleton-loading 1.5s ease-in-out infinite;
  border-radius: 6px;
}

@keyframes skeleton-loading {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.trade-plan-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 2rem;
  color: var(--color-text-muted);
  gap: 0.5rem;
}

.trade-plan-empty-hint {
  font-size: 0.875rem;
  color: var(--color-text-tertiary);
}

.trade-plan-groups {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.trade-plan-group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
  font-weight: 600;
}

.action-buy {
  background: rgba(34, 197, 94, 0.1);
  color: #16a34a;
}

.action-sell {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}

.action-hold {
  background: rgba(148, 163, 184, 0.1);
  color: #64748b;
}

.trade-plan-group-count {
  font-size: 0.875rem;
  opacity: 0.8;
}

.trade-plan-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.trade-plan-item {
  padding: 0.75rem;
  background: var(--color-card-bg, #f9fafb);
  border-radius: 6px;
  border: 1px solid var(--color-border, #e5e7eb);
}

.trade-plan-item-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.trade-plan-fund-name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.trade-plan-fund-code {
  font-size: 0.875rem;
  color: var(--color-text-secondary);
}

.trade-plan-item-body {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin-bottom: 0.5rem;
}

.trade-plan-amount,
.trade-plan-batch,
.trade-plan-stop-loss,
.trade-plan-take-profit {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.trade-plan-amount-label,
.trade-plan-batch-label,
.trade-plan-stop-loss-label,
.trade-plan-take-profit-label {
  font-size: 0.75rem;
  color: var(--color-text-tertiary);
}

.trade-plan-amount-value {
  font-weight: 600;
  font-size: 0.9rem;
}

.trade-plan-batch-value {
  font-size: 0.875rem;
  color: var(--color-text-secondary);
}

.trade-plan-stop-loss-value {
  font-size: 0.875rem;
  color: #dc2626;
}

.trade-plan-take-profit-value {
  font-size: 0.875rem;
  color: #16a34a;
}

.trade-plan-item-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.trade-plan-create-time {
  font-size: 0.75rem;
}

.btn-trash {
  padding: 2px 6px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  border-radius: 4px;
}

.btn-trash:hover {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}
</style>
