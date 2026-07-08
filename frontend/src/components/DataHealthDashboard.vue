<template>
  <div class="data-health bg-mesh">
    <div class="dh-header">
      <h2 class="editorial-title-lg">数据健康监控</h2>
      <p class="dh-subtitle editorial-subtitle">各数据源的最新状态与新鲜度</p>
      <div class="dh-summary">
        <span class="dot dot-green" /> <span class="font-jet">{{ greenCount }}</span> 正常
        <span class="dot dot-yellow" /> <span class="font-jet">{{ yellowCount }}</span> 注意
        <span class="dot dot-red" /> <span class="font-jet">{{ redCount }}</span> 过期
      </div>
    </div>

    <div v-if="loading" class="dh-grid">
      <div v-for="i in 8" :key="i" class="dh-card skeleton" />
    </div>

    <div v-else-if="error" class="dh-error">
      {{ error }}
    </div>

    <div v-else class="dh-grid">
      <div
        v-for="(item, key) in items"
        :key="key"
        class="dh-card editorial-card reveal-stagger"
        :class="statusClass(item.stale_days)"
      >
        <div class="dh-card-header">
          <span class="dh-card-name">{{ item.label }}</span>
        </div>

        <div class="dh-card-body">
          <div class="dh-stat">
            <span class="dh-stat-label terminal-label">记录数</span>
            <span class="dh-stat-value font-jet">{{ formatCount(item.count) }}</span>
          </div>
          <div class="dh-stat">
            <span class="dh-stat-label terminal-label">最后更新</span>
            <span class="dh-stat-value font-jet">{{ formatDate(item.latest) }}</span>
          </div>
          <div class="dh-stat" v-if="item.stale_days !== undefined">
            <span class="dh-stat-label terminal-label">距今</span>
            <span class="dh-stat-value font-jet" :class="statusClass(item.stale_days)">
              {{ formatDays(item.stale_days) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getDataHealth } from '../api'

const loading = ref(true)
const error = ref('')
const items = ref({})

onMounted(async () => {
  try {
    const res = await getDataHealth()
    items.value = res.health || {}
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
})

const greenCount = computed(() => Object.values(items.value).filter(i => i.stale_days < 3).length)
const yellowCount = computed(() => Object.values(items.value).filter(i => i.stale_days >= 3 && i.stale_days < 7).length)
const redCount = computed(() => Object.values(items.value).filter(i => i.stale_days >= 7).length)

function statusClass(days) {
  if (days === undefined || days === null) return 'status-gray'
  if (days < 3) return 'status-green'
  if (days < 7) return 'status-yellow'
  return 'status-red'
}

function formatCount(n) {
  if (!n) return '0'
  return n.toLocaleString()
}

function formatDate(str) {
  if (!str) return '暂无数据'
  const s = String(str).slice(0, 16).replace('T', ' ')
  return s
}

function formatDays(days) {
  if (days === undefined || days === null) return '-'
  if (days >= 999) return '暂无数据'
  if (days === 0) return '今天'
  if (days === 1) return '昨天'
  return `${days} 天前`
}
</script>

<style scoped>
.data-health {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.dh-header {
  margin-bottom: 24px;
}

.dh-header h2 {
  margin: 0 0 4px 0;
  font-size: inherit;
  font-weight: inherit;
}

.dh-subtitle {
  margin: 0 0 12px 0;
  color: var(--color-text-muted, #888);
  font-size: inherit;
  font-weight: inherit;
}

.dh-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 0.85rem;
  color: var(--color-text-muted, #666);
}

.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 4px;
}

.dot-green { background: #22c55e; }
.dot-yellow { background: #eab308; }
.dot-red { background: #ef4444; }

.dh-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}

.dh-card {
  background: var(--color-bg-card, #fff);
  border-radius: 12px;
  padding: 20px;
  border-left: 4px solid #ccc;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  transition: transform 0.15s, box-shadow 0.15s;
}

.dh-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.dh-card.status-green { border-left-color: #22c55e; }
.dh-card.status-yellow { border-left-color: #eab308; }
.dh-card.status-red { border-left-color: #ef4444; }
.dh-card.status-gray { border-left-color: #94a3b8; }

.dh-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.dh-card-icon {
  font-size: 1.2rem;
}

.dh-card-name {
  font-weight: 600;
  font-size: 1rem;
}

.dh-card-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dh-stat {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.85rem;
}

.dh-stat-label {
  color: var(--color-text-muted, #888);
}

.dh-stat-value {
  font-weight: 500;
  font-variant-numeric: inherit;
}

.status-green { color: #16a34a; }
.status-yellow { color: #ca8a04; }
.status-red { color: #dc2626; }
.status-gray { color: #94a3b8; }

.dh-card.status-green .dh-card-name { color: #16a34a; }
.dh-card.status-yellow .dh-card-name { color: #ca8a04; }
.dh-card.status-red .dh-card-name { color: #dc2626; }

.dh-error {
  text-align: center;
  padding: 48px;
  color: #dc2626;
  font-size: 1rem;
}

/* skeleton loading */
.skeleton {
  min-height: 140px;
  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  border-left-color: #e5e7eb !important;
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.dark .skeleton {
  background: linear-gradient(90deg, #2a2a2a 25%, #333 50%, #2a2a2a 75%);
  background-size: 200% 100%;
}

@media (max-width: 640px) {
  .data-health { padding: 16px; }
  .dh-grid { grid-template-columns: 1fr; }
  .dh-summary { flex-wrap: wrap; gap: 8px; }
}
</style>
