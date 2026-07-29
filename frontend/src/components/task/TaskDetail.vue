<script setup>
import { ref, watch, computed, onBeforeUnmount, reactive, defineAsyncComponent } from 'vue'
import { marked } from 'marked'
import { getTask, getTaskImages, pollTask, chat, analyzeTaskImages, getTraceDetail, getMessages } from '../../api'
import ImageGrid from '../knowledge/ImageGrid.vue'
import StockChart from '../valuation/StockChart.vue'
// ValuationHistory 改为懒加载，避免与 MobileApp/Home 的动态 import 冲突导致打包到主 chunk
const ValuationHistory = defineAsyncComponent(() => import('../valuation/ValuationHistory.vue'))

const props = defineProps({ taskId: Number })
const emit = defineEmits(['back'])

// 保存 pollTask 返回的停止函数，组件卸载或重新加载任务时调用以避免内存泄漏与重复请求
let stopPollFn = null

const task = ref(null)
const images = ref([])
const loading = ref(true)
const chatQuestion = ref('')
const chatAnswer = ref('')
const chatLoading = ref(false)
const imageAnalysis = ref(null)
const imageAnalysisLoading = ref(false)
const activeTab = ref('analysis')
const valuationHistoryRef = ref(null)

// ── Agent 执行细节展示状态 ──────────────────────────────────────
// 动态进度条阶段（反映实际 Agent 编排阶段，区别于上方 4 步固定流程）
const phases = [
  { icon: '🔍', label: '检索' },
  { icon: '🤖', label: '专家分析' },
  { icon: '🔄', label: '交叉审阅' },
  { icon: '⚖️', label: '仲裁' },
  { icon: '📝', label: '综合报告' },
]

// 工具调用展开/收起状态（用 reactive Set 触发模板更新）
const expandedTools = reactive(new Set())

const renderedAnalysis = computed(() => {
  if (!task.value?.llm_analysis) return ''
  return marked(task.value.llm_analysis)
})

const statusText = computed(() => {
  const map = {
    pending: '等待中...',
    fetching: '正在抓取文章...',
    analyzing: 'AI 正在分析...',
    done: '分析完成',
    error: '分析失败',
  }
  return map[task.value?.status] || ''
})

const statusStep = computed(() => {
  const map = { pending: 0, fetching: 1, analyzing: 2, done: 3, error: -1 }
  return map[task.value?.status] ?? 0
})

// 是否有动态阶段数据（后端提供 phase_index/current_phase/total_phases 时才展示动态进度条）
const hasPhaseData = computed(() => {
  const t = task.value
  return !!(t && (t.phase_index != null || t.current_phase != null || t.total_phases))
})

// 当前动态阶段索引：优先用后端字段，否则按 status 回退
const currentPhase = computed(() => {
  const t = task.value
  if (!t) return -1
  if (typeof t.phase_index === 'number') return t.phase_index
  if (typeof t.current_phase === 'number') return t.current_phase
  const fallback = { pending: -1, fetching: 0, analyzing: 1, done: phases.length - 1, error: -1 }
  return fallback[t.status] ?? -1
})

// 专家状态文案
function specialistStatusText(status) {
  return { running: '执行中', success: '完成', failed: '失败' }[status] || status || ''
}

// 工具调用结果格式化：截断到 500 字符
function formatResult(result) {
  if (result == null) return ''
  let text = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
  if (text.length > 500) {
    text = text.slice(0, 500) + `\n... (截断，共 ${text.length} 字符)`
  }
  return text
}

// 思考流摘要：截断到 1000 字符
function formatReasoning(text) {
  if (!text) return ''
  return text.length > 1000 ? text.slice(0, 1000) + '\n... (思考流已截断)' : text
}

function toggleTool(i) {
  if (expandedTools.has(i)) expandedTools.delete(i)
  else expandedTools.add(i)
}

