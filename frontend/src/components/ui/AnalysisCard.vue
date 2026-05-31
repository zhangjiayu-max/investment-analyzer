<!-- AI 分析结果卡片：结构化展示分析内容 -->
<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../../composables/useMarkdown'

const props = defineProps({
  result: { type: String, default: '' },
  agentName: { type: String, default: '' },
  tokenUsage: { type: Number, default: 0 },
  createdAt: { type: String, default: '' },
})

const emit = defineEmits(['feedback'])
const feedback = defineModel('feedback', { type: String, default: null })

const formattedTime = computed(() => {
  if (!props.createdAt) return ''
  const d = new Date(props.createdAt.replace(' ', 'T'))
  if (isNaN(d)) return props.createdAt
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})

const renderedContent = computed(() => renderMarkdown(props.result))
</script>

<template>
  <div class="analysis-card">
    <div class="analysis-header">
      <div class="analysis-meta">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="analysis-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
            d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
        </svg>
        <span class="analysis-agent">{{ agentName || 'AI 分析' }}</span>
        <span v-if="formattedTime" class="analysis-time">{{ formattedTime }}</span>
        <span v-if="tokenUsage" class="analysis-tokens">{{ tokenUsage }} tokens</span>
      </div>
      <div class="analysis-actions">
        <button
          class="rec-feedback-btn helpful"
          :class="{ active: feedback === 'helpful' }"
          @click="emit('feedback', feedback === 'helpful' ? null : 'helpful')"
          title="点赞"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"/>
          </svg>
        </button>
        <button
          class="rec-feedback-btn unhelpful"
          :class="{ active: feedback === 'unhelpful' }"
          @click="emit('feedback', feedback === 'unhelpful' ? null : 'unhelpful')"
          title="点踩"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018c.163 0 .326.02.485.06L17 4m-7 10v5a2 2 0 002 2h.095c.5 0 .905-.405.905-.905 0-.714.211-1.412.608-2.006L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5"/>
          </svg>
        </button>
      </div>
    </div>
    <div class="analysis-body prose" v-html="renderedContent"></div>
  </div>
</template>

<style scoped>
.analysis-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--color-surface);
}

.analysis-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.625rem 0.875rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-hover);
}

.analysis-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
  color: var(--color-text-tertiary);
}

.analysis-icon {
  color: var(--color-primary);
  flex-shrink: 0;
}

.analysis-agent {
  font-weight: 600;
  color: var(--color-text-secondary);
}

.analysis-time,
.analysis-tokens {
  opacity: 0.6;
}

.analysis-tokens::before {
  content: '·';
  margin-right: 0.25rem;
}

.analysis-actions {
  display: flex;
  gap: 0.25rem;
}

.analysis-body {
  padding: 1rem;
  font-size: 0.875rem;
  line-height: 1.7;
  color: var(--color-text-primary);
}

.analysis-body :deep(h1),
.analysis-body :deep(h2),
.analysis-body :deep(h3) {
  margin-top: 1rem;
  margin-bottom: 0.5rem;
}

.analysis-body :deep(ul),
.analysis-body :deep(ol) {
  padding-left: 1.25rem;
  margin: 0.5rem 0;
}

.analysis-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75rem 0;
}

.analysis-body :deep(th),
.analysis-body :deep(td) {
  padding: 0.375rem 0.625rem;
  border: 1px solid var(--color-border);
  text-align: left;
}

.analysis-body :deep(th) {
  background: var(--color-surface-hover);
  font-weight: 600;
}

.rec-feedback-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  color: var(--color-text-tertiary);
  transition: all 0.15s;
}

.rec-feedback-btn:hover { background: var(--color-surface-hover); }
.rec-feedback-btn.helpful.active { color: #10b981; border-color: #10b981; background: rgba(16,185,129,0.08); }
.rec-feedback-btn.unhelpful.active { color: #ef4444; border-color: #ef4444; background: rgba(239,68,68,0.08); }
</style>
