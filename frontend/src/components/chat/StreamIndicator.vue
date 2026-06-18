<script setup>
import { renderMarkdown } from '../../composables/useMarkdown'

const props = defineProps({
  currentStream: { type: Object, default: null },
})

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

function formatElapsed(ms) {
  if (!ms) return '0s'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}
</script>

<template>
  <div class="message assistant">
    <!-- 已完成的专家分析 -->
    <div v-if="currentStream.completedSpecialists.length > 0" class="specialists-container streaming">
      <div v-for="(s, j) in currentStream.completedSpecialists" :key="j" class="specialist-item completed">
        <div class="specialist-header" @click="s.expanded = !s.expanded">
          <span class="specialist-icon">{{ s.icon }}</span>
          <span class="specialist-name">{{ s.agent }}</span>
          <span class="specialist-status done">✓</span>
          <span v-if="s.duration_ms" class="specialist-time">{{ (s.duration_ms / 1000).toFixed(1) }}s</span>
          <span class="specialist-toggle">{{ s.expanded ? '▲' : '▼' }}</span>
        </div>
        <div v-if="s.expanded" class="specialist-analysis markdown-body" v-html="renderMarkdown(s.analysis || '（暂无分析内容）')"></div>
      </div>
    </div>
    <!-- 正在工作的专家 -->
    <div v-if="currentStream.activeSpecialists.length > 0" class="specialists-container streaming">
      <div v-for="(s, j) in currentStream.activeSpecialists" :key="j" class="specialist-item running">
        <div class="specialist-header">
          <span class="specialist-icon spinning">{{ s.icon }}</span>
          <span class="specialist-name">正在咨询{{ s.agent }}...</span>
          <span class="specialist-status running">
            <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          </span>
        </div>
      </div>
    </div>
    <!-- 交叉审阅进度 -->
    <div v-if="currentStream.completedCrossReviews.length > 0" class="specialists-container streaming cross-review">
      <div class="cross-review-label">交叉审阅</div>
      <div v-for="(s, j) in currentStream.completedCrossReviews" :key="j" class="specialist-item completed cross-review-item">
        <div class="specialist-header" @click="s.expanded = !s.expanded">
          <span class="specialist-icon">{{ s.icon }}</span>
          <span class="specialist-name">{{ s.agent }} 审阅</span>
          <span class="specialist-status done">✓</span>
          <span v-if="s.duration_ms" class="specialist-time">{{ (s.duration_ms / 1000).toFixed(1) }}s</span>
          <span class="specialist-toggle">{{ s.expanded ? '▲' : '▼' }}</span>
        </div>
        <div v-if="s.expanded" class="specialist-analysis markdown-body" v-html="renderMarkdown(s.analysis || '（暂无审阅内容）')"></div>
      </div>
    </div>
    <div v-if="currentStream.crossReviewSpecialists.length > 0" class="specialists-container streaming cross-review">
      <div v-for="(s, j) in currentStream.crossReviewSpecialists" :key="j" class="specialist-item running cross-review-item">
        <div class="specialist-header">
          <span class="specialist-icon spinning">{{ s.icon }}</span>
          <span class="specialist-name">{{ s.agent }} 交叉审阅中...</span>
          <span class="specialist-status running">
            <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          </span>
        </div>
      </div>
    </div>
    <!-- 实时工具调用 -->
    <div v-if="filterToolCalls(currentStream.currentToolCalls).length > 0" class="tool-calls-container streaming">
      <div v-for="(tc, j) in filterToolCalls(currentStream.currentToolCalls)" :key="j" class="tool-call-item">
        <div class="tool-call-header">
          <span class="tool-icon spinning">&#9881;</span>
          <span class="tool-name">{{ toolDisplayName(tc.name) }}</span>
          <span class="tool-args">{{ JSON.stringify(tc.arguments || {}).slice(0, 40) }}</span>
        </div>
      </div>
    </div>
    <!-- 执行计划 -->
    <div v-if="currentStream.executionPlan" class="execution-plan">
      <div class="plan-header">
        <span class="plan-icon">📋</span>
        <span class="plan-label">执行计划</span>
        <span class="plan-complexity" :class="'complexity-' + currentStream.executionPlan.complexity">
          {{ {simple: '简单', medium: '中等', complex: '复杂'}[currentStream.executionPlan.complexity] || currentStream.executionPlan.complexity }}
        </span>
      </div>
      <div v-if="currentStream.executionPlan.reason" class="plan-reason">{{ currentStream.executionPlan.reason }}</div>
      <div v-if="currentStream.activeSpecialists.length > 0 || currentStream.completedSpecialists.length > 0" class="plan-steps">
        <div v-for="(s, i) in currentStream.completedSpecialists" :key="'done-'+i" class="plan-step done">
          <span class="step-check">✓</span>
          <span class="step-name">{{ s.agent }}</span>
        </div>
        <div v-for="(s, i) in currentStream.activeSpecialists" :key="'run-'+i" class="plan-step running">
          <span class="step-spinner"></span>
          <span class="step-name">{{ s.agent }}</span>
        </div>
      </div>
    </div>
    <!-- 阶段进度条 -->
    <div v-if="currentStream.totalPhases > 0" class="stream-progress">
      <div class="progress-bar-container">
        <div class="progress-bar-fill" :style="{ width: currentStream.progressPct + '%' }"></div>
      </div>
      <div v-if="currentStream.substep" class="substep-text">
        <span class="dot-pulse"></span> {{ currentStream.substep }}
      </div>
    </div>
    <!-- 状态文字 -->
    <div v-else-if="currentStream.streamStatus === 'searching'" class="stream-status">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
      <span class="status-text">{{ currentStream.statusMessage || '正在检索知识库...' }}</span>
    </div>
    <div v-else-if="currentStream.streamStatus === 'thinking'" class="stream-status">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
      <span class="status-text">{{ currentStream.statusMessage || '正在分析问题，决定需要咨询哪些专家...' }}</span>
    </div>
    <div v-else-if="currentStream.streamStatus === 'calling_specialist'" class="stream-status">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
      <span class="status-text">{{ currentStream.statusMessage || '专家团队正在分析中...' }}</span>
    </div>
    <div v-else-if="currentStream.streamStatus === 'calling_tool'" class="stream-status">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
      <span class="status-text">正在调用工具...</span>
    </div>
    <div v-else-if="currentStream.streamStatus === 'cross_reviewing'" class="stream-status">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
      <span class="status-text">正在进行交叉审阅...</span>
    </div>
    <div v-else class="message-bubble typing">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
    </div>
    <!-- 实时计时器 -->
    <div v-if="currentStream.elapsedMs > 0" class="elapsed-timer">
      <span class="elapsed-icon">⏱</span>
      <span class="elapsed-text">已执行 {{ formatElapsed(currentStream.elapsedMs) }}</span>
    </div>
  </div>
