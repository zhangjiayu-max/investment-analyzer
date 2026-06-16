<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../../composables/useMarkdown'
import TraceDetail from '../TraceDetail.vue'

const props = defineProps({
  msg: { type: Object, required: true },
  index: { type: Number, required: true },
  convId: { type: [Number, String], default: null },
  feedbackGiven: { type: Object, default: () => ({}) },
  specialistFeedback: { type: Object, default: () => ({}) },
  messageEvalStates: { type: Object, default: () => ({}) },
  traceDetailVisible: { type: Object, default: () => ({}) },
  traceDetailData: { type: Object, default: () => ({}) },
})

const emit = defineEmits([
  'feedback',
  'specialist-feedback',
  'toggle-eval',
  'trigger-eval',
  'trigger-llm-eval',
  'copy-message-id',
  'toggle-trace',
  'retry',
  'resume',
])

// 评估状态相关
function getEvalStatusClass(messageId) {
  const state = props.messageEvalStates[messageId]
  if (!state) return 'eval-status--none'
  if (state.loading) return 'eval-status--loading'
  if (state.score >= 80) return 'eval-status--good'
  if (state.score >= 60) return 'eval-status--ok'
  if (state.score > 0) return 'eval-status--bad'
  return 'eval-status--none'
}

function getEvalStatusIcon(messageId) {
  const state = props.messageEvalStates[messageId]
  if (!state || state.loading) return '📊'
  if (state.score >= 80) return '✅'
  if (state.score >= 60) return '⚠️'
  if (state.score > 0) return '❌'
  return '📊'
}

function getEvalStatusTitle(messageId) {
  const state = props.messageEvalStates[messageId]
  if (!state) return '点击进行质量评估'
  if (state.loading) return '正在评估中...'
  if (state.score >= 80) return `质量优秀 (${state.score.toFixed(0)}分)，点击查看详情`
  if (state.score >= 60) return `质量一般 (${state.score.toFixed(0)}分)，点击查看详情`
  if (state.score > 0) return `质量较差 (${state.score.toFixed(0)}分)，点击查看详情`
  return '点击进行质量评估'
}

function getEvalDimensions(messageId) {
  const state = props.messageEvalStates[messageId]
  if (!state?.evaluation?.auto_score_breakdown) return {}

  let breakdown = state.evaluation.auto_score_breakdown
  if (typeof breakdown === 'string') {
    try { breakdown = JSON.parse(breakdown) } catch { return {} }
  }

  const dimConfig = {
    execution: { name: '执行效率', icon: '⚡' },
    data: { name: '数据利用', icon: '📊' },
    collaboration: { name: '专家协作', icon: '🤝' },
    response: { name: '响应质量', icon: '📝' },
  }

  const result = {}
  for (const [key, config] of Object.entries(dimConfig)) {
    if (breakdown[key] !== undefined) {
      result[key] = { ...config, score: Math.round(breakdown[key]) }
    }
  }
  return result
}

function getEvalLevel(score) {
  if (score >= 80) return '优秀'
  if (score >= 60) return '一般'
  if (score >= 40) return '较差'
  return '很差'
}

function getEvalLevelClass(score) {
  if (score >= 80) return 'eval-level--good'
  if (score >= 60) return 'eval-level--ok'
  if (score >= 40) return 'eval-level--bad'
  return 'eval-level--very-bad'
}

function getEvalSuggestions(messageId) {
  const state = props.messageEvalStates[messageId]
  if (!state?.evaluation?.suggestions) return []
  let suggestions = state.evaluation.suggestions
  if (typeof suggestions === 'string') {
    try { suggestions = JSON.parse(suggestions) } catch { return [] }
  }
  return Array.isArray(suggestions) ? suggestions : []
}

function scoreColor(score) {
  if (score >= 80) return '#52c41a'
  if (score >= 60) return '#faad14'
  if (score >= 40) return '#fa8c16'
  return '#ff4d4f'
}