/**
 * 拉取 Agent 执行细节（专家编排 / 工具调用 / 思考流）。
 * 前向兼容：后端 task 若已直接携带这些字段则跳过；否则尝试用 conversation_id + trace_id
 * 调用 trace API，回退到 messages 元数据。任一环节失败均静默处理（执行细节为增强信息）。
 */
async function fetchExecutionDetail(t) {
  if (!t) return
  // 后端已直接携带，无需再请求
  if (t.specialists?.length || t.tool_calls?.length || t.reasoning) return
  const convId = t.conversation_id
  if (!convId) return
  try {
    if (t.trace_id) {
      const { data } = await getTraceDetail(convId, t.trace_id)
      if (data?.agent_runs?.length) {
        t.specialists = data.agent_runs.map(r => ({
          agent_key: r.agent_key || r.id,
          name: r.agent_name || r.agent || r.agent_key,
          icon: r.icon || '🤖',
          status: r.status === 'running' ? 'running' : (r.status === 'failed' ? 'failed' : 'success'),
          duration_ms: r.duration_ms,
        }))
      }
      if (data?.tool_audit_logs?.length) {
        t.tool_calls = data.tool_audit_logs.map(l => {
          let args = l.arguments ?? l.args
          if (typeof args === 'string') { try { args = JSON.parse(args) } catch (_) { /* keep raw */ } }
          return {
            tool_name: l.tool_name || l.name,
            args: args ?? null,
            result: l.result_preview || l.result || '',
            error: !l.success,
          }
        })
      }
      if (data?.trace?.reasoning) t.reasoning = data.trace.reasoning
    } else {
      // 回退：从对话消息元数据中提取
      const { data } = await getMessages(convId, 5)
      const msg = data?.messages?.find(m => m.metadata)
      if (msg?.metadata) {
        const md = typeof msg.metadata === 'string' ? JSON.parse(msg.metadata) : msg.metadata
        if (md.specialists) t.specialists = md.specialists
        if (md.tool_calls) t.tool_calls = md.tool_calls
        if (md.reasoning_content) t.reasoning = md.reasoning_content
      }
    }
  } catch (e) {
    // 静默失败：执行细节是增强信息，不影响主流程
    console.debug('fetchExecutionDetail failed:', e)
  }
}

async function loadTask() {
  // 切换任务前先停掉上一次的轮询，避免并发 pollTask 累积
  if (stopPollFn) { stopPollFn(); stopPollFn = null }
  loading.value = true
  try {
    const { data } = await getTask(props.taskId)
    task.value = data
    await fetchExecutionDetail(data)
    if (data.status === 'pending' || data.status === 'fetching' || data.status === 'analyzing') {
      stopPollFn = pollTask(props.taskId, async (updated) => {
        task.value = updated
        await fetchExecutionDetail(updated)
        if (updated.status === 'done') loadImages()
      })
    } else if (data.status === 'done') {
      loadImages()
    }
  } catch (e) {
    console.error('Load task failed:', e)
  } finally {
    loading.value = false
  }
}

// 组件卸载时停止轮询，避免切走后继续打后端 + 闭包持有已卸载组件的 ref
onBeforeUnmount(() => {
  if (stopPollFn) { stopPollFn(); stopPollFn = null }
})

async function loadImages() {
  try {
    const { data } = await getTaskImages(props.taskId)
    images.value = data.images || []
  } catch (e) { /* ignore */ }
}

async function onAnalyzeImages() {
  imageAnalysisLoading.value = true
  imageAnalysis.value = null
  try {
    const { data } = await analyzeTaskImages(props.taskId)
    imageAnalysis.value = data.results || []
  } catch (e) {
    imageAnalysis.value = [{ error: '分析失败: ' + e.message }]
  } finally {
    imageAnalysisLoading.value = false
  }
}

