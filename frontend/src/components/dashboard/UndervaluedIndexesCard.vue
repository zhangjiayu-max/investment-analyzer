<script setup>
import EmptyState from '../ui/EmptyState.vue'
import { getPercentileColor, assessmentColors } from '../../composables/useDashboardHelpers'

const props = defineProps({
  undervaluedIndexes: { type: Array, default: () => [] },
  undervaluedUpdatedAt: { type: String, default: '' },
  dataDate: { type: String, default: '' },
  fetchingValuation: { type: Boolean, default: false },
  count: { type: Number, default: 0 },
})

const emit = defineEmits(['refresh', 'navigate'])
</script>

<template>
  <div class="dash-card card">
    <div class="card-header">
      <div class="card-title-row">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
        </svg>
        <span>今日低估指数</span>
      </div>
      <div class="card-header-actions">
        <span class="card-data-time">{{ undervaluedUpdatedAt || dataDate || '' }}</span>
        <span v-if="undervaluedIndexes.length" class="card-badge">{{ count || undervaluedIndexes.length }}只</span>
        <button
          class="btn-ai-action btn-card-refresh"
          :class="{ 'btn-loading': fetchingValuation }"
          :disabled="fetchingValuation"
          @click="emit('refresh')"
        >
          <svg :class="['icon-spin', { 'spinning': fetchingValuation }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          <span>{{ fetchingValuation ? '抓取中...' : '刷新' }}</span>
        </button>
      </div>
    </div>
    <div v-if="!undervaluedIndexes.length" class="card-empty">
      <EmptyState
        icon="chart"
        title="暂无低估指数数据"
        description="通过文章导入估值数据后自动展示"
        action-text="去导入"
        @action="emit('navigate', 'articles')"
      />
    </div>
    <div v-else class="card-body">
      <!-- 摘要指标条 -->
      <div class="health-metrics">
        <div class="metric-item">
          <span class="metric-label">低估数量</span>
          <span class="metric-value">{{ undervaluedIndexes.length }} 只</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">最低百分位</span>
          <span class="metric-value" :style="{ color: getPercentileColor(undervaluedIndexes[0]?.percentile) }">
            {{ undervaluedIndexes[0]?.percentile }}%
          </span>
        </div>
        <div class="metric-item">
          <span class="metric-label">极度低估</span>
          <span class="metric-value" style="color: #dc2626">
            {{ undervaluedIndexes.filter(i => i.assessment_level === 'extreme').length }} 只
          </span>
        </div>
      </div>

      <div v-for="idx in undervaluedIndexes.slice(0, 10)" :key="idx.index_code" class="index-row" @click="emit('navigate', 'valuation')">
        <div class="index-info">
          <span class="index-name">{{ idx.index_name || idx.index_code }}</span>
          <span class="index-meta">{{ idx.metric_type }} {{ idx.current_value }}</span>
        </div>
        <div class="index-percentile">
          <div class="percentile-bar-bg">
            <div class="percentile-bar" :style="{ width: idx.percentile + '%', background: getPercentileColor(idx.percentile) }"></div>
            <div class="percentile-marks">
              <span class="mark" style="left:20%">20</span>
              <span class="mark" style="left:40%">40</span>
              <span class="mark" style="left:60%">60</span>
              <span class="mark" style="left:80%">80</span>
            </div>
          </div>
          <div class="percentile-value" :style="{ color: getPercentileColor(idx.percentile) }">
            {{ idx.percentile }}%
          </div>
        </div>
        <span class="assessment-tag" :style="{ background: (assessmentColors[idx.assessment_level] || assessmentColors.fair).bg + '18', color: (assessmentColors[idx.assessment_level] || assessmentColors.fair).bg }">
          {{ idx.assessment }}
        </span>
      </div>
      <button class="btn-link" @click="emit('navigate', 'valuation')">查看全部估值 →</button>
    </div>
  </div>
</template>

