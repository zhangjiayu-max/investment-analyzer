<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  state: { type: String, default: 'ready' },
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  icon: { type: String, default: '' },
  actionText: { type: String, default: '' },
  retryText: { type: String, default: '重试' },
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['retry', 'action'])

const panelStates = ['loading', 'empty', 'error']
const isPanelState = computed(() => panelStates.includes(props.state))
const stateIcon = computed(() => {
  if (props.icon) return props.icon
  if (props.state === 'loading') return 'spinner'
  if (props.state === 'error') return 'error'
  return 'inbox'
})
</script>

<template>
  <div :class="['async-state-block', `async-state-block--${state}`, { 'async-state-block--compact': compact }]">
    <div v-if="isPanelState" class="async-state-block__panel">
      <Icon class="async-state-block__icon" :name="stateIcon" />
      <h3 v-if="title" class="async-state-block__title">{{ title }}</h3>
      <p v-if="description" class="async-state-block__description">{{ description }}</p>

      <button
        v-if="state === 'empty' && actionText"
        type="button"
        class="async-state-block__action"
        @click="emit('action')"
      >
        {{ actionText }}
      </button>

      <button
        v-if="state === 'error'"
        type="button"
        class="async-state-block__retry"
        @click="emit('retry')"
      >
        {{ retryText }}
      </button>
    </div>

    <slot v-else />
  </div>
</template>

<style scoped>
.async-state-block {
  width: 100%;
}

.async-state-block__panel {
  display: flex;
  min-height: 160px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-8) var(--space-6);
  text-align: center;
  color: var(--color-text-secondary);
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
}

.async-state-block--compact .async-state-block__panel {
  min-height: 108px;
  padding: var(--space-5) var(--space-4);
}

.async-state-block__icon {
  margin-bottom: var(--space-3);
  color: var(--color-text-tertiary);
}

.async-state-block--loading .async-state-block__icon {
  color: var(--color-primary);
}

.async-state-block--empty .async-state-block__icon {
  color: var(--color-text-tertiary);
}

.async-state-block--error .async-state-block__icon {
  color: var(--color-danger);
}

.async-state-block__title {
  margin: 0;
  color: var(--color-text-primary);
  font-size: 0.9375rem;
  font-weight: 600;
  line-height: 1.4;
}

.async-state-block__description {
  max-width: 360px;
  margin: var(--space-2) 0 0;
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  line-height: 1.6;
}

.async-state-block__action,
.async-state-block__retry {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 32px;
  margin-top: var(--space-4);
  padding: 0 var(--space-4);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  font-size: 0.8125rem;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast), transform var(--transition-fast);
}

.async-state-block__action {
  color: var(--color-text-inverse);
  background: var(--color-primary);
}

.async-state-block__action:hover {
  background: var(--color-primary-hover);
  transform: var(--hover-lift);
}

.async-state-block__retry {
  color: var(--color-danger);
  background: var(--color-danger-bg);
  border-color: var(--color-danger-border);
}

.async-state-block__retry:hover {
  background: rgba(220, 38, 38, 0.12);
  transform: var(--hover-lift);
}
</style>
