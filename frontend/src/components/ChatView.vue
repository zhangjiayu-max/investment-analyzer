<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { marked } from 'marked'
import {
  listConversations, createConversation, deleteConversation,
  getMessages, sendMessage, sendMessageStream,
  submitChatFeedback, submitLlmFeedback,
  cancelConversationExecution,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'

const conversations = ref([])
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })
const selectedConv = ref(null)
const messages = ref([])
const inputText = ref('')
const sending = ref(false)
const messagesContainer = ref(null)

// 流式对话状态
const streamStatus = ref('')  // '' | 'searching' | 'calling_tool' | 'thinking' | 'answering'
const statusMessage = ref('')  // 详细状态消息
const executionPlan = ref(null)  // 执行计划
const currentToolCalls = ref([])  // 当前正在执行的工具调用
const streamAbort = ref(null)  // AbortController
const activeSpecialists = ref([])  // 正在工作的专家列表
const completedSpecialists = ref([])  // 已完成的专家分析结果
const crossReviewSpecialists = ref([])  // 正在交叉审阅的专家
const completedCrossReviews = ref([])  // 已完成的交叉审阅结果

// 计时器
const elapsedMs = ref(0)
let elapsedTimer = null
const lastTiming = ref(null)

onMounted(async () => {
  await loadConversations()
})

onBeforeUnmount(() => {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
  cancelStream()
})

async function loadConversations() {
  try {
    const { data } = await listConversations()
    conversations.value = data.conversations || []
  } catch (e) {
    console.error('Failed to load conversations:', e)
  }
}

async function selectConversation(conv) {
  selectedConv.value = conv
  try {
    const { data } = await getMessages(conv.id)
    messages.value = (data.messages || []).map(msg => {
      // 解析 metadata JSON（后端已解析，此处兜底）
      if (msg.metadata && typeof msg.metadata === 'string') {
        try { msg.metadata = JSON.parse(msg.metadata) } catch {}
      }
      if (msg.metadata && typeof msg.metadata === 'object') {
        msg.specialist_results = msg.metadata.specialist_results || []
        msg.cross_review_results = msg.metadata.cross_review_results || []
        msg.tool_calls = msg.metadata.tool_calls || []
        msg.execution_status = msg.metadata.execution_status
        msg.complexity = msg.metadata.complexity
        msg.phase_timings = msg.metadata.phase_timings
        msg.error_message = msg.metadata.error_message
        // 从 phase_timings.total_ms 恢复 duration_ms
        if (msg.phase_timings?.total_ms && !msg.duration_ms) {
          msg.duration_ms = msg.phase_timings.total_ms
        }
        // 为 specialist_results 添加 expanded 属性
        msg.specialist_results.forEach(s => { s.expanded = false })
        msg.cross_review_results.forEach(s => { s.expanded = false })
      }
      return msg
    })
    await nextTick()
    scrollToBottom()
  } catch (e) {
    console.error('Failed to load messages:', e)
    messages.value = []
  }
}

async function handleNewConversation() {
  try {
    const { data } = await createConversation({
      title: '新对话',
    })
    await loadConversations()
    const conv = conversations.value.find(c => c.id === data.conversation_id)
    if (conv) selectConversation(conv)
  } catch (e) {
    console.error('Failed to create conversation:', e)
  }
}

function handleDeleteConversation(conv, e) {
  e.stopPropagation()
  confirm.value = {
    visible: true,
    title: '删除确认',
    message: '确定删除这个对话吗？',
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      try {
        await deleteConversation(conv.id)
        if (selectedConv.value?.id === conv.id) {
          selectedConv.value = null
          messages.value = []
        }
        await loadConversations()
      } catch (e) {
        console.error('Failed to delete conversation:', e)
      }
    }
  }
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || !selectedConv.value || sending.value) return

  inputText.value = ''
  sending.value = true
  streamStatus.value = 'searching'
  statusMessage.value = ''
  executionPlan.value = null
  currentToolCalls.value = []
  activeSpecialists.value = []
  completedSpecialists.value = []
  lastTiming.value = null

  // 启动计时器
  elapsedMs.value = 0
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = setInterval(() => { elapsedMs.value += 1000 }, 1000)

  // 立即显示用户消息
  messages.value.push({ role: 'user', content: text, created_at: new Date().toISOString() })
  await nextTick()
  scrollToBottom()

  // 使用 SSE 流式接口
  streamAbort.value = sendMessageStream(selectedConv.value.id, text, (event) => {
    handleStreamEvent(event)
  })
}

