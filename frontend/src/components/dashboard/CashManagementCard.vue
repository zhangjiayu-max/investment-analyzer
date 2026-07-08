<script setup>
import { _cmpTemp } from '../../composables/useDashboardHelpers'
import Icon from '../ui/Icon.vue'
import AIActionButton from '../ui/AIActionButton.vue'

const ALERT_ICON_MAP = { warning: 'warning', info: 'info', opportunity: 'lightbulb' }

const props = defineProps({
  cashManagement: { type: Object, default: null },
  cashUpdatedAt: { type: String, default: '' },
  bondLoading: { type: Boolean, default: false },
  bondResult: { type: Object, default: null },
})

const emit = defineEmits(['bond-recommend'])
</script>

<template>
  <div class="dash-card card editorial-card">
    <div class="card-header editorial-card-header">
      <div class="card-title-row">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <span class="title editorial-title">零钱配置</span>
      </div>
      <div class="card-header-actions">
        <span v-if="cashManagement" class="card-data-time meta terminal-label">{{ cashUpdatedAt || '' }}</span>
        <AIActionButton
          v-if="cashManagement?.balance > 0"
          :label="bondResult ? '重新分析' : 'AI 债券推荐'"
          agent="债券配置顾问"
          icon="coins"
          variant="soft"
          size="sm"
          :loading="bondLoading"
          @click="emit('bond-recommend')"
        />
      </div>
    </div>
    <div class="card-body">
      <div class="cash-balance-row">
        <span class="cash-label terminal-label">可用零钱</span>
        <span class="cash-value font-jet-lg">¥{{ (cashManagement?.balance || 0).toLocaleString() }}</span>
        <span v-if="cashManagement?.suggestion?.cash_ratio != null" class="cash-ratio-tag font-jet">
          占比 {{ (cashManagement.suggestion.cash_ratio * 100).toFixed(0) }}%
        </span>
      </div>
      <div v-if="cashManagement?.cash_details" class="cash-details-row">
        <span v-for="(bal, uid) in cashManagement.cash_details" :key="uid" class="cash-detail-tag reveal-stagger font-jet">
          <Icon name="circle-user" size="12" class="cash-detail-icon" /> {{ uid }} ¥{{ bal.toLocaleString() }}
        </span>
      </div>

      <!-- 现金预警 + 权益机会 -->
      <div v-if="cashManagement?.suggestion?.alerts?.length" class="cash-alerts">
        <div v-for="(alert, i) in cashManagement.suggestion.alerts" :key="i"
          :class="['cash-alert', 'reveal-stagger', alert.level]">
          <span class="cash-alert-icon"><Icon :name="ALERT_ICON_MAP[alert.level] || 'info'" size="13" /></span>
          <span>{{ alert.message }}</span>
        </div>
      </div>

      <div v-if="cashManagement?.bond_market" class="bond-info">
        <div class="bond-temp-row">
          <span class="bond-temp-label terminal-label">债市温度</span>
          <span :class="['bond-temp-value', 'font-jet-lg', { 'bond-cold': cashManagement.bond_market.temperature <= 30, 'bond-hot': cashManagement.bond_market.temperature > 70 }]">
            {{ cashManagement.bond_market.temperature }}°
          </span>
          <span class="bond-yield font-jet">10Y收益率 {{ cashManagement.bond_market.yield_val }}%</span>
        </div>

        <!-- 趋势对比 -->
        <div v-if="cashManagement.bond_market.trend" class="bond-trend">
          <div class="trend-item">
            <span class="trend-label terminal-label">较一周前</span>
            <span :class="['trend-val', 'font-jet', { 'trend-up': (cashManagement.bond_market.trend.week_ago_yield || 0) < (cashManagement.bond_market.yield_val || 0) }]">
              {{ _cmpTemp(cashManagement.bond_market.trend.week_ago_temp, cashManagement.bond_market.temperature) }}
            </span>
          </div>
          <div class="trend-item">
            <span class="trend-label terminal-label">较一月前</span>
            <span :class="['trend-val', 'font-jet', { 'trend-up': (cashManagement.bond_market.trend.month_ago_yield || 0) < (cashManagement.bond_market.yield_val || 0) }]">
              {{ _cmpTemp(cashManagement.bond_market.trend.month_ago_temp, cashManagement.bond_market.temperature) }}
            </span>
          </div>
          <div class="trend-note">温度↑=利率↓=债价高，温度↓=利率↑=债价低</div>
        </div>
      </div>
      <div v-else class="bond-info bond-unavailable">
        <p>债市数据暂不可用</p>
      </div>

      <div v-if="cashManagement?.suggestion?.allocation?.length" class="allocation-section">
        <p class="allocation-summary">{{ cashManagement.suggestion.summary }}</p>
        <div class="allocation-chart">
          <div v-for="(item, i) in cashManagement.suggestion.allocation" :key="i" class="allocation-bar-item reveal-stagger">
            <div class="alloc-bar-label">
              <span>{{ item.name }}</span>
              <span class="alloc-bar-pct font-jet">{{ item.ratio }}%</span>
            </div>
            <div class="alloc-bar-bg">
              <div class="alloc-bar-fill" :style="{ width: item.ratio + '%', background: ['#c9a84c', '#10b981', '#f59e0b'][i % 3] }"></div>
            </div>
            <div class="alloc-bar-desc">{{ item.desc }}</div>
          </div>
        </div>
        <div class="alloc-money">
          <div v-for="(item, i) in cashManagement.suggestion.allocation" :key="'m'+i" class="alloc-money-item reveal-stagger">
            <span class="alloc-money-name">{{ item.name }}</span>
            <span class="alloc-money-val font-jet">≈ ¥{{ ((cashManagement.balance || 0) * item.ratio / 100).toLocaleString() }}</span>
          </div>
        </div>
      </div>

      <!-- AI 债券推荐结果 -->
      <div v-if="bondResult" class="bond-ai-result">
        <div class="bond-ai-loading" v-if="bondLoading">
          <div class="spinner"></div>
          <p>AI 分析中...</p>
        </div>
        <template v-if="!bondLoading && bondResult">
          <div class="bond-ai-header">
            <span class="bond-ai-summary">{{ bondResult.summary }}</span>
            <span class="bond-ai-market">{{ bondResult.market_assessment }}</span>
          </div>
          <div v-if="bondResult.policy_analysis" class="bond-ai-trend">
            <span class="bond-ai-label terminal-label">政策环境：</span>{{ bondResult.policy_analysis }}
          </div>
          <div v-if="bondResult.trend_analysis" class="bond-ai-trend">
            <span class="bond-ai-label terminal-label">趋势判断：</span>{{ bondResult.trend_analysis }}
          </div>
          <div v-if="bondResult.current_bond_analysis" class="bond-ai-analysis">
            <span class="bond-ai-label terminal-label">持仓评估：</span>{{ bondResult.current_bond_analysis }}
          </div>
          <div v-if="bondResult.recommendations?.length" class="bond-rec-list">
            <div v-for="(rec, i) in bondResult.recommendations" :key="i" class="bond-rec-item reveal-stagger">
              <div class="bond-rec-head">
                <span class="bond-rec-name">{{ rec.fund_name }}</span>
                <span class="bond-rec-code font-jet">{{ rec.fund_code }}</span>
                <span class="bond-rec-type terminal-label">{{ rec.fund_type }}</span>
              </div>
              <div class="bond-rec-body">
                <span class="bond-rec-reason">{{ rec.reason }}</span>
                <span class="bond-rec-amount font-jet">≈ ¥{{ (rec.amount || 0).toLocaleString() }}</span>
                <span class="bond-rec-desc">{{ rec.amount_desc }}</span>
              </div>
            </div>
          </div>
          <div v-if="bondResult.note" class="bond-rec-alt">
            {{ bondResult.note }}
          </div>
        </template>
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
/* 金色竖线由全局 .editorial-card::before 提供，此处不再覆盖 */
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
.card-body {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  padding: 0.25rem 0;
}
.cash-balance-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 0.35rem 0;
}
.cash-label {
  font-size: 0.85rem;
  color: var(--color-text-muted);
}
.cash-value {
  font-size: 1.5rem;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
}
.cash-ratio-tag {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0.1rem 0.45rem;
  border-radius: var(--radius-sm);
  background: var(--color-primary-bg);
  color: var(--color-primary);
}
.cash-details-row {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.15rem;
  padding-bottom: 0.25rem;
}
.cash-detail-tag {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  background: var(--color-bg-hover);
  padding: 0.12rem 0.4rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}
