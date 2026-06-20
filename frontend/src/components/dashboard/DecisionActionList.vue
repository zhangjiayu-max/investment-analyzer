<script setup>
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  decisions: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['status-change', 'complete-action'])

const visibleDecisions = computed(() => props.decisions || [])

function decisionLabel(type) {
  const map = {
    buy: '买入',
    sell: '卖出',
    add: '加仓',
    reduce: '减仓',
    hold: '持有',
    watch: '观察',
    rebalance: '再平衡',
    collect_data: '补数据',
  }
  return map[type] || type || '行动'
}

function statusLabel(status) {
  const map = {
    proposed: '待处理',
    accepted: '已接受',
    rejected: '已拒绝',
    deferred: '已暂缓',
    executed: '已执行',
    expired: '已过期',
    reviewed: '已复盘',
  }
  return map[status] || status || '待处理'
}

function evidenceLine(decision) {
  const evidence = decision.evidence_json || {}
  const points = Array.isArray(evidence.data_points) ? evidence.data_points : []
  if (!points.length) return ''
  const first = points[0]
  const main = [first.name, first.value].filter(Boolean).join(' ')
  const suffix = first.as_of ? ` · ${first.as_of}` : ''
  return `${main}${suffix}`
}

function targetText(decision) {
  return decision.target_name || decision.target_code || decision.target_type || '组合'
}

function firstText(value) {
  return Array.isArray(value) ? (value[0] || '') : (value || '')
}

function suitabilityLine(decision) {
  return firstText(decision.suitability_json?.notes)
}

function portfolioContextLine(decision) {
  const context = decision.evidence_json?.portfolio_context || {}
  if (context.opportunity_names) return context.opportunity_names
  if (context.cash_ratio) return `现金占比 ${context.cash_ratio}`
  if (context.concentration_level) return `集中度 ${context.concentration_level}`
  return ''
}

function missingDataLine(decision) {
  return firstText(decision.evidence_json?.missing_data)
}

function counterArgumentLine(decision) {
  return firstText(decision.evidence_json?.counter_arguments)
}

function hasInsight(decision) {
  return Boolean(
    suitabilityLine(decision)
      || portfolioContextLine(decision)
      || missingDataLine(decision)
      || counterArgumentLine(decision)
  )
}
</script>

<template>
  <section class="decision-panel">
    <header class="decision-panel__header">
      <div>
        <h3 class="decision-panel__title">
          <Icon name="clipboard-list" size="16" />
          今日行动
        </h3>
        <p class="decision-panel__subtitle">把 AI 建议收敛成可追踪的理财动作</p>
      </div>
      <span class="decision-panel__count">{{ visibleDecisions.length }} 项</span>
    </header>

    <div v-if="loading" class="decision-loading">
      <span class="decision-loading__bar"></span>
      <span>正在整理今日行动...</span>
    </div>

    <div v-else-if="!visibleDecisions.length" class="decision-empty">
      <Icon name="check" size="18" />
      <span>今日暂无需要处理的行动</span>
    </div>

    <div v-else class="decision-list">
      <article
        v-for="decision in visibleDecisions"
        :key="decision.id"
        class="decision-card"
        :class="`decision-card--${decision.status}`"
      >
        <div class="decision-card__main">
          <div class="decision-card__topline">
            <span class="decision-card__type">{{ decisionLabel(decision.decision_type) }}</span>
            <span class="decision-card__target">{{ targetText(decision) }}</span>
            <span class="decision-card__status">{{ statusLabel(decision.status) }}</span>
          </div>
          <h4 class="decision-card__summary">{{ decision.summary }}</h4>
          <p v-if="decision.rationale" class="decision-card__rationale">{{ decision.rationale }}</p>
          <div v-if="evidenceLine(decision)" class="decision-card__evidence">
            <Icon name="book-open" size="13" />
            <span>{{ evidenceLine(decision) }}</span>
          </div>
          <div v-if="hasInsight(decision)" class="decision-card__insights">
            <div v-if="portfolioContextLine(decision) || suitabilityLine(decision)" class="decision-card__insight">
              <span class="decision-card__insight-label">适配</span>
              <span>{{ [portfolioContextLine(decision), suitabilityLine(decision)].filter(Boolean).join(' · ') }}</span>
            </div>
            <div v-if="missingDataLine(decision)" class="decision-card__insight">
              <span class="decision-card__insight-label">待确认</span>
              <span>{{ missingDataLine(decision) }}</span>
            </div>
            <div v-if="counterArgumentLine(decision)" class="decision-card__insight decision-card__insight--risk">
              <span class="decision-card__insight-label">反方提醒</span>
              <span>{{ counterArgumentLine(decision) }}</span>
            </div>
          </div>
          <div v-if="decision.actions?.length" class="decision-card__actions">
            <button
              v-for="action in decision.actions"
              :key="action.id"
              type="button"
              class="decision-card__action"
              :disabled="action.status === 'done'"
              data-test="complete-action"
              @click="emit('complete-action', decision.id, action.id)"
            >
              <Icon :name="action.status === 'done' ? 'check' : 'circle'" size="12" />
              {{ action.title }}
            </button>
          </div>
        </div>

        <div class="decision-card__controls">
          <button
            type="button"
            data-test="accept-decision"
            class="decision-card__btn decision-card__btn--accept"
            @click="emit('status-change', decision.id, 'accepted')"
          >
            接受
          </button>
          <button
            type="button"
            data-test="defer-decision"
            class="decision-card__btn"
            @click="emit('status-change', decision.id, 'deferred')"
          >
            暂缓
          </button>
          <button
            type="button"
            data-test="reject-decision"
            class="decision-card__btn"
            @click="emit('status-change', decision.id, 'rejected')"
          >
            拒绝
          </button>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.decision-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  margin-bottom: 1rem;
  overflow: hidden;
}

