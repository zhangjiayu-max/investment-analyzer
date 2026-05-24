<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'
import { marked } from 'marked'
import {
  listConversations, createConversation, deleteConversation,
  getMessages, sendMessage, sendMessageStream,
} from '../api'

const conversations = ref([])
const selectedConv = ref(null)
const messages = ref([])
const inputText = ref('')
const sending = ref(false)
const messagesContainer = ref(null)

// 流式对话状态
const streamStatus = ref('')  // '' | 'searching' | 'calling_tool' | 'thinking' | 'answering'
const currentToolCalls = ref([])  // 当前正在执行的工具调用
const streamAbort = ref(null)  // AbortController
const activeSpecialists = ref([])  // 正在工作的专家列表
const completedSpecialists = ref([])  // 已完成的专家分析结果

onMounted(async () => {
  await loadConversations()
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
    messages.value = data.messages || []
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

async function handleDeleteConversation(conv, e) {
  e.stopPropagation()
  if (!confirm('确定删除这个对话吗？')) return
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

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || !selectedConv.value || sending.value) return

  inputText.value = ''
  sending.value = true
  streamStatus.value = 'searching'
  currentToolCalls.value = []
  activeSpecialists.value = []
  completedSpecialists.value = []

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

    case 'answer':
      streamStatus.value = 'answering'
      // 构建最终消息
      const assistantMsg = {
        role: 'assistant',
        content: data.content,
        created_at: new Date().toISOString(),
        specialist_results: data.specialist_results || (completedSpecialists.value.length > 0 ? [...completedSpecialists.value] : null),
        tool_calls: currentToolCalls.value.length > 0 ? [...currentToolCalls.value] : null,
        rag: currentToolCalls.value._ragSources ? { sources: currentToolCalls.value._ragSources } : null,
      }
      messages.value.push(assistantMsg)
      nextTick(() => scrollToBottom())
      break

    case 'done':
      sending.value = false
      streamStatus.value = ''
      currentToolCalls.value = []
      activeSpecialists.value = []
      completedSpecialists.value = []
      loadConversations()
      break

    case 'error':
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
  if (streamAbort.value) {
    streamAbort.value.abort()
    streamAbort.value = null
  }
  sending.value = false
  streamStatus.value = ''
  currentToolCalls.value = []
  activeSpecialists.value = []
  completedSpecialists.value = []
}

function scrollToBottom() {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

function toolDisplayName(name) {
  const map = {
    query_valuation: '查询估值',
    search_knowledge: '检索知识库',
    get_bond_temperature: '债市温度',
    get_valuation_list: '估值概览',
    get_author_opinions: '作者观点',
    calculate_metrics: '计算指标',
  }
  return map[name] || name
}

function renderMarkdown(text) {
  return marked(text || '')
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
        </div>

        <!-- 消息列表 -->
        <div ref="messagesContainer" class="messages-container">
          <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
            <div class="message-bubble" v-if="msg.role === 'user'">{{ msg.content }}</div>
            <div v-else>
              <!-- 专家分析展示 -->
              <div v-if="msg.specialist_results && msg.specialist_results.length" class="specialists-container">
                <div v-for="(s, j) in msg.specialist_results" :key="j" class="specialist-item">
                  <div class="specialist-header" @click="s.expanded = !s.expanded">
                    <span class="specialist-icon">{{ s.icon }}</span>
                    <span class="specialist-name">{{ s.agent }}</span>
                    <span v-if="s.duration_ms" class="specialist-time">{{ (s.duration_ms / 1000).toFixed(1) }}s</span>
                    <span class="specialist-toggle">{{ s.expanded ? '▲' : '▼' }}</span>
                  </div>
                  <div v-if="s.expanded" class="specialist-analysis markdown-body" v-html="renderMarkdown(s.analysis)"></div>
                </div>
              </div>
              <!-- 工具调用展示 -->
              <div v-if="msg.tool_calls && msg.tool_calls.length" class="tool-calls-container">
                <div v-for="(tc, j) in msg.tool_calls" :key="j" class="tool-call-item">
                  <div class="tool-call-header" @click="tc.expanded = !tc.expanded">
                    <span class="tool-icon">&#128295;</span>
                    <span class="tool-name">{{ toolDisplayName(tc.name) }}</span>
                    <span class="tool-args">{{ JSON.stringify(tc.arguments || {}).slice(0, 40) }}</span>
                    <span class="tool-toggle">{{ tc.expanded ? '▲' : '▼' }}</span>
                  </div>
                  <pre v-if="tc.expanded" class="tool-result">{{ tc.result_preview }}</pre>
                </div>
              </div>
              <div class="message-bubble markdown-body" v-html="renderMarkdown(msg.content)"></div>
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
                <div v-if="s.expanded" class="specialist-analysis markdown-body" v-html="renderMarkdown(s.analysis)"></div>
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
            <!-- 实时工具调用 -->
            <div v-if="currentToolCalls.length > 0" class="tool-calls-container streaming">
              <div v-for="(tc, j) in currentToolCalls" :key="j" class="tool-call-item">
                <div class="tool-call-header">
                  <span class="tool-icon spinning">&#9881;</span>
                  <span class="tool-name">{{ toolDisplayName(tc.name) }}</span>
                  <span class="tool-args">{{ JSON.stringify(tc.arguments || {}).slice(0, 40) }}</span>
                </div>
              </div>
            </div>
            <!-- 状态文字 -->
            <div v-if="streamStatus === 'searching'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">正在检索知识库...</span>
            </div>
            <div v-else-if="streamStatus === 'thinking'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">正在分析问题，决定需要咨询哪些专家...</span>
            </div>
            <div v-else-if="streamStatus === 'calling_specialist'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">专家团队正在分析中...</span>
            </div>
            <div v-else-if="streamStatus === 'calling_tool'" class="stream-status">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="status-text">正在调用工具...</span>
            </div>
            <div v-else class="message-bubble typing">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
            </div>
          </div>
        </div>

        <!-- 输入框 -->
        <div class="chat-input-area">
          <form @submit.prevent="handleSend" class="chat-form">
            <textarea
              v-model="inputText"
              placeholder="输入消息..."
              class="chat-input"
              :disabled="sending"
              @keydown.enter.exact.prevent="handleSend"
              rows="1"
            ></textarea>
            <button type="submit" :disabled="sending || !inputText.trim()" class="btn-send">
              <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M12 5l7 7-7 7"/>
              </svg>
            </button>
          </form>
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
  background: rgba(0,0,0,0.06);
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

.specialist-analysis {
  padding: 0.5rem 0.75rem;
  border-top: 1px solid var(--color-border);
  font-size: 0.8rem;
  max-height: 400px;
  overflow-y: auto;
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .conv-sidebar { width: 100%; max-width: 280px; }
  .chat-page { flex-direction: row; }
  .agent-grid { grid-template-columns: 1fr; }
}
</style>
