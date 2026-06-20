<script setup>
import EmptyState from '../ui/EmptyState.vue'
import Icon from '../ui/Icon.vue'
import AIActionButton from '../ui/AIActionButton.vue'
import AgentResultCard from '../ui/AgentResultCard.vue'
import { formatMoney, concentrationColor, concentrationIcon } from '../../composables/useDashboardHelpers'

const props = defineProps({
  portfolioHealth: { type: Object, default: null },
  portfolioUpdatedAt: { type: String, default: '' },
  rebalanceLoading: { type: Boolean, default: false },
  rebalanceResult: { type: Object, default: null },
  showRebalance: { type: Boolean, default: false },
  panoramaLoading: { type: Boolean, default: false },
  panoramaResult: { type: Object, default: null },
})

const emit = defineEmits(['rebalance', 'panorama', 'navigate'])

const categoryLabels = {
  equity: '股票型', bond: '债券型', index: '指数型',
  hybrid: '混合型', money: '货币型', qdii: 'QDII', cash: '现金',
}

function allocationValue(allocation, category) {
  return (allocation && allocation[category]) || 0
}
</script>

<template>
  <div class="dash-card card">
    <div class="card-header">
      <div class="card-title-row">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M2 7l10-4 10 4-10 4-10-4zM2 17l10 4 10-4M2 12l10 4 10-4"/>
        </svg>
        <span>持仓健康度</span>
      </div>
      <div class="card-header-actions">
        <span v-if="portfolioHealth" class="card-data-time">{{ portfolioUpdatedAt || '' }}</span>
        <AIActionButton
          v-if="portfolioHealth"
          :label="rebalanceResult ? '重新生成' : 'AI 再平衡建议'"
          agent="全景诊断分析师"
          icon="refresh"
          variant="soft"
          size="sm"
          :loading="rebalanceLoading"
          @click="emit('rebalance')"
        />
        <AIActionButton
          v-if="portfolioHealth"
          label="AI 全景诊断"
          agent="全景诊断分析师"
          icon="bot"
          variant="primary"
          size="sm"
          :loading="panoramaLoading"
          @click="emit('panorama')"
        />
      </div>
    </div>
    <div v-if="!portfolioHealth" class="card-empty">
      <EmptyState
        icon="briefcase"
        title="暂无持仓数据"
        description="在持仓管理中添加基金后自动分析"
        action-text="去添加"
        @action="emit('navigate', 'portfolio')"
      />
    </div>
    <div v-else class="card-body">
      <div class="health-metrics">
        <div class="metric-item">
          <span class="metric-label">持有基金</span>
          <span class="metric-value">{{ portfolioHealth.holding_count }} 只</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">总市值</span>
          <span class="metric-value">{{ formatMoney(portfolioHealth.total_value) }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">总盈亏</span>
          <span :class="['metric-value', (portfolioHealth.total_profit || 0) >= 0 ? 'profit' : 'loss']">
            {{ formatMoney(portfolioHealth.total_profit) }}
            ({{ (portfolioHealth.profit_rate * 100).toFixed(1) }}%)
          </span>
        </div>
      </div>

      <!-- 集中度 -->
      <div class="concentration-row">
        <span class="concentration-icon"><Icon :name="concentrationIcon[portfolioHealth.concentration_level] || 'shield-check'" size="14" /></span>
        <div>
          <div class="concentration-text">{{ portfolioHealth.concentration_assessment }}</div>
          <div class="concentration-bar-bg">
            <div
              class="concentration-bar"
              :style="{ width: Math.min(portfolioHealth.top3_concentration, 100) + '%', background: concentrationColor[portfolioHealth.concentration_level] || 'var(--color-success)' }"
            ></div>
          </div>
        </div>
      </div>

      <!-- 类型分布 -->
      <div v-if="Object.keys(portfolioHealth.type_distribution || {}).length" class="type-dist">
        <div v-for="(v, k) in portfolioHealth.type_distribution" :key="k" class="type-item">
          <span class="type-name">{{ k }}</span>
          <div class="type-bar-bg">
            <div class="type-bar" :style="{ width: Math.min(v / (portfolioHealth.total_value || 1) * 100, 100) + '%' }"></div>
          </div>
          <span class="type-pct">{{ (v / (portfolioHealth.total_value || 1) * 100).toFixed(0) }}%</span>
        </div>
      </div>

      <!-- AI 再平衡建议 -->
      <div v-if="showRebalance" class="rebalance-section">
        <div v-if="rebalanceLoading" class="card-loading">
          <div class="spinner"></div>
          <p>正在分析偏离度...</p>
        </div>
        <div v-else-if="rebalanceResult && !rebalanceResult.error" class="rebalance-result">
          <div class="rebalance-header">
            <span class="rebalance-title">调仓分析</span>
            <span :class="['drift-badge', rebalanceResult.drift_level]">
              {{ { balanced: '已平衡', slight: '轻微偏离', significant: '显著偏离' }[rebalanceResult.drift_level] || '未知' }}
            </span>
            <span class="market-tag">{{ rebalanceResult.market_level }} · {{ rebalanceResult.market_avg_percentile }}%</span>
          </div>

          <div class="allocation-compare">
            <div class="alloc-row" v-for="cat in ['equity','bond','index','hybrid','money','qdii','cash']" :key="cat"
              v-show="allocationValue(rebalanceResult.current_allocation, cat) > 0.001 || allocationValue(rebalanceResult.target_allocation, cat) > 0.001">
              <span class="alloc-label">{{ categoryLabels[cat] || cat }}</span>
              <div class="alloc-bars">
                <div class="alloc-bar-current" :style="{ width: Math.min(allocationValue(rebalanceResult.current_allocation, cat)*100, 100) + '%' }"></div>
                <div class="alloc-bar-target" :style="{ width: Math.min(allocationValue(rebalanceResult.target_allocation, cat)*100, 100) + '%' }"></div>
              </div>
              <span class="alloc-values">
                <span class="alloc-current">{{ (allocationValue(rebalanceResult.current_allocation, cat)*100).toFixed(0) }}%</span>
                <span class="alloc-arrow"><Icon name="arrow-right" size="11" /></span>
                <span class="alloc-target">{{ (allocationValue(rebalanceResult.target_allocation, cat)*100).toFixed(0) }}%</span>
              </span>
            </div>
          </div>

          <div v-if="rebalanceResult.suggestions?.length" class="rebalance-suggestions">
            <div v-for="(s, i) in rebalanceResult.suggestions" :key="i" class="suggestion-item">
              <span :class="['suggestion-action', s.action]">
                {{ {buy:'买入',sell:'卖出',buy_index:'定投',deploy_cash:'配置现金',reserve_cash:'保留现金'}[s.action] || s.action }}
              </span>
              <span class="suggestion-detail">
                {{ s.fund_name || s.category }} · {{ s.reason }}
                <span v-if="s.amount_range" class="suggestion-amount">{{ s.amount_range }}</span>
              </span>
            </div>
          </div>
        </div>
        <div v-else-if="rebalanceResult?.error" class="rebalance-result">
          <span class="rebalance-content" style="color: var(--text-muted)">{{ rebalanceResult.error }}</span>
        </div>
      </div>

      <!-- AI 全景诊断结果 -->
      <AgentResultCard
        v-if="panoramaResult"
        title="AI 全景诊断"
        agent="全景诊断分析师"
        class="portfolio-agent-result"
        :status="panoramaLoading || panoramaResult.status === 'running' ? 'running' : panoramaResult.error ? 'error' : 'done'"
        :content="panoramaResult.result_data || panoramaResult.result || ''"
        :error="panoramaResult.error || ''"
        :token-usage="panoramaResult.token_usage || 0"
        :record-id="panoramaResult.id"
        :target-id="panoramaResult.id || null"
        target-type="portfolio_panorama"
        source="持仓健康度"
        :updated-at="portfolioUpdatedAt || panoramaResult.updated_at || panoramaResult.created_at || ''"
        feedback-caller="dashboard_portfolio_health"
        collapsible
        :default-expanded="true"
        @retry="emit('panorama')"
      />

      <div class="card-actions">
        <button class="btn-ghost btn-sm" @click="emit('navigate', 'portfolio')">查看全部持仓 <Icon name="arrow-right" size="12" /></button>
      </div>
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
  transition: box-shadow 0.3s cubic-bezier(0.4, 0, 0.2, 1), border-color 0.3s ease, transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
  min-height: auto;
  max-height: none;
}
.dash-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--gradient-accent);
  opacity: 0;
  transition: opacity 0.3s ease;
}
.dash-card::after {
  content: '';
  position: absolute;
  top: -40px;
  right: -40px;
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: var(--color-primary);
  opacity: 0;
  filter: blur(40px);
  transition: opacity 0.4s ease;
  pointer-events: none;
}
.dash-card:hover {
  box-shadow: var(--shadow-lg), var(--shadow-glow);
  border-color: var(--color-primary-border);
  transform: translateY(-1px);
}
.dash-card:hover::before {
  opacity: 1;
}
.dash-card:hover::after {
  opacity: 0.08;
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
.card-body {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  padding: 0.25rem 0;
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
.card-data-time {
  font-size: 0.6rem;
  color: var(--color-text-muted);
  font-weight: 400;
  margin-left: 0.25rem;
  opacity: 0.7;
}
.card-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
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
  gap: 0.35rem;
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
.metric-value.profit { color: var(--color-profit); }
.metric-value.loss { color: var(--color-loss); }
.concentration-row {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem;
  border-top: 1px solid var(--color-border-light);
  border-bottom: 1px solid var(--color-border-light);
  background: var(--color-bg-card);
  border-radius: var(--radius-md);
}
.concentration-icon { font-size: 1.25rem; line-height: 1.5; }
.concentration-text {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.5rem;
  font-weight: 500;
}
.concentration-bar-bg {
  height: 7px;
  background: var(--color-bg-input);
  border-radius: 6px;
  overflow: hidden;
  max-width: 220px;
}
.concentration-bar {
  height: 100%;
  border-radius: 6px;
  transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}
.type-dist {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.5rem 0;
}
.type-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.type-name {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  width: 55px;
  flex-shrink: 0;
  font-weight: 600;
}
.type-bar-bg {
  flex: 1;
  height: 7px;
  background: var(--color-bg-input);
  border-radius: 6px;
  overflow: hidden;
  max-width: 180px;
}
.type-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
  border-radius: 6px;
  transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}
.type-pct {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  width: 32px;
  text-align: right;
  font-weight: 600;
}
.rebalance-section {
  border-top: 1px solid var(--color-border-light);
  padding-top: 0.75rem;
  margin-top: 0.5rem;
}
.card-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2.5rem 0;
  gap: 1rem;
  color: var(--color-text-muted);
  font-size: 0.9rem;
}
.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}
.rebalance-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  flex-wrap: wrap;
}
.rebalance-title {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-primary);
}
.drift-badge {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius-sm);
  letter-spacing: 0.03em;
}
.drift-badge.balanced { background: var(--color-success-bg); color: var(--color-success); }
.drift-badge.slight { background: var(--color-warning-bg); color: var(--color-warning); }
.drift-badge.significant { background: var(--color-danger-bg); color: var(--color-danger); }
.market-tag {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-left: auto;
}
.rebalance-result {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.allocation-compare {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  background: linear-gradient(135deg, var(--color-bg-card), var(--color-bg-card));
  border-radius: var(--radius-lg);
  padding: 0.75rem;
  border: 1px solid var(--color-border-light);
}
.alloc-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
}
.alloc-label {
  width: 3.5rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  flex-shrink: 0;
  font-size: 0.75rem;
}
.alloc-bars {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  height: 16px;
  position: relative;
}
.alloc-bar-current {
  height: 7px;
  background: var(--color-primary);
  border-radius: 3px;
  opacity: 0.7;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}