function handleStreamEvent(event) {
  const { type, data } = event

  switch (type) {
    case 'status':
      streamStatus.value = data.message.includes('检索') ? 'searching' : 'thinking'
      statusMessage.value = data.message
      break

    case 'plan':
      executionPlan.value = data
      break

    case 'rag_sources':
      currentToolCalls.value._ragSources = data.sources
      break

    case 'tool_call':
      streamStatus.value = 'calling_tool'
      currentToolCalls.value.push({
        name: data.name,
        arguments: data.arguments,
        result_preview: data.result_preview,
        expanded: false,
      })
      nextTick(() => scrollToBottom())
      break

    case 'specialist_start':
      streamStatus.value = 'calling_specialist'
      activeSpecialists.value.push({
        agent_key: data.agent_key,
        agent: data.agent,
        icon: data.icon,
        status: 'running',
      })
      nextTick(() => scrollToBottom())
      break

    case 'specialist_done':
      // 从活跃列表移到完成列表
      activeSpecialists.value = activeSpecialists.value.filter(s => s.agent_key !== data.agent_key)
      completedSpecialists.value.push({
        agent_key: data.agent_key,
        agent: data.agent,
        icon: data.icon,
        analysis: data.analysis,
        duration_ms: data.duration_ms,
        expanded: false,
      })
      nextTick(() => scrollToBottom())
      break

    case 'cross_review_start':
      streamStatus.value = 'cross_reviewing'
      crossReviewSpecialists.value.push({
        agent_key: data.agent_key,
        agent: data.agent,
        icon: data.icon,
        status: 'running',
      })
      nextTick(() => scrollToBottom())
      break

    case 'cross_review_done':
      crossReviewSpecialists.value = crossReviewSpecialists.value.filter(s => s.agent_key !== data.agent_key)
      completedCrossReviews.value.push({
        agent_key: data.agent_key,
        agent: data.agent,
        icon: data.icon,
        analysis: data.analysis,
        duration_ms: data.duration_ms,
        expanded: false,
      })
      nextTick(() => scrollToBottom())
      break

    case 'answer':
      streamStatus.value = 'answering'
      // 分离 Phase A 和 Phase B 的专家结果
      const allSpecResults = data.specialist_results || (completedSpecialists.value.length > 0 ? [...completedSpecialists.value] : [])
      const phaseAResults = allSpecResults.filter(s => !s.is_cross_review)
      const phaseBResults = allSpecResults.filter(s => s.is_cross_review)
      // 构建最终消息
      const assistantMsg = {
        role: 'assistant',
        content: data.content,
        created_at: new Date().toISOString(),
        specialist_results: phaseAResults.length > 0 ? phaseAResults : null,
        cross_review_results: phaseBResults.length > 0 ? phaseBResults : (completedCrossReviews.value.length > 0 ? [...completedCrossReviews.value] : null),
        tool_calls: currentToolCalls.value.length > 0 ? [...currentToolCalls.value] : null,
        rag: currentToolCalls.value._ragSources ? { sources: currentToolCalls.value._ragSources } : null,
      }
      messages.value.push(assistantMsg)
      nextTick(() => scrollToBottom())
      break

    case 'done':
      if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
      lastTiming.value = {
        duration_ms: data.duration_ms,
        phase_timings: data.phase_timings,
      }
      // 将 timing 附加到最后一条 assistant 消息
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.role === 'assistant' && lastTiming.value) {
        lastMsg.duration_ms = lastTiming.value.duration_ms
        lastMsg.phase_timings = lastTiming.value.phase_timings
      }
      sending.value = false
      streamStatus.value = ''
      currentToolCalls.value = []
      activeSpecialists.value = []
      completedSpecialists.value = []
      crossReviewSpecialists.value = []
      completedCrossReviews.value = []
      loadConversations()
      break

    case 'error':
      if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
      messages.value.push({
        role: 'assistant',
        content: '发生错误: ' + (data.message || '未知错误'),
        created_at: new Date().toISOString(),
      })
      sending.value = false
      streamStatus.value = ''
      currentToolCalls.value = []
      activeSpecialists.value = []
      completedSpecialists.value = []
      break
  }
}

function cancelStream() {
  // 通知后端取消执行，将 streaming 消息标记为 cancelled
  if (selectedConv.value?.id && sending.value) {
    cancelConversationExecution(selectedConv.value.id).catch(() => {})
  }
  if (streamAbort.value) {
    streamAbort.value.abort()
    streamAbort.value = null
  }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
  elapsedMs.value = 0
  // 显示取消提示消息
  messages.value.push({
    role: 'assistant',
    content: '⏹ 执行已取消',
    created_at: new Date().toISOString(),
    cancelled: true,
  })
  sending.value = false
  streamStatus.value = ''
  statusMessage.value = ''
  executionPlan.value = null
  currentToolCalls.value = []
  activeSpecialists.value = []
  completedSpecialists.value = []
  crossReviewSpecialists.value = []
  completedCrossReviews.value = []
  nextTick(() => scrollToBottom())
}

