<script setup>
defineProps({
  visible: Boolean,
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '确定要执行此操作吗？' },
  confirmText: { type: String, default: '确定' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="visible" class="dialog-backdrop" @click.self="emit('cancel')">
        <div class="dialog-box">
          <div class="dialog-body">
            <div :class="['dialog-icon', danger ? 'icon-danger' : 'icon-info']">
              <svg v-if="danger" width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>
              </svg>
              <svg v-else width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
              </svg>
            </div>
            <div>
              <h3 class="dialog-title">{{ title }}</h3>
              <p class="dialog-message">{{ message }}</p>
            </div>
          </div>
          <div class="dialog-actions">
            <button @click="emit('cancel')" :disabled="loading" class="btn-secondary">
              {{ cancelText }}
            </button>
            <button @click="emit('confirm')" :disabled="loading"
              :class="['btn-primary', danger ? 'btn-danger' : '']">
              <svg v-if="loading" class="spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/></svg>
              {{ loading ? '执行中...' : confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0,0,0,0.4);
  backdrop-filter: blur(4px);
}

.dialog-box {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 400px;
  margin: 0 1rem;
  overflow: hidden;
}

.dialog-body {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 1.5rem 1.5rem 1rem;
}

.dialog-icon {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.icon-info {
  background: var(--color-primary-50);
  color: var(--color-primary-600);
}

.icon-danger {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.dark .icon-info {
  background: var(--color-primary-bg);
  color: var(--color-primary-400);
}

.dialog-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 0.25rem 0;
}

.dialog-message {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin: 0;
  line-height: 1.5;
}

.dialog-actions {
  display: flex;
  gap: 0.5rem;
  padding: 0 1.5rem 1.5rem;
}

.dialog-actions button {
  flex: 1;
  padding: 0.6rem 1rem;
  font-size: 0.85rem;
}

/* 移动端适配 */
@media (max-width: 768px) {
  .dialog-box {
    margin: 0 0.75rem;
    max-width: calc(100% - 1.5rem);
  }

  .dialog-body {
    padding: 1.25rem 1rem 0.75rem;
  }

  .dialog-actions {
    padding: 0 1rem 1rem;
    flex-direction: column;
  }

  .dialog-actions button {
    padding: 0.75rem 1rem;
    font-size: 0.9rem;
    min-height: 44px;
  }
}
</style>