async function onChat() {
  if (!chatQuestion.value.trim()) return
  chatLoading.value = true
  try {
    const { data } = await chat(chatQuestion.value, task.value?.llm_analysis || '')
    chatAnswer.value = data.answer
  } catch (e) {
    chatAnswer.value = '问答失败: ' + e.message
  } finally {
    chatLoading.value = false
  }
}

function onImageParsed(result) {
  activeTab.value = 'valuation'
  setTimeout(() => {
    valuationHistoryRef.value?.loadHistory()
  }, 100)
}

watch(() => props.taskId, () => {
  imageAnalysis.value = null
  loadTask()
}, { immediate: true })
</script>

<template>
  <div class="task-detail bg-mesh">
    <!-- Loading -->
    <div v-if="loading && !task" class="loading-state">
      <div class="spinner-lg"></div>
    </div>

    <template v-else-if="task">
      <!-- Back button + Status -->
      <div class="detail-header">
        <button @click="emit('back')" class="btn-ghost back-btn">
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
          </svg>
          返回列表
        </button>
      </div>

      <!-- Progress Steps -->
      <div v-if="task.status !== 'done' && task.status !== 'error'" class="card progress-card editorial-card">
        <div class="steps">
          <div :class="['step', { active: statusStep >= 1, done: statusStep > 1 }]">
            <div class="step-dot font-jet">
              <svg v-if="statusStep > 1" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
              <span v-else>1</span>
            </div>
            <span class="step-label terminal-label">抓取文章</span>
          </div>
          <div :class="['step-line', { active: statusStep > 1 }]"></div>
          <div :class="['step', { active: statusStep >= 2, done: statusStep > 2 }]">
            <div class="step-dot font-jet">
              <div v-if="statusStep === 2" class="step-spinner"></div>
              <svg v-else-if="statusStep > 2" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
              <span v-else>2</span>
            </div>
            <span class="step-label terminal-label">AI 分析</span>
          </div>
          <div :class="['step-line', { active: statusStep > 2 }]"></div>
          <div :class="['step', { active: statusStep >= 3 }]">
            <div class="step-dot font-jet">
              <svg v-if="statusStep >= 3" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
              <span v-else>3</span>
            </div>
            <span class="step-label terminal-label">完成</span>
          </div>
        </div>
        <p v-if="task.title" class="progress-title">{{ task.title }}</p>
      </div>

      <!-- Dynamic Phase Progress（Agent 编排阶段，后端提供阶段数据时展示） -->
      <div v-if="hasPhaseData && task.status !== 'error'" class="card phase-card editorial-card">
        <div class="phase-card-header">
          <h4 class="phase-card-title title">Agent 执行阶段</h4>
          <span v-if="task.phaseLabel || task.phase_label" class="phase-current font-jet">
            {{ task.phaseLabel || task.phase_label }}
          </span>
        </div>
        <div class="progress-steps">
          <div
            v-for="(phase, i) in phases"
            :key="i"
            class="progress-step"
            :class="{ active: currentPhase === i, done: currentPhase > i }"
          >
            <span class="step-icon">{{ phase.icon }}</span>
            <span class="step-label">{{ phase.label }}</span>
          </div>
        </div>
      </div>

      <!-- Error -->
      <div v-if="task.status === 'error'" class="card error-card">
        <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <span>分析失败: {{ task.error_msg }}</span>
      </div>

      <!-- Article Info -->
      <div v-if="task.title" class="card article-info-card editorial-card">
        <h2 class="article-title editorial-title-lg">{{ task.title }}</h2>
        <a :href="task.url" target="_blank" class="article-link font-jet">{{ task.url }}</a>
        <div class="article-meta">
          <span v-if="task.author" class="meta-item">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
            {{ task.author }}
          </span>
          <span v-if="task.publish_time" class="meta-item">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
            <span class="font-jet">{{ task.publish_time }}</span>
          </span>
        </div>
        <div v-if="task.codes_found?.length" class="codes-found">
          <span class="codes-label terminal-label">识别到的标的：</span>
          <span v-for="code in task.codes_found" :key="code" class="badge badge-info font-jet">{{ code }}</span>
        </div>
      </div>

      <!-- Agent 执行细节：专家编排 / 工具调用 / 思考流（task 携带数据时才展示） -->

      <!-- A. 专家编排区 -->
      <div v-if="task.specialists?.length" class="card specialists-panel editorial-card">
        <div class="card-header editorial-card-header">
          <h4 class="card-title title">参与专家 <span class="count-badge font-jet">{{ task.specialists.length }}</span></h4>
        </div>
        <div class="specialist-list">
          <div v-for="s in task.specialists" :key="s.agent_key" class="specialist-item reveal-stagger">
            <span class="specialist-icon">{{ s.icon || '🤖' }}</span>
            <span class="specialist-name">{{ s.name || s.agent_key }}</span>
            <span class="specialist-status" :class="s.status">{{ specialistStatusText(s.status) }}</span>
            <span v-if="s.duration_ms" class="specialist-duration font-jet">{{ (s.duration_ms / 1000).toFixed(1) }}s</span>
          </div>
        </div>
      </div>

      <!-- B. 工具调用区 -->
      <div v-if="task.tool_calls?.length" class="card tool-calls-panel editorial-card">
        <div class="card-header editorial-card-header">
          <h4 class="card-title title">工具调用 <span class="count-badge font-jet">{{ task.tool_calls.length }}</span></h4>
        </div>
        <div class="tool-call-list">
          <div v-for="(tc, i) in task.tool_calls" :key="i" class="tool-call-item reveal-stagger">
            <div class="tool-header" @click="toggleTool(i)">
              <span class="tool-name font-jet">{{ tc.tool_name }}</span>
              <span class="tool-status" :class="tc.error ? 'error' : 'success'">
                {{ tc.error ? '失败' : '成功' }}
              </span>
              <span class="tool-toggle">{{ expandedTools.has(i) ? '▼' : '▶' }}</span>
            </div>
            <div v-if="expandedTools.has(i)" class="tool-detail">
              <div v-if="tc.args" class="tool-args">
                <div class="tool-detail-label terminal-label">参数</div>
                <pre>{{ typeof tc.args === 'string' ? tc.args : JSON.stringify(tc.args, null, 2) }}</pre>
              </div>
              <div v-if="tc.result" class="tool-result">
                <div class="tool-detail-label terminal-label">结果</div>
                <pre>{{ formatResult(tc.result) }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- C. 思考流摘要 -->
      <div v-if="task.reasoning" class="card reasoning-panel editorial-card">
        <div class="card-header editorial-card-header">
          <h4 class="card-title title">思考过程</h4>
        </div>
        <div class="reasoning-content">{{ formatReasoning(task.reasoning) }}</div>
      </div>

      <!-- Images -->
      <div v-if="images.length" class="card images-card editorial-card">
        <div class="card-header editorial-card-header">
          <h3 class="card-title title">文章图片 <span class="count-badge font-jet">{{ images.length }}</span></h3>
          <button
            @click="onAnalyzeImages"
            :disabled="imageAnalysisLoading"
            class="btn-primary btn-ai-action"
          >
            <svg v-if="imageAnalysisLoading" class="spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/></svg>
            {{ imageAnalysisLoading ? '分析中...' : '解读全部图片' }}
            <span class="ai-agent-tooltip">文章解读助手</span>
          </button>
        </div>
        <ImageGrid :images="images" @parsed="onImageParsed" />
      </div>

      <!-- Tab Switch: Analysis / Valuation -->
      <div v-if="task.status === 'done'" class="tab-bar">
        <button
          @click="activeTab = 'analysis'"
          :class="['tab-btn', { active: activeTab === 'analysis' }]"
        >
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
          AI 分析
        </button>
        <button
          @click="activeTab = 'valuation'"
          :class="['tab-btn', { active: activeTab === 'valuation' }]"
        >
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
          估值历史
        </button>
      </div>

      <!-- Image Analysis Results -->
      <div v-if="imageAnalysis" class="card editorial-card">
        <div class="card-header editorial-card-header">
          <h3 class="card-title title">图片解读结果</h3>
        </div>
        <div class="image-results">
          <div v-for="(result, i) in imageAnalysis" :key="i" class="image-result-item reveal-stagger">
            <div v-if="result.error" class="text-danger">{{ result.error }}</div>
            <div v-else>
              <div class="result-header">
                <span class="badge badge-info">{{ result.image_type || '图片' }}</span>
                <span class="result-title">{{ result.title || '' }}</span>
              </div>
              <p class="result-summary">{{ result.summary || '' }}</p>
              <details v-if="result.data && Object.keys(result.data).length" class="result-details">
                <summary>查看结构化数据</summary>
                <pre class="font-jet">{{ JSON.stringify(result.data, null, 2) }}</pre>
              </details>
            </div>
          </div>
        </div>
      </div>

      <!-- Market Data -->
      <div v-if="task.market_data && Object.keys(task.market_data).length" class="card editorial-card">
        <div class="card-header editorial-card-header">
          <h3 class="card-title title">行情数据</h3>
        </div>
        <div class="market-grid">
          <div v-for="(info, code) in task.market_data" :key="code" class="market-item reveal-stagger">
            <div class="market-name">{{ info.name || '未知' }}</div>
            <div class="market-code font-jet">{{ code }}</div>
            <div v-if="info.recommendation" class="market-rec">
              <span
                :class="[
                  'badge',
                  info.recommendation?.includes('低估') ? 'badge-success' :
                  info.recommendation?.includes('合理') ? 'badge-warning' :
                  'badge-danger'
                ]"
              >{{ info.recommendation }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- LLM Analysis -->
      <div v-if="task.llm_analysis && activeTab === 'analysis'" class="card editorial-card">
        <div class="card-header editorial-card-header">
          <h3 class="card-title title">AI 分析</h3>
        </div>
        <div class="prose" v-html="renderedAnalysis"></div>
      </div>

      <!-- Valuation History -->
      <div v-if="activeTab === 'valuation'">
        <ValuationHistory ref="valuationHistoryRef" />
      </div>

      <!-- K-line Charts -->
      <div v-if="task.codes_found?.length" class="chart-grid">
        <StockChart v-for="code in task.codes_found.slice(0, 4)" :key="code" :symbol="code" :name="task.market_data?.[code]?.name || code" />
      </div>

      <!-- Chat Follow-up -->
      <div v-if="task.status === 'done'" class="card chat-card editorial-card">
        <div class="card-header editorial-card-header">
          <h3 class="card-title title">追问</h3>
        </div>
        <form @submit.prevent="onChat" class="chat-form">
          <input
            v-model="chatQuestion"
            placeholder="针对分析结果提问..."
            class="input-field"
          />
          <button type="submit" :disabled="chatLoading || !chatQuestion.trim()" class="btn-primary btn-ai-action">
            <svg v-if="chatLoading" class="spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/></svg>
            {{ chatLoading ? '思考中...' : '发送' }}
            <span class="ai-agent-tooltip">投资研究助手</span>
          </button>
        </form>
        <div v-if="chatAnswer" class="chat-answer">
          <div class="chat-bubble ai">{{ chatAnswer }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.task-detail {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}

/* Header */
.detail-header {
  display: flex;
  align-items: center;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.875rem;
}

/* Progress Steps */
.progress-card {
  padding: 1.5rem;
}

.steps {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
}

.step {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  opacity: 0.4;
  transition: opacity var(--transition-fast);
}

.step.active {
  opacity: 1;
}

.step-dot {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--color-bg-input);
  border: 2px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
  transition: all var(--transition-fast);
}

.step.active .step-dot {
  background: var(--color-primary-500);
  border-color: var(--color-primary-500);
  color: white;
}

.step.done .step-dot {
  background: var(--color-success);
  border-color: var(--color-success);
  color: white;
}

.step-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.step-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  font-weight: 500;
}