.cash-alerts {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin: 0.35rem 0;
}
.cash-alert {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
  font-size: 0.78rem;
  line-height: 1.5;
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-md);
}
.cash-alert.warning { background: var(--color-danger-bg); color: var(--color-danger); }
.cash-alert.info { background: var(--color-info-bg); color: var(--color-info); }
.cash-alert.opportunity { background: var(--color-success-bg); color: var(--color-success); }
.cash-alert-icon { flex-shrink: 0; }
.bond-info { padding: 0.6rem 0; }
.bond-temp-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.bond-temp-label {
  font-size: 0.85rem;
  color: var(--color-text-muted);
}
.bond-temp-value {
  font-size: 1.15rem;
  font-weight: 700;
  padding: 0.15rem 0.6rem;
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  transition: transform 0.2s;
}
.bond-temp-value:hover { transform: scale(1.05); }
.bond-temp-value.bond-cold {
  color: var(--color-info);
  background: var(--color-info-bg);
}
.bond-temp-value.bond-hot {
  color: var(--color-danger);
  background: var(--color-danger-bg);
}
.bond-yield {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-left: auto;
}
.bond-trend {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--color-border-light);
}
.trend-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.78rem;
}
.trend-label {
  color: var(--color-text-muted);
  width: 68px;
  flex-shrink: 0;
}
.trend-val {
  color: var(--color-text-secondary);
  font-weight: 500;
}
.trend-val.trend-up { color: var(--color-danger); }
.trend-note {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
  font-style: italic;
}
.bond-unavailable p {
  font-size: 0.85rem;
  color: var(--color-text-muted);
}
.allocation-section {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding-top: 0.6rem;
  border-top: 1px solid var(--color-border-light);
}
.allocation-summary {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0;
}
.allocation-chart {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.allocation-bar-item {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.alloc-bar-label {
  display: flex;
  justify-content: space-between;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
.alloc-bar-pct {
  font-weight: 700;
  color: var(--color-text-primary);
}
.alloc-bar-bg {
  height: 8px;
  background: var(--color-bg-input);
  border-radius: 4px;
  overflow: hidden;
}
.alloc-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary));
}
.alloc-bar-desc {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.alloc-money {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  padding-top: 0.35rem;
}
.alloc-money-item {
  display: flex;
  gap: 0.35rem;
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
.alloc-money-name { color: var(--color-text-secondary); }
.alloc-money-val { font-weight: 600; }
.bond-ai-result {
  margin-top: 1rem;
  padding: 0.75rem;
  background: var(--color-info-bg);
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.bond-ai-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem;
  justify-content: center;
}
.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}
.bond-ai-header {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.bond-ai-summary {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-primary);
}
.bond-ai-market {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
.bond-ai-analysis {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.5rem;
  line-height: 1.5;
}
.bond-ai-label {
  font-weight: 600;
  color: var(--color-text-primary);
}
.bond-rec-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.bond-rec-item {
  padding: 0.5rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 6px;
}
.bond-rec-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.bond-rec-name {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--color-text-primary);
}
.bond-rec-code {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
.bond-rec-type {
  font-size: 0.72rem;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--color-primary-100);
  color: var(--color-primary-700);
}
.bond-rec-body {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.3rem;
  flex-wrap: wrap;
}
.bond-rec-reason {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  flex: 1;
}
.bond-rec-amount {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-success);
}
.bond-rec-desc {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.bond-rec-alt {
  margin-top: 0.4rem;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  font-style: italic;
}
@media (max-width: 768px) {
  .dash-card { min-height: auto; max-height: none; }
  .card-header-actions { flex-wrap: wrap; gap: 0.25rem; }
  .bond-temp-row { flex-wrap: wrap; gap: 0.25rem; }
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