.decision-panel__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  padding: 0.95rem 1rem;
  border-bottom: 1px solid var(--color-border-light);
  background: linear-gradient(180deg, var(--color-bg-hover), transparent);
}

.decision-panel__title {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  margin: 0;
  color: var(--color-text-primary);
  font-size: 0.98rem;
  font-weight: 800;
}

.decision-panel__subtitle {
  margin: 0.2rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.76rem;
}

.decision-panel__count {
  padding: 0.18rem 0.5rem;
  border-radius: var(--radius-sm);
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-size: 0.72rem;
  font-weight: 800;
}

.decision-loading,
.decision-empty {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 1rem;
  color: var(--color-text-secondary);
  font-size: 0.84rem;
}

.decision-loading__bar {
  width: 58px;
  height: 4px;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--color-primary), var(--color-warning), var(--color-primary));
  background-size: 200% 100%;
  animation: decision-loading 1.4s linear infinite;
}

.decision-list {
  display: grid;
  gap: 0.65rem;
  padding: 0.8rem;
}

.decision-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.9rem;
  padding: 0.8rem;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg);
}

.decision-card__main {
  min-width: 0;
}

.decision-card__topline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.35rem;
}

.decision-card__type,
.decision-card__status {
  padding: 0.12rem 0.42rem;
  border-radius: var(--radius-xs);
  font-size: 0.68rem;
  font-weight: 800;
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
}

.decision-card__type {
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.decision-card__target {
  color: var(--color-text-primary);
  font-size: 0.78rem;
  font-weight: 800;
}

.decision-card__summary {
  margin: 0;
  color: var(--color-text-primary);
  font-size: 0.9rem;
  font-weight: 800;
  line-height: 1.45;
}

.decision-card__rationale {
  margin: 0.28rem 0 0;
  color: var(--color-text-secondary);
  font-size: 0.78rem;
  line-height: 1.5;
}

.decision-card__evidence {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  margin-top: 0.45rem;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.decision-card__insights {
  display: grid;
  gap: 0.35rem;
  margin-top: 0.55rem;
}

.decision-card__insight {
  display: flex;
  align-items: flex-start;
  gap: 0.45rem;
  color: var(--color-text-secondary);
  font-size: 0.73rem;
  line-height: 1.45;
}

.decision-card__insight-label {
  flex: 0 0 auto;
  min-width: 3.3rem;
  color: var(--color-primary);
  font-size: 0.68rem;
  font-weight: 800;
}

.decision-card__insight--risk .decision-card__insight-label {
  color: var(--color-warning);
}

.decision-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.5rem;
}

.decision-card__action,
.decision-card__btn {
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.72rem;
  font-weight: 700;
}

.decision-card__action {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.25rem 0.48rem;
}

.decision-card__action:disabled {
  cursor: default;
  opacity: 0.65;
}

.decision-card__controls {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
}

.decision-card__btn {
  padding: 0.28rem 0.5rem;
}

.decision-card__btn:hover,
.decision-card__action:hover:not(:disabled) {
  border-color: var(--color-primary-border);
  color: var(--color-primary);
}

.decision-card__btn--accept {
  background: var(--color-success-bg);
  border-color: var(--color-success-border);
  color: var(--color-success);
}

@media (max-width: 720px) {
  .decision-card {
    grid-template-columns: 1fr;
  }

  .decision-card__controls {
    justify-content: flex-start;
  }
}

@keyframes decision-loading {
  from { background-position: 0% 50%; }
  to { background-position: 200% 50%; }
}
</style>
