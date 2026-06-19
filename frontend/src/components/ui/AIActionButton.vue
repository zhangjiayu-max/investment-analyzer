<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  label: { type: String, required: true },
  agent: { type: String, default: '' },
  icon: { type: String, default: 'bot' },
  loading: { type: Boolean, default: false },
  state: {
    type: String,
    default: 'idle',
    validator: (value) => ['idle', 'submitting', 'queued', 'running', 'error'].includes(value),
  },
  disabled: { type: Boolean, default: false },
  variant: { type: String, default: 'primary' },
  size: { type: String, default: 'md' },
})

const emit = defineEmits(['click'])

const effectiveState = computed(() => (props.loading ? 'running' : props.state))

const stateConfig = computed(() => {
  const states = {
    idle: { label: props.label, icon: props.icon, disabled: false, loading: false },
    submitting: { label: '提交中...', icon: 'hourglass', disabled: true, loading: true },
    queued: { label: '已提交后台分析', icon: 'check', disabled: true, loading: false },
    running: { label: '分析中...', icon: 'hourglass', disabled: true, loading: true },
    error: { label: '重试分析', icon: 'refresh', disabled: false, loading: false },
  }

  return states[effectiveState.value] || states.idle
})

const isDisabled = computed(() => props.disabled || stateConfig.value.disabled)
</script>

<template>
  <button
    type="button"
    :class="[
      'ai-action-button',
      `ai-action-button--${variant}`,
      `ai-action-button--${size}`,
      `ai-action-button--${effectiveState}`,
      { 'is-loading': stateConfig.loading },
    ]"
    :disabled="isDisabled"
    :title="agent || label"
    @click="emit('click', $event)"
  >
    <Icon :name="stateConfig.icon" :size="size === 'sm' ? 13 : 15" class="ai-action-button__icon" />
    <span class="ai-action-button__label">{{ stateConfig.label }}</span>
    <span v-if="agent" class="ai-action-button__agent">{{ agent }}</span>
    <span v-if="agent" class="ai-action-button__tooltip">{{ agent }}</span>
  </button>
</template>

<style scoped>
.ai-action-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.42rem;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  font-weight: 700;
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast), transform var(--transition-fast), box-shadow var(--transition-fast);
  white-space: nowrap;
}

.ai-action-button--md {
  min-height: 34px;
  padding: 0.48rem 0.72rem;
  font-size: 0.8rem;
}

.ai-action-button--sm {
  min-height: 28px;
  padding: 0.34rem 0.55rem;
  font-size: 0.74rem;
}

.ai-action-button--primary {
  background: var(--color-primary);
  color: white;
  box-shadow: var(--shadow-sm);
}

.ai-action-button--soft {
  background: var(--color-primary-bg);
  border-color: var(--color-primary-border);
  color: var(--color-primary);
}

.ai-action-button--ghost {
  background: var(--color-bg-card);
  border-color: var(--color-border);
  color: var(--color-text-secondary);
}

.ai-action-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

.ai-action-button:disabled {
  opacity: 0.62;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.ai-action-button--queued {
  border-color: var(--color-success, #16a34a);
}

.ai-action-button--error {
  border-color: var(--color-danger, #dc2626);
}

.ai-action-button.is-loading .ai-action-button__icon {
  animation: ai-action-pulse 1s ease-in-out infinite;
}

.ai-action-button__icon {
  flex-shrink: 0;
}

.ai-action-button__agent {
  max-width: 8rem;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 0.68rem;
  font-weight: 600;
  opacity: 0.72;
}

.ai-action-button__tooltip {
  position: absolute;
  top: calc(100% + 8px);
  left: 50%;
  z-index: 100;
  transform: translateX(-50%);
  padding: 0.4rem 0.7rem;
  border-radius: var(--radius-md);
  background: #1a1a2e;
  color: white;
  box-shadow: var(--shadow-md);
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity var(--transition-fast), visibility var(--transition-fast), transform var(--transition-fast);
}

.ai-action-button__tooltip::after {
  content: '';
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent;
  border-bottom-color: #1a1a2e;
}

.ai-action-button:hover .ai-action-button__tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) translateY(2px);
}

@keyframes ai-action-pulse {
  0%, 100% { opacity: 0.45; transform: rotate(0deg); }
  50% { opacity: 1; transform: rotate(10deg); }
}
</style>
