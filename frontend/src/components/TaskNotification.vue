<template>
  <Teleport to="body">
    <Transition name="slide">
      <div v-if="task" class="task-notification" :class="task.status">
        <div class="notification-content">
          <span class="notification-icon">
            {{ task.status === 'completed' ? '✅' : '❌' }}
          </span>
          <div class="notification-text">
            <span class="notification-title">
              {{ task.status === 'completed' ? '任务完成' : '任务失败' }}
            </span>
            <span class="notification-message">{{ task.title }}</span>
          </div>
        </div>
        <div class="notification-actions">
          <button class="btn-view" @click="$emit('view', task.convId)">
            查看结果
          </button>
          <button class="btn-close" @click="$emit('close')">
            ✕
          </button>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
defineProps({
  task: {
    type: Object,
    default: null,
  },
})

defineEmits(['view', 'close'])
</script>

<style scoped>
.task-notification {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 20px;
  background: var(--color-bg-primary, #fff);
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12),
              0 2px 8px rgba(0, 0, 0, 0.08);
  min-width: 320px;
  max-width: 450px;
}

.task-notification.completed {
  border-left: 4px solid #10b981;
}

.task-notification.failed {
  border-left: 4px solid #ef4444;
}

.notification-content {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.notification-icon {
  font-size: 24px;
  flex-shrink: 0;
}

.notification-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.notification-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.notification-message {
  font-size: 13px;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notification-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.btn-view {
  padding: 8px 16px;
  background: var(--color-primary-500, #3b82f6);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-view:hover {
  background: var(--color-primary-600, #2563eb);
  transform: translateY(-1px);
}

.btn-close {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  border-radius: 6px;
  font-size: 14px;
  transition: all 0.2s;
}

.btn-close:hover {
  background: var(--color-bg-secondary, #f3f4f6);
  color: var(--color-text-primary);
}

/* 动画 */
.slide-enter-active,
.slide-leave-active {
  transition: all 0.3s ease;
}

.slide-enter-from {
  opacity: 0;
  transform: translateX(100px);
}

.slide-leave-to {
  opacity: 0;
  transform: translateX(100px);
}
</style>