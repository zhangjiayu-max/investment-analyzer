<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'action'])

const notifications = ref([])
const unreadCount = ref(0)
let eventSource = null

const isOpen = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

// 兼容 ISO 字符串与旧数字时间戳（秒/毫秒）
function parseTs(ts) {
  if (!ts) return 0
  if (typeof ts === 'number') return ts < 1e12 ? ts * 1000 : ts
  if (typeof ts === 'string') {
    const d = new Date(ts)
    return isNaN(d.getTime()) ? 0 : d.getTime()
  }
  return 0
}

const sortedNotifications = computed(() => {
  return [...notifications.value].sort((a, b) => parseTs(b.timestamp) - parseTs(a.timestamp))
})

function connectNotificationStream() {
  if (eventSource) {
    eventSource.close()
  }

  const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:'
  const host = window.location.host
  eventSource = new EventSource(`${protocol}//${host}/api/notifications/stream`)

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      notifications.value.unshift(data)
      unreadCount.value++
      if (notifications.value.length > 50) {
        notifications.value = notifications.value.slice(0, 50)
      }
    } catch (e) {
      console.error('Notification parse error:', e)
    }
  }

  eventSource.onerror = () => {
    console.warn('Notification stream error, reconnecting...')
    setTimeout(connectNotificationStream, 5000)
  }
}

function markAsRead(notification) {
  notification.read = true
  if (unreadCount.value > 0) {
    unreadCount.value--
  }
}

function markAllAsRead() {
  notifications.value.forEach(n => {
    n.read = true
  })
  unreadCount.value = 0
}

function getNotificationIcon(n) {
  const cat = n.category || n.type
  const icons = {
    info: 'ℹ️',
    warning: '⚠️',
    error: '❌',
    success: '✅',
    valuation: '📊',
    strategy: '🎯',
    watchlist_signal_change: '🔔',
    alert: '🚨',
    news: '📰',
    decision: '🧭',
    system: '⚙️',
  }
  return icons[cat] || '📌'
}

function formatTime(ts) {
  const ms = parseTs(ts)
  if (!ms) return ''
  return new Date(ms).toLocaleString()
}

function onNotificationClick(n) {
  markAsRead(n)
  // P1-7: 携带 action_url 时上抛动作，由父组件决定跳转
  if (n.data?.action_url) {
    emit('action', n.data.action_url)
  }
}

function snapshotEntries(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return []
  return Object.entries(snapshot).slice(0, 3)
}

onMounted(() => {
  connectNotificationStream()
})

onUnmounted(() => {
  if (eventSource) {
    eventSource.close()
  }
})
</script>

<template>
  <Transition name="slide-up">
    <div v-if="isOpen" class="notification-overlay" @click.self="isOpen = false">
      <div class="notification-panel">
        <div class="notification-header">
          <div class="notification-title">
            <span class="editorial-title">通知中心</span>
            <span v-if="unreadCount > 0" class="unread-badge">{{ unreadCount }}</span>
          </div>
          <div class="notification-actions">
            <button v-if="unreadCount > 0" class="btn-ghost btn-sm" @click="markAllAsRead">全部已读</button>
            <button class="btn-ghost btn-sm" @click="isOpen = false">关闭</button>
          </div>
        </div>

        <div class="notification-list">
          <div v-if="notifications.length === 0" class="notification-empty">
            <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
            </svg>
            <p>暂无通知</p>
            <p class="hint">估值越限、策略触发等提醒会在这里显示</p>
          </div>

          <div
            v-for="(notification, index) in sortedNotifications"
            :key="index"
            :class="['notification-item', { unread: !notification.read }]"
            @click="onNotificationClick(notification)"
          >
            <span class="notification-icon">{{ getNotificationIcon(notification) }}</span>
            <div class="notification-content">
              <div class="notification-title-text">{{ notification.title }}</div>
              <div class="notification-message">{{ notification.message }}</div>
              <div v-if="notification.data?.news_summary" class="notification-news">📰 {{ notification.data.news_summary }}</div>
              <div v-if="snapshotEntries(notification.data?.snapshot).length" class="notification-snapshot">
                <span v-for="([k, v]) in snapshotEntries(notification.data.snapshot)" :key="k" class="snapshot-item">
                  <span class="snapshot-key">{{ k }}</span>: {{ v }}
                </span>
              </div>
              <div class="notification-meta">
                <span>{{ formatTime(notification.timestamp) }}</span>
                <span v-if="notification.category || notification.type" :class="['type-tag', `type-${notification.category || notification.type}`]">{{ notification.category || notification.type }}</span>
                <span v-if="notification.data?.fund_name" class="fund-name">· {{ notification.data.fund_name }}</span>
                <span v-if="notification.data?.action_url" class="action-hint">查看 →</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.notification-overlay {
  position: fixed;
  inset: 0;
  background: var(--color-overlay);
  z-index: 70;
  display: flex;
  align-items: flex-end;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

.notification-panel {
  width: 100%;
  max-height: 70vh;
  background: var(--color-bg-card);
  border-radius: var(--radius-2xl) var(--radius-2xl) 0 0;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  padding-bottom: env(safe-area-inset-bottom, 0);
}

.notification-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.notification-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.unread-badge {
  min-width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: var(--color-loss);
  color: white;
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0 5px;
}

.notification-actions {
  display: flex;
  gap: 0.5rem;
}

.notification-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
  -webkit-overflow-scrolling: touch;
}