.step.active .step-label {
  color: var(--color-primary-600);
}

.step-line {
  width: 60px;
  height: 2px;
  background: var(--color-border);
  margin: 0 0.5rem;
  margin-bottom: 1.5rem;
  transition: background var(--transition-fast);
}

.step-line.active {
  background: var(--color-success);
}

.progress-title {
  text-align: center;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin: 1rem 0 0 0;
}

/* Error */
.error-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem 1.25rem;
  color: var(--color-danger);
  background: rgba(239, 68, 68, 0.05);
  border-color: rgba(239, 68, 68, 0.2);
}

/* Article Info */
.article-info-card {
  padding: 1.5rem;
}

.article-title {
  color: var(--color-text-primary);
  margin: 0 0 0.25rem 0;
}

.article-link {
  font-size:  0.75rem;
  color: var(--color-primary-500);
  text-decoration: none;
  word-break: break-all;
}
.article-link:hover {
  text-decoration: underline;
}

.article-meta {
  display: flex;
  gap: 1rem;
  margin-top: 0.5rem;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.codes-found {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.75rem;
  flex-wrap: wrap;
}

.codes-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

/* Card shared */
.card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 1.25rem;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.card-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.count-badge {
  font-size: 0.72rem;
  font-weight: 500;
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
}

/* Images */
.images-card {
  padding: 1.25rem;
}

/* Image Results */
.image-results {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.image-result-item {
  padding: 1rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
}

.result-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.result-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.result-summary {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin: 0;
  line-height: 1.6;
}

.result-details summary {
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-primary-500);
  margin-top: 0.5rem;
}

.result-details pre {
  margin-top: 0.5rem;
  padding: 0.75rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 0.75rem;
  overflow-x: auto;
}

/* Market Data */
.market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.75rem;
}

