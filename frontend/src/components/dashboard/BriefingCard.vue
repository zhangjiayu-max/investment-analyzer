<script setup>
import { ref } from 'vue'
import DOMPurify from 'dompurify'
import { formatBriefingTime, renderBriefing } from '../../composables/useDashboardHelpers'

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
        <button
          class="btn-ai-action btn-briefing-gen"
          :class="{ 'btn-loading': regenerating }"
          :disabled="regenerating"
          @click.stop="emit('regenerate')"
        >
          <svg :class="['icon-spin', { 'spinning': regenerating }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          <span>重新生成</span>
          <span class="ai-agent-tooltip">市场日报分析师</span>
        </button>
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
}
.briefing-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.85rem 1.25rem;
  cursor: pointer;
  user-select: none;
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
.btn-briefing-gen {
  padding: 0.3rem 0.65rem;
  font-size: 0.75rem;
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
  padding: 0 1.25rem 1rem;
  font-size: 0.88rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
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
  justify-content: flex-end;
  padding: 0.5rem 1.25rem 0.75rem;
  border-top: 1px solid var(--color-border);
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
  background: var(--color-bg);
  color: var(--color-text-primary);
  outline: none;
  transition: border-color 0.2s;
}
.briefing-comment-input:focus {
  border-color: var(--color-primary);
}
.briefing-comment-input::placeholder {
  color: var(--color-text-muted);
}
.briefing-comment-submit {
  padding: 0.45rem 1rem;
  font-size: 0.82rem;
  font-weight: 600;
  color: #fff;
  background: var(--color-primary);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: opacity 0.2s;
  white-space: nowrap;
}
.briefing-comment-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.briefing-comment-submit:hover:not(:disabled) {
  opacity: 0.9;
}
.slide-fade-enter-active {
  transition: all 0.25s ease-out;
}
.slide-fade-leave-active {
  transition: all 0.2s ease-in;
}
.slide-fade-enter-from,
.slide-fade-leave-to {
  opacity: 0;
  max-height: 0;
  padding-top: 0;
  padding-bottom: 0;
}
</style>
