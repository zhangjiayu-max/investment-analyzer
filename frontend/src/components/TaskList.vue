<script setup>
import { ref, onMounted } from 'vue'
import { listTasks, deleteTask } from '../api'
import ConfirmDialog from './ConfirmDialog.vue'

const emit = defineEmits(['select', 'newTask'])
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })
const tasks = ref([])
const loading = ref(false)

async function loadTasks() {
  loading.value = true
  try {
    const { data } = await listTasks()
    tasks.value = data.tasks || []
  } catch (e) {
    console.error('Load tasks failed:', e)
  } finally {
    loading.value = false
  }
}

function onDelete(taskId, e) {
  e.stopPropagation()
  confirm.value = {
    visible: true,
    title: '删除确认',
    message: '确定删除这个任务？',
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      await deleteTask(taskId)
      tasks.value = tasks.value.filter(t => t.id !== taskId)
    }
  }
}

function statusText(status) {
  return { pending: '等待中', fetching: '抓取中', analyzing: '分析中', done: '已完成', error: '失败' }[status] || status
}

function statusClass(status) {
  return {
    pending: 'badge-warning',
    fetching: 'badge-info',
    analyzing: 'badge-info',
    done: 'badge-success',
    error: 'badge-danger',
  }[status] || 'badge-neutral'
}

defineExpose({ loadTasks })
onMounted(loadTasks)
</script>

<template>
  <div class="task-list-wrap">
    <div class="task-list-header">
      <h2 class="task-list-title">任务历史</h2>
      <button @click="$emit('newTask')" class="btn-ghost new-task-btn">+ 新任务</button>
    </div>

    <div class="task-list-scroll">
      <div v-if="loading" class="task-empty">加载中...</div>
      <div v-else-if="tasks.length === 0" class="task-empty">暂无任务</div>

      <div
        v-for="task in tasks"
        :key="task.id"
        @click="$emit('select', task.id)"
        class="task-item"
      >
        <div class="task-icon">
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
          </svg>
        </div>
        <div class="task-info">
          <div class="task-name">{{ task.title || '分析中...' }}</div>
          <div class="task-time">{{ task.created_at }}</div>
        </div>
        <div class="task-actions">
          <span :class="['badge', statusClass(task.status)]">{{ statusText(task.status) }}</span>
          <button @click="onDelete(task.id, $event)" class="delete-btn" title="删除">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
  <ConfirmDialog
    :visible="confirm.visible"
    :title="confirm.title"
    :message="confirm.message"
    :danger="confirm.danger"
    @confirm="() => confirm.onConfirm?.()"
    @cancel="confirm.visible = false"
  />
</template>

<style scoped>
.task-list-wrap {
  padding: 0.75rem 0;
}

.task-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1.25rem 0.75rem;
}

.task-list-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.new-task-btn {
  font-size: 0.75rem;
  padding: 0.3rem 0.6rem;
  color: var(--color-primary-600);
}

.dark .new-task-btn {
  color: var(--color-primary-400);
}

.task-list-scroll {
  max-height: 320px;
  overflow-y: auto;
}

.task-empty {
  padding: 2rem;
  text-align: center;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.task-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1.25rem;
  cursor: pointer;
  transition: background var(--transition-fast);
  border-bottom: 1px solid var(--color-border-light);
}

.task-item:last-child {
  border-bottom: none;
}

.task-item:hover {
  background: var(--color-bg-hover);
}

.task-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.task-info {
  flex: 1;
  min-width: 0;
}

.task-name {
  font-size: 0.825rem;
  font-weight: 500;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-time {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  margin-top: 0.15rem;
}

.task-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.delete-btn {
  opacity: 0;
  color: var(--color-text-muted);
  padding: 0.25rem;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
}

.task-item:hover .delete-btn {
  opacity: 1;
}

.delete-btn:hover {
  color: var(--color-danger);
  background: rgba(239, 68, 68, 0.1);
}

/* 移动端：始终显示删除按钮 */
@media (max-width: 768px) {
  .delete-btn {
    opacity: 0.7;
    padding: 0.5rem;
    min-width: 44px;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
}
</style>