.market-item {
  padding: 0.75rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
}

.market-name {
  font-weight: 600;
  font-size: 0.875rem;
  color: var(--color-text-primary);
}

.market-code {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-top: 0.15rem;
}

.market-rec {
  margin-top: 0.5rem;
}

/* Chart Grid */
.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
  gap: 1rem;
}

/* Chat */
.chat-card {
  padding: 1.25rem;
}

.chat-form {
  display: flex;
  gap: 0.5rem;
}

.chat-form .input-field {
  flex: 1;
}

.chat-answer {
  margin-top: 0.75rem;
}

.chat-bubble {
  padding: 0.75rem 1rem;
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  line-height: 1.6;
  white-space: pre-wrap;
}

.chat-bubble.ai {
  background: var(--color-primary-50);
  color: var(--color-text-primary);
  border: 1px solid var(--color-primary-200);
}

.dark .chat-bubble.ai {
  background: var(--color-primary-bg);
  border-color: rgba(201, 168, 76, 0.2);
}

.text-danger {
  color: var(--color-danger);
  font-size: 0.875rem;
}

/* ── 动态阶段进度条 ────────────────────────────────────── */
.phase-card {
  padding: 1.25rem;
}

.phase-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.phase-card-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.phase-current {
  font-size: 0.75rem;
  color: var(--color-primary-600);
  background: var(--color-primary-50);
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius-sm);
}

