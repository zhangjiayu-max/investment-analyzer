<script setup>
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import QualityFeedback from './QualityFeedback.vue'
import { renderMarkdown } from '../../composables/useMarkdown'

const props = defineProps({
  title: { type: String, default: 'AI 分析结果' },
  agent: { type: String, default: '' },
  status: { type: String, default: 'done' },
  content: { type: String, default: '' },
  error: { type: String, default: '' },
  tokenUsage: { type: Number, default: 0 },
  recordId: { type: [String, Number], default: null },
  updatedAt: { type: String, default: '' },
  source: { type: String, default: '' },
  targetType: { type: String, default: '' },
  targetId: { type: [String, Number], default: null },
  feedbackCaller: { type: String, default: 'agent_result_card' },
  showFeedback: { type: Boolean, default: true },
  showCopy: { type: Boolean, default: true },
  collapsible: { type: Boolean, default: false },
  defaultExpanded: { type: Boolean, default: true },
})

const emit = defineEmits(['retry', 'copied', 'feedback-submitted', 'toggle-expanded'])

const expanded = ref(props.defaultExpanded)
const copied = ref(false)
let copiedTimer = null

watch(() => props.defaultExpanded, (value) => {
  expanded.value = value
})

const statusConfig = computed(() => {
  if (props.status === 'running') return { icon: 'hourglass', label: '后台分析中', tone: 'running' }
  if (props.status === 'error') return { icon: 'error', label: '分析失败', tone: 'error' }
  if (props.status === 'empty') return { icon: 'inbox', label: '暂无结果', tone: 'empty' }
  return { icon: 'check', label: '已完成', tone: 'done' }
})

const renderedContent = computed(() => renderMarkdown(props.content || ''))
const hasFeedbackTarget = computed(() => props.showFeedback && props.targetType && props.targetId !== null && props.status === 'done')
const canCopy = computed(() => props.showCopy && props.status === 'done' && Boolean(props.content))
const showBody = computed(() => !props.collapsible || expanded.value)

function toggleExpanded() {
  expanded.value = !expanded.value
  emit('toggle-expanded', expanded.value)
}

async function copyContent() {
  if (!props.content) return
  await navigator.clipboard.writeText(props.content)
  copied.value = true
  emit('copied', props.content)
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { copied.value = false }, 1200)
}
</script>

<template>
  <section :class="['agent-result-card', `agent-result-card--${statusConfig.tone}`]">
    <header class="agent-result-card__header">
      <div class="agent-result-card__title-wrap">
        <span class="agent-result-card__icon">
          <Icon :name="statusConfig.icon" size="15" />
        </span>
        <div>
          <h3 class="agent-result-card__title">{{ title }}</h3>
          <p v-if="agent" class="agent-result-card__agent">{{ agent }}</p>
        </div>
      </div>
      <div class="agent-result-card__meta">
        <span class="agent-result-card__status">{{ statusConfig.label }}</span>
        <span v-if="tokenUsage" class="agent-result-card__token">{{ tokenUsage }} tokens</span>
        <span v-if="recordId" class="agent-result-card__id">#{{ recordId }}</span>
        <span v-if="updatedAt" class="agent-result-card__time">{{ updatedAt }}</span>
        <span v-if="source" class="agent-result-card__source">{{ source }}</span>
        <button
          v-if="canCopy"
          type="button"
          class="agent-result-card__copy"
          @click="copyContent"
        >
          {{ copied ? '已复制' : '复制' }}
        </button>
        <button
          v-if="collapsible"
          type="button"
          class="agent-result-card__toggle"
          @click="toggleExpanded"
        >
          {{ expanded ? '收起' : '展开' }}
        </button>
      </div>
    </header>

    <div v-if="status === 'running'" class="agent-result-card__running">
      <div class="agent-result-card__bar"></div>
      <span>任务已提交，可以切换页面，结果会自动刷新。</span>
    </div>
    <div v-else-if="status === 'error'" class="agent-result-card__error">
      <span>{{ error || content || '分析失败，请稍后重试。' }}</span>
      <button type="button" class="agent-result-card__retry" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="status === 'empty'" class="agent-result-card__empty">
      暂无分析结果。
    </div>
    <div v-else-if="showBody" class="agent-result-card__body markdown-body" v-html="renderedContent"></div>

    <footer v-if="hasFeedbackTarget" class="agent-result-card__footer">
      <QualityFeedback
        :target-type="targetType"
        :target-id="targetId"
        :caller="feedbackCaller"
        simple
        @submitted="emit('feedback-submitted', $event)"
      />
    </footer>
  </section>
</template>

<style scoped>
.agent-result-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  overflow: hidden;
}

.agent-result-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.8rem 0.95rem;
  border-bottom: 1px solid var(--color-border-light);
  background: linear-gradient(180deg, var(--color-bg-hover), transparent);
}

.agent-result-card__title-wrap {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  min-width: 0;
}

.agent-result-card__icon {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: var(--color-primary-bg);
  color: var(--color-primary);
  flex-shrink: 0;
}

.agent-result-card__title {
  margin: 0;
  font-size: 0.9rem;
  font-weight: 800;
  color: var(--color-text-primary);
}

.agent-result-card__agent {
  margin: 0.12rem 0 0;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.agent-result-card__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.35rem;
  font-size: 0.68rem;
  color: var(--color-text-muted);
}

.agent-result-card__status,
.agent-result-card__token,
.agent-result-card__id,
.agent-result-card__time,
.agent-result-card__source,
.agent-result-card__copy,
.agent-result-card__toggle,
.agent-result-card__retry {
  padding: 0.16rem 0.42rem;
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  color: inherit;
  font: inherit;
}

.agent-result-card__copy,
.agent-result-card__toggle,
.agent-result-card__retry {
  cursor: pointer;
  font-weight: 700;
}

.agent-result-card__copy:hover,
.agent-result-card__toggle:hover,
.agent-result-card__retry:hover {
  color: var(--color-primary);
  border-color: var(--color-primary-border);
}

.agent-result-card--done .agent-result-card__status {
  color: var(--color-success);
  background: var(--color-success-bg);
  border-color: var(--color-success-border);
}

.agent-result-card--running .agent-result-card__status {
  color: var(--color-warning);
  background: var(--color-warning-bg);
  border-color: var(--color-warning-border);
}

.agent-result-card--error .agent-result-card__status {
  color: var(--color-danger);
  background: var(--color-danger-bg);
  border-color: var(--color-danger-border);
}

.agent-result-card--empty .agent-result-card__status {
  color: var(--color-text-muted);
  background: var(--color-bg-input);
  border-color: var(--color-border-light);
}

.agent-result-card__body,
.agent-result-card__running,
.agent-result-card__error,
.agent-result-card__empty {
  padding: 0.95rem;
}

.agent-result-card__running,
.agent-result-card__error,
.agent-result-card__empty {
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}

.agent-result-card__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  color: var(--color-danger);
}

.agent-result-card__bar {
  height: 4px;
  margin-bottom: 0.65rem;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--color-primary), var(--color-warning), var(--color-primary));
  background-size: 200% 100%;
  animation: agent-result-loading 1.4s ease-in-out infinite;
}

.agent-result-card__footer {
  padding: 0.7rem 0.95rem;
  border-top: 1px solid var(--color-border-light);
  background: var(--color-bg-hover);
}

@keyframes agent-result-loading {
  0% { background-position: 0% 50%; }
  100% { background-position: 200% 50%; }
}
</style>