.alloc-bar-target {
  height: 7px;
  background: var(--color-success);
  border-radius: 3px;
  opacity: 0.4;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}
.alloc-values {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-shrink: 0;
  width: 5.5rem;
  font-size: 0.75rem;
}
.alloc-current { font-weight: 700; color: var(--color-primary); }
.alloc-arrow { color: var(--color-text-muted); font-size: 0.72rem; }
.alloc-target { font-weight: 600; color: var(--color-success); }
.rebalance-suggestions {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.suggestion-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem 0.65rem;
  background: var(--color-bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  font-size: 0.82rem;
}
.suggestion-action {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  letter-spacing: 0.02em;
}
.suggestion-action.buy, .suggestion-action.buy_index { background: var(--color-success-bg); color: var(--color-success); }
.suggestion-action.sell { background: var(--color-danger-bg); color: var(--color-danger); }
.suggestion-action.deploy_cash { background: var(--color-info-bg); color: var(--color-info); }
.suggestion-action.reserve_cash { background: var(--color-warning-bg); color: var(--color-warning); }
.suggestion-detail { color: var(--color-text-secondary); line-height: 1.5; }
.suggestion-amount {
  display: inline-block;
  margin-left: 0.25rem;
  font-weight: 600;
  color: var(--color-primary);
}
.portfolio-agent-result {
  margin-top: 0.75rem;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (max-width: 768px) {
  .health-metrics {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