function scrollToBottom() {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

function copyConvId() {
  if (!selectedConv.value) return
  const text = `对话ID: ${selectedConv.value.id}`
  navigator.clipboard.writeText(text).then(() => {
    // 简单提示
    const btn = document.querySelector('.btn-conv-id')
    if (btn) {
      btn.classList.add('copied')
      setTimeout(() => btn.classList.remove('copied'), 1500)
    }
  }).catch(() => {
    // fallback
    window.prompt('复制对话ID:', selectedConv.value.id)
  })
}

function retryMessage(userMsg) {
  if (!userMsg || userMsg.role !== 'user' || sending.value) return
  inputText.value = userMsg.content
  handleSend()
}

function formatDuration(ms) {
  if (!ms) return '0ms'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`
}

function formatElapsed(ms) {
  if (!ms) return '0s'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}

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

// 过滤掉 consult_* 的编排调用（已在专家结果中展示）
function filterToolCalls(toolCalls) {
  if (!toolCalls) return []
  return toolCalls.filter(tc => !tc.name?.startsWith('consult_'))
}

function renderMarkdown(text) {
  return marked(text || '')
}

// 反馈功能
const feedbackGiven = ref({})  // {msgIndex: 'helpful'|'unhelpful'}
const specialistFeedback = ref({})  // {msgIndex_agentKey: 'helpful'|'unhelpful'}
const feedbackModal = ref({ visible: false, type: '', feedbackType: '', msgIndex: 0, msg: null, specialist: null, note: '' })

function submitFeedback() {
  if (feedbackModal.value.type === 'message') {
    submitMessageFeedback()
  } else {
    submitSpecialistFeedback()
  }
}

async function handleSpecialistFeedback(s, msgIndex, feedbackType) {
  const key = `${msgIndex}_${s.agent_key}`
  if (specialistFeedback.value[key]) return
  // 弹出反馈输入框
  feedbackModal.value = {
    visible: true,
    type: 'specialist',
    feedbackType,
    msgIndex,
    specialist: s,
    note: '',
  }
}

async function submitSpecialistFeedback() {
  const modal = feedbackModal.value
  const s = modal.specialist
  const key = `${modal.msgIndex}_${s.agent_key}`
  specialistFeedback.value[key] = modal.feedbackType
  feedbackModal.value.visible = false
  try {
    await submitLlmFeedback({
      caller: `specialist:${s.agent_key}`,
      input_summary: (s.analysis || '').slice(0, 200),
      output_summary: modal.feedbackType === 'helpful' ? '用户认为分析有用' : '用户认为分析无用',
      rating: modal.feedbackType,
      comment: modal.note || '',
    })
  } catch (e) {
    console.error('专家反馈提交失败:', e)
  }
}

async function handleFeedback(msg, index, feedbackType) {
  if (feedbackGiven.value[index]) return
  // 弹出反馈输入框
  feedbackModal.value = {
    visible: true,
    type: 'message',
    feedbackType,
    msgIndex: index,
    msg,
    note: '',
  }
}

async function submitMessageFeedback() {
  const modal = feedbackModal.value
  feedbackGiven.value[modal.msgIndex] = modal.feedbackType
  feedbackModal.value.visible = false
  try {
    const inputSummary = modal.msgIndex > 0 ? (messages.value[modal.msgIndex - 1]?.content || '').slice(0, 200) : ''
    const outputSummary = (modal.msg.content || '').slice(0, 200)
    await submitChatFeedback(modal.msg.id, modal.feedbackType, modal.note || '', inputSummary, outputSummary)
  } catch (e) {
    console.error('反馈提交失败:', e)
  }
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
  <div class="chat-page">
    <!-- 对话列表 -->
    <div class="conv-sidebar">
      <div class="conv-header">
        <h3>对话</h3>
        <button @click="handleNewConversation()" class="btn-new-conv" title="新建对话">
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
          </svg>
        </button>
      </div>
      <div class="conv-list">
        <div
          v-for="conv in conversations" :key="conv.id"
          @click="selectConversation(conv)"
          :class="['conv-item', { active: selectedConv?.id === conv.id }]"
        >
          <div class="conv-icon">🤖</div>
          <div class="conv-info">
            <div class="conv-title">{{ conv.title }}</div>
            <div class="conv-meta">
              <span class="conv-time">{{ formatTime(conv.updated_at) }}</span>
            </div>
          </div>
          <button @click="handleDeleteConversation(conv, $event)" class="btn-delete-conv" title="删除">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div v-if="!conversations.length" class="conv-empty">
          <p>暂无对话</p>
          <p class="conv-empty-hint">点击右上角 + 创建</p>
        </div>
      </div>
    </div>

    <!-- 聊天区域 -->
    <div class="chat-area">
      <template v-if="selectedConv">
        <!-- 聊天头部 -->
        <div class="chat-header">
          <div class="chat-header-info">
            <span class="chat-agent-icon">🤖</span>
            <span class="chat-agent-name">投资分析助手</span>
          </div>
          <button class="btn-conv-id" @click="copyConvId" :title="'对话 #' + selectedConv.id">
            <span class="conv-id-text">#{{ selectedConv.id }}</span>
            <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <rect x="9" y="9" width="13" height="13" rx="2" stroke-width="2"/>
              <path stroke-width="2" d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
            </svg>
          </button>
        </div>

        <!-- 消息列表 -->
        <div ref="messagesContainer" class="messages-container">
          <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
            <div class="message-bubble" v-if="msg.role === 'user'">{{ msg.content }}</div>
            <div v-else>
              <!-- 执行状态徽章 -->
              <div v-if="msg.execution_status && msg.execution_status !== 'completed'" class="execution-status-badge" :class="'status-' + msg.execution_status">
                <template v-if="msg.execution_status === 'streaming'">⏳ 执行中断（切页面或刷新导致）</template>
                <template v-else-if="msg.execution_status === 'failed'">❌ 执行失败{{ msg.error_message ? ': ' + msg.error_message : '（超时或异常）' }}</template>
                <template v-else-if="msg.execution_status === 'cancelled'">⏹ 已取消</template>
                <template v-else-if="msg.execution_status === 'timeout'">⏰ 执行超时</template>
                <button v-if="i > 0" class="btn-retry" @click="retryMessage(messages[i - 1])" title="重试">🔄 重试</button>
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
                      <template v-if="specialistFeedback[i + '_' + s.agent_key]">
                        <span class="feedback-done">{{ specialistFeedback[i + '_' + s.agent_key] === 'helpful' ? '👍 已赞' : '👎 已踩' }}</span>
                      </template>
                      <template v-else>
                        <button class="btn-spec-feedback" @click="handleSpecialistFeedback(s, i, 'helpful')" title="分析准确">👍</button>
                        <button class="btn-spec-feedback" @click="handleSpecialistFeedback(s, i, 'unhelpful')" title="分析不准">👎</button>
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
              <!-- 工具调用展示（过滤掉 consult_* 编排调用） -->
              <div v-if="filterToolCalls(msg.tool_calls).length" class="tool-calls-container">
                <div v-for="(tc, j) in filterToolCalls(msg.tool_calls)" :key="j" class="tool-call-item">
                  <div class="tool-call-header" @click="tc.expanded = !tc.expanded">
                    <span class="tool-icon">&#128295;</span>
                    <span class="tool-name">{{ toolDisplayName(tc.name) }}</span>
                    <span class="tool-args">{{ JSON.stringify(tc.arguments || {}).slice(0, 40) }}</span>
                    <span class="tool-toggle">{{ tc.expanded ? '▲' : '▼' }}</span>
                  </div>
                  <pre v-if="tc.expanded" class="tool-result">{{ tc.result_preview || '（无数据返回）' }}</pre>
                </div>
              </div>
              <div class="message-bubble markdown-body" v-html="renderMarkdown(msg.content)"></div>
              <!-- 耗时摘要 -->
              <div v-if="msg.duration_ms" class="message-timing">
                <span class="timing-total">总耗时 {{ formatDuration(msg.duration_ms) }}</span>
                <template v-if="msg.phase_timings">
                  <span v-if="msg.phase_timings.clarification_ms" class="timing-phase">理解 {{ formatDuration(msg.phase_timings.clarification_ms) }}</span>
                  <span v-if="msg.phase_timings.rag_ms" class="timing-phase">检索 {{ formatDuration(msg.phase_timings.rag_ms) }}</span>
                  <span v-if="msg.phase_timings.orchestrator_ms" class="timing-phase">分析 {{ formatDuration(msg.phase_timings.orchestrator_ms) }}</span>
                  <span v-if="msg.phase_timings.specialist_ms" class="timing-phase">专家 {{ formatDuration(msg.phase_timings.specialist_ms) }}</span>
                </template>
              </div>
              <!-- 反馈按钮 -->
              <div v-if="msg.role === 'assistant' && !feedbackGiven[i]" class="message-feedback">
                <button class="btn-msg-feedback" @click="handleFeedback(msg, i, 'helpful')" title="有用">
                  <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/></svg>
                </button>
                <button class="btn-msg-feedback" @click="handleFeedback(msg, i, 'unhelpful')" title="没用">
                  <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 2h3a2 2 0 012 2v7a2 2 0 01-2 2h-3"/></svg>
                </button>
              </div>
              <div v-else-if="feedbackGiven[i]" class="message-feedback-given">
                {{ feedbackGiven[i] === 'helpful' ? '已标记有用' : '已标记，感谢反馈' }}
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
            </div>
            <div class="message-time">{{ formatTime(msg.created_at) }}</div>
          </div>
          <!-- 流式状态指示器 -->
          <div v-if="sending" class="message assistant">
            <!-- 已完成的专家分析 -->
            <div v-if="completedSpecialists.length > 0" class="specialists-container streaming">
              <div v-for="(s, j) in completedSpecialists" :key="j" class="specialist-item completed">
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
            <div v-if="activeSpecialists.length > 0" class="specialists-container streaming">
              <div v-for="(s, j) in activeSpecialists" :key="j" class="specialist-item running">
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
            <div v-if="completedCrossReviews.length > 0" class="specialists-container streaming cross-review">
              <div class="cross-review-label">交叉审阅</div>
              <div v-for="(s, j) in completedCrossReviews" :key="j" class="specialist-item completed cross-review-item">
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
            <div v-if="crossReviewSpecialists.length > 0" class="specialists-container streaming cross-review">
              <div v-for="(s, j) in crossReviewSpecialists" :key="j" class="specialist-item running cross-review-item">
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
            <div v-if="filterToolCalls(currentToolCalls).length > 0" class="tool-calls-container streaming">
              <div v-for="(tc, j) in filterToolCalls(currentToolCalls)" :key="j" class="tool-call-item">
                <div class="tool-call-header">
                  <span class="tool-icon spinning">&#9881;</span>
                  <span class="tool-name">{{ toolDisplayName(tc.name) }}</span>
                  <span class="tool-args">{{ JSON.stringify(tc.arguments || {}).slice(0, 40) }}</span>
                </div>
              </div>
            </div>
            <!-- 执行计划 -->
            <div v-if="executionPlan" class="execution-plan">
              <div class="plan-header">
                <span class="plan-icon">📋</span>
                <span class="plan-label">执行计划</span>
                <span class="plan-complexity" :class="'complexity-' + executionPlan.complexity">
                  {{ {simple: '简单', medium: '中等', complex: '复杂'}[executionPlan.complexity] || executionPlan.complexity }}
                </span>
              </div>
              <div v-if="executionPlan.reason" class="plan-reason">{{ executionPlan.reason }}</div>
              <div v-if="activeSpecialists.length > 0 || completedSpecialists.length > 0" class="plan-steps">
                <div v-for="(s, i) in completedSpecialists" :key="'done-'+i" class="plan-step done">
                  <span class="step-check">✓</span>
                  <span class="step-name">{{ s.agent }}</span>
                </div>
                <div v-for="(s, i) in activeSpecialists" :key="'run-'+i" class="plan-step running">
                  <span class="step-spinner"></span>
                  <span class="step-name">{{ s.agent }}</span>
                </div>
              </div>
            </div>
            <!-- 状态文字 -->
            <div v-if="streamStatus === 'searching'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">{{ statusMessage || '正在检索知识库...' }}</span>
            </div>
            <div v-else-if="streamStatus === 'thinking'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">{{ statusMessage || '正在分析问题，决定需要咨询哪些专家...' }}</span>
            </div>
            <div v-else-if="streamStatus === 'calling_specialist'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">{{ statusMessage || '专家团队正在分析中...' }}</span>
            </div>
            <div v-else-if="streamStatus === 'calling_tool'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">正在调用工具...</span>
            </div>
            <div v-else-if="streamStatus === 'cross_reviewing'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">正在进行交叉审阅...</span>
            </div>
            <div v-else class="message-bubble typing">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
            </div>
            <!-- 实时计时器 -->
            <div v-if="elapsedMs > 0" class="elapsed-timer">
              <span class="elapsed-icon">⏱</span>
              <span class="elapsed-text">已执行 {{ formatElapsed(elapsedMs) }}</span>
            </div>
          </div>
        </div>

        <!-- 输入框 -->
        <div :class="['chat-input-area', { 'is-sending': sending }]">
          <div v-if="sending" class="input-progress-bar">
            <div class="input-progress-fill"></div>
          </div>
          <form @submit.prevent="handleSend" class="chat-form">
            <textarea
              v-model="inputText"
              :placeholder="sending ? '正在执行中，请稍候...' : '输入消息...'"
              class="chat-input"
              :disabled="sending"
              @keydown.enter.exact.prevent="handleSend"
              rows="1"
            ></textarea>
            <button v-if="sending" type="button" @click="cancelStream" class="btn-stop" title="终止执行">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2"/>
              </svg>
            </button>
            <button v-else type="submit" :disabled="!inputText.trim()" class="btn-send">
              <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M12 5l7 7-7 7"/>
              </svg>
            </button>
          </form>
          <div v-if="sending" class="sending-hint">
            <span class="sending-spinner"></span>
            <span class="sending-text">{{ statusMessage || 'AI 正在分析中...' }}</span>
          </div>
        </div>
      </template>

      <!-- 空状态 -->
      <div v-else class="chat-empty">
        <div class="chat-empty-icon">💬</div>
        <h3>选择或创建一个对话</h3>
        <p>选择左侧对话继续，或点击 + 创建新对话</p>
      </div>
    </div>

  </div>

  <!-- 反馈输入弹窗 -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="feedbackModal.visible" class="dialog-backdrop" @click.self="feedbackModal.visible = false">
        <div class="feedback-dialog">
          <div class="feedback-dialog-header">
            <span class="feedback-dialog-icon">{{ feedbackModal.feedbackType === 'helpful' ? '👍' : '👎' }}</span>
            <span class="feedback-dialog-title">{{ feedbackModal.feedbackType === 'helpful' ? '标记为有用' : '标记为需改进' }}</span>
          </div>
          <div class="feedback-dialog-body">
            <textarea
              v-model="feedbackModal.note"
              placeholder="可选：描述您的反馈意见，帮助我们改进..."
              class="feedback-textarea"
              rows="3"
            ></textarea>
          </div>
          <div class="feedback-dialog-actions">
            <button class="btn-secondary" @click="feedbackModal.note = ''; submitFeedback()">跳过</button>
            <button class="btn-primary" @click="submitFeedback">提交反馈</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
  <ConfirmDialog
    :visible="confirm.visible"
    :title="confirm.title"
    :message="confirm.message"
    :danger="confirm.danger"
    @confirm="() => confirm.onConfirm?.()"
    @cancel="confirm.visible = false"
  />
</template>

<style scoped>
.chat-page {
  display: flex;
  height: calc(100vh - 120px);
  gap: 0;
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
}

/* ── 对话侧边栏 ── */
.conv-sidebar {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--color-border);
  background: var(--color-bg-input);
}

.conv-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.conv-header h3 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
}

.btn-new-conv {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--color-primary-600);
  background: var(--color-primary-50);
  transition: all var(--transition-fast);
}

.btn-new-conv:hover {
  background: var(--color-primary-100);
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.7rem 0.6rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.conv-item:hover {
  background: var(--color-bg-hover);
}

.conv-item.active {
  background: var(--color-primary-50);
}

.dark .conv-item.active {
  background: var(--color-primary-bg);
}

.conv-icon {
  font-size: 1.3rem;
  flex-shrink: 0;
}

.conv-info {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-meta {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.15rem;
}

.conv-agent {
  font-size: 0.65rem;
  color: var(--color-primary-600);
  background: var(--color-primary-50);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-sm);
}

.conv-time {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

.btn-delete-conv {
  opacity: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.conv-item:hover .btn-delete-conv {
  opacity: 1;
}

.btn-delete-conv:hover {
  color: var(--color-danger);
  background: rgba(239, 68, 68, 0.1);
}

.conv-empty {
  padding: 2rem 1rem;
  text-align: center;
  color: var(--color-text-muted);
}

.conv-empty p { margin: 0; font-size: 0.8rem; }
.conv-empty-hint { font-size: 0.7rem; margin-top: 0.3rem !important; }

/* ── 聊天区域 ── */
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg-card);
}

.chat-header-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.chat-agent-icon { font-size: 1.2rem; }
.chat-agent-name { font-size: 0.85rem; font-weight: 600; color: var(--color-text-primary); }

.btn-conv-id {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  font-size: 0.7rem;
  cursor: pointer;
  transition: all var(--transition-fast);
  margin-left: auto;
}

.btn-conv-id:hover {
  color: var(--color-primary-500);
  border-color: var(--color-primary-300);
  background: var(--color-primary-50);
}

.btn-conv-id.copied {
  color: #10b981;
  border-color: #10b981;
}

.conv-id-text {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

/* ── 消息列表 ── */
.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

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

/* typing animation */
.typing {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.75rem 1.25rem;
}

.dot {
  width: 7px;
  height: 7px;
  background: var(--color-text-muted);
  border-radius: 50%;
  animation: typingBounce 1.4s infinite ease-in-out;
}

.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes typingBounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

/* ── 输入框 ── */
.chat-input-area {
  padding: 0.75rem 1.25rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-bg-card);
  position: relative;
}

.chat-input-area.is-sending {
  border-top-color: var(--color-primary-300);
}

.input-progress-bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--color-primary-100);
  overflow: hidden;
}

.input-progress-fill {
  height: 100%;
  width: 30%;
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary-600));
  border-radius: 2px;
  animation: progressSlide 1.5s ease-in-out infinite;
}

@keyframes progressSlide {
  0% { transform: translateX(-100%); }
  50% { transform: translateX(233%); }
  100% { transform: translateX(-100%); }
}

.chat-form {
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
}

.chat-input {
  flex: 1;
  resize: none;
  padding: 0.6rem 0.85rem;
  font-size: 0.85rem;
  line-height: 1.5;
  max-height: 120px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  outline: none;
  transition: border-color var(--transition-fast);
}

.chat-input:focus {
  border-color: var(--color-primary-400);
}

.is-sending .chat-input {
  border-color: var(--color-primary-300);
  background: var(--color-primary-50, rgba(201, 168, 76, 0.03));
  animation: inputPulse 2s ease-in-out infinite;
}

@keyframes inputPulse {
  0%, 100% { border-color: var(--color-primary-200); }
  50% { border-color: var(--color-primary-400); }
}

.btn-send {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--color-primary-500);
  color: white;
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.btn-send:hover:not(:disabled) {
  background: var(--color-primary-600);
}

.btn-send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-stop {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: #ef4444;
  color: white;
  transition: all var(--transition-fast);
  flex-shrink: 0;
  animation: pulseStop 2s ease-in-out infinite;
}

.btn-stop:hover {
  background: #dc2626;
  transform: scale(1.05);
}

@keyframes pulseStop {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.3); }
  50% { box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
}

.sending-hint {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.4rem;
  padding: 0.2rem 0;
}

.sending-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--color-primary-200);
  border-top-color: var(--color-primary-500);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}

.sending-text {
  font-size: 0.72rem;
  color: var(--color-primary-500);
  font-weight: 500;
}

/* ── 空状态 ── */
.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  gap: 0.5rem;
}

.chat-empty-icon { font-size: 3rem; }
.chat-empty h3 { margin: 0; font-size: 1rem; color: var(--color-text-secondary); }
.chat-empty p { margin: 0; font-size: 0.8rem; }


/* ── Markdown 样式 ── */
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin-top: 0.75rem;
  margin-bottom: 0.4rem;
  font-size: 0.9rem;
}

.markdown-body :deep(p) {
  margin: 0.3rem 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.2rem;
  margin: 0.3rem 0;
}

.markdown-body :deep(code) {
  background: rgba(255, 255, 255, 0.06);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  font-size: 0.8rem;
}

.dark .markdown-body :deep(code) {
  background: rgba(255,255,255,0.1);
}

.markdown-body :deep(strong) {
  font-weight: 600;
}

/* ── Transition ── */
.fade-enter-active { transition: opacity 0.2s; }
.fade-leave-active { transition: opacity 0.15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* ── RAG 来源标注 ── */
.rag-sources {
  margin-top: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
  font-size: 0.7rem;
}

.rag-header {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  color: var(--color-text-secondary);
  font-weight: 500;
  margin-bottom: 0.4rem;
}

.rag-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-bottom: 0.3rem;
}

.rag-tag {
  display: inline-flex;
  align-items: center;
  padding: 0.15rem 0.4rem;
  border-radius: var(--radius-sm);
  font-size: 0.65rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
}

.rag-tag-估值 { border-color: #f59e0b; color: #d97706; background: var(--color-warning-bg); }
.rag-tag-作者文章 { border-color: #10b981; color: #059669; background: var(--color-success-bg); }
.rag-tag-技能知识 { border-color: #8b5cf6; color: #7c3aed; background: rgba(139, 92, 246, 0.1); }
.rag-tag-文章 { border-color: #3b82f6; color: #2563eb; background: var(--color-info-bg); }

.rag-keywords {
  font-size: 0.6rem;
  color: var(--color-text-muted);
}

/* ── 工具调用展示 ── */
.tool-calls-container {
  margin-bottom: 0.4rem;
  border-left: 3px solid var(--color-primary-300);
  padding-left: 0.5rem;
}

.tool-calls-container.streaming {
  border-left-color: var(--color-primary-400);
  opacity: 0.8;
}

.tool-call-item {
  margin-bottom: 0.2rem;
}

.tool-call-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  padding: 0.2rem 0;
  cursor: pointer;
}

.tool-call-header:hover {
  color: var(--color-text-primary);
}

.tool-icon {
  font-size: 0.7rem;
}

.tool-icon.spinning {
  animation: spin 1.5s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.tool-name {
  font-weight: 600;
  color: var(--color-primary-600);
}

.tool-args {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}

.tool-toggle {
  font-size: 0.6rem;
  color: var(--color-text-muted);
  margin-left: auto;
}

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

.stream-status {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 0.75rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.stream-status .status-text {
  margin-left: 0.3rem;
}

/* ── 执行计划 ── */
.execution-plan {
  margin: 0.5rem 0;
  padding: 0.6rem 0.8rem;
  background: linear-gradient(135deg, rgba(201, 168, 76, 0.04), rgba(201, 168, 76, 0.02));
  border: 1px solid rgba(201, 168, 76, 0.15);
  border-radius: var(--radius-md);
  font-size: 0.8rem;
}

.plan-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.3rem;
}

.plan-icon {
  font-size: 0.9rem;
}

.plan-label {
  font-weight: 600;
  color: var(--color-text-primary);
}

.plan-complexity {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius-sm);
  margin-left: auto;
}

.complexity-simple { background: rgba(16, 185, 129, 0.1); color: #10b981; }
.complexity-medium { background: rgba(245, 158, 11, 0.1); color: #f59e0b; }
.complexity-complex { background: rgba(201, 168, 76, 0.1); color: #c9a84c; }

.plan-reason {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-bottom: 0.4rem;
}

.plan-steps {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.plan-step {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
}

.plan-step.done {
  color: #10b981;
}

.plan-step.running {
  color: var(--color-primary);
}

.step-check {
  font-weight: 700;
}

.step-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(201, 168, 76, 0.2);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ── 专家分析展示 ── */
.specialists-container {
  margin-bottom: 0.5rem;
}

.specialists-container.streaming {
  opacity: 0.9;
}

.specialist-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 0.4rem;
  overflow: hidden;
  background: var(--color-bg-card);
}

.specialist-item.running {
  border-color: var(--color-primary-300);
  background: var(--color-primary-50);
}

.dark .specialist-item.running {
  background: var(--color-primary-bg);
}

.specialist-item.completed {
  border-color: var(--color-success-border, #10b981);
}

.specialist-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  cursor: pointer;
  transition: background var(--transition-fast);
}

.specialist-header:hover {
  background: var(--color-bg-hover);
}

.specialist-icon {
  font-size: 1rem;
  flex-shrink: 0;
}

.specialist-icon.spinning {
  animation: spin 2s linear infinite;
}

.specialist-name {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.specialist-status {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.2rem;
}

.specialist-status.done {
  color: var(--color-success, #10b981);
  font-size: 0.8rem;
  font-weight: 600;
}

.specialist-status.running .dot {
  width: 5px;
  height: 5px;
  background: var(--color-primary-400);
  border-radius: 50%;
  animation: typingBounce 1.4s infinite ease-in-out;
}

.specialist-status.running .dot:nth-child(2) { animation-delay: 0.2s; }
.specialist-status.running .dot:nth-child(3) { animation-delay: 0.4s; }

.specialist-time {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

.specialist-toggle {
  font-size: 0.6rem;
  color: var(--color-text-muted);
}

.specialist-feedback {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  margin-left: 0.3rem;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.specialist-item:hover .specialist-feedback {
  opacity: 1;
}

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

.btn-spec-feedback:hover {
  border-color: var(--color-primary-400);
  background: var(--color-primary-50);
  transform: scale(1.15);
}

.feedback-done {
  font-size: 0.6rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.specialist-analysis {
  padding: 0.5rem 0.75rem;
  border-top: 1px solid var(--color-border);
  font-size: 0.8rem;
  max-height: 400px;
  overflow-y: auto;
}

/* ── 交叉审阅 ── */
.specialists-container.cross-review {
  margin-top: 0.5rem;
  margin-bottom: 0.5rem;
  padding-left: 0.5rem;
  border-left: 3px solid var(--color-primary-300);
}

.cross-review-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-primary);
  padding: 0.25rem 0.75rem;
  margin-bottom: 0.25rem;
  letter-spacing: 0.05em;
}

.specialist-item.cross-review-item {
  border-color: var(--color-primary-200);
  background: var(--color-primary-50, rgba(201, 168, 76, 0.04));
}

.dark .specialist-item.cross-review-item {
  background: var(--color-primary-bg, rgba(201, 168, 76, 0.08));
}

/* ── 消息反馈按钮 ── */
.message-feedback {
  display: flex;
  gap: 0.25rem;
  padding: 0.25rem 0;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.message:hover .message-feedback {
  opacity: 1;
}

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

.btn-msg-feedback:hover {
  color: var(--color-primary);
  border-color: var(--color-primary);
  background: var(--color-primary-50);
}

.message-feedback-given {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  padding: 0.25rem 0;
}

/* ── 实时计时器 ── */
.elapsed-timer {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.75rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  opacity: 0.85;
}

.elapsed-icon {
  font-size: 0.8rem;
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.6; }
  50% { opacity: 1; }
}

.elapsed-text {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: var(--color-primary-500);
}

/* ── 耗时摘要 ── */
.message-timing {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.3rem;
  padding: 0.25rem 0.75rem;
  font-size: 0.68rem;
  color: var(--color-text-muted);
  opacity: 0.7;
}

.timing-total {
  font-weight: 600;
  color: var(--color-text-secondary);
}

.timing-phase::before {
  content: '·';
  margin-right: 0.3rem;
  color: var(--color-border);
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .conv-sidebar { width: 100%; max-width: 280px; }
  .chat-page { flex-direction: row; }
  .agent-grid { grid-template-columns: 1fr; }
}

/* ── 执行状态徽章 ── */
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
.execution-status-badge.status-streaming {
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fcd34d;
}
.dark .execution-status-badge.status-streaming {
  background: #451a03;
  color: #fcd34d;
  border-color: #78350f;
}
.execution-status-badge.status-failed {
  background: #fee2e2;
  color: #991b1b;
  border: 1px solid #fca5a5;
}
.dark .execution-status-badge.status-failed {
  background: #450a0a;
  color: #fca5a5;
  border-color: #7f1d1d;
}
.execution-status-badge.status-cancelled {
  background: #f3f4f6;
  color: #4b5563;
  border: 1px solid #d1d5db;
}
.dark .execution-status-badge.status-cancelled {
  background: #1f2937;
  color: #9ca3af;
  border-color: #374151;
}
.execution-status-badge.status-timeout {
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fcd34d;
}
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
.btn-retry:hover {
  opacity: 1;
}

/* ── 反馈输入弹窗 ── */
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 1000);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0,0,0,0.4);
  backdrop-filter: blur(4px);
}
.feedback-dialog {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg, 0 10px 25px rgba(0,0,0,0.15));
  width: 100%;
  max-width: 420px;
  margin: 0 1rem;
  overflow: hidden;
}
.feedback-dialog-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1.25rem 1.25rem 0.75rem;
}
.feedback-dialog-icon {
  font-size: 1.25rem;
}
.feedback-dialog-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.feedback-dialog-body {
  padding: 0 1.25rem;
}
.feedback-textarea {
  width: 100%;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  font-size: 0.85rem;
  line-height: 1.5;
  resize: vertical;
  outline: none;
  transition: border-color 0.15s;
  box-sizing: border-box;
}
.feedback-textarea:focus {
  border-color: var(--color-primary-500, #3b82f6);
}
.feedback-textarea::placeholder {
  color: var(--color-text-tertiary, #9ca3af);
}
.feedback-dialog-actions {
  display: flex;
  gap: 0.5rem;
  padding: 1rem 1.25rem 1.25rem;
}
.feedback-dialog-actions .btn-secondary {
  flex: 1;
  padding: 0.55rem 1rem;
  font-size: 0.85rem;
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}
.feedback-dialog-actions .btn-secondary:hover {
  background: var(--color-bg-hover);
}
.feedback-dialog-actions .btn-primary {
  flex: 1;
  padding: 0.55rem 1rem;
  font-size: 0.85rem;
  background: linear-gradient(135deg, var(--color-primary-600, #2563eb), var(--color-primary-500, #3b82f6));
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}
.feedback-dialog-actions .btn-primary:hover {
  background: linear-gradient(135deg, var(--color-primary-700, #1d4ed8), var(--color-primary-600, #2563eb));
}
</style>
