<script setup>
import { computed } from 'vue'
import { getPercentileColor } from '../../composables/useDashboardHelpers'
import Icon from '../ui/Icon.vue'
import AIActionButton from '../ui/AIActionButton.vue'
import ActionCard from '../ActionCard.vue'

const REC_STATUS_ICON = { correct: 'check', wrong: 'error', flat: 'arrow-right', pending: 'hourglass' }

const props = defineProps({
  hotTopics: { type: Array, default: null },
  hotTopicsFetchedAt: { type: String, default: '' },
  hotTopicsLoading: { type: Boolean, default: false },
  hotspotLoading: { type: Boolean, default: false },
  hotspotError: { type: Boolean, default: false },
  hotspotsAnalysis: { type: Object, default: null },
  opportunities: { type: Array, default: () => [] },
  opportunitiesLoading: { type: Boolean, default: false },
  hotspotsRelate: { type: Array, default: () => [] },
  recHistory: { type: Array, default: null },
  recStats: { type: Object, default: null },
  showVerify: { type: Boolean, default: false },
  feedbackSending: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['analyze', 'feedback', 'navigate', 'update:showVerify', 'create-decision', 'watch-opportunity'])

// 聚合所有关联行业和持仓
const allSectors = computed(() => {
  if (!props.hotspotsRelate.length) return []
  const set = new Set()
  for (const item of props.hotspotsRelate) {
    for (const s of (item.sectors || [])) set.add(s)
  }
  return [...set]
})
const allRelatedHoldings = computed(() => {
  if (!props.hotspotsRelate.length) return []
  const seen = new Set()
  const result = []
  for (const item of props.hotspotsRelate) {
    for (const h of (item.related_holdings || [])) {
      if (!seen.has(h.fund_code)) {
        seen.add(h.fund_code)
        result.push(h)
      }
    }
  }
  return result
})

const verdictMeta = {
  can_buy: { label: '可小仓', className: 'verdict-buy' },
  watch: { label: '观察', className: 'verdict-watch' },
  avoid: { label: '回避', className: 'verdict-avoid' },
  sell_or_reduce: { label: '减仓', className: 'verdict-reduce' },
}
</script>

<template>
  <div class="dash-card card editorial-card">
    <div class="card-header editorial-card-header">
      <div class="card-title-row">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 10V3L4 14h7v7l9-11h-7z"/>
        </svg>
        <span class="title editorial-title">今日热门机会</span>
      </div>
      <div class="card-header-actions">
        <span v-if="hotTopicsFetchedAt" class="card-data-time meta terminal-label">{{ hotTopicsFetchedAt }}</span>
        <AIActionButton
          :label="hotspotsAnalysis ? '重新分析' : 'AI 分析'"
          agent="热点分析专家"
          icon="lightbulb"
          variant="soft"
          size="sm"
          :loading="hotspotLoading"
          @click="emit('analyze')"
        />
      </div>
    </div>

    <!-- 自动加载的新闻列表（始终显示） -->
    <div v-if="hotTopics?.length && !hotspotLoading" class="card-body news-list" :class="{ 'news-list-compact': hotspotsAnalysis }">
      <div v-for="(item, i) in hotTopics.slice(0, hotspotsAnalysis ? 3 : 4)" :key="i" class="news-item reveal-stagger">
        <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="news-title">{{ item.title }}</a>
        <span v-else class="news-title">{{ item.title }}</span>
        <p v-if="!hotspotsAnalysis" class="news-summary">{{ item.summary?.slice(0, 120) }}{{ item.summary?.length > 120 ? '...' : '' }}</p>
        <div v-if="hotspotsRelate?.[i]?.sectors?.length" class="news-relate">
          <div class="news-sectors">
            <span v-for="s in hotspotsRelate[i].sectors" :key="s" class="sector-tag">{{ s }}</span>
          </div>
          <div v-if="hotspotsRelate[i].related_indexes?.length" class="news-indexes">
            <span class="relate-label terminal-label">相关指数：</span>
            <span v-for="idx in hotspotsRelate[i].related_indexes.slice(0, 3)" :key="idx.index_code" class="index-link" @click="emit('navigate', 'valuation')">
              {{ idx.index_name }}
              <em v-if="idx.percentile != null" class="font-jet" :style="{ color: getPercentileColor(idx.percentile) }">{{ idx.percentile }}%</em>
            </span>
          </div>
          <div v-if="hotspotsRelate[i].related_holdings?.length" class="news-holdings">
            <span class="relate-label terminal-label">持仓关联：</span>
            <span v-for="h in hotspotsRelate[i].related_holdings" :key="h.fund_code" class="holding-tag">
              {{ h.fund_name }}
            </span>
          </div>
        </div>
        <div class="news-meta">
          <span class="news-source terminal-label">{{ item.source }}</span>
          <span v-if="item.date" class="news-date terminal-label font-jet">{{ item.date?.slice(0, 10) }}</span>
        </div>
      </div>
      <p v-if="!hotspotsAnalysis" class="hotspots-hint">点击「AI 分析」获取结构化投资机会推荐</p>
    </div>

    <!-- 热点数据加载中 -->
    <div v-if="hotTopicsLoading && !hotspotLoading" class="card-empty">
      <div class="spinner" style="width:20px;height:20px;border-width:2px"></div>
      <p style="margin-top:0.5rem">加载今日热点...</p>
    </div>

    <!-- 无数据 -->
    <div v-if="!hotTopics?.length && !hotTopicsLoading && !hotspotLoading" class="card-empty">
      <p>点击「AI 分析」获取今日市场热点</p>
      <p class="card-empty-hint">AI 基于最新财经新闻生成板块解读和投资机会分析</p>
    </div>

    <!-- AI 加载中 -->
    <div v-if="hotspotLoading" class="card-loading">
      <div class="spinner"></div>
      <p>正在分析市场热点...</p>
    </div>

    <!-- 主题机会卡 -->
    <div v-if="opportunitiesLoading" class="card-loading">
      <div class="spinner"></div>
      <p>正在筛选主题机会...</p>
    </div>

    <div v-if="opportunities?.length && !opportunitiesLoading && !hotspotLoading" class="card-body opportunities-body">
      <div class="opportunity-summary-row">
        <span class="opportunity-summary-title terminal-label">机会引擎</span>
        <span class="opportunity-count font-jet">{{ opportunities.length }} 个主题</span>
      </div>
      <div v-for="item in opportunities.slice(0, 4)" :key="item.id" class="opportunity-card reveal-stagger">
        <div class="opportunity-top">
          <span :class="['opportunity-verdict', 'terminal-label', verdictMeta[item.verdict]?.className || 'verdict-watch']">
            {{ verdictMeta[item.verdict]?.label || item.verdict }}
          </span>
          <span class="opportunity-theme">{{ item.theme }}</span>
          <span class="opportunity-score font-jet">{{ Math.round(item.opportunity_score || 0) }}</span>
        </div>
        <p class="opportunity-line">{{ item.policy_signal }}</p>
        <p class="opportunity-risk">{{ item.risk_note }}</p>
        <div v-if="item.matched_funds?.length" class="opportunity-funds">
          <span v-for="fund in item.matched_funds.slice(0, 2)" :key="fund.fund_code" class="opportunity-fund">
            {{ fund.fund_name || fund.fund_code }}
            <em class="terminal-label">{{ fund.vehicle_type === 'etf' ? 'ETF' : '场外' }}</em>
          </span>
        </div>
        <div class="opportunity-plan font-jet">
          <span>金额 {{ item.entry_plan?.amount ? '¥' + Number(item.entry_plan.amount).toLocaleString() : '观察' }}</span>
          <span>{{ item.exit_plan?.time_stop || '需复盘' }}</span>
        </div>
        <div class="opportunity-actions">
          <button class="btn-ghost btn-sm" @click="emit('watch-opportunity', item)">
            <Icon name="eye" size="13" /> 观察
          </button>
          <button class="btn-ghost btn-sm btn-primary-text" @click="emit('create-decision', item)">
            <Icon name="check" size="13" /> 存决策
          </button>
        </div>
      </div>
    </div>

    <!-- AI 结果（结构化推荐卡片，作为完整分析保留） -->
    <div v-if="hotspotsAnalysis && !hotspotLoading" class="card-body hotspots-body">
      <div v-if="hotspotsRelate?.length" class="hotspots-relate-summary">
        <span class="relate-label terminal-label">涉及行业：</span>
        <span v-for="s in allSectors" :key="s" class="sector-tag">{{ s }}</span>
        <span v-if="allRelatedHoldings.length" class="relate-holding-hint font-jet">· 持仓关联 {{ allRelatedHoldings.length }} 只</span>
      </div>
      <p class="hotspots-summary">{{ hotspotsAnalysis.summary }}</p>
      <div v-for="(rec, i) in hotspotsAnalysis.recommendations" :key="i" class="rec-card reveal-stagger">
        <div class="rec-main">
          <span :class="['rec-badge', 'terminal-label', 'rec-' + rec.direction]">
            {{ rec.direction === 'up' ? '关注' : rec.direction === 'down' ? '回避' : '观察' }}
          </span>
          <span class="rec-name rec-name-link" @click="emit('navigate', 'valuation')">{{ rec.index_name }}</span>
          <span v-if="rec.index_code" class="rec-code font-jet">{{ rec.index_code }}</span>
          <span v-if="rec.percentile != null" class="rec-pct font-jet" :style="{ color: getPercentileColor(rec.percentile) }">
            {{ rec.percentile }}%
          </span>
          <span v-if="rec.user_portfolio" :class="['rec-portfolio', 'terminal-label', 'rp-' + rec.user_portfolio]">
            {{ rec.user_portfolio === 'already_have' ? '已持有' : rec.user_portfolio === 'can_add' ? '可加仓' : rec.user_portfolio === 'reduce' ? '应减仓' : '新机会' }}
          </span>
          <span class="rec-reason">{{ rec.reason }}</span>
          <span :class="['rec-conf', 'terminal-label', 'conf-' + rec.confidence]">
            {{ rec.confidence === 'high' ? '高置信度' : rec.confidence === 'medium' ? '中置信度' : '低置信度' }}
          </span>
        </div>
        <div class="rec-actions">
          <button class="rec-feedback-btn helpful" :class="{ active: feedbackSending[rec.id] === 'helpful' }"
            :disabled="feedbackSending[rec.id]" @click="emit('feedback', rec, 'helpful')"
            title="有用">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"/>
            </svg>
          </button>
          <button class="rec-feedback-btn unhelpful" :class="{ active: feedbackSending[rec.id] === 'unhelpful' }"
            :disabled="feedbackSending[rec.id]" @click="emit('feedback', rec, 'unhelpful')"
            title="不准确">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018a2 2 0 01.485.06l3.76.94m-7 10v5a2 2 0 002 2h.096c.5 0 .905-.405.905-.904 0-.715.211-1.413.608-2.008L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5"/>
            </svg>
          </button>
        </div>
      </div>
      <div v-if="hotspotsAnalysis.recommendations?.length === 0 && hotspotsAnalysis.summary" class="card-empty">
        <p>暂无明确推荐</p>
      </div>
      <details v-if="hotspotsAnalysis.analysis_text" class="hotspots-details">
        <summary class="hotspots-details-summary">查看完整分析 ↓</summary>
        <div class="hotspots-content">{{ hotspotsAnalysis.analysis_text }}</div>
      </details>
    </div>

    <!-- 热点分析行动卡片 -->
    <ActionCard
      v-if="hotspotsAnalysis?.actions?.length > 0"
      :actions="hotspotsAnalysis.actions"
      source="hotspots"
      @watch="(action) => emit('watch-opportunity', action)"
      @decision="(action) => emit('create-decision', action)"
      @dismiss="(idx) => hotspotsAnalysis.actions.splice(idx, 1)"
    />

    <!-- 推荐验证历史 -->
    <div v-if="recStats" class="verify-bar" @click="emit('update:showVerify', !showVerify)">
      <span class="verify-label terminal-label">推荐验证</span>
      <span class="verify-stat font-jet">总计 {{ recStats.total }} 条</span>
      <span v-if="recStats.verified > 0" class="verify-stat correct font-jet">正确 {{ recStats.correct }}</span>
      <span v-if="recStats.verified > 0" class="verify-stat wrong font-jet">错误 {{ recStats.wrong }}</span>
      <span v-if="recStats.flat > 0" class="verify-stat flat font-jet">平局 {{ recStats.flat }}</span>
      <span v-if="recStats.pending_not_due > 0" class="verify-stat pending font-jet">待到期 {{ recStats.pending_not_due }}</span>
      <span v-if="recStats.accuracy != null" class="verify-accuracy font-jet">命中率 {{ recStats.accuracy }}%</span>
      <span v-else class="verify-accuracy pending">暂无验证数据</span>
      <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" :class="{ rotated: showVerify }">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
      </svg>
    </div>
    <div v-if="showVerify" class="verify-detail">
      <div class="verify-hint">验证规则：T+5 交易日后自动对比行情，涨跌 <2% 为平局；观察方向对比沪深300</div>
      <div v-if="recStats.watch_total > 0" class="verify-watch-stats font-jet">
        <span>观察方向：{{ recStats.watch_total }} 条</span>
        <span v-if="recStats.watch_correct > 0" class="correct">跑赢基准 {{ recStats.watch_correct }}</span>
        <span v-if="recStats.watch_wrong > 0" class="wrong">跑输基准 {{ recStats.watch_wrong }}</span>
      </div>
      <div v-if="recHistory?.length" class="verify-list">
        <div v-for="rec in recHistory.slice(0, 10)" :key="rec.id" class="verify-item reveal-stagger">
          <span :class="['rec-badge', 'terminal-label', 'rec-' + rec.direction]">
            {{ rec.direction === 'up' ? '关注' : rec.direction === 'down' ? '回避' : '观察' }}
          </span>
          <span class="verify-name">{{ rec.index_name }}</span>
          <span :class="['verify-status', 'font-jet', 'vs-' + rec.status]">
            <Icon :name="REC_STATUS_ICON[rec.status] || 'hourglass'" size="12" class="inline-icon" />
            {{ rec.status === 'correct' ? '正确' : rec.status === 'wrong' ? '错误' : rec.status === 'flat' ? '平局' : '待验证' }}
            <template v-if="rec.change_pct != null">({{ rec.change_pct > 0 ? '+' : '' }}{{ rec.change_pct }}%)</template>
            <template v-if="rec.benchmark_change_pct != null"> vs 基准{{ rec.benchmark_change_pct > 0 ? '+' : '' }}{{ rec.benchmark_change_pct }}%</template>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inline-icon { vertical-align: middle; margin-right: 2px; }
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
.card-empty-hint {
  font-size: 0.8rem;
  margin-top: 0.35rem;
  opacity: 0.8;
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
.hotspots-relate-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px dashed var(--color-border);
}
.relate-holding-hint {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.hotspots-body {
  max-height: none;
  overflow-y: visible;
}
.opportunities-body {
  gap: 0.65rem;
  padding: 0.2rem 0 0.4rem;
  border-bottom: 1px dashed var(--color-border);
}
.opportunity-summary-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.opportunity-summary-title {
  color: var(--color-text-primary);
  font-weight: 700;
}
.opportunity-count {
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
  font-weight: 600;
}
.opportunity-card {
  display: grid;
  gap: 0.55rem;
  padding: 0.8rem 0.9rem;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
}
.opportunity-card:hover {
  background: var(--color-bg-card);
  border-color: var(--color-primary-border);
  box-shadow: var(--shadow-sm);
}
.opportunity-top {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.5rem;
}
.opportunity-verdict {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 3.2rem;
  padding: 0.18rem 0.45rem;
  border-radius: 4px;
  font-size: 0.68rem;
  font-weight: 800;
  line-height: 1.2;
  white-space: nowrap;
}
.verdict-buy { background: var(--color-success-bg); color: var(--color-success); }
.verdict-watch { background: var(--color-warning-bg); color: var(--color-warning); }
.verdict-avoid { background: var(--color-danger-bg); color: var(--color-danger); }
.verdict-reduce { background: var(--color-danger-bg); color: var(--color-danger); }
.opportunity-theme {
  color: var(--color-text-primary);
  font-size: 0.92rem;
  font-weight: 800;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.opportunity-score {
  width: 2rem;
  height: 2rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  color: var(--color-text-primary);
  font-size: 0.82rem;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}
.opportunity-line,
.opportunity-risk {
  margin: 0;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.opportunity-line {
  color: var(--color-text-secondary);
  font-size: 0.78rem;
  -webkit-line-clamp: 2;
}
.opportunity-risk {
  color: var(--color-danger);
  font-size: 0.72rem;
  -webkit-line-clamp: 2;
}
.opportunity-funds {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.opportunity-fund {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  max-width: 100%;
  padding: 0.18rem 0.45rem;
  border-radius: 4px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  color: var(--color-text-secondary);
  font-size: 0.7rem;
  font-weight: 600;
}
.opportunity-fund em {
  font-style: normal;
  color: var(--color-text-muted);
  font-size: 0.62rem;
  font-weight: 700;
}
.opportunity-plan {
  display: grid;
  grid-template-columns: minmax(0, auto) minmax(0, 1fr);
  gap: 0.5rem;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}
.opportunity-plan span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.opportunity-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.4rem;
}
.opportunity-actions .btn-sm {
  height: 1.75rem;
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0 0.55rem;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 700;
}
.btn-primary-text {
  color: var(--color-primary);
}
.hotspots-summary {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: 1rem;
  line-height: 1.7;
  padding-bottom: 0.85rem;
  border-bottom: 1px solid var(--color-border-light);
}
.rec-card {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  padding: 0.9rem;
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, var(--color-bg-card), var(--color-bg-card));
  margin-bottom: 0.7rem;
  border: 1px solid var(--color-border-light);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.rec-card:hover {
  background: var(--color-bg-hover);
  box-shadow: var(--shadow-md);
}
.rec-main {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}
.rec-code {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.rec-portfolio {
  font-size: 0.65rem;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
  font-weight: 600;
}
.rp-already_have { background: var(--color-primary-bg); color: var(--color-primary); }
.rp-can_add { background: var(--color-success-bg); color: var(--color-success); }
.rp-reduce { background: var(--color-danger-bg); color: var(--color-danger); }
.rp-new { background: var(--color-warning-bg); color: var(--color-warning); }
.rec-actions {
  display: flex;
  gap: 0.2rem;
  flex-shrink: 0;
  padding-top: 0.15rem;
}
.rec-feedback-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--color-text-muted);
  opacity: 0.4;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  padding: 0;
}
.rec-feedback-btn:hover {
  opacity: 1;
  transform: scale(1.15);
}
.rec-feedback-btn.helpful:hover,
.rec-feedback-btn.helpful.active {
  color: var(--color-success);
  background: var(--color-success-bg);
  opacity: 1;
}
.rec-feedback-btn.unhelpful:hover,
.rec-feedback-btn.unhelpful.active {
  color: var(--color-danger);
  background: var(--color-danger-bg);
  opacity: 1;
}
.rec-feedback-btn:disabled { cursor: default; }
.rec-badge {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: 0.02em;
}
.rec-up { background: var(--color-success-bg); color: var(--color-success); }
.rec-down { background: var(--color-danger-bg); color: var(--color-danger); }
.rec-watch { background: var(--color-warning-bg); color: var(--color-warning); }
.rec-name {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-text-primary);
  flex-shrink: 0;
}
.rec-name-link {
  cursor: pointer;
  transition: color 0.2s;
}
.rec-name-link:hover {
  color: var(--color-primary);
  text-decoration: underline;
}
.rec-pct {
  font-size: 0.75rem;
  font-weight: 700;
  padding: 0.1rem 0.35rem;
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  flex-shrink: 0;
}
.rec-reason {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  flex: 1;
  min-width: 0;
  line-height: 1.5;
}
.rec-conf {
  font-size: 0.65rem;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
  font-weight: 600;
}
.conf-high { background: var(--color-success-bg); color: var(--color-success); }
.conf-medium { background: var(--color-warning-bg); color: var(--color-warning); }
.conf-low { background: var(--color-bg-hover); color: var(--color-text-muted); }
.hotspots-details { margin-top: 0.75rem; }
.hotspots-details-summary {
  font-size: 0.8rem;
  color: var(--color-primary);
  cursor: pointer;
  user-select: none;
  font-weight: 500;
  transition: color 0.2s;
}
.hotspots-details-summary:hover { color: var(--color-primary-hover); }
.hotspots-content {
  font-size: 0.85rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  margin-top: 0.75rem;
  padding: 0.75rem 1rem;
  background: var(--color-primary-bg);
  border: 1px solid var(--color-primary-border);
  border-radius: var(--radius-md);
}
.news-list {
  max-height: 400px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.news-list-compact {
  max-height: 200px;
  gap: 0.4rem;
  border-bottom: 1px dashed var(--color-border);
  padding-bottom: 0.5rem;
  margin-bottom: 0.25rem;
}
.news-item {
  padding: 0.75rem 0.85rem;
  border-radius: var(--radius-md);
  border-left: 3px solid var(--color-primary-300);
  background: var(--color-bg-input);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.news-item:hover {
  background: var(--color-bg-hover);
  border-left-color: var(--color-primary);
}
.news-title {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-text-primary);
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color 0.2s;
}
.news-title:hover { color: var(--color-primary); }
.news-summary {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  margin: 0.3rem 0 0;
  line-height: 1.5;
}
.news-meta {
  display: flex;
  gap: 0.6rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.3rem;
}
.news-source { color: var(--color-primary); font-weight: 600; }
.news-relate {
  margin-top: 0.4rem;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--color-border);
}
.news-sectors {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.3rem;
}
.sector-tag {
  font-size: 0.65rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: var(--color-info-bg);
  color: var(--color-primary);
  font-weight: 600;
}
.news-indexes, .news-holdings {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.2rem;
}
.relate-label {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}
.index-link {
  cursor: pointer;
  color: var(--color-primary);
  font-weight: 500;
}
.index-link:hover { text-decoration: underline; }
.index-link em {
  font-style: normal;
  font-size: 0.65rem;
  margin-left: 0.15rem;
}
.holding-tag {
  font-size: 0.65rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: var(--color-success-bg);
  color: var(--color-success);
  font-weight: 500;
}
.hotspots-hint {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  text-align: center;
  margin: 0.4rem 0 0;
}
/* 推荐验证 */
.verify-bar {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.65rem;
  border-top: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.78rem;
  user-select: none;
  transition: background 0.2s;
}
.verify-bar:hover { background: var(--color-bg-hover); }
.verify-label { font-weight: 600; color: var(--color-text-primary); }
.verify-stat { color: var(--color-text-muted); }
.verify-stat.correct { color: var(--color-success); }
.verify-stat.wrong { color: var(--color-danger); }
.verify-accuracy {
  margin-left: auto;
  font-weight: 700;
  color: var(--color-primary);
}
.verify-accuracy.pending {
  color: var(--color-text-muted);
  font-weight: 400;
}
.verify-bar svg {
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  color: var(--color-text-muted);
}
.verify-bar svg.rotated { transform: rotate(180deg); }
.verify-detail {
  border-top: 1px solid var(--color-border);
  padding: 0.5rem 0.65rem;
}
.verify-hint {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-bottom: 0.6rem;
  line-height: 1.5;
}
.verify-watch-stats {
  display: flex;
  gap: 0.8rem;
  font-size: 0.75rem;
  margin-bottom: 0.6rem;
  color: var(--color-text-secondary);
}
.verify-watch-stats span { white-space: nowrap; }
.verify-list {
  border-top: 1px solid var(--color-border);
  max-height: 300px;
  overflow-y: auto;
  padding: 0.4rem 0;
}
.verify-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.65rem;
  font-size: 0.78rem;
  transition: background 0.15s;
}
.verify-item:hover { background: var(--color-bg-hover); }
.verify-name { flex: 1; color: var(--color-text-primary); font-weight: 500; }
.verify-status { font-size: 0.72rem; white-space: nowrap; }
.vs-correct { color: var(--color-success); }
.vs-wrong { color: var(--color-danger); }
.vs-flat { color: var(--color-warning); }
.vs-pending { color: var(--color-text-muted); }
@media (max-width: 768px) {
  .card-header-actions { flex-wrap: wrap; gap: 0.25rem; }
  .verify-bar { flex-wrap: wrap; gap: 0.25rem; }
  .rec-main { gap: 0.4rem; }
  .opportunity-plan { grid-template-columns: 1fr; gap: 0.25rem; }
  .opportunity-actions { justify-content: stretch; }
  .opportunity-actions .btn-sm { flex: 1; justify-content: center; }
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
