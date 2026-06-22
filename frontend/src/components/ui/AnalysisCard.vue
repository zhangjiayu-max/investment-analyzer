<!-- AI 分析结果卡片：结构化展示分析内容 -->
<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../../composables/useMarkdown'

const props = defineProps({
  result: { type: String, default: '' },
  agentName: { type: String, default: '' },
  tokenUsage: { type: Number, default: 0 },
  createdAt: { type: String, default: '' },
  recordId: { type: [Number, String], default: null },
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

function copyRecordId() {
  if (props.recordId) {
    navigator.clipboard.writeText(String(props.recordId)).catch(() => {})
  }
}

function copyContent() {
  const text = props.result || ''
  if (!text) return
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.activeElement
    if (btn) {
      const orig = btn.innerHTML
      btn.innerHTML = '<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
      setTimeout(() => { btn.innerHTML = orig }, 1500)
    }
  }).catch(() => {})
}
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
        <span v-if="recordId" class="analysis-record-id" title="点击复制记录 ID" @click="copyRecordId">ID:{{ recordId }}</span>
      </div>
      <div class="analysis-actions">
        <button class="rec-feedback-btn" @click="copyContent" title="复制内容">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/>
          </svg>
        </button>
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
  padding: 0.5rem 0.875rem;
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

.analysis-record-id {
  opacity: 0.5;
  font-size: 0.75rem;
  font-family: monospace;
  cursor: pointer;
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  transition: all 0.15s;
}

.analysis-record-id:hover {
  opacity: 1;
  background: var(--color-surface-hover);
}

.analysis-actions {
  display: flex;
  gap: 0.35rem;
}

/* ── Report Body ── */
.analysis-body {
  padding: 0.875rem 1.25rem;
  font-size: 0.875rem;
  line-height: 1.6;
  color: var(--color-text-primary);
}

/* h2 作为主章节标题：紧凑分隔线 + 渐变左边框 */
.analysis-body :deep(h2) {
  font-size: 1em;
  font-weight: 700;
  margin: 0.8rem 0 0.3rem;
  padding: 0.25rem 0 0.2rem 0.6rem;
  border-left: 3px solid var(--color-primary);
  background: linear-gradient(90deg, var(--color-surface-hover), transparent);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  color: var(--color-text-primary);
  letter-spacing: 0.02em;
}

.analysis-body :deep(h2:first-child) {
  margin-top: 0;
}

/* h3 子标题 */
.analysis-body :deep(h3) {
  font-size: 0.9rem;
  font-weight: 600;
  margin: 0.5rem 0 0.2rem;
  color: var(--color-text-secondary);
}

/* h1 基本不用，保留安全样式 */
.analysis-body :deep(h1) {
  font-size: 1.1em;
  font-weight: 700;
  margin: 0.6rem 0 0.3rem;
}

/* 段落紧凑 */
.analysis-body :deep(p) {
  margin: 0.2rem 0;
}

/* 列表紧凑 */
.analysis-body :deep(ul),
.analysis-body :deep(ol) {
  padding-left: 1.25rem;
  margin: 0.2rem 0;
}

.analysis-body :deep(li) {
  margin: 0.1rem 0;
}

/* 表格：紧凑行高 + 斑马纹 */
.analysis-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.4rem 0;
  font-size: 0.8125rem;
}

.analysis-body :deep(th),
.analysis-body :deep(td) {
  padding: 0.35rem 0.6rem;
  border: 1px solid var(--color-border);
  text-align: left;
}

.analysis-body :deep(th) {
  background: var(--color-surface-hover);
  font-weight: 600;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}

.analysis-body :deep(tbody tr:nth-child(even)) {
  background: var(--color-surface-hover);
}

/* 引用块 */
.analysis-body :deep(blockquote) {
  border-left: 3px solid var(--color-primary);
  padding: 0.25rem 0.75rem;
  margin: 0.4rem 0;
  color: var(--color-text-secondary);
  font-style: italic;
  background: var(--color-surface-hover);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

/* 代码 */
.analysis-body :deep(code) {
  background: var(--color-surface-hover);
  padding: 0.1em 0.35em;
  border-radius: 3px;
  font-size: 0.85em;
}

.analysis-body :deep(pre) {
  background: var(--color-surface-hover);
  padding: 0.75rem;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  margin: 0.5rem 0;
}

.analysis-body :deep(pre code) {
  background: none;
  padding: 0;
}

/* 强调文本 */
.analysis-body :deep(strong) {
  font-weight: 600;
  color: var(--color-text-primary);
}

/* 分隔线 */
.analysis-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 0.75rem 0;
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

@media (max-width: 768px) {
  .analysis-header { flex-wrap: wrap; gap: 0.25rem; padding: 0.4rem 0.625rem; }
  .analysis-meta { flex-wrap: wrap; gap: 0.3rem; font-size: 0.75rem; }
  .analysis-body { padding: 0.75rem; font-size: 0.82rem; }
  .analysis-body :deep(table) { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; white-space: nowrap; font-size: 0.75rem; }
  .analysis-body :deep(th),
  .analysis-body :deep(td) { padding: 0.25rem 0.4rem; }
  .analysis-body :deep(pre) { font-size: 0.72rem; padding: 0.5rem; overflow-x: auto; }
}
</style>
