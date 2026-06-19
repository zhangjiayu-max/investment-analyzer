<script setup>
import Icon from './Icon.vue'

defineProps({
  label: { type: String, required: true },
  agent: { type: String, default: '' },
  icon: { type: String, default: 'bot' },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  variant: { type: String, default: 'primary' },
  size: { type: String, default: 'md' },
})

const emit = defineEmits(['click'])
</script>

<template>
  <button
    type="button"
    :class="['ai-action-button', `ai-action-button--${variant}`, `ai-action-button--${size}`, { 'is-loading': loading }]"
    :disabled="disabled || loading"
    :title="agent || label"
    @click="emit('click', $event)"
  >
    <Icon :name="loading ? 'hourglass' : icon" :size="size === 'sm' ? 13 : 15" class="ai-action-button__icon" />
    <span class="ai-action-button__label">{{ loading ? '分析中...' : label }}</span>
    <span v-if="agent" class="ai-action-button__agent">{{ agent }}</span>
  </button>
</template>

<style scoped>
.ai-action-button {
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

@keyframes ai-action-pulse {
  0%, 100% { opacity: 0.45; transform: rotate(0deg); }
  50% { opacity: 1; transform: rotate(10deg); }
}
</style>
