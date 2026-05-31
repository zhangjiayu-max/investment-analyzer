<script setup>
import { ref, onMounted } from 'vue'
import { getTraceDetail } from '../api'

const props = defineProps({
  convId: { type: Number, required: true },
  traceId: { type: String, required: true },
})

const trace = ref(null)
const loading = ref(false)
const error = ref(null)

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await getTraceDetail(props.convId, props.traceId)
    trace.value = data
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

function formatDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`
}

function statusLabel(s) {
  return { completed: '✅ 完成', failed: '❌ 失败', cancelled: '⏹ 已取消', running: '⏳ 执行中' }[s] || s
}

function errorCategoryLabel(c) {
  return {
    none: '无错误', model_error: '模型错误', tool_error: '工具错误',
    rag_miss: 'RAG 无结果', timeout: '执行超时', cancelled: '用户取消',
  }[c] || c
}
</script>

<template>
  <div class="trace-detail">
    <div v-if="loading" class="trace-loading">加载中...</div>
    <div v-else-if="error" class="trace-error">{{ error }}</div>
    <template v-else-if="trace">
      <!-- 基本信息 -->
      <div class="trace-header">
        <div class="trace-meta">
          <span class="trace-id">Trace: {{ trace.trace.trace_id }}</span>
          <span :class="['trace-status', 'status-' + trace.trace.status]">{{ statusLabel(trace.trace.status) }}</span>
          <span v-if="trace.trace.error_category !== 'none'" class="trace-error-cat">
            {{ errorCategoryLabel(trace.trace.error_category) }}
          </span>
        </div>
        <div class="trace-timing">
          总耗时: <strong>{{ formatDuration(trace.trace.total_ms) }}</strong>
        </div>
      </div>

      <!-- 质量指标 -->
      <div v-if="trace.trace.quality_metrics" class="trace-metrics">
        <div class="metrics-label">质量指标</div>
        <div class="metrics-grid">
          <div class="metric-item">
            <span class="metric-name">RAG 覆盖</span>
            <span class="metric-value" :class="{ good: JSON.parse(trace.trace.quality_metrics).rag_coverage > 0 }">
              {{ (JSON.parse(trace.trace.quality_metrics).rag_coverage * 100).toFixed(0) }}%
            </span>
          </div>
          <div class="metric-item">
            <span class="metric-name">工具成功率</span>
            <span class="metric-value" :class="{ good: JSON.parse(trace.trace.quality_metrics).tool_success_rate > 0.8 }">
              {{ (JSON.parse(trace.trace.quality_metrics).tool_success_rate * 100).toFixed(0) }}%
            </span>
          </div>
          <div class="metric-item">
            <span class="metric-name">专家完成度</span>
            <span class="metric-value" :class="{ good: JSON.parse(trace.trace.quality_metrics).specialist_completion > 0.8 }">
              {{ (JSON.parse(trace.trace.quality_metrics).specialist_completion * 100).toFixed(0) }}%
            </span>
          </div>
        </div>
      </div>

      <!-- 时间线 -->
      <div v-if="trace.trace.phase_timings" class="trace-timeline">
        <div class="timeline-label">执行时间线</div>
        <div class="timeline-items">
          <div v-for="(ms, phase) in JSON.parse(trace.trace.phase_timings)" :key="phase" class="timeline-item">
            <span class="timeline-phase">{{ phase.replace('_ms', '') }}</span>
            <div class="timeline-bar-container">
              <div class="timeline-bar" :style="{ width: Math.min(ms / (trace.trace.total_ms || 1) * 100, 100) + '%' }"></div>
            </div>
            <span class="timeline-time">{{ formatDuration(ms) }}</span>
          </div>
        </div>
      </div>

      <!-- Agent 执行记录 -->
      <div v-if="trace.agent_runs?.length" class="trace-runs">
        <div class="runs-label">Agent 执行记录 ({{ trace.agent_runs.length }})</div>
        <div v-for="run in trace.agent_runs" :key="run.id" class="run-item">
          <span class="run-agent">{{ run.agent_name }}</span>
          <span class="run-key">{{ run.agent_key }}</span>
          <span class="run-time">{{ formatDuration(run.duration_ms) }}</span>
        </div>
      </div>

      <!-- 工具审计日志 -->
      <div v-if="trace.tool_audit_logs?.length" class="trace-tools">
        <div class="tools-label">工具调用审计 ({{ trace.tool_audit_logs.length }})</div>
        <div v-for="log in trace.tool_audit_logs" :key="log.id" :class="['tool-item', { failed: !log.success }]">
          <span class="tool-name">{{ log.tool_name }}</span>
          <span :class="['tool-status', log.success ? 'ok' : 'err']">{{ log.success ? '✓' : '✗' }}</span>
          <span class="tool-time">{{ formatDuration(log.duration_ms) }}</span>
          <span v-if="log.error_category !== 'none'" class="tool-error">{{ log.error_category }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.trace-detail {
  font-size: 0.78rem;
  padding: 0.5rem;
}

.trace-loading, .trace-error {
  color: var(--color-text-muted);
  padding: 0.5rem;
}

.trace-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.6rem;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.trace-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.trace-id {
  font-family: monospace;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.trace-status {
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
}

.status-completed { background: rgba(34, 197, 94, 0.1); color: #22c55e; }
.status-failed { background: rgba(239, 68, 68, 0.1); color: #ef4444; }
.status-cancelled { background: rgba(156, 163, 175, 0.1); color: #9ca3af; }

.trace-error-cat {
  font-size: 0.7rem;
  color: var(--color-danger, #ef4444);
}

.trace-timing {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

/* 质量指标 */
.trace-metrics {
  margin-bottom: 0.6rem;
}

.metrics-label, .timeline-label, .runs-label, .tools-label {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 0.3rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.metrics-grid {
  display: flex;
  gap: 0.8rem;
}

.metric-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.metric-name {
  font-size: 0.68rem;
  color: var(--color-text-muted);
}

.metric-value {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

.metric-value.good {
  color: var(--color-success, #22c55e);
}

/* 时间线 */
.trace-timeline {
  margin-bottom: 0.6rem;
}

.timeline-items {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.timeline-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.timeline-phase {
  width: 80px;
  font-size: 0.7rem;
  color: var(--color-text-muted);
  text-align: right;
}

.timeline-bar-container {
  flex: 1;
  height: 6px;
  background: var(--color-border);
  border-radius: 3px;
  overflow: hidden;
}

.timeline-bar {
  height: 100%;
  background: var(--color-primary);
  border-radius: 3px;
  min-width: 2px;
}

.timeline-time {
  width: 50px;
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

/* Agent 执行记录 */
.trace-runs {
  margin-bottom: 0.6rem;
}

.run-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.2rem 0;
  border-bottom: 1px solid var(--color-border);
}

.run-agent {
  font-weight: 500;
}

.run-key {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  font-family: monospace;
}

.run-time {
  margin-left: auto;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

/* 工具审计日志 */
.trace-tools {
  margin-bottom: 0.4rem;
}

.tool-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.2rem 0;
  border-bottom: 1px solid var(--color-border);
}

.tool-item.failed {
  background: rgba(239, 68, 68, 0.05);
}

.tool-name {
  font-weight: 500;
}

.tool-status.ok { color: var(--color-success, #22c55e); }
.tool-status.err { color: var(--color-danger, #ef4444); }

.tool-time {
  margin-left: auto;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.tool-error {
  font-size: 0.68rem;
  color: var(--color-danger, #ef4444);
}
</style>
