<script setup>
import { ref } from 'vue'
import DOMPurify from 'dompurify'
import { formatBriefingTime, renderBriefing } from '../../composables/useDashboardHelpers'
import AIActionButton from '../ui/AIActionButton.vue'

const props = defineProps({
  dailyReport: { type: Object, default: null },
  regenerating: { type: Boolean, default: false },
  showBriefing: { type: Boolean, default: true },
  feedback: { type: Object, required: true },
})

const emit = defineEmits(['regenerate', 'toggle', 'submit-feedback'])

function safeRenderBriefing(text) {
  const html = renderBriefing(text)
  return DOMPurify.sanitize(html)
}

function toggleFeedback(rating) {
  emit('submit-feedback', 'toggle', rating)
}

function submitFeedbackWithComment() {
  emit('submit-feedback', 'submit-comment')
}
</script>

<template>
  <div v-if="dailyReport" class="briefing-card card">
    <div class="briefing-header" @click="emit('toggle')">
      <div class="briefing-title-row">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="briefing-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"/>
        </svg>
        <span class="briefing-label">每日市场简报</span>
        <span class="briefing-time">{{ formatBriefingTime(dailyReport.created_at) }}</span>
        <AIActionButton
          label="重新生成"
          agent="市场日报分析师"
          icon="refresh"
          variant="soft"
          size="sm"
          :loading="regenerating"
          @click.stop="emit('regenerate')"
        />
      </div>
      <svg :class="['briefing-chevron', { 'briefing-chevron-up': showBriefing }]" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
      </svg>
    </div>
    <Transition name="slide-fade">
      <div v-if="showBriefing" class="briefing-body" v-html="safeRenderBriefing(dailyReport.result)"></div>
    </Transition>
    <div v-if="showBriefing" class="briefing-footer">
      <div class="briefing-feedback">
        <button
          class="rec-feedback-btn helpful"
          :class="{ active: feedback.rating === 'helpful' }"
          :disabled="feedback.sending"
          @click="toggleFeedback('helpful')"
          title="点赞"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"/>
          </svg>
        </button>
        <button
          class="rec-feedback-btn unhelpful"
          :class="{ active: feedback.rating === 'unhelpful' }"
          :disabled="feedback.sending"
          @click="toggleFeedback('unhelpful')"
          title="点踩"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018c.163 0 .326.02.485.06L17 4m-7 10v5a2 2 0 002 2h.095c.5 0 .905-.405.905-.905 0-.714.211-1.412.608-2.006L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5"/>
          </svg>
        </button>
        <span v-if="feedback.sent && !feedback.showComment" class="briefing-feedback-hint">感谢反馈</span>
      </div>
      <Transition name="slide-fade">
        <div v-if="feedback.showComment" class="briefing-comment-box">
          <input
            v-model="feedback.comment"
            class="briefing-comment-input"
            placeholder="请描述问题，帮助我们改进..."
            @keydown.enter="submitFeedbackWithComment"
          />
          <button
            class="briefing-comment-submit"
            :disabled="feedback.sending || !feedback.comment.trim()"
            @click="submitFeedbackWithComment"
          >提交</button>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.briefing-card {
  margin-bottom: 1rem;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--color-primary-border);
  background: linear-gradient(135deg, var(--color-primary-bg-gradient-start) 0%, var(--color-bg-card) 100%);
  position: relative;
  transition: transform var(--transition-fast), box-shadow var(--transition-normal), border-color var(--transition-normal);
}
/* 渐变顶条 */
.briefing-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--gradient-accent);
  opacity: 0.7;
  transition: opacity var(--transition-fast);
}
/* 角落发光 */
.briefing-card::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 80px;
  height: 80px;
  background: radial-gradient(circle at top right, var(--color-primary-glow), transparent 70%);
  pointer-events: none;
  opacity: 0;
  transition: opacity var(--transition-normal);
}
.briefing-card:hover {
  transform: var(--hover-lift);
  box-shadow: var(--shadow-glow);
  border-color: var(--color-primary-border-strong);
}
.briefing-card:hover::before {
  opacity: 1;
}
.briefing-card:hover::after {
  opacity: 1;
}
.briefing-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.85rem 1.25rem;
  cursor: pointer;
  user-select: none;
  position: relative;
  z-index: 1;
}
.briefing-header:hover {
  background: var(--color-primary-bg-weak);
}
.briefing-title-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.briefing-icon {
  color: var(--color-primary);
  flex-shrink: 0;
}
.briefing-label {
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--color-text-primary);
}
.briefing-time {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  background: var(--color-primary-50);
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  margin-right: 0.5rem;
}
.briefing-chevron {
  color: var(--color-text-muted);
  transition: transform 0.25s ease;
  flex-shrink: 0;
}
.briefing-chevron-up {
  transform: rotate(180deg);
}
.briefing-body {
  padding: 0.75rem 1.25rem 1rem;
  margin: 0 0.5rem;
  font-size: 0.88rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
  background: linear-gradient(135deg, var(--color-primary-bg-gradient-start), var(--color-primary-bg-gradient-end));
  border-radius: var(--radius-md);
  position: relative;
  z-index: 1;
}
.briefing-body :deep(ul) {
  margin: 0.3rem 0;
  padding-left: 1.2rem;
}
.briefing-body :deep(li) {
  margin-bottom: 0.2rem;
}
.briefing-body :deep(strong) {
  color: var(--color-text-primary);
  font-weight: 600;
}
.briefing-footer {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  padding: 0.5rem 1.25rem 0.75rem;
  border-top: 1px solid var(--color-border);
  position: relative;
  z-index: 1;
}
.briefing-feedback {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.briefing-feedback-hint {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-left: 0.3rem;
}
.briefing-comment-box {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.6rem;
  width: 100%;
}
.briefing-comment-input {
  flex: 1;
  padding: 0.45rem 0.75rem;
  font-size: 0.82rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  outline: none;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.briefing-comment-input:focus {
  border-color: var(--color-primary-500);
  box-shadow: var(--focus-ring);
}
.briefing-comment-input::placeholder {
  color: var(--color-text-muted);
}
.briefing-comment-submit {
  padding: 0.45rem 1rem;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-inverse);
  background: var(--gradient-primary);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  white-space: nowrap;
  box-shadow: 0 1px 3px var(--color-primary-shadow);
}
.briefing-comment-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.briefing-comment-submit:hover:not(:disabled) {
  box-shadow: 0 4px 12px var(--color-primary-glow-strong);
  transform: var(--hover-lift);
}
@media (max-width: 768px) {
  .briefing-title-row { flex-wrap: wrap; gap: 0.25rem; }
  .briefing-body { padding: 0.5rem 0.75rem; }
}
.slide-fade-enter-active {
  transition: all 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.slide-fade-leave-active {
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.slide-fade-enter-from,
.slide-fade-leave-to {
  opacity: 0;
  max-height: 0;
  padding-top: 0;
  padding-bottom: 0;
  transform: translateY(-8px);
}
</style>