.dark .phase-current {
  background: rgba(201, 168, 76, 0.12);
  color: var(--color-primary-500);
}

.progress-steps {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.progress-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.4rem;
  flex: 1;
  opacity: 0.4;
  transition: opacity var(--transition-fast);
  position: relative;
}

.progress-step.active,
.progress-step.done {
  opacity: 1;
}

.progress-step:not(:last-child)::after {
  content: '';
  position: absolute;
  top: 14px;
  right: -50%;
  width: 100%;
  height: 2px;
  background: var(--color-border);
  z-index: 0;
}

.progress-step.done:not(:last-child)::after {
  background: var(--color-success);
}

.step-icon {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--color-bg-input);
  border: 2px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  z-index: 1;
  transition: all var(--transition-fast);
}

.progress-step.active .step-icon {
  background: var(--color-primary-500);
  border-color: var(--color-primary-500);
  box-shadow: 0 0 0 4px rgba(201, 168, 76, 0.15);
}

.progress-step.done .step-icon {
  background: var(--color-success);
  border-color: var(--color-success);
}

.progress-step .step-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  font-weight: 500;
}

.progress-step.active .step-label {
  color: var(--color-primary-600);
  font-weight: 600;
}

.progress-step.done .step-label {
  color: var(--color-text-secondary);
}

