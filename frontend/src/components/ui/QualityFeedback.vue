<template>
  <div class="quality-feedback">
    <!-- 简单模式：只显示有用/无帮助 -->
    <div v-if="simple" class="simple-feedback">
      <button
        :class="['fb-btn', { active: currentRating === 'helpful' }]"
        @click="toggleSimple('helpful')"
        :disabled="sending"
      >
        {{ currentRating === 'helpful' ? '👍 已赞' : '👍' }}
      </button>
      <button
        :class="['fb-btn', 'fb-btn-bad', { active: currentRating === 'unhelpful' }]"
        @click="toggleSimple('unhelpful')"
        :disabled="sending"
      >
        {{ currentRating === 'unhelpful' ? '👎 已踩' : '👎' }}
      </button>
      <span v-if="sent" class="fb-sent">已反馈，感谢</span>
    </div>

    <!-- 多维度模式 -->
    <div v-else class="multi-feedback">
      <div v-if="!expanded && !sent" class="fb-trigger" @click="expanded = true">
        <span class="fb-trigger-text">评估质量</span>
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
        </svg>
      </div>

      <Transition name="expand">
        <div v-if="expanded && !sent" class="fb-form">
          <div class="fb-dim" v-for="dim in dimensions" :key="dim.key">
            <span class="fb-dim-label">{{ dim.label }}</span>
            <div class="fb-stars">
              <button
                v-for="n in 5"
                :key="n"
                :class="['fb-star', { active: scores[dim.key] >= n }]"
                @click="scores[dim.key] = n"
              >
                ★
              </button>
            </div>
            <span class="fb-dim-hint">{{ dim.hints[scores[dim.key]] || '' }}</span>
          </div>

          <div class="fb-comment-row">
            <input
              v-model="comment"
              class="fb-comment-input"
              placeholder="补充说明（可选）"
              maxlength="200"
            />
          </div>

          <div class="fb-actions">
            <button class="fb-submit" @click="submit" :disabled="sending || !allScored">
              {{ sending ? '提交中...' : '提交评分' }}
            </button>
            <button class="fb-cancel" @click="expanded = false">取消</button>
          </div>
        </div>
      </Transition>

      <div v-if="sent" class="fb-result">
        <span class="fb-result-score">综合 {{ overallScore }} 分</span>
        <span class="fb-result-dims">
          数据{{ scores.data_accuracy }} · 逻辑{{ scores.logic }} · 可执行{{ scores.actionability }}
        </span>
        <span class="fb-sent">已反馈，感谢</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { submitLlmFeedback } from '../../api'

const props = defineProps({
  /** 目标类型：analysis / daily_report / chat / specialist */
  targetType: { type: String, default: '' },
  /** 目标 ID */
  targetId: { type: Number, default: null },
  /** 简单模式（只显示有用/无帮助） */
  simple: { type: Boolean, default: false },
  /** 调用方标识 */
  caller: { type: String, default: 'quality_feedback' },
})

const emit = defineEmits(['submitted'])

const expanded = ref(false)
const sending = ref(false)
const sent = ref(false)
const currentRating = ref(null)
const comment = ref('')

const scores = ref({
  data_accuracy: 0,
  logic: 0,
  actionability: 0,
})

const dimensions = [
  {
    key: 'data_accuracy',
    label: '数据准确性',
    hints: { 1: '数据错误', 2: '有偏差', 3: '基本正确', 4: '准确', 5: '精确有来源' },
  },
  {
    key: 'logic',
    label: '逻辑一致性',
    hints: { 1: '逻辑混乱', 2: '有漏洞', 3: '基本合理', 4: '逻辑清晰', 5: '严密自洽' },
  },
  {
    key: 'actionability',
    label: '可执行性',
    hints: { 1: '空泛无用', 2: '模糊', 3: '有方向', 4: '较具体', 5: '可直接操作' },
  },
]

