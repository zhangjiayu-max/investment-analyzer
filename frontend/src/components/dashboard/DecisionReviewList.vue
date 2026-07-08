<script setup>
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  reviews: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['submit-review'])

const visibleReviews = computed(() => props.reviews || [])

function decisionLabel(type) {
  const map = {
    add: '配置',
    rebalance: '再平衡',
    watch: '观察',
    hold: '持有',
    reduce: '减仓',
    sell: '卖出',
  }
  return map[type] || type || '决策'
}

function targetText(item) {
  return item.target_name || item.target_code || item.target_type || '组合'
}

function evidenceLine(item) {
  const points = Array.isArray(item.evidence_json?.data_points) ? item.evidence_json.data_points : []
  if (!points.length) return ''
  const first = points[0]
  return [first.name, first.value].filter(Boolean).join(' ')
}
</script>

<template>
  <section v-if="loading || visibleReviews.length" class="review-panel editorial-card">
    <header class="review-panel__header editorial-card-header">
      <div>
        <h3 class="review-panel__title title editorial-title">
          <Icon name="history" size="16" />
          待复盘
        </h3>
        <p class="review-panel__subtitle">把执行结果反馈给系统，下一次建议会更贴近你</p>
      </div>
      <span class="review-panel__count font-jet">{{ visibleReviews.length }} 项</span>
    </header>

    <div v-if="loading" class="review-loading">
      <span class="review-loading__dot"></span>
      <span>正在检查到期复盘...</span>
    </div>

    <div v-else class="review-list">
      <article v-for="item in visibleReviews" :key="item.id" class="review-card reveal-stagger">
        <div class="review-card__main">
          <div class="review-card__topline">
            <span class="review-card__type terminal-label">{{ decisionLabel(item.decision_type) }}</span>
            <span class="review-card__target">{{ targetText(item) }}</span>
            <span v-if="item.review_at" class="review-card__date terminal-label font-jet">{{ item.review_at }}</span>
          </div>
          <h4 class="review-card__summary">{{ item.summary }}</h4>
          <p v-if="item.rationale" class="review-card__rationale">{{ item.rationale }}</p>
          <div v-if="evidenceLine(item)" class="review-card__evidence">
            <Icon name="book-open" size="13" />
            <span>{{ evidenceLine(item) }}</span>
          </div>
        </div>

        <div class="review-card__controls">
          <button
            type="button"
            class="review-card__btn review-card__btn--good"
            data-test="review-helpful"
            @click="emit('submit-review', item.id, 'helpful')"
          >
            有帮助
          </button>
          <button
            type="button"
            class="review-card__btn"
            data-test="review-neutral"
            @click="emit('submit-review', item.id, 'neutral')"
          >
            一般
          </button>
          <button
            type="button"
            class="review-card__btn"
            data-test="review-unhelpful"
            @click="emit('submit-review', item.id, 'unhelpful')"
          >
            无帮助
          </button>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.review-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  margin-bottom: 1rem;
  overflow: hidden;
}

.review-panel__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  padding: 0.9rem 1rem;
  border-bottom: 1px solid var(--color-border-light);
  background: var(--color-bg-hover);
}

.review-panel__title {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  margin: 0;
  color: var(--color-text-primary);
  font-size: 0.96rem;
}

.review-panel__subtitle {
  margin: 0.2rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.review-panel__count {
  padding: 0.18rem 0.5rem;
  border-radius: var(--radius-sm);
  background: var(--color-warning-bg);
  color: var(--color-warning);
  font-size: 0.72rem;
  font-weight: 800;
}

.review-loading {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 1rem;
  color: var(--color-text-secondary);
  font-size: 0.82rem;
}

.review-loading__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-warning);
  animation: review-pulse 1s ease-in-out infinite;
}

.review-list {
  display: grid;
  gap: 0.65rem;
  padding: 0.8rem;
}

.review-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.9rem;
  padding: 0.8rem;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg);
}

.review-card__main {
  min-width: 0;
}

.review-card__topline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.35rem;
}

.review-card__type {
  padding: 0.12rem 0.42rem;
  border-radius: var(--radius-xs);
  background: var(--color-warning-bg);
  color: var(--color-warning);
  font-size: 0.68rem;
  font-weight: 800;
}

.review-card__target,
.review-card__date {
  color: var(--color-text-secondary);
  font-size: 0.76rem;
  font-weight: 800;
}

.review-card__summary {
  margin: 0;
  color: var(--color-text-primary);
  font-size: 0.9rem;
  font-weight: 800;
  line-height: 1.45;
}

.review-card__rationale {
  margin: 0.28rem 0 0;
  color: var(--color-text-secondary);
  font-size: 0.78rem;
  line-height: 1.5;
}

.review-card__evidence {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  margin-top: 0.45rem;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.review-card__controls {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
}

.review-card__btn {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0.28rem 0.5rem;
}

.review-card__btn:hover {
  border-color: var(--color-primary-border);
  color: var(--color-primary);
}

.review-card__btn--good {
  background: var(--color-success-bg);
  border-color: var(--color-success-border);
  color: var(--color-success);
}

@media (max-width: 720px) {
  .review-card {
    grid-template-columns: 1fr;
  }

  .review-card__controls {
    justify-content: flex-start;
  }
}

@keyframes review-pulse {
  0%, 100% { opacity: 0.45; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1); }
}
</style>