/* ── 专家编排区 ────────────────────────────────────── */
.specialists-panel {
  padding: 1.25rem;
}

.specialist-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.specialist-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 0.75rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
}

.specialist-icon {
  font-size: 1.1rem;
  line-height: 1;
}

.specialist-name {
  flex: 1;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.specialist-status {
  font-size: 0.72rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  font-weight: 500;
}

.specialist-status.running {
  background: rgba(201, 168, 76, 0.15);
  color: var(--color-primary-600);
}

.specialist-status.success {
  background: rgba(34, 197, 94, 0.12);
  color: var(--color-success);
}

.specialist-status.failed {
  background: rgba(239, 68, 68, 0.12);
  color: var(--color-danger);
}

.specialist-duration {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  min-width: 40px;
  text-align: right;
}

/* ── 工具调用区 ────────────────────────────────────── */
.tool-calls-panel {
  padding: 1.25rem;
}

.tool-call-list {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.tool-call-item {
  background: var(--color-bg-input);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.tool-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 0.75rem;
  cursor: pointer;
  user-select: none;
  transition: background var(--transition-fast);
}

.tool-header:hover {
  background: var(--color-bg-card);
}

.tool-name {
  flex: 1;
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.tool-status {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  font-weight: 500;
}

.tool-status.success {
  background: rgba(34, 197, 94, 0.12);
  color: var(--color-success);
}

.tool-status.error {
  background: rgba(239, 68, 68, 0.12);
  color: var(--color-danger);
}

.tool-toggle {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  width: 14px;
  text-align: center;
}

.tool-detail {
  padding: 0.5rem 0.75rem 0.75rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-bg-card);
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.tool-detail-label {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 0.25rem;
}

.tool-args pre,
.tool-result pre {
  margin: 0;
  padding: 0.6rem;
  background: var(--color-bg-input);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  line-height: 1.5;
  color: var(--color-text-secondary);
  overflow-x: auto;
  max-height: 240px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── 思考流摘要 ────────────────────────────────────── */
.reasoning-panel {
  padding: 1.25rem;
}

.reasoning-content {
  font-size: 0.8rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-bg-input);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-primary-500);
  border-radius: var(--radius-md);
  padding: 0.85rem 1rem;
  max-height: 320px;
  overflow-y: auto;
}

@media (max-width: 640px) {
  .chart-grid {
    grid-template-columns: 1fr;
  }
  .steps {
    flex-wrap: wrap;
  }
  .step-line {
    width: 30px;
  }
  .chat-form {
    flex-direction: column;
  }
  /* 动态阶段进度条：移动端允许换行 */
  .progress-steps {
    flex-wrap: wrap;
    gap: 0.75rem 0.5rem;
  }
  .progress-step {
    flex: 1 1 30%;
    min-width: 60px;
  }
  .progress-step:not(:last-child)::after {
    display: none;
  }
  /* 工具调用 header 在移动端紧凑展示 */
  .tool-header {
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  .tool-name {
    width: 100%;
  }
  .specialist-item {
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .specialist-duration {
    margin-left: auto;
  }
}
</style>