</template>

<style scoped>
.message.assistant { align-self: flex-start; }

.specialists-container { margin-bottom: 0.5rem; }
.specialists-container.streaming { opacity: 0.9; }
.specialists-container.cross-review { margin-top: 0.5rem; margin-bottom: 0.5rem; padding-left: 0.5rem; border-left: 3px solid var(--color-primary-300); }

.cross-review-label { font-size: 0.75rem; font-weight: 600; color: var(--color-primary); padding: 0.25rem 0.75rem; margin-bottom: 0.25rem; letter-spacing: 0.05em; }

.specialist-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 0.6rem;
  overflow: hidden;
  background: var(--color-bg-card);
}
.specialist-item.running { border-color: var(--color-primary-300); background: var(--color-primary-50); }
.dark .specialist-item.running { background: var(--color-primary-bg); }
.specialist-item.completed { border-color: var(--color-success-border, #10b981); }
.specialist-item.cross-review-item { border-color: var(--color-primary-200); background: var(--color-primary-50, rgba(201, 168, 76, 0.04)); }
.dark .specialist-item.cross-review-item { background: var(--color-primary-bg, rgba(201, 168, 76, 0.08)); }

.specialist-header { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0.75rem; cursor: pointer; transition: background var(--transition-fast); }
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

.specialist-analysis {
  padding: 0.75rem 1rem;
  border-top: 1px solid var(--color-border);
  font-size: 0.82rem;
  line-height: 1.6;
  max-height: 500px;
  overflow-y: auto;
}

/* 工具调用 */
.tool-calls-container { margin-bottom: 0.6rem; border-left: 3px solid var(--color-primary-300); padding-left: 0.5rem; }
.tool-calls-container.streaming { border-left-color: var(--color-primary-400); opacity: 0.8; }
.tool-call-item { margin-bottom: 0.2rem; }
.tool-call-header { display: flex; align-items: center; gap: 0.4rem; font-size: 0.75rem; color: var(--color-text-secondary); padding: 0.2rem 0; }
.tool-icon { font-size: 0.72rem; }
.tool-icon.spinning { animation: spin 1.5s linear infinite; }
.tool-name { font-weight: 600; color: var(--color-primary-600); }
.tool-args { font-size: 0.65rem; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }

/* 执行计划 */
.execution-plan { margin: 0.5rem 0; padding: 0.6rem 0.8rem; background: linear-gradient(135deg, rgba(201, 168, 76, 0.04), rgba(201, 168, 76, 0.02)); border: 1px solid rgba(201, 168, 76, 0.15); border-radius: var(--radius-md); font-size: 0.8rem; }
.plan-header { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.3rem; }
.plan-icon { font-size: 0.9rem; }
.plan-label { font-weight: 600; color: var(--color-text-primary); }
.plan-complexity { font-size: 0.72rem; font-weight: 600; padding: 0.15rem 0.45rem; border-radius: var(--radius-sm); margin-left: auto; }
.complexity-simple { background: rgba(16, 185, 129, 0.1); color: #10b981; }
.complexity-medium { background: rgba(245, 158, 11, 0.1); color: #f59e0b; }
.complexity-complex { background: rgba(201, 168, 76, 0.1); color: var(--color-primary); }
.plan-reason { font-size: 0.75rem; color: var(--color-text-muted); margin-bottom: 0.6rem; }
.plan-steps { display: flex; flex-direction: column; gap: 0.35rem; }
.plan-step { display: flex; align-items: center; gap: 0.4rem; font-size: 0.78rem; }
.plan-step.done { color: #10b981; }
.plan-step.running { color: var(--color-primary); }
.step-check { font-weight: 700; }
.step-spinner { width: 12px; height: 12px; border: 2px solid rgba(201, 168, 76, 0.2); border-top-color: var(--color-primary); border-radius: 50%; animation: spin 0.8s linear infinite; }

/* 进度条 */
.stream-progress { padding: 0.5rem 0.75rem; }
.progress-bar-container { height: 4px; background: var(--color-border); border-radius: 2px; overflow: hidden; margin-bottom: 0.6rem; }
.progress-bar-fill { height: 100%; background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light, var(--color-primary))); border-radius: 2px; transition: width 0.3s ease; }
.substep-text { display: flex; align-items: center; gap: 0.4rem; font-size: 0.75rem; color: var(--color-text-muted); }
.dot-pulse { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--color-primary); animation: dot-pulse 1.2s ease-in-out infinite; }
@keyframes dot-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(0.8); } }

/* 流式状态 */
.stream-status { display: flex; align-items: center; gap: 0.5rem; padding: 0.4rem 0.75rem; font-size: 0.8rem; color: var(--color-text-muted); }
.stream-status .status-text { margin-left: 0.3rem; }

.typing { display: flex; align-items: center; gap: 0.5rem; padding: 0.75rem 1.25rem; }
.dot { width: 7px; height: 7px; background: var(--color-text-muted); border-radius: 50%; animation: typingBounce 1.4s infinite ease-in-out; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes typingBounce { 0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; } 40% { transform: scale(1); opacity: 1; } }

/* 实时计时器 */
.elapsed-timer { display: flex; align-items: center; gap: 0.4rem; padding: 0.3rem 0.75rem; font-size: 0.75rem; color: var(--color-text-muted); opacity: 0.85; }
.elapsed-icon { font-size: 0.8rem; animation: pulse 2s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 0.6; } 50% { opacity: 1; } }
.elapsed-text { font-variant-numeric: tabular-nums; font-weight: 500; color: var(--color-primary-500); }

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) { margin-top: 0.75rem; margin-bottom: 0.6rem; font-size: 0.9rem; }
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

@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
</style>