<style scoped>
.dash-card {
  padding: 1.25rem 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.3s cubic-bezier(0.4, 0, 0.2, 1), border-color 0.3s ease;
  position: relative;
  overflow: hidden;
  min-height: 420px;
  max-height: 540px;
}
.dash-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
  opacity: 0;
  transition: opacity 0.3s ease;
}
.dash-card:hover {
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4), 0 0 24px var(--color-primary-glow);
  border-color: var(--color-primary-border);
}
.dash-card:hover::before {
  opacity: 1;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.card-title-row {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  font-weight: 700;
  font-size: 1.05rem;
  color: var(--color-text-primary);
  letter-spacing: -0.01em;
}
.card-icon {
  color: var(--color-primary);
  flex-shrink: 0;
  width: 20px;
  height: 20px;
}
.card-badge {
  font-size: 0.72rem;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  background: var(--color-primary-50);
  color: var(--color-primary);
  font-weight: 700;
  border: 1px solid var(--color-primary-border);
}
.card-body {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.card-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2.5rem 0;
  text-align: center;
  color: var(--color-text-muted);
  font-size: 0.9rem;
}
.card-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.btn-card-refresh {
  padding: 0.3rem 0.65rem;
  font-size: 0.75rem;
}
.btn-ai-action {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.85rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-primary);
  background: linear-gradient(135deg, var(--color-primary-bg), var(--color-primary-bg-gradient-end));
  border: 1px solid var(--color-primary-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
}
.btn-ai-action:hover {
  background: linear-gradient(135deg, var(--color-primary-bg-strong), var(--color-primary-bg-hover));
  border-color: var(--color-primary-border-strong);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px var(--color-primary-bg-strong);
}
.btn-ai-action:active {
  transform: translateY(0);
  box-shadow: none;
}
.btn-ai-action:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}
.btn-ai-action.btn-loading {
  background: linear-gradient(135deg, var(--color-primary-bg-gradient-end), var(--color-primary-bg-weak));
  border-color: var(--color-primary-bg-strong);
}
.icon-spin {
  transition: transform 0.3s ease;
}
.icon-spin.spinning {
  animation: spin 1s linear infinite;
}
.card-data-time {
  font-size: 0.6rem;
  color: var(--color-text-muted);
  font-weight: 400;
  margin-left: 0.25rem;
  opacity: 0.7;
}
.health-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0.75rem;
  padding: 0.75rem;
  background: linear-gradient(135deg, var(--color-bg-card), var(--color-bg-card));
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
}
.metric-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.metric-label {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  font-weight: 600;
}
.metric-value {
  font-size: 1.1rem;
  font-weight: 800;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
}
.index-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.5rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.index-row:hover {
  background: var(--color-bg-hover);
}
.index-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 0 0 auto;
  width: 105px;
}
.index-name {
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.index-meta {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  font-weight: 500;
}
.index-percentile {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex: 1;
  min-width: 0;
}
.percentile-bar-bg {
  flex: 1;
  height: 8px;
  background: var(--color-bg-input);
  border-radius: 4px;
  overflow: visible;
  min-width: 50px;
  position: relative;
}
.percentile-bar {
  height: 100%;
  border-radius: 4px;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}
.percentile-marks {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
}
.percentile-marks .mark {
  position: absolute;
  top: -14px;
  transform: translateX(-50%);
  font-size: 0.6rem;
  color: var(--color-text-tertiary);
  opacity: 0.5;
}
.percentile-marks .mark::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 100%;
  width: 1px;
  height: 4px;
  background: var(--color-text-tertiary);
  opacity: 0.3;
}
.percentile-value {
  font-size: 0.88rem;
  font-weight: 800;
  width: 42px;
  text-align: right;
  flex-shrink: 0;
}
.assessment-tag {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.2rem 0.55rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: 0.03em;
}
.btn-link {
  background: none;
  border: none;
  color: var(--color-primary);
  font-size: 0.88rem;
  font-weight: 600;
  cursor: pointer;
  padding: 0.4rem 0;
  text-align: left;
  align-self: flex-start;
  transition: all 0.2s ease;
}
.btn-link:hover {
  color: var(--color-primary-dark);
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (max-width: 768px) {
  .health-metrics {
    grid-template-columns: 1fr 1fr;
  }
  .index-row {
    flex-wrap: wrap;
  }
  .index-info {
    width: auto;
    flex: 0 0 100%;
  }
  .index-percentile {
    width: 100%;
  }
}
</style>