<template>
  <div v-if="actions && actions.length > 0" class="action-section">
    <div class="action-section-header">
      <span class="action-section-icon">🎯</span>
      <span class="action-section-title">下一步行动</span>
      <span class="action-count">{{ actions.length }} 条建议</span>
    </div>
    <div class="action-list">
      <div v-for="(action, idx) in actions" :key="idx" class="action-card" :class="[action.priority, action.action_type]">
        <div class="action-card-header">
          <span class="action-type-badge">{{ actionTypeLabel(action.action_type) }}</span>
          <span class="action-priority-badge">{{ priorityLabel(action.priority) }}</span>
        </div>
        <div class="action-card-body">
          <p class="action-target">{{ action.target_name }}</p>
          <p class="action-reason">{{ action.reason }}</p>
          <p v-if="action.estimated_savings" class="action-savings">
            预计年节省：¥{{ action.estimated_savings.toLocaleString() }}
          </p>
          <p v-if="action.score" class="action-score">
            评分：{{ action.score }}/100
          </p>
        </div>
        <div class="action-card-buttons">
          <button v-if="action.target_code" class="btn-action btn-primary" @click="addWatchlist(action)">
            ⭐ 加入关注
          </button>
          <button v-if="action.action_type === 'buy' || action.action_type === 'sell' || action.action_type === 'reduce'"
                  class="btn-action btn-decision" @click="createDecision(action)">
            📋 创建决策
          </button>
          <button class="btn-action btn-dismiss" @click="dismissAction(idx)">
            忽略
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { defineProps, defineEmits } from 'vue'

const props = defineProps({
  actions: { type: Array, default: () => [] },
  source: { type: String, default: '' },
})

const emit = defineEmits(['watch', 'decision', 'dismiss'])

const actionTypeLabels = {
  watch: '👀 关注',
  buy: '📈 买入',
  sell: '📉 卖出',
  reduce: '⚠️ 减仓',
  replace: '🔄 替换',
  rebalance: '⚖️ 再平衡',
  review: '📊 复盘',
}

const priorityLabels = {
  high: '🔴 高',
  medium: '🟡 中',
  low: '🟢 低',
}

function actionTypeLabel(type) {
  return actionTypeLabels[type] || type
}

function priorityLabel(priority) {
  return priorityLabels[priority] || priority
}

function addWatchlist(action) {
  emit('watch', action)
}

function createDecision(action) {
  emit('decision', action)
}

function dismissAction(idx) {
  emit('dismiss', idx)
}
</script>

<style scoped>
.action-section {
  margin-top: 24px;
  padding: 16px;
  background: var(--surface-2, #f8f9fa);
  border-radius: 12px;
  border: 1px solid var(--border, #e5e7eb);
}

.action-section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border, #e5e7eb);
}

.action-section-icon {
  font-size: 18px;
}

.action-section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #1a1a2e);
}

.action-count {
  font-size: 12px;
  color: var(--text-secondary, #666);
  margin-left: auto;
}

.action-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.action-card {
  padding: 12px;
  background: var(--surface, #fff);
  border-radius: 8px;
  border: 1px solid var(--border, #e5e7eb);
  transition: border-color 0.2s;
}

.action-card:hover {
  border-color: var(--accent, #2563eb);
}

.action-card.high {
  border-left: 3px solid #ef4444;
}

.action-card.medium {
  border-left: 3px solid #f59e0b;
}

.action-card.low {
  border-left: 3px solid #10b981;
}

.action-card-header {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.action-type-badge, .action-priority-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  font-weight: 500;
}

.action-type-badge {
  background: var(--surface-2, #f0f0f0);
  color: var(--text-primary, #333);
}

.action-priority-badge {
  background: transparent;
  color: var(--text-secondary, #666);
}

.action-card-body {
  margin-bottom: 8px;
}

.action-target {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #1a1a2e);
  margin: 0 0 4px;
}

.action-reason {
  font-size: 13px;
  color: var(--text-secondary, #666);
  margin: 0 0 4px;
  line-height: 1.4;
}

.action-savings, .action-score {
  font-size: 12px;
  color: var(--accent, #2563eb);
  margin: 0;
  font-weight: 500;
}

.action-card-buttons {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.btn-action {
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 6px;
  border: 1px solid var(--border, #e5e7eb);
  cursor: pointer;
  transition: all 0.2s;
  background: var(--surface, #fff);
  color: var(--text-primary, #333);
}

.btn-action:hover {
  background: var(--surface-2, #f0f0f0);
}

.btn-action.btn-primary {
  background: var(--accent, #2563eb);
  color: #fff;
  border-color: var(--accent, #2563eb);
}

.btn-action.btn-primary:hover {
  background: #1d4ed8;
}

.btn-action.btn-decision {
  background: #f0fdf4;
  color: #166534;
  border-color: #bbf7d0;
}

.btn-action.btn-decision:hover {
  background: #dcfce7;
}

.btn-action.btn-dismiss {
  color: var(--text-secondary, #999);
  border-color: transparent;
  background: transparent;
}

.btn-action.btn-dismiss:hover {
  color: var(--text-primary, #333);
}
</style>
