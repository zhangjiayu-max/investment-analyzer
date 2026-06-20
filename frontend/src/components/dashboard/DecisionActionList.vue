<script setup>
import { ref } from 'vue'
import { computed } from 'vue'
import { triggerPeerReview, getPeerReviews } from '../../api'
import { useToast } from '../../composables/useToast'
import Icon from '../ui/Icon.vue'

const { showToast } = useToast()

const props = defineProps({
  decisions: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  precheckStates: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['status-change', 'complete-action', 'precheck'])

const visibleDecisions = computed(() => props.decisions || [])

// ── 评审状态 ──
const peerReviews = ref({})  // {decisionId: [reviews]}
const reviewLoading = ref({})  // {decisionId: bool}

async function loadPeerReviews(decisionId) {
  try {
    const { data } = await getPeerReviews(decisionId)
    peerReviews.value[decisionId] = data.items || []
  } catch (e) {
    console.error('加载评审失败:', e)
  }
}

async function startPeerReview(decisionId) {
  reviewLoading.value[decisionId] = true
  try {
    const { data } = await triggerPeerReview(decisionId)
    peerReviews.value[decisionId] = data.reviews || []
    if (data.auto_deferred) {
      showToast('多个评审给出高风险结论，决策已自动降级为暂缓', 'warning')
    } else {
      showToast(`评审完成，${data.reviews?.length || 0} 个维度已评审`, 'success')
    }
  } catch (e) {
    showToast('评审失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    reviewLoading.value[decisionId] = false
  }
}

function verdictLabel(verdict) {
  return {
    approve: '通过',
    approve_with_concerns: '有条件通过',
    reject: '拒绝',
    defer: '建议暂缓',
  }[verdict] || verdict
}

function verdictClass(verdict) {
  return {
    approve: 'verdict-ok',
    approve_with_concerns: 'verdict-warn',
    reject: 'verdict-danger',
    defer: 'verdict-danger',
  }[verdict] || ''
}

function reviewerLabel(type) {
  return {
    suitability: '适当性',
    evidence: '证据质量',
    counter: '反方观点',
    overconfidence: '过度自信',
  }[type] || type
}

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

function precheckState(decisionId) {
  return props.precheckStates?.[decisionId] || {}
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
          <div v-if="precheckState(decision.id).expanded" class="decision-card__precheck">
            <div v-if="precheckState(decision.id).loading" class="precheck-loading">正在检查执行条件...</div>
            <template v-else-if="precheckState(decision.id).result">
              <div
                class="precheck-status"
                :class="precheckState(decision.id).result.ok_to_execute ? 'precheck-status--ok' : 'precheck-status--blocked'"
              >
                {{ precheckState(decision.id).result.ok_to_execute ? '未发现硬性阻断' : '暂不建议直接执行' }}
              </div>
              <div v-if="precheckState(decision.id).result.blockers?.length" class="precheck-group">
                <strong>阻断项</strong>
                <span v-for="item in precheckState(decision.id).result.blockers" :key="item">{{ item }}</span>
              </div>
              <div v-if="precheckState(decision.id).result.warnings?.length" class="precheck-group">
                <strong>提醒</strong>
                <span v-for="item in precheckState(decision.id).result.warnings" :key="item">{{ item }}</span>
              </div>
              <div v-if="precheckState(decision.id).result.checklist?.length" class="precheck-group">
                <strong>检查清单</strong>
                <span v-for="item in precheckState(decision.id).result.checklist" :key="item">{{ item }}</span>
              </div>
            </template>
          </div>
        </div>

        <div class="decision-card__controls">
          <button
            type="button"
            data-test="precheck-decision"
            class="decision-card__btn"
            @click="emit('precheck', decision.id)"
          >
            检查
          </button>
          <button
            type="button"
            class="decision-card__btn decision-card__btn--review"
            :disabled="reviewLoading[decision.id]"
            @click="startPeerReview(decision.id)"
          >
            <Icon :name="reviewLoading[decision.id] ? 'spinner' : 'users'" size="12" />
            {{ reviewLoading[decision.id] ? '评审中...' : '多模型评审' }}
          </button>
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

        <!-- 评审结果 -->
        <div v-if="peerReviews[decision.id]?.length" class="peer-reviews">
          <div class="peer-reviews__title">
            <Icon name="users" size="14" />
            多模型评审结果
          </div>
          <div v-for="review in peerReviews[decision.id]" :key="review.id || review.reviewer_type" class="peer-review-item">
            <div class="peer-review__head">
              <span class="peer-review__type">{{ reviewerLabel(review.reviewer_type) }}</span>
              <span :class="['peer-review__verdict', verdictClass(review.verdict)]">
                {{ verdictLabel(review.verdict) }}
              </span>
            </div>
            <div v-if="review.concerns?.length" class="peer-review__concerns">
              <div v-for="c in review.concerns" :key="c" class="peer-review__concern">
                <Icon name="alert-circle" size="11" />
                {{ c }}
              </div>
            </div>
            <div v-if="review.suggestions?.length" class="peer-review__suggestions">
              <div v-for="s in review.suggestions" :key="s" class="peer-review__suggestion">
                <Icon name="lightbulb" size="11" />
                {{ s }}
              </div>
            </div>
          </div>
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

.decision-card__precheck {
  display: grid;
  gap: 0.45rem;
  margin-top: 0.65rem;
  padding: 0.65rem;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  background: var(--color-bg-hover);
}

.precheck-loading {
  color: var(--color-text-secondary);
  font-size: 0.76rem;
}

.precheck-status {
  width: fit-content;
  padding: 0.16rem 0.5rem;
  border-radius: var(--radius-xs);
  font-size: 0.7rem;
  font-weight: 800;
}

.precheck-status--ok {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.precheck-status--blocked {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.precheck-group {
  display: grid;
  gap: 0.24rem;
  color: var(--color-text-secondary);
  font-size: 0.75rem;
  line-height: 1.45;
}

.precheck-group strong {
  color: var(--color-text-primary);
  font-size: 0.72rem;
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

.decision-card__btn--review {
  background: #f0f9ff;
  border-color: #93c5fd;
  color: #2563eb;
}

/* ── 评审结果 ── */
.peer-reviews {
  grid-column: 1 / -1;
  padding: 0.75rem;
  background: var(--color-bg-input);
  border-top: 1px solid var(--color-border-light);
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.peer-reviews__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: 0.25rem;
}
.peer-review-item {
  padding: 0.5rem 0.75rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.peer-review__head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.peer-review__type {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
}
.peer-review__verdict {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}
.verdict-ok {
  color: var(--color-success);
  background: var(--color-success-bg);
}
.verdict-warn {
  color: var(--color-warning-text);
  background: var(--color-warning-bg);
}
.verdict-danger {
  color: var(--color-danger);
  background: var(--color-danger-bg);
}
.peer-review__concerns,
.peer-review__suggestions {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.peer-review__concern,
.peer-review__suggestion {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
}
.peer-review__concern { color: var(--color-warning-text); }
.peer-review__suggestion { color: var(--color-primary); }

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