.notification-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem 1rem;
  color: var(--color-text-muted);
}

.notification-empty svg {
  color: var(--color-text-tertiary);
  margin-bottom: 1rem;
}

.notification-empty p {
  margin: 0.25rem 0;
}

.notification-empty .hint {
  font-size: 0.8rem;
  color: var(--color-text-tertiary);
}

.notification-item {
  display: flex;
  gap: 0.75rem;
  padding: 0.75rem;
  border-radius: var(--radius-lg);
  margin-bottom: 0.25rem;
  background: var(--color-bg-input);
  transition: all var(--transition-fast);
  cursor: pointer;
}

.notification-item.unread {
  background: var(--color-primary-bg);
}

.notification-item:active {
  transform: scale(0.98);
}

.notification-icon {
  font-size: 1.25rem;
  flex-shrink: 0;
}

.notification-content {
  flex: 1;
  min-width: 0;
}

.notification-title-text {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 0.2rem;
}

.notification-message {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.3rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.notification-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
}

.type-tag {
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  font-size: 0.65rem;
  font-weight: 500;
}

.type-info { background: var(--color-info-bg); color: var(--color-info); }
.type-warning { background: var(--color-warning-bg); color: var(--color-warning); }
.type-error { background: var(--color-loss-bg); color: var(--color-loss); }
.type-success { background: var(--color-profit-bg); color: var(--color-profit); }
.type-valuation { background: var(--color-gold-bg); color: var(--color-gold); }
.type-strategy { background: var(--color-primary-bg); color: var(--color-primary); }
.type-watchlist_signal_change { background: var(--color-gold-bg); color: var(--color-gold); }
.type-alert { background: var(--color-loss-bg); color: var(--color-loss); }
.type-news { background: var(--color-primary-bg); color: var(--color-primary); }
.type-decision { background: var(--color-info-bg); color: var(--color-info); }
.type-system { background: var(--color-bg-hover, rgba(255,255,255,0.08)); color: var(--color-text-muted); }

.notification-news {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  background: var(--color-bg-hover, rgba(255,255,255,0.04));
  border-left: 2px solid var(--color-primary);
  padding: 0.3rem 0.5rem;
  border-radius: var(--radius-sm, 4px);
  margin-bottom: 0.3rem;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.notification-snapshot {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.3rem;
}

.snapshot-item {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: var(--color-bg-hover, rgba(255,255,255,0.05));
  color: var(--color-text-secondary);
}

.snapshot-key {
  color: var(--color-text-tertiary);
}

.fund-name {
  font-style: italic;
}

.action-hint {
  margin-left: auto;
  color: var(--color-primary);
  font-weight: 600;
  font-size: 0.7rem;
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
