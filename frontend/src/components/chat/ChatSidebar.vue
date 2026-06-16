<script setup>
import { ref } from 'vue'

const props = defineProps({
  conversations: { type: Array, default: () => [] },
  selectedConv: { type: Object, default: null },
  streamStates: { type: Object, required: true },
  isTaskRunning: { type: Function, required: true },
  showMobileSidebar: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'new', 'delete', 'update:showMobileSidebar'])

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const time = `${hh}:${mm}`
  if (isToday) return time
  return `${d.getMonth() + 1}/${d.getDate()} ${time}`
}

function handleSelect(conv) {
  emit('select', conv)
}

function handleNew() {
  emit('new')
}

function handleDelete(conv, e) {
  e.stopPropagation()
  emit('delete', conv)
}

function closeMobileSidebar() {
  emit('update:showMobileSidebar', false)
}
</script>

<template>
  <!-- 移动端遮罩 -->
  <div v-if="showMobileSidebar" class="mobile-sidebar-overlay" @click="closeMobileSidebar"></div>
  <!-- 对话列表 -->
  <div :class="['conv-sidebar', { 'mobile-open': showMobileSidebar }]">
    <div class="conv-header">
      <h3>对话</h3>
      <button @click="handleNew()" class="btn-new-conv" title="新建对话">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
        </svg>
      </button>
    </div>
    <div class="conv-list">
      <div
        v-for="conv in conversations" :key="conv.id"
        @click="handleSelect(conv)"
        :class="['conv-item', { active: selectedConv?.id === conv.id, streaming: streamStates.has(conv.id) || isTaskRunning(conv.id) }]"
      >
        <div class="conv-icon">
          <span v-if="streamStates.has(conv.id) || isTaskRunning(conv.id)" class="conv-streaming-indicator">●</span>
          <span v-else>🤖</span>
        </div>
        <div class="conv-info">
          <div class="conv-title">{{ conv.title }}</div>
          <div class="conv-meta">
            <span v-if="isTaskRunning(conv.id)" class="conv-task-status">后台执行中...</span>
            <span class="conv-time">{{ formatTime(conv.updated_at) }}</span>
          </div>
        </div>
        <button @click="handleDelete(conv, $event)" class="btn-delete-conv" title="删除">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>
      <div v-if="!conversations.length" class="conv-empty">
        <p>暂无对话</p>
        <p class="conv-empty-hint">点击右上角 + 创建</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ── 对话侧边栏 ── */
.conv-sidebar {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--color-border);
  background: var(--color-bg-input);
}

.conv-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.conv-header h3 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
}

.btn-new-conv {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--color-primary-600);
  background: var(--color-primary-50);
  transition: all var(--transition-fast);
}

.btn-new-conv:hover {
  background: var(--color-primary-100);
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.7rem 0.6rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.conv-item:hover {
  background: var(--color-bg-hover);
}

.conv-item.active {
  background: var(--color-primary-50);
}

.dark .conv-item.active {
  background: var(--color-primary-bg);
}

.conv-icon {
  font-size: 1.3rem;
  flex-shrink: 0;
  position: relative;
}

.conv-item.streaming {
  background: var(--color-primary-50);
}

.conv-streaming-indicator {
  color: var(--color-primary);
  animation: pulse-streaming 1.5s ease-in-out infinite;
  font-size: 1rem;
}

@keyframes pulse-streaming {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.conv-info {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-meta {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.15rem;
}

.conv-time {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

.conv-task-status {
  font-size: 0.65rem;
  color: var(--color-primary-600);
  background: var(--color-primary-50);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-sm);
  animation: pulse-streaming 1.5s ease-in-out infinite;
}

.btn-delete-conv {
  opacity: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.conv-item:hover .btn-delete-conv {
  opacity: 1;
}

.btn-delete-conv:hover {
  color: var(--color-danger);
  background: rgba(239, 68, 68, 0.1);
}

.conv-empty {
  padding: 2rem 1rem;
  text-align: center;
  color: var(--color-text-muted);
}

.conv-empty p { margin: 0; font-size: 0.8rem; }
.conv-empty-hint { font-size: 0.7rem; margin-top: 0.3rem !important; }

.mobile-sidebar-overlay {
  display: block;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  z-index: 49;
}

@media (max-width: 768px) {
  .mobile-sidebar-overlay {
    display: block;
  }

  .conv-sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: 280px;
    max-width: 80vw;
    z-index: 50;
    transform: translateX(-100%);
    transition: transform 0.25s ease;
  }
  .conv-sidebar.mobile-open {
    transform: translateX(0);
  }

  .btn-new-conv {
    width: 44px;
    height: 44px;
  }

  .btn-delete-conv {
    min-width: 44px;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .conv-item {
    padding: 0.8rem 0.6rem;
    min-height: 48px;
  }
}
</style>