// 工具名称映射
function toolDisplayName(name) {
  const map = {
    query_valuation: '查询估值',
    search_knowledge: '检索知识库',
    get_bond_temperature: '债市温度',
    get_valuation_list: '估值概览',
    get_author_opinions: '作者观点',
    calculate_metrics: '计算指标',
    consult_valuation_expert: '咨询估值专家',
    consult_market_analyst: '咨询择时分析师',
    consult_risk_assessor: '咨询风险评估师',
    consult_allocation_advisor: '咨询资产配置师',
    consult_fund_analyst: '咨询基金分析师',
  }
  return map[name] || name
}

function filterToolCalls(toolCalls) {
  if (!toolCalls) return []
  return toolCalls.filter(tc => !tc.name?.startsWith('consult_'))
}

function formatDuration(ms) {
  if (!ms) return '0ms'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`
}

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
</script>

<template>
  <div :class="['message', msg.role]">
    <div class="message-bubble" v-if="msg.role === 'user'">
      <span class="message-id-badge" v-if="msg.id" @click="emit('copy-message-id', msg.id)" :title="'点击复制消息ID: ' + msg.id">
        #{{ msg.id }}
      </span>
      {{ msg.content }}
    </div>
    <div v-else>
      <!-- 消息 ID 标识 + 评估状态 -->
      <div class="message-id-header" v-if="msg.id">
        <span class="message-id-badge" @click="emit('copy-message-id', msg.id)" :title="'点击复制消息ID: ' + msg.id">
          #{{ msg.id }} 📋
        </span>
        <!-- 评估状态指示器 -->
        <span
          class="eval-status-badge"
          :class="getEvalStatusClass(msg.id)"
          @click.stop="emit('toggle-eval', msg.id)"
          :title="getEvalStatusTitle(msg.id)"
        >
          <span v-if="messageEvalStates[msg.id]?.score > 0" class="eval-score-mini">
            {{ messageEvalStates[msg.id].score.toFixed(0) }}
          </span>
          <span v-else>{{ getEvalStatusIcon(msg.id) }}</span>
        </span>
      </div>
      <!-- 执行状态徽章 -->
      <div v-if="msg.execution_status && msg.execution_status !== 'completed'" class="execution-status-badge" :class="'status-' + msg.execution_status">
        <template v-if="msg.execution_status === 'streaming'">⏳ 后台执行中 <button class="btn-retry btn-ai-action" @click="emit('resume', convId)" title="恢复">🔄 恢复<span class="ai-agent-tooltip">投资分析助手</span></button></template>
        <template v-else-if="msg.execution_status === 'failed'">❌ 执行失败{{ msg.error_message ? ': ' + msg.error_message : '（超时或异常）' }}</template>
        <template v-else-if="msg.execution_status === 'cancelled'">⏹ 已取消</template>
        <template v-else-if="msg.execution_status === 'timeout'">⏰ 执行超时</template>
        <button v-if="index > 0" class="btn-retry btn-ai-action" @click="emit('retry', msg)" title="重试">🔄 重试<span class="ai-agent-tooltip">投资分析助手</span></button>
      </div>
      <!-- 专家分析展示 -->
      <div v-if="msg.specialist_results && msg.specialist_results.length" class="specialists-container">
        <div v-for="(s, j) in msg.specialist_results" :key="j" class="specialist-item">
          <div class="specialist-header" @click="s.expanded = !s.expanded">
            <span class="specialist-icon">{{ s.icon }}</span>
            <span class="specialist-name">{{ s.agent }}</span>
            <span v-if="s.duration_ms" class="specialist-time">{{ (s.duration_ms / 1000).toFixed(1) }}s</span>
            <!-- 专家反馈按钮 -->
            <div class="specialist-feedback" @click.stop>
              <template v-if="specialistFeedback[index + '_' + s.agent_key]">
                <span class="feedback-done">{{ specialistFeedback[index + '_' + s.agent_key] === 'helpful' ? '👍 已赞' : '👎 已踩' }}</span>
              </template>
              <template v-else>
                <button class="btn-spec-feedback" @click="emit('specialist-feedback', s, index, 'helpful')" title="分析准确">👍</button>
                <button class="btn-spec-feedback" @click="emit('specialist-feedback', s, index, 'unhelpful')" title="分析不准">👎</button>
              </template>
            </div>
            <span class="specialist-toggle">{{ s.expanded ? '▲' : '▼' }}</span>
          </div>
          <div v-if="s.expanded" class="specialist-analysis markdown-body" v-html="renderMarkdown(s.analysis || '（暂无分析内容）')"></div>
        </div>
      </div>
      <!-- 交叉审阅展示 -->
      <div v-if="msg.cross_review_results && msg.cross_review_results.length" class="specialists-container cross-review">
        <div class="cross-review-label">交叉审阅</div>
        <div v-for="(s, j) in msg.cross_review_results" :key="j" class="specialist-item cross-review-item">
          <div class="specialist-header" @click="s.expanded = !s.expanded">
            <span class="specialist-icon">{{ s.icon }}</span>
            <span class="specialist-name">{{ s.agent }} 审阅</span>
            <span v-if="s.duration_ms" class="specialist-time">{{ (s.duration_ms / 1000).toFixed(1) }}s</span>
            <span class="specialist-toggle">{{ s.expanded ? '▲' : '▼' }}</span>
          </div>
          <div v-if="s.expanded" class="specialist-analysis markdown-body" v-html="renderMarkdown(s.analysis || '（暂无审阅内容）')"></div>
        </div>
      </div>
      <!-- 执行过程面板（可展开） -->
      <div v-if="msg.specialist_results?.length || msg.phase_timings" class="execution-panel">
        <div class="execution-toggle" @click="msg._showExecution = !msg._showExecution">
          <span class="execution-toggle-icon">⚙️</span>
          <span>执行过程</span>
          <span v-if="msg.complexity" class="execution-complexity" :class="'complexity-' + msg.complexity">
            {{ msg.complexity === 'complex' ? '复杂' : msg.complexity === 'medium' ? '中等' : '简单' }}
          </span>
          <span v-if="msg.duration_ms" class="execution-total-time">{{ formatDuration(msg.duration_ms) }}</span>
          <span class="execution-toggle-arrow">{{ msg._showExecution ? '▲' : '▼' }}</span>
        </div>
        <Transition name="expand">
          <div v-if="msg._showExecution" class="execution-detail">
            <!-- 时间线 -->
            <div v-if="msg.phase_timings" class="execution-timeline">
              <div class="timeline-item" v-if="msg.phase_timings.clarification_ms">
                <span class="timeline-dot"></span>
                <span class="timeline-label">理解问题</span>
                <span class="timeline-time">{{ formatDuration(msg.phase_timings.clarification_ms) }}</span>
              </div>
              <div class="timeline-item" v-if="msg.phase_timings.rag_ms">
                <span class="timeline-dot"></span>
                <span class="timeline-label">知识检索</span>
                <span class="timeline-time">{{ formatDuration(msg.phase_timings.rag_ms) }}</span>
              </div>
              <div class="timeline-item" v-if="msg.phase_timings.orchestrator_ms">
                <span class="timeline-dot"></span>
                <span class="timeline-label">专家协作</span>
                <span class="timeline-time">{{ formatDuration(msg.phase_timings.orchestrator_ms) }}</span>
              </div>
            </div>
            <!-- 专家列表 -->
            <div v-if="msg.specialist_results?.length" class="execution-agents">
              <span class="execution-agents-label">参与专家：</span>
              <span v-for="s in msg.specialist_results" :key="s.agent_key" class="execution-agent-tag">
                {{ s.icon }} {{ s.agent }}
                <span v-if="s.duration_ms" class="execution-agent-time">{{ (s.duration_ms / 1000).toFixed(0) }}s</span>
              </span>
            </div>
            <!-- 工具调用统计 -->
            <div v-if="msg.tool_calls?.length" class="execution-tools">
              <span>工具调用：{{ msg.tool_calls.length }} 次</span>
            </div>
            <!-- Trace 详情按钮 -->
            <button class="btn-trace-detail" @click.stop="emit('toggle-trace', msg, index)">
              🔍 查看执行链路详情
            </button>
            <TraceDetail
              v-if="traceDetailVisible[`${convId}_${index}`]"
              :convId="convId"
              :traceId="traceDetailData[`${convId}_${index}`]?.trace_id"
            />
          </div>
        </Transition>
      </div>
      <!-- 工具调用展示 -->
      <div v-if="filterToolCalls(msg.tool_calls).length" class="tool-calls-container">
        <div v-for="(tc, j) in filterToolCalls(msg.tool_calls)" :key="j" class="tool-call-item">
          <div class="tool-call-header" @click="tc.expanded = !tc.expanded">
            <span class="tool-icon">🔧</span>
            <span class="tool-name">{{ toolDisplayName(tc.name) }}</span>
            <span class="tool-args">{{ JSON.stringify(tc.arguments || {}).slice(0, 40) }}</span>
            <span class="tool-toggle">{{ tc.expanded ? '▲' : '▼' }}</span>
          </div>
          <pre v-if="tc.expanded" class="tool-result">{{ tc.result_preview || '（无数据返回）' }}</pre>
        </div>
      </div>
      <div class="message-bubble markdown-body" v-html="renderMarkdown(msg.content)"></div>
      <!-- 反馈按钮 -->
      <div v-if="msg.role === 'assistant' && !feedbackGiven[index]" class="message-feedback">
        <button class="btn-msg-feedback" @click="emit('feedback', msg, index, 'helpful')" title="有用">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/></svg>
        </button>
        <button class="btn-msg-feedback" @click="emit('feedback', msg, index, 'unhelpful')" title="没用">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 2h3a2 2 0 012 2v7a2 2 0 01-2 2h-3"/></svg>
        </button>
      </div>
      <div v-else-if="feedbackGiven[index]" class="message-feedback-given">
        {{ feedbackGiven[index] === 'helpful' ? '已标记有用' : '已标记，感谢反馈' }}
      </div>
      <!-- RAG 来源标注 -->
      <div v-if="msg.rag && msg.rag.sources && msg.rag.sources.length" class="rag-sources">
        <div class="rag-header">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/>
          </svg>
          <span>参考来源 ({{ msg.rag.results_count || msg.rag.sources.length }})</span>
        </div>
        <div class="rag-tags">
          <span v-for="(s, j) in msg.rag.sources.slice(0, 5)" :key="j" :class="['rag-tag', 'rag-tag-' + s.type]">
            {{ s.type }}: {{ s.title ? s.title.slice(0, 20) : '' }}
          </span>
        </div>
      </div>
      <!-- 消息评估详情（可展开） -->
      <div v-if="msg.id && messageEvalStates[msg.id]?.expanded" class="message-eval-detail">
        <div v-if="messageEvalStates[msg.id]?.loading" class="eval-loading">
          <div class="eval-spinner"></div>
          <span>正在评估中，请稍候...</span>
        </div>
        <div v-else-if="messageEvalStates[msg.id]?.evaluation" class="eval-content">
          <!-- 评估分数概览 -->
          <div class="eval-header">
            <div class="eval-total">
              <span class="eval-total-score" :style="{ color: scoreColor(messageEvalStates[msg.id].evaluation.auto_score) }">
                {{ messageEvalStates[msg.id].evaluation.auto_score?.toFixed(0) }}
              </span>
              <span class="eval-total-label">总分</span>
            </div>
            <span class="eval-level" :class="getEvalLevelClass(messageEvalStates[msg.id].evaluation.auto_score)">
              {{ getEvalLevel(messageEvalStates[msg.id].evaluation.auto_score) }}
            </span>
          </div>

          <!-- 维度分数 -->
          <div class="eval-dimensions">
            <div v-for="(dim, key) in getEvalDimensions(msg.id)" :key="key" class="eval-dim">
              <span class="eval-dim-icon">{{ dim.icon }}</span>
              <span class="eval-dim-name">{{ dim.name }}</span>
              <div class="eval-dim-bar">
                <div class="eval-dim-bar-fill" :style="{ width: dim.score + '%', backgroundColor: scoreColor(dim.score) }"></div>
              </div>
              <span class="eval-dim-score" :style="{ color: scoreColor(dim.score) }">{{ dim.score }}</span>
            </div>
          </div>

          <!-- 优化建议 -->
          <div v-if="getEvalSuggestions(msg.id).length" class="eval-suggestions">
            <div class="eval-suggestions-title">💡 优化建议</div>
            <div v-for="(s, idx) in getEvalSuggestions(msg.id)" :key="idx" class="eval-suggestion">
              {{ s }}
            </div>
          </div>

          <!-- 操作按钮 -->
          <div class="eval-actions">
            <button class="btn-eval-llm" @click="emit('trigger-llm-eval', msg.id)">
              🤖 LLM 深度评估
            </button>
          </div>
        </div>
        <div v-else class="eval-empty">
          <div class="eval-empty-text">暂无评估数据</div>
          <button class="btn-eval-trigger" @click="emit('trigger-eval', msg.id)">
            📊 快速评估
          </button>
        </div>
      </div>
    </div>
    <div class="message-time">{{ formatTime(msg.created_at) }}</div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  flex-direction: column;
  max-width: 80%;
}

.message.user {
  align-self: flex-end;
}

.message.assistant {
  align-self: flex-start;
}

.message-bubble {
  padding: 0.75rem 1rem;
  border-radius: var(--radius-lg);
  font-size: 0.85rem;
  line-height: 1.6;
  word-break: break-word;
}

.message.user .message-bubble {
  background: var(--color-primary-500);
  color: white;
  border-bottom-right-radius: var(--radius-sm);
}

.message.assistant .message-bubble {
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  border-bottom-left-radius: var(--radius-sm);
}

.message-time {
  font-size: 0.6rem;
  color: var(--color-text-muted);
  margin-top: 0.25rem;
  padding: 0 0.25rem;
}

.message.user .message-time {
  text-align: right;
}

/* 消息 ID 样式 */
.message-id-badge {
  display: inline-block;
  font-size: 10px;
  color: var(--color-text-muted, #999);
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 6px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.2s;
  margin-right: 6px;
  vertical-align: middle;
}

.message-id-badge:hover {
  background: rgba(0, 0, 0, 0.1);
}

.message.user .message-id-badge {
  background: rgba(255, 255, 255, 0.2);
  color: rgba(255, 255, 255, 0.8);
}

.message.user .message-id-badge:hover {
  background: rgba(255, 255, 255, 0.3);
}

.message-id-header {
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 评估状态指示器 */
.eval-status-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.eval-status-badge:hover {
  transform: scale(1.1);
}

.eval-score-mini {
  font-size: 11px;
  font-weight: 700;
}

.eval-status--none {
  background: var(--color-bg-secondary, #f0f0f0);
  opacity: 0.7;
}

.eval-status--loading {
  background: var(--color-primary-100, #e6f7ff);
  animation: pulse 1.5s infinite;
}

.eval-status--good {
  background: #f6ffed;
}

.eval-status--ok {
  background: #fffbe6;
}

.eval-status--bad {
  background: #fff2f0;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* 消息评估详情 */
.message-eval-detail {
  margin-top: 8px;
  padding: 10px;
  background: var(--color-bg-secondary, #f5f5f5);
  border-radius: 8px;
  border-left: 3px solid var(--color-primary-500, #1890ff);
}

.eval-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--color-text-muted, #999);
}

.eval-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--color-border, #e8e8e8);
  border-top-color: var(--color-primary-500, #1890ff);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.eval-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.eval-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.eval-total {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.eval-total-score {
  font-size: 28px;
  font-weight: 700;
}

.eval-total-label {
  font-size: 12px;
  color: var(--color-text-muted, #999);
}

.eval-level {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 500;
}

.eval-level--good { background: #f6ffed; color: #52c41a; }
.eval-level--ok { background: #fffbe6; color: #faad14; }
.eval-level--bad { background: #fff7e6; color: #fa8c16; }
.eval-level--very-bad { background: #fff2f0; color: #ff4d4f; }

.eval-dimensions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.eval-dim {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.eval-dim-icon { font-size: 14px; width: 20px; text-align: center; }
.eval-dim-name { width: 60px; color: var(--color-text-secondary, #666); }

.eval-dim-bar {
  flex: 1;
  height: 6px;
  background: var(--color-bg-secondary, #f0f0f0);
  border-radius: 3px;
  overflow: hidden;
}

.eval-dim-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.5s ease;
}

.eval-dim-score {
  width: 30px;
  text-align: right;
  font-weight: 600;
}

.eval-suggestions { display: flex; flex-direction: column; gap: 4px; }
.eval-suggestions-title { font-size: 12px; font-weight: 600; color: var(--color-text-primary, #333); margin-bottom: 4px; }
.eval-suggestion { font-size: 12px; color: var(--color-text-secondary, #666); padding-left: 16px; position: relative; }
.eval-suggestion::before { content: '•'; position: absolute; left: 4px; color: var(--color-primary-500, #1890ff); }

.eval-empty { text-align: center; padding: 8px; }
.eval-empty-text { font-size: 12px; color: var(--color-text-muted, #999); margin-bottom: 8px; }

.btn-eval-trigger {
  padding: 6px 12px;
  background: var(--color-primary-500, #1890ff);
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-eval-trigger:hover { background: var(--color-primary-600, #40a9ff); }

.eval-actions { display: flex; gap: 8px; padding-top: 8px; border-top: 1px solid var(--color-border, #e8e8e8); }
.btn-eval-llm {
  padding: 6px 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: opacity 0.2s;
}
.btn-eval-llm:hover { opacity: 0.9; }

/* 执行状态徽章 */
.execution-status-badge {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.75rem;
  border-radius: var(--radius-md);
  font-size: 0.8rem;
  margin-bottom: 0.5rem;
  font-weight: 500;
}
.execution-status-badge.status-streaming { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
.dark .execution-status-badge.status-streaming { background: #451a03; color: #fcd34d; border-color: #78350f; }
.execution-status-badge.status-failed { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
.dark .execution-status-badge.status-failed { background: #450a0a; color: #fca5a5; border-color: #7f1d1d; }
.execution-status-badge.status-cancelled { background: #f3f4f6; color: #4b5563; border: 1px solid #d1d5db; }
.dark .execution-status-badge.status-cancelled { background: #1f2937; color: #9ca3af; border-color: #374151; }
.execution-status-badge.status-timeout { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }

.btn-retry {
  margin-left: auto;
  padding: 0.2rem 0.5rem;
  border: 1px solid currentColor;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-size: 0.75rem;
  cursor: pointer;
  opacity: 0.7;
  transition: opacity 0.15s;
}
.btn-retry:hover { opacity: 1; }

/* 专家分析展示 */
.specialists-container { margin-bottom: 0.5rem; }
.specialists-container.streaming { opacity: 0.9; }

.specialist-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 0.4rem;
  overflow: hidden;
  background: var(--color-bg-card);
}

.specialist-item.running { border-color: var(--color-primary-300); background: var(--color-primary-50); }
.dark .specialist-item.running { background: var(--color-primary-bg); }
.specialist-item.completed { border-color: var(--color-success-border, #10b981); }

.specialist-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  cursor: pointer;
  transition: background var(--transition-fast);
}
.specialist-header:hover { background: var(--color-bg-hover); }

.specialist-icon { font-size: 1rem; flex-shrink: 0; }
.specialist-icon.spinning { animation: spin 2s linear infinite; }
.specialist-name { font-size: 0.8rem; font-weight: 600; color: var(--color-text-primary); }

.specialist-status { margin-left: auto; display: flex; align-items: center; gap: 0.2rem; }
.specialist-status.done { color: var(--color-success, #10b981); font-size: 0.8rem; font-weight: 600; }
.specialist-status.running .dot { width: 5px; height: 5px; background: var(--color-primary-400); border-radius: 50%; animation: typingBounce 1.4s infinite ease-in-out; }
.specialist-status.running .dot:nth-child(2) { animation-delay: 0.2s; }
.specialist-status.running .dot:nth-child(3) { animation-delay: 0.4s; }

.specialist-time { font-size: 0.65rem; color: var(--color-text-muted); }
.specialist-toggle { font-size: 0.6rem; color: var(--color-text-muted); }

.specialist-feedback {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  margin-left: 0.3rem;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

@media (hover: none) { .specialist-feedback { opacity: 0.8; } }
@media (hover: hover) { .specialist-item:hover .specialist-feedback { opacity: 1; } }

.btn-spec-feedback {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.65rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  cursor: pointer;
  transition: all var(--transition-fast);
  padding: 0;
}
.btn-spec-feedback:hover { border-color: var(--color-primary-400); background: var(--color-primary-50); transform: scale(1.15); }

.feedback-done { font-size: 0.6rem; color: var(--color-text-muted); white-space: nowrap; }

.specialist-analysis {
  padding: 0.75rem 1rem;
  border-top: 1px solid var(--color-border);
  font-size: 0.82rem;
  line-height: 1.6;
  max-height: 500px;
  overflow-y: auto;
}

/* 交叉审阅 */
.specialists-container.cross-review { margin-top: 0.5rem; margin-bottom: 0.5rem; padding-left: 0.5rem; border-left: 3px solid var(--color-primary-300); }
.cross-review-label { font-size: 0.75rem; font-weight: 600; color: var(--color-primary); padding: 0.25rem 0.75rem; margin-bottom: 0.25rem; letter-spacing: 0.05em; }
.specialist-item.cross-review-item { border-color: var(--color-primary-200); background: var(--color-primary-50, rgba(201, 168, 76, 0.04)); }
.dark .specialist-item.cross-review-item { background: var(--color-primary-bg, rgba(201, 168, 76, 0.08)); }

/* 执行过程面板 */
.execution-panel { margin-bottom: 0.5rem; border: 1px solid var(--color-border-light); border-radius: 8px; overflow: hidden; }

.execution-toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--color-bg-secondary);
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  transition: background 0.15s;
}
.execution-toggle:hover { background: var(--color-bg-hover); }
.execution-toggle-icon { font-size: 0.9rem; }

.execution-complexity { font-size: 0.7rem; padding: 0.15rem 0.5rem; border-radius: 4px; font-weight: 600; }
.complexity-simple { background: #dcfce7; color: #166534; }
.complexity-medium { background: #fef9c3; color: #854d0e; }
.complexity-complex { background: #fee2e2; color: #991b1b; }

.execution-total-time { margin-left: auto; font-weight: 600; color: var(--color-text-primary); }
.execution-toggle-arrow { color: var(--color-text-muted); font-size: 0.75rem; }

.execution-detail { padding: 0.75rem; background: var(--color-bg-primary); border-top: 1px solid var(--color-border-light); }

.execution-timeline { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.75rem; padding: 0.5rem 0; }
.timeline-item { display: flex; align-items: center; gap: 0.35rem; font-size: 0.75rem; }
.timeline-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-primary-500); }
.timeline-label { color: var(--color-text-secondary); }
.timeline-time { font-weight: 600; color: var(--color-text-primary); }

.execution-agents { display: flex; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.5rem; font-size: 0.8rem; }
.execution-agents-label { color: var(--color-text-secondary); }
.execution-agent-tag { display: inline-flex; align-items: center; gap: 0.25rem; padding: 0.2rem 0.5rem; background: var(--color-bg-secondary); border-radius: 4px; font-size: 0.75rem; }
.execution-agent-time { color: var(--color-text-muted); font-size: 0.7rem; }
.execution-tools { font-size: 0.75rem; color: var(--color-text-muted); }

.btn-trace-detail {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.4rem;
  padding: 0.25rem 0.6rem;
  font-size: 0.72rem;
  color: var(--color-primary);
  background: transparent;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.btn-trace-detail:hover { background: var(--color-primary); color: white; }

/* 工具调用展示 */
.tool-calls-container { margin-bottom: 0.4rem; border-left: 3px solid var(--color-primary-300); padding-left: 0.5rem; }
.tool-calls-container.streaming { border-left-color: var(--color-primary-400); opacity: 0.8; }
.tool-call-item { margin-bottom: 0.2rem; }
.tool-call-header { display: flex; align-items: center; gap: 0.4rem; font-size: 0.75rem; color: var(--color-text-secondary); padding: 0.2rem 0; cursor: pointer; }
.tool-call-header:hover { color: var(--color-text-primary); }
.tool-icon { font-size: 0.7rem; }
.tool-icon.spinning { animation: spin 1.5s linear infinite; }
.tool-name { font-weight: 600; color: var(--color-primary-600); }
.tool-args { font-size: 0.65rem; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.tool-toggle { font-size: 0.6rem; color: var(--color-text-muted); margin-left: auto; }

.tool-result {
  background: var(--color-bg-hover);
  padding: 0.4rem 0.6rem;
  border-radius: var(--radius-sm);
  font-size: 0.7rem;
  max-height: 120px;
  overflow-y: auto;
  margin: 0.2rem 0 0.3rem;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text-secondary);
}

/* 消息反馈按钮 */
.message-feedback {
  display: flex;
  gap: 0.25rem;
  padding: 0.25rem 0;
  opacity: 0;
  transition: opacity var(--transition-fast);
}
@media (hover: none) { .message-feedback { opacity: 0.8; } }
@media (hover: hover) { .message:hover .message-feedback { opacity: 1; } }

.btn-msg-feedback {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.btn-msg-feedback:hover { color: var(--color-primary); border-color: var(--color-primary); background: var(--color-primary-50); }

.message-feedback-given { font-size: 0.7rem; color: var(--color-text-muted); padding: 0.25rem 0; }

/* RAG 来源标注 */
.rag-sources { margin-top: 0.5rem; padding: 0.5rem 0.75rem; background: var(--color-bg-input); border-radius: var(--radius-sm); font-size: 0.7rem; }
.rag-header { display: flex; align-items: center; gap: 0.3rem; color: var(--color-text-secondary); font-weight: 500; margin-bottom: 0.4rem; }
.rag-tags { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-bottom: 0.3rem; }
.rag-tag { display: inline-flex; align-items: center; padding: 0.15rem 0.4rem; border-radius: var(--radius-sm); font-size: 0.65rem; background: var(--color-bg-card); border: 1px solid var(--color-border); color: var(--color-text-secondary); }
.rag-tag-估值 { border-color: #f59e0b; color: #d97706; background: var(--color-warning-bg); }
.rag-tag-作者文章 { border-color: #10b981; color: #059669; background: var(--color-success-bg); }
.rag-tag-技能知识 { border-color: #8b5cf6; color: #7c3aed; background: rgba(139, 92, 246, 0.1); }
.rag-tag-文章 { border-color: #3b82f6; color: #2563eb; background: var(--color-info-bg); }

@keyframes typingBounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Markdown 样式 */
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) { margin-top: 0.75rem; margin-bottom: 0.4rem; font-size: 0.9rem; }
.markdown-body :deep(p) { margin: 0.3rem 0; }
.markdown-body :deep(ul),
.markdown-body :deep(ol) { padding-left: 1.2rem; margin: 0.3rem 0; }
.markdown-body :deep(code) { background: rgba(255, 255, 255, 0.06); padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.8rem; }
.dark .markdown-body :deep(code) { background: rgba(255,255,255,0.1); }
.markdown-body :deep(strong) { font-weight: 600; }
.markdown-body :deep(table) { width: 100%; border-collapse: collapse; margin: 0.5rem 0; font-size: 0.8rem; }
.markdown-body :deep(th),
.markdown-body :deep(td) { padding: 0.35rem 0.6rem; border: 1px solid var(--color-border); text-align: left; }
.markdown-body :deep(th) { background: var(--color-surface-hover); font-weight: 600; }
.markdown-body :deep(blockquote) { border-left: 3px solid var(--color-primary-300); padding: 0.3rem 0.75rem; margin: 0.5rem 0; color: var(--color-text-secondary); background: var(--color-surface-hover); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
.markdown-body :deep(hr) { border: none; border-top: 1px solid var(--color-border); margin: 0.75rem 0; }
</style>
