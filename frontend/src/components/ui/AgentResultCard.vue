<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'
import { renderMarkdown } from '../../composables/useMarkdown'

const props = defineProps({
  title: { type: String, default: 'AI 分析结果' },
  agent: { type: String, default: '' },
  status: { type: String, default: 'done' },
  content: { type: String, default: '' },
  error: { type: String, default: '' },
  tokenUsage: { type: Number, default: 0 },
  recordId: { type: [String, Number], default: null },
})

const statusConfig = computed(() => {
  if (props.status === 'running') return { icon: 'hourglass', label: '后台分析中', tone: 'running' }
  if (props.status === 'error') return { icon: 'error', label: '分析失败', tone: 'error' }
  return { icon: 'check', label: '已完成', tone: 'done' }
})

const renderedContent = computed(() => renderMarkdown(props.content || ''))
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
      </div>
    </header>

    <div v-if="status === 'running'" class="agent-result-card__running">
      <div class="agent-result-card__bar"></div>
      <span>任务已提交，可以切换页面，结果会自动刷新。</span>
    </div>
    <div v-else-if="status === 'error'" class="agent-result-card__error">
      {{ error || content || '分析失败，请稍后重试。' }}
    </div>
    <div v-else class="agent-result-card__body markdown-body" v-html="renderedContent"></div>
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
.agent-result-card__id {
  padding: 0.16rem 0.42rem;
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
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

.agent-result-card__body {
  padding: 0.95rem;
}

.agent-result-card__running,
.agent-result-card__error {
  padding: 0.95rem;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}

.agent-result-card__error {
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

@keyframes agent-result-loading {
  0% { background-position: 0% 50%; }
  100% { background-position: 200% 50%; }
}
</style>
