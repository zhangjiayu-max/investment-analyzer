<script setup>
import { ref, onMounted, onActivated, onDeactivated, computed } from 'vue'
import { listAlerts, markAlertRead } from '../../api'

const emit = defineEmits(['navigate'])

const loading = ref(true)
const alerts = ref([])
const unreadCount = ref(0)

// severity 排序权重
const severityOrder = { danger: 0, warning: 1, info: 2 }
// 展示标签映射
const alertTypeLabels = {
  buy_drop_alert: '大跌机会',
  concentration_alert: '集中度过高',
  concentration_high: '集中度过高',
  loss_warning: '亏损预警',
  valuation_high: '估值偏高',
  valuation_low: '估值偏低',
  daily_advice_signal: '持仓信号',
  news_impact: '新闻影响',
  recommendation_verified: '推荐验证',
  watchlist_trigger: '观察命中',
}

const sortedAlerts = computed(() => {
  return [...alerts.value]
    .sort((a, b) => {
      const sa = severityOrder[a.severity] ?? 3
      const sb = severityOrder[b.severity] ?? 3
      if (sa !== sb) return sa - sb
      // 同 severity 按时间倒序
      return (b.created_at || '').localeCompare(a.created_at || '')
    })
    .slice(0, 5) // 最多展示 5 条
})

async function loadData() {
  try {
    const { data } = await listAlerts(true, 20) // unread_only=true, limit=20
    alerts.value = data.alerts || []
    unreadCount.value = alerts.value.length
  } catch (e) {
    console.error('[AlertsCard] 加载预警失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleMarkRead(alert) {
  try {
    await markAlertRead(alert.id)
    alerts.value = alerts.value.filter(a => a.id !== alert.id)
    unreadCount.value = alerts.value.length
  } catch (e) {
    console.error('[AlertsCard] 标记已读失败:', e)
  }
}

function getLabel(alert) {
  return alertTypeLabels[alert.alert_type] || alert.alert_type || '提示'
}

function severityClass(s) {
  return `sev-${s || 'info'}`
}

function fmtTime(t) {
  if (!t) return ''
  // 简化：只显示时间部分
  const today = new Date().toISOString().slice(0, 10)
  if (t.startsWith(today)) return t.slice(11, 16)
  return t.slice(5, 16)
}

onMounted(loadData)
onActivated(loadData)
onDeactivated(() => { /* KeepAlive 切走时保留数据，不清理 */ })
</script>

<template>
  <div class="dash-card card editorial-card alerts-card">
    <div class="card-header editorial-card-header">
      <div class="card-title-row">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"/>
        </svg>
        <span class="title editorial-title">持仓预警</span>
        <span v-if="unreadCount" class="alert-badge font-jet">{{ unreadCount }}</span>
      </div>
      <div class="card-header-actions">
        <button class="btn-text-link" @click="emit('navigate', 'portfolio')">全部</button>
      </div>
    </div>

    <!-- 加载骨架 -->
    <div v-if="loading" class="card-body">
      <div class="skeleton-row" v-for="i in 3" :key="i"></div>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!sortedAlerts.length" class="card-body">
      <div class="alerts-empty">
        <svg width="28" height="28" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="opacity:0.3">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <p class="terminal-label" style="opacity:0.5">暂无未读预警</p>
        <p class="meta" style="font-size:11px;opacity:0.4">每 30 分钟自动扫描</p>
      </div>
    </div>

    <!-- 预警列表 -->
    <div v-else class="card-body">
      <div
        v-for="alert in sortedAlerts"
        :key="alert.id"
        class="alert-row"
        :class="severityClass(alert.severity)"
      >
        <div class="alert-main">
          <div class="alert-title-row">
            <span class="alert-tag" :class="severityClass(alert.severity)">{{ getLabel(alert) }}</span>
            <span v-if="alert.related_fund_name" class="alert-fund-name">{{ alert.related_fund_name }}</span>
          </div>
          <div v-if="alert.title" class="alert-content">{{ alert.title }}</div>
          <div class="alert-meta">
            <span class="alert-time meta">{{ fmtTime(alert.created_at) }}</span>
          </div>
        </div>
        <button class="btn-mark-read" @click="handleMarkRead(alert)" title="标记已读">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.5 12.75l6 6 9-13.5"/>
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.alerts-card .card-body { padding: 0 1rem 1rem; }

.alert-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 10px;
  background: var(--color-accent);
  color: var(--color-bg);
  font-size: 0.7rem;
  font-weight: 600;
  margin-left: 0.4rem;
}

.btn-text-link {
  background: none;
  border: none;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  cursor: pointer;
  padding: 2px 6px;
  transition: color 0.2s;
}
.btn-text-link:hover { color: var(--color-accent); }

/* 预警行 */
.alert-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.6rem 0.5rem 0.6rem 0.75rem;
  border-bottom: 1px solid var(--color-border-soft);
  border-left: 3px solid var(--color-border);
  transition: background 0.15s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: var(--color-bg-hover); }

.alert-row.sev-danger { border-left-color: var(--color-down); }
.alert-row.sev-warning { border-left-color: #f0a020; }
.alert-row.sev-info { border-left-color: var(--color-text-muted); }

.alert-main { flex: 1; min-width: 0; }
.alert-title-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.15rem;
}
.alert-tag {
  font-size: 0.65rem;
  padding: 1px 6px;
  border-radius: 3px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.alert-tag.sev-danger { background: rgba(220, 53, 69, 0.15); color: var(--color-down); }
.alert-tag.sev-warning { background: rgba(240, 160, 32, 0.15); color: #c08010; }
.alert-tag.sev-info { background: var(--color-bg-hover); color: var(--color-text-muted); }

.alert-fund-name { font-size: 0.78rem; font-weight: 500; }
.alert-content {
  font-size: 0.75rem;
  opacity: 0.75;
  line-height: 1.4;
  margin-bottom: 0.2rem;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.alert-meta { display: flex; align-items: center; gap: 0.5rem; }
.alert-time { font-size: 0.65rem; opacity: 0.5; }

.btn-mark-read {
  flex: 0 0 auto;
  background: none;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: all 0.15s;
}
.btn-mark-read:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-bg-hover);
}

/* 空状态 */
.alerts-empty {
  text-align: center;
  padding: 1.5rem 0.5rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.3rem;
}

/* 骨架屏 */
.skeleton-row {
  height: 52px;
  border-radius: 4px;
  background: var(--color-skeleton);
  margin-bottom: 0.5rem;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: 0.6; } 50% { opacity: 0.3; } }

/* 移动端 */
@media (max-width: 768px) {
  .alert-row { padding: 0.5rem 0.4rem 0.5rem 0.6rem; }
  .alert-content { -webkit-line-clamp: 1; }
}
</style>
