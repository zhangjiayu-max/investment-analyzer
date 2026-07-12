<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'action'])

const quickActions = [
  { key: 'valuation', label: '查估值', icon: 'trending-up', color: 'gold' },
  { key: 'portfolio', label: '看持仓', icon: 'briefcase', color: 'blue' },
  { key: 'news', label: '看热点', icon: 'fire', color: 'red' },
  { key: 'alert', label: '风险', icon: 'alert', color: 'orange' },
  { key: 'chat', label: '问AI', icon: 'message-circle', color: 'green' },
]

const activeAction = ref(null)

function handleAction(action) {
  activeAction.value = action.key
  emit('action', action)
  setTimeout(() => {
    activeAction.value = null
  }, 300)
}

const isOpen = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})
</script>

<template>
  <Transition name="slide-up">
    <div v-if="isOpen" class="mobile-quick-bar">
      <div class="quick-bar-wrapper">
        <button
          v-for="action in quickActions"
          :key="action.key"
          @click="handleAction(action)"
          :class="['quick-action-btn', { active: activeAction === action.key }]"
          :style="{ '--action-color': getColor(action.color) }"
        >
          <div class="quick-action-icon">
            <svg v-if="action.icon === 'trending-up'" width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
            </svg>
            <svg v-else-if="action.icon === 'briefcase'" width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/>
            </svg>
            <svg v-else-if="action.icon === 'fire'" width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
            </svg>
            <svg v-else-if="action.icon === 'alert'" width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
            </svg>
            <svg v-else-if="action.icon === 'message-circle'" width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
            </svg>
          </div>
          <span class="quick-action-label">{{ action.label }}</span>
        </button>
      </div>
      <button class="quick-bar-close" @click="isOpen = false">
        <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>
    </div>
  </Transition>
</template>

<script>
function getColor(color) {
  const colors = {
    gold: '#f59e0b',
    blue: '#3b82f6',
    red: '#ef4444',
    orange: '#f97316',
    green: '#10b981',
  }
  return colors[color] || '#f59e0b'
}
</script>

<style scoped>
.mobile-quick-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  padding-bottom: env(safe-area-inset-bottom, 0);
  z-index: 60;
}

.quick-bar-wrapper {
  display: flex;
  justify-content: center;
  gap: 0.5rem;
  padding: 1rem;
  padding-bottom: calc(1rem + env(safe-area-inset-bottom, 0));
  background: var(--glass-bg);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border-top: 1px solid var(--color-border);
  box-shadow: var(--shadow-lg);
}

.quick-action-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
  padding: 0.75rem 1rem;
  border-radius: var(--radius-xl);
  background: var(--color-bg-input);
  border: 2px solid var(--color-border-light);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
  min-width: 64px;
  position: relative;
  overflow: hidden;
}

.quick-action-btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, var(--action-color) 0%, transparent 100%);
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.quick-action-btn:active,
.quick-action-btn.active {
  transform: scale(0.95);
  border-color: var(--action-color);
}

.quick-action-btn:active::before,
.quick-action-btn.active::before {
  opacity: 0.1;
}

.quick-action-icon {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--action-color);
  transition: color var(--transition-fast);
  position: relative;
  z-index: 1;
}

.quick-action-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  position: relative;
  z-index: 1;
}

.quick-bar-close {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: transform 0.3s cubic-bezier(0.34, 1.2, 0.64, 1);
}

.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(100%);
}
</style>