const allScored = computed(() =>
  scores.value.data_accuracy > 0 && scores.value.logic > 0 && scores.value.actionability > 0
)

const overallScore = computed(() => {
  const s = scores.value
  return ((s.data_accuracy + s.logic + s.actionability) / 3).toFixed(1)
})

async function toggleSimple(rating) {
  if (currentRating.value === rating) return
  currentRating.value = rating
  sending.value = true
  try {
    await submitLlmFeedback({
      caller: props.caller,
      rating,
      target_type: props.targetType,
      target_id: props.targetId,
    })
    sent.value = true
    emit('submitted', { rating })
  } catch (e) {
    console.error('Feedback failed:', e)
  } finally {
    sending.value = false
  }
}

async function submit() {
  if (!allScored.value || sending.value) return
  sending.value = true
  try {
    await submitLlmFeedback({
      caller: props.caller,
      rating: overallScore.value >= 3 ? 'helpful' : 'unhelpful',
      score_data_accuracy: scores.value.data_accuracy,
      score_logic: scores.value.logic,
      score_actionability: scores.value.actionability,
      comment: comment.value,
      target_type: props.targetType,
      target_id: props.targetId,
    })
    sent.value = true
    emit('submitted', { scores: scores.value, overall: overallScore.value })
  } catch (e) {
    console.error('Feedback failed:', e)
  } finally {
    sending.value = false
  }
}
</script>

<style scoped>
.quality-feedback {
  font-size: 0.85rem;
}

/* Simple mode */
.simple-feedback {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.fb-btn {
  padding: 0.35rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg-primary);
  cursor: pointer;
  font-size: 0.85rem;
  transition: all 0.15s;
}

.fb-btn:hover {
  border-color: var(--color-primary-400);
}

.fb-btn.active {
  background: #dcfce7;
  border-color: #86efac;
  color: #166534;
}

.fb-btn-bad.active {
  background: #fee2e2;
  border-color: #fca5a5;
  color: #991b1b;
}

/* Multi-dimension mode */
.fb-trigger {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  cursor: pointer;
  color: var(--color-text-muted);
  padding: 0.35rem 0;
}

.fb-trigger:hover {
  color: var(--color-primary-500);
}

.fb-trigger-text {
  font-size: 0.8rem;
}

.fb-form {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 1rem;
  margin-top: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.fb-dim {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.fb-dim-label {
  width: 5rem;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.fb-stars {
  display: flex;
  gap: 0.15rem;
}

.fb-star {
  background: none;
  border: none;
  font-size: 1.2rem;
  color: var(--color-border);
  cursor: pointer;
  padding: 0;
  transition: color 0.1s;
}

.fb-star.active {
  color: #f59e0b;
}

.fb-star:hover {
  color: #fbbf24;
}

.fb-dim-hint {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  min-width: 4rem;
}

.fb-comment-row {
  margin-top: 0.25rem;
}

.fb-comment-input {
  width: 100%;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: 0.8rem;
  background: var(--color-bg-primary);
  color: var(--color-text-primary);
}

.fb-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.25rem;
}

.fb-submit {
  padding: 0.5rem 1rem;
  background: var(--color-primary-500);
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 0.8rem;
  cursor: pointer;
}

.fb-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.fb-cancel {
  padding: 0.5rem 1rem;
  background: none;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: 0.8rem;
  cursor: pointer;
  color: var(--color-text-secondary);
}

/* Result */
.fb-result {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0;
}

.fb-result-score {
  font-weight: 700;
  color: var(--color-primary-500);
}

.fb-result-dims {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.fb-sent {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

/* Transition */
.expand-enter-active,
.expand-leave-active {
  transition: all 0.2s ease;
  overflow: hidden;
}

.expand-enter-from,
.expand-leave-to {
  opacity: 0;
  max-height: 0;
}

.expand-enter-to,
.expand-leave-from {
  opacity: 1;
  max-height: 300px;
}
</style>
