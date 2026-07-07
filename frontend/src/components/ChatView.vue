<script setup>
import { ref, computed, onMounted, onBeforeUnmount, onActivated, onDeactivated, nextTick, watch } from 'vue'
import {
  listConversations, createConversation, deleteConversation,
  getMessages, sendMessage, sendMessageStream,
  submitChatFeedback, submitLlmFeedback,
  cancelConversationExecution,
  clearCancelFlag,
  continueConversation,
  retryConversationMessage,
  resumeConversationStream,
  replayConversationStream,
  getConversationExecutionState,
  listTraces, listAgents,
  getConversationEvaluation, evaluateConversation, evaluateConversationWithLLM,
  createDecisionFromChat,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { ChatSidebar, ChatMessage, ChatInput, StreamIndicator, FeedbackModal } from './chat'
import Icon from './ui/Icon.vue'
import { useToast } from '../composables/useToast'
import { renderMarkdown } from '../composables/useMarkdown'
import { useStreamingState } from '../composables/useStreamingState'
import { useTaskTracker } from '../composables/useTaskTracker'
import { usePendingAction } from '../composables/usePendingAction'

const props = defineProps({
  onNavigate: { type: Function, default: null },
})

const { showToast } = useToast()
const { pendingChatPrefill, clearChatPrefill, setTradeAction } = usePendingAction()
const {
  streamStates, getStreamState, startStream,
  handleStreamEvent: routeStreamEvent,
  finishStream, cancelStream: cancelStreamState,
} = useStreamingState()

const { pendingTasks, addTask, removeTask } = useTaskTracker()

const conversations = ref([])
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })
const selectedConv = ref(null)
const messages = ref([])
const inputText = ref('')
const agents = ref([])
const messagesContainer = ref(null)
const showMobileSidebar = ref(false)
const isRecovering = ref(false)
const pendingImages = ref([])

const currentStream = computed(() => getStreamState(selectedConv.value?.id))
const sending = computed(() => currentStream.value?.sending || false)

// 消息评估状态管理
const messageEvalStates = ref({})

// 反馈功能
const feedbackGiven = ref({})
const specialistFeedback = ref({})
const feedbackModal = ref({ visible: false, type: '', feedbackType: '', msgIndex: 0, msg: null, specialist: null, note: '' })

// Trace 详情
const traceDetailVisible = ref({})
const traceDetailData = ref({})

// ─── P2: 持仓 ↔ 对话双向联动 ───
// 监听预填问题（来自持仓操作"咨询AI"）
watch(pendingChatPrefill, (text) => {
  if (!text) return
  inputText.value = text
  clearChatPrefill()
  nextTick(() => {
    const textarea = document.querySelector('.chat-input textarea, .chat-input input')
    if (textarea) textarea.focus()
  })
})

// 对话决策落地为交易：跳转到持仓管理并预填
function executeTradeFromChat(action) {
  setTradeAction(action)
  if (props.onNavigate) props.onNavigate('portfolio')
}

// ─── 生命周期 ───

onMounted(async () => {
  await loadConversations()
  document.addEventListener('visibilitychange', handleVisibilityChange)
  window.addEventListener('pageshow', handlePageShow)
  if (!(await checkPendingTasks())) {
    await autoSelectLastConversation()
  }
  // 加载 Agent 列表供 @mention 使用
  try {
    const res = await listAgents()
    const allAgents = res.data?.agents || res.data || []
    agents.value = allAgents.filter(a => a.is_specialist)
  } catch (e) {
    console.warn('加载 Agent 列表失败:', e)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  window.removeEventListener('pageshow', handlePageShow)
  stopPollingProgress()
})

// ─── KeepAlive 页面切换钩子（SPA 内部 activePage 切换）───
// 首次挂载时 onActivated 也会触发，但此时 selectedConv 尚未就绪，recoverFromDisconnect 会自动跳过
onActivated(() => {
  if (!selectedConv.value?.id) return
  recoverFromDisconnect()
})

onDeactivated(() => {
  // 切到其他页面：中断当前 SSE 连接（后端任务继续执行，切回时由 onActivated 恢复）
  pauseCurrentStream()
  stopPollingProgress()
})

// ─── 移动端恢复 ───

function handleVisibilityChange() {
  if (document.visibilityState === 'visible') recoverFromDisconnect()
  else pauseCurrentStream()
}

// 切走时中断当前 SSE 连接（后端任务继续执行，切回时从 DB 刷新）
function pauseCurrentStream() {
  if (!selectedConv.value?.id) return
  const convId = selectedConv.value.id
  const state = getStreamState(convId)
  if (state?.sending) {
    finishStream(convId)
    console.log('[ChatView] 页面切走，中断 SSE 连接，任务将在后台继续')
  }
}

function handlePageShow(e) {
  if (e.persisted) recoverFromDisconnect()
}

async function recoverFromDisconnect() {
  if (!selectedConv.value?.id) return
  if (isRecovering.value) return
  isRecovering.value = true
  try {
    const convId = selectedConv.value.id
    // 强制从 DB 刷新消息（DB 有真实的 execution_status）
    await loadMessages(convId)
    const lastAssistant = [...messages.value].reverse().find(m => m.role === 'assistant')
    if (lastAssistant?.execution_status === 'streaming') {
      // DB 中仍为 streaming，说明后端任务还在跑，恢复 SSE 连接
      showToast('检测到后台执行中的任务，正在恢复连接...', 'info')
      reconnectStream(convId, lastAssistant)
    } else if (lastAssistant?.execution_status === 'completed') {
      // DB 已完成，SSE 之前断了没收到 done 事件，静默刷新即可
      console.log('[ChatView] 页面切回，任务已完成，已刷新消息')
    }
    loadConversations()
  } finally {
    isRecovering.value = false
  }
}

const LAST_CONV_KEY = 'investment_last_conv'

async function checkPendingTasks() {
  if (pendingTasks.value.length === 0) return false
  const sorted = [...pendingTasks.value].sort((a, b) => b.addedAt - a.addedAt)
  const latest = sorted[0]
  const conv = conversations.value.find(c => c.id === latest.convId)
  if (conv) {
    await selectConversation(conv)
    return true
  }
  return false
}

async function autoSelectLastConversation() {
  if (selectedConv.value) return
  const lastId = localStorage.getItem(LAST_CONV_KEY)
  if (lastId) {
    const conv = conversations.value.find(c => c.id === parseInt(lastId))
    if (conv && conv.message_count > 0) {
      await selectConversation(conv)
      return
    }
  }
  const valid = conversations.value.find(c => c.message_count > 0)
  if (valid) await selectConversation(valid)
}

// ─── 对话管理 ───

async function loadConversations() {
  try {
    const { data } = await listConversations()
    conversations.value = data.conversations || []
  } catch (e) {
    console.error('Failed to load conversations:', e)
  }
}

async function selectConversation(conv) {
  showMobileSidebar.value = false
  selectedConv.value = conv
  localStorage.setItem(LAST_CONV_KEY, String(conv.id))
  await loadMessages(conv.id)
  await autoRecoverIfNeeded(conv.id)
}

async function autoRecoverIfNeeded(convId) {
  if (isRecovering.value) return
  isRecovering.value = true
  try {
    const msgs = messages.value
    if (msgs.length === 0) return
    const lastMsg = msgs[msgs.length - 1]
    const lastAssistant = [...msgs].reverse().find(m => m.role === 'assistant')
    if (lastAssistant?.execution_status === 'streaming') {
      const state = getStreamState(convId)
      if (state?.sending) return
      showToast('正在恢复任务...', 'info')
      tryResumeConversation(convId)
      return
    }
    // cancelled 状态不自动恢复 — 用户主动取消的不应自动重新执行
    if (lastAssistant?.execution_status === 'cancelled') {
      return
    }
    // failed 状态不自动恢复 — 需用户显式点击「重新生成」或「继续分析」
    if (lastAssistant?.execution_status === 'failed') {
      return
    }
    // 只有最后一条是 user 消息且后面没有 assistant 时才恢复
    // 如果最后一条是 assistant 且不是 streaming/queued，不恢复
    if (lastMsg.role === 'user') {
      const state = getStreamState(convId)
      if (state?.sending) return
      const hasAssistantAfter = msgs.some((m, i) => i > msgs.indexOf(lastMsg) && m.role === 'assistant')
      if (!hasAssistantAfter) {
        showToast('检测到中断的请求，正在重新执行...', 'info')
        sendMessageAndTrack(convId, lastMsg.content)
      }
    }
  } finally {
    isRecovering.value = false
  }
}

function tryResumeConversation(convId) {
  // 新流程：先查 execution-state，按 channel 状态分流
  getConversationExecutionState(convId).then(({ data }) => {
    const item = data?.item
    if (!item) {
      // 无可恢复消息 → 不做任何操作
      return
    }
    const channelId = item.channel_id
    const channelStatus = item.channel_status

    if (!channelId) {
      // 旧对话无 channel → 走旧 resume 逻辑兼容
      _legacyResumeConversation(convId)
      return
    }

    if (channelStatus === 'running') {
      // 任务还活着 → 调 replay 从头回放（前端尚未渲染任何事件）
      showToast('正在续接任务...', 'info')
      _reconnectReplay(convId, channelId, 0)
    } else if (channelStatus === 'completed') {
      // 已完成 → 直接刷新消息
      loadMessages(convId).then(() => {
        showToast('任务已完成', 'success')
        loadConversations()
        nextTick(() => scrollToBottom())
      })
    } else {
      // failed / aborted / cancelled → 刷新消息显示真实状态 + 重试按钮
      finishStream(convId)
      removeTask(convId)
      loadMessages(convId).then(() => {
        if (channelStatus === 'aborted') {
          showToast('任务中断，可点击「重新生成」重试', 'info')
        } else if (channelStatus === 'failed') {
          showToast('对话执行失败，可点击「重新生成」重试', 'info')
        } else if (channelStatus === 'cancelled') {
          showToast('对话已取消，可点击「继续分析」重新执行', 'info')
        }
      })
    }
  }).catch(err => {
    console.warn('[ChatView] 查询 execution-state 失败，回退到旧 resume:', err)
    _legacyResumeConversation(convId)
  })
}

// 旧 resume 逻辑（兼容无 channel 的旧对话）
function _legacyResumeConversation(convId) {
  const controller = resumeConversationStream(convId, (event) => {
    routeStreamEvent(convId, event, {
      onAnswer: (cid, data, state) => {
        if (selectedConv.value?.id !== cid) return
        const msgIndex = messages.value.findIndex(m =>
          m.role === 'assistant' && m.execution_status === 'streaming'
        )
        if (msgIndex >= 0) {
          messages.value[msgIndex].content = data.content
          messages.value[msgIndex].specialist_results = state.completedSpecialists.filter(s => !s.is_cross_review)
          messages.value[msgIndex].cross_review_results = state.completedCrossReviews
          messages.value[msgIndex].tool_calls = state.currentToolCalls
          messages.value = [...messages.value]
        }
        nextTick(() => scrollToBottom())
      },
      onDone: (cid) => {
        finishStream(cid)
        removeTask(cid)
        loadMessages(cid).then(() => {
          showToast('任务已完成', 'success')
          loadConversations()
          nextTick(() => scrollToBottom())
        })
      },
      onError: (cid, errorData) => {
        if (errorData.code === 'RESUME_FAILED') {
          finishStream(cid)
          removeTask(cid)
          loadMessages(cid).then(() => {
            const lastAssistant = [...messages.value].reverse().find(m => m.role === 'assistant')
            const status = lastAssistant?.execution_status
            if (status === 'cancelled') {
              showToast('对话已取消，可点击「继续分析」重新执行', 'info')
            } else if (status === 'failed') {
              showToast('对话执行失败，可点击「重新生成」重试', 'info')
            } else if (status === 'completed') {
              showToast('任务已完成', 'success')
            }
          })
          return
        }
        finishStream(cid)
        removeTask(cid)
        loadMessages(cid).then(() => {
          showToast('任务状态已更新', 'info')
        })
      },
    })
  })
  startStream(convId, controller)
  addTask(convId, null, selectedConv.value?.title || '')
}

// replay 续接：切回页面时续接 channel 事件流
function _reconnectReplay(convId, channelId, lastSeq) {
  const controller = replayConversationStream(convId, channelId, lastSeq, (event) => {
    // replay_end 事件：回放结束，清理状态
    if (event.type === 'replay_end') {
      const status = event.data?.status
      finishStream(convId)
      removeTask(convId)
      if (status === 'completed') {
        loadMessages(convId).then(() => {
          showToast('任务已完成', 'success')
          loadConversations()
          nextTick(() => scrollToBottom())
        })
      } else if (status === 'aborted' || status === 'failed') {
        loadMessages(convId).then(() => {
          showToast('任务中断，可点击「重新生成」重试', 'info')
        })
      } else if (status === 'cancelled') {
        loadMessages(convId).then(() => {
          showToast('对话已取消', 'info')
        })
      }
      return
    }
    // channel_started 事件：replay 不需要再次缓存 channel_id
    if (event.type === 'channel_started') return
    // 其他事件复用 routeStreamEvent 处理
    routeStreamEvent(convId, event, {
      onAnswer: (cid, data, state) => {
        if (selectedConv.value?.id !== cid) return
        const msgIndex = messages.value.findIndex(m =>
          m.role === 'assistant' && (m.execution_status === 'streaming' || m.streaming)
        )
        if (msgIndex >= 0) {
          messages.value[msgIndex].content = data.content
          messages.value[msgIndex].specialist_results = state.completedSpecialists.filter(s => !s.is_cross_review)
          messages.value[msgIndex].cross_review_results = state.completedCrossReviews
          messages.value[msgIndex].tool_calls = state.currentToolCalls
          messages.value = [...messages.value]
        }
        nextTick(() => scrollToBottom())
      },
      onDone: (cid) => {
        finishStream(cid)
        removeTask(cid)
        loadMessages(cid).then(() => {
          showToast('任务已完成', 'success')
          loadConversations()
          nextTick(() => scrollToBottom())
        })
      },
      onError: (cid, errorData) => {
        finishStream(cid)
        removeTask(cid)
        loadMessages(cid).then(() => {
          showToast('任务中断，可点击「重新生成」重试', 'info')
        })
      },
    })
  })
  startStream(convId, controller)
  addTask(convId, null, selectedConv.value?.title || '')
}

function parseMessageMeta(msg) {
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
    msg.reasoning_trail = msg.metadata.reasoning_trail
    if (msg.phase_timings?.total_ms && !msg.duration_ms) {
      msg.duration_ms = msg.phase_timings.total_ms
    }
    ;(msg.specialist_results || []).forEach(s => { s.expanded = false })
    ;(msg.cross_review_results || []).forEach(s => { s.expanded = false })
    msg._showExecution = false
  }
  return msg
}

function sendMessageAndTrack(convId, text) {
  const existingState = getStreamState(convId)
  if (existingState?.sending) {
    console.warn(`对话 ${convId} 已有进行中的请求，跳过重复发送`)
    return
  }
  const controller = sendMessageStream(convId, text, (event) => {
    routeStreamEvent(convId, event, {
      onAnswerChunk: (cid, data, state) => {
        if (selectedConv.value?.id !== cid) return
        const lastMsg = messages.value[messages.value.length - 1]
        if (lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming) {
          // 增量追加答案 + 持续更新思考过程
          lastMsg.content += data.content || ''
          if (state.streamingReasoning) lastMsg.reasoning = state.streamingReasoning
        } else {
          // 首个 chunk：push 占位消息（带已累积的思考过程）
          messages.value.push({
            role: 'assistant',
            content: data.content || '',
            reasoning: state.streamingReasoning || '',
            streaming: true,
            created_at: new Date().toISOString(),
          })
        }
        nextTick(() => scrollToBottom())
      },
      onAnswer: (cid, data, state) => {
        if (selectedConv.value?.id !== cid) return
        const allSpecResults = data.specialist_results || (state.completedSpecialists.length > 0 ? [...state.completedSpecialists] : [])
        const phaseAResults = allSpecResults.filter(s => !s.is_cross_review)
        const phaseBResults = allSpecResults.filter(s => s.is_cross_review)
        const lastMsg = messages.value[messages.value.length - 1]
        if (lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming) {
          // 流式已产出：用全文兜底覆盖 + 附加专家结果
          lastMsg.content = data.content || lastMsg.content
          lastMsg.streaming = false
          lastMsg.specialist_results = phaseAResults.length > 0 ? phaseAResults : null
          lastMsg.cross_review_results = phaseBResults.length > 0 ? phaseBResults : (state.completedCrossReviews.length > 0 ? [...state.completedCrossReviews] : null)
          lastMsg.tool_calls = state.currentToolCalls.length > 0 ? [...state.currentToolCalls] : null
          lastMsg.rag = state.currentToolCalls._ragSources ? { sources: state.currentToolCalls._ragSources } : null
        } else {
          // 未收到 chunk（simple chat / 流式未生效 / 回退）：走原逻辑 push
          messages.value.push({
            role: 'assistant',
            content: data.content,
            created_at: new Date().toISOString(),
            specialist_results: phaseAResults.length > 0 ? phaseAResults : null,
            cross_review_results: phaseBResults.length > 0 ? phaseBResults : (state.completedCrossReviews.length > 0 ? [...state.completedCrossReviews] : null),
            tool_calls: state.currentToolCalls.length > 0 ? [...state.currentToolCalls] : null,
            rag: state.currentToolCalls._ragSources ? { sources: state.currentToolCalls._ragSources } : null,
          })
        }
        nextTick(() => scrollToBottom())
      },
      onDone: (cid, doneData) => {
        if (selectedConv.value?.id === cid) {
          const state = getStreamState(cid)
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg && lastMsg.role === 'assistant' && state?.lastTiming) {
            lastMsg.duration_ms = state.lastTiming.duration_ms
            lastMsg.phase_timings = state.lastTiming.phase_timings
          }
        }
        finishStream(cid)
        removeTask(cid)
        loadConversations()
        stopPollingProgress()
      },
      onError: (cid, errorData) => {
        if (selectedConv.value?.id === cid) {
          // 409 表示重复请求，不显示错误，直接重新加载消息
          if (errorData.status === 409) {
            loadMessages(cid).then(() => {
              showToast(errorData.message || '该问题已处理完成', 'info')
            })
          } else {
            messages.value.push({
              role: 'assistant',
              content: '执行失败: ' + (errorData.message || '未知错误'),
              created_at: new Date().toISOString(),
            })
          }
        }
        finishStream(cid)
        removeTask(cid)
        stopPollingProgress()
      },
    })
  })
  startStream(convId, controller)
  addTask(convId, null, selectedConv.value?.title || `对话 #${convId}`)
}

async function loadMessages(convId) {
  try {
    const { data } = await getMessages(convId)
    messages.value = (data.messages || []).map(msg => parseMessageMeta(msg))
    for (const msg of messages.value) {
      if (msg.role === 'assistant' && msg.id) {
        loadMessageEvalStatus(msg.id)
      }
    }
    await nextTick()
    scrollToBottom()
  } catch (e) {
    console.error('Failed to load messages:', e)
    messages.value = []
  }
}

async function loadMessageEvalStatus(messageId) {
  if (!selectedConv.value?.id) return
  try {
    const { data } = await getConversationEvaluation(selectedConv.value.id, messageId)
    if (data.ok && data.evaluation) {
      messageEvalStates.value[messageId] = {
        score: data.evaluation.auto_score || 0,
        loading: false,
        expanded: false,
        evaluation: data.evaluation,
      }
    }
  } catch (e) {
    console.error(`loadMessageEvalStatus: error for message ${messageId}`, e)
  }
}

// ─── 对话 CRUD ───

async function handleNewConversation() {
  try {
    // 去重：如果已有未发消息的空会话，直接选中
    const emptyConv = conversations.value.find(c =>
      (!c.title || c.title === '新对话') && c.message_count === 0
    )
    // 通过API查找最近的空会话
    const { data: recentConvs } = await listConversations({ page: 1, page_size: 5 })
    const recentEmpty = (recentConvs?.conversations || []).find(c =>
      c.title === '新对话' && c.message_count === 0
    )
    if (recentEmpty) {
      selectConversation(recentEmpty)
      showToast('已切换到未开始的对话', 'info')
      return
    }

    const { data } = await createConversation({ title: '新对话' })
    showToast('新对话已创建', 'success')
    await loadConversations()
    // 等待列表更新后再选中
    nextTick(() => {
      const conv = conversations.value.find(c => c.id === data.conversation_id)
      if (conv) {
        selectConversation(conv)
      } else {
        // fallback: 选第一个
        if (conversations.value.length > 0) selectConversation(conversations.value[0])
      }
    })
  } catch (e) {
    console.error('Failed to create conversation:', e)
    showToast('创建对话失败', 'error')
  }
}

function handleDeleteConversation(conv) {
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

// ─── 发送消息 ───

// 从文本中提取 @mention 的 agent_key 列表，并清理文本中的 @标记
function extractMentions(text) {
  const targetSpecialists = []
  // 匹配 @Agent名称 模式（名称中可含中文、字母、数字）
  let cleanText = text
  for (const agent of agents.value) {
    const pattern = new RegExp(`@${agent.name}(?=\\s|$)`, 'g')
    if (pattern.test(cleanText)) {
      targetSpecialists.push(agent.agent_key)
      cleanText = cleanText.replace(pattern, '').trim()
    }
  }
  // 清理多余空格
  cleanText = cleanText.replace(/\s+/g, ' ').trim()
  return { targetSpecialists, cleanText }
}

function handleImagesReady(images) {
  pendingImages.value = images
}

async function handleSend() {
  const rawText = inputText.value.trim()
  if (!rawText || !selectedConv.value || sending.value) return

  const { targetSpecialists, cleanText } = extractMentions(rawText)
  const text = cleanText || rawText

  inputText.value = ''
  // 用户消息附带 images metadata
  const userMsg = { role: 'user', content: rawText, created_at: new Date().toISOString() }
  if (pendingImages.value.length > 0) {
    userMsg.metadata = { images: pendingImages.value }
  }
  messages.value.push(userMsg)
  await nextTick()
  scrollToBottom()

  const convId = selectedConv.value.id
  const imagesToSend = [...pendingImages.value]
  pendingImages.value = []
  const controller = sendMessageStream(convId, text, (event) => {
    routeStreamEvent(convId, event, {
      onAnswerChunk: (cid, data, state) => {
        if (selectedConv.value?.id !== cid) return
        const lastMsg = messages.value[messages.value.length - 1]
        if (lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming) {
          // 增量追加答案 + 持续更新思考过程
          lastMsg.content += data.content || ''
          if (state.streamingReasoning) lastMsg.reasoning = state.streamingReasoning
        } else {
          // 首个 chunk：push 占位消息（带已累积的思考过程）
          messages.value.push({
            role: 'assistant',
            content: data.content || '',
            reasoning: state.streamingReasoning || '',
            streaming: true,
            created_at: new Date().toISOString(),
          })
        }
        nextTick(() => scrollToBottom())
      },
      onAnswer: (cid, data, state) => {
        if (selectedConv.value?.id !== cid) return
        const allSpecResults = data.specialist_results || (state.completedSpecialists.length > 0 ? [...state.completedSpecialists] : [])
        const phaseAResults = allSpecResults.filter(s => !s.is_cross_review)
        const phaseBResults = allSpecResults.filter(s => s.is_cross_review)
        const lastMsg = messages.value[messages.value.length - 1]
        if (lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming) {
          // 流式已产出：用全文兜底覆盖 + 附加专家结果
          lastMsg.content = data.content || lastMsg.content
          lastMsg.streaming = false
          lastMsg.specialist_results = phaseAResults.length > 0 ? phaseAResults : null
          lastMsg.cross_review_results = phaseBResults.length > 0 ? phaseBResults : (state.completedCrossReviews.length > 0 ? [...state.completedCrossReviews] : null)
          lastMsg.tool_calls = state.currentToolCalls.length > 0 ? [...state.currentToolCalls] : null
          lastMsg.rag = state.currentToolCalls._ragSources ? { sources: state.currentToolCalls._ragSources } : null
        } else {
          // 未收到 chunk（simple chat / 流式未生效 / 回退）：走原逻辑 push
          messages.value.push({
            role: 'assistant',
            content: data.content,
            created_at: new Date().toISOString(),
            specialist_results: phaseAResults.length > 0 ? phaseAResults : null,
            cross_review_results: phaseBResults.length > 0 ? phaseBResults : (state.completedCrossReviews.length > 0 ? [...state.completedCrossReviews] : null),
            tool_calls: state.currentToolCalls.length > 0 ? [...state.currentToolCalls] : null,
            rag: state.currentToolCalls._ragSources ? { sources: state.currentToolCalls._ragSources } : null,
          })
        }
        nextTick(() => scrollToBottom())
      },
      onDone: (cid, data) => {
        if (selectedConv.value?.id === cid) {
          const state = getStreamState(cid)
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg && lastMsg.role === 'assistant' && state?.lastTiming) {
            lastMsg.duration_ms = state.lastTiming.duration_ms
            lastMsg.phase_timings = state.lastTiming.phase_timings
          }
        }
        finishStream(cid)
        removeTask(cid)
        loadConversations()
      },
      onError: (cid, data) => {
        if (selectedConv.value?.id === cid) {
          messages.value.push({
            role: 'assistant',
            content: '发生错误: ' + (data.message || '未知错误'),
            created_at: new Date().toISOString(),
          })
        }
        finishStream(cid)
        removeTask(cid)
      },
      onTitleUpdated: (cid, title) => {
        loadConversations()
        if (selectedConv.value?.id === cid) {
          selectedConv.value.title = title
        }
      },
    })
  }, targetSpecialists, imagesToSend)
  startStream(convId, controller)
  addTask(convId, null, selectedConv.value?.title || `对话 #${convId}`)
}

function handleCancelStream() {
  const convId = selectedConv.value?.id
  if (!convId) return
  cancelConversationExecution(convId).catch(() => {})
  cancelStreamState(convId)
  messages.value.push({
    role: 'assistant',
    content: '⏹ 执行已取消',
    created_at: new Date().toISOString(),
    cancelled: true,
  })
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
    const btn = document.querySelector('.btn-conv-id')
    if (btn) {
      btn.classList.add('copied')
      setTimeout(() => btn.classList.remove('copied'), 1500)
    }
  }).catch(() => {
    window.prompt('复制对话ID:', selectedConv.value.id)
  })
}

// ─── 恢复流 ───

function restoreStreamStateFromMessage(convId, msg) {
  if (streamStates.has(convId)) return
  const controller = new AbortController()
  startStream(convId, controller)
  const state = getStreamState(convId)
  if (!state) return

  if (msg.specialist_results?.length) {
    state.completedSpecialists = msg.specialist_results.map(s => ({
      agent_key: s.agent_key, agent: s.agent, icon: s.icon || '🤖',
      analysis: s.analysis, duration_ms: s.duration_ms, expanded: false,
    }))
  }
  if (msg.cross_review_results?.length) {
    state.completedCrossReviews = msg.cross_review_results.map(s => ({
      agent_key: s.agent_key, agent: s.agent, icon: s.icon || '🤖',
      analysis: s.analysis, duration_ms: s.duration_ms, expanded: false,
      is_cross_review: true,
    }))
  }
  if (msg.tool_calls?.length) {
    state.currentToolCalls = msg.tool_calls.map(tc => ({ ...tc, expanded: false }))
  }
  if (msg.rag?.sources) {
    state.currentToolCalls._ragSources = msg.rag.sources
  }
  if (msg.complexity) {
    state.executionPlan = { complexity: msg.complexity }
  }
  const totalPhases = msg.complexity === 'simple' ? 3 : (msg.complexity === 'medium' ? 4 : 6)
  state.totalPhases = totalPhases
  state.progressPct = Math.min(35 + (state.completedSpecialists.length * 10), 95)
  state.streamStatus = 'calling_specialist'
  state.substep = `已完成 ${state.completedSpecialists.length} 个专家`
}

async function reconnectStream(convId, existingMsg) {
  restoreStreamStateFromMessage(convId, existingMsg)
  const existingSpecKeys = new Set((existingMsg?.specialist_results || []).map(s => s.agent_key))
  const existingCrossKeys = new Set((existingMsg?.cross_review_results || []).map(s => s.agent_key))

  const controller = resumeConversationStream(convId, (event) => {
    const { type, data } = event
    if (type === 'specialist_done' && existingSpecKeys.has(data.agent_key)) return
    if (type === 'cross_review_done' && existingCrossKeys.has(data.agent_key)) return

    routeStreamEvent(convId, event, {
      onAnswer: (cid, answerData, state) => {
        if (selectedConv.value?.id !== cid) return
        const msgIndex = messages.value.findIndex(m =>
          m.role === 'assistant' && m.execution_status === 'streaming'
        )
        if (msgIndex >= 0) {
          const msg = messages.value[msgIndex]
          msg.content = answerData.content
          msg.specialist_results = state.completedSpecialists.filter(s => !s.is_cross_review)
          msg.cross_review_results = state.completedCrossReviews
          msg.tool_calls = state.currentToolCalls
          msg.rag = state.currentToolCalls._ragSources ? { sources: state.currentToolCalls._ragSources } : null
        } else {
          messages.value.push({
            role: 'assistant',
            content: answerData.content,
            created_at: new Date().toISOString(),
            specialist_results: state.completedSpecialists.filter(s => !s.is_cross_review),
            cross_review_results: state.completedCrossReviews,
            tool_calls: state.currentToolCalls,
            rag: state.currentToolCalls._ragSources ? { sources: state.currentToolCalls._ragSources } : null,
          })
        }
        nextTick(() => scrollToBottom())
      },
      onDone: (cid, doneData) => {
        if (selectedConv.value?.id === cid) {
          const state = getStreamState(cid)
          const msgIndex = messages.value.findIndex(m =>
            m.role === 'assistant' && m.execution_status === 'streaming'
          )
          if (msgIndex >= 0) {
            const msg = messages.value[msgIndex]
            if (state?.lastTiming) {
              msg.duration_ms = state.lastTiming.duration_ms
              msg.phase_timings = state.lastTiming.phase_timings
            }
            msg.execution_status = 'completed'
          }
        }
        finishStream(cid)
        removeTask(cid)
        loadConversations()
        showToast('任务执行完成', 'success')
      },
      onError: (cid, errorData) => {
        if (selectedConv.value?.id === cid) {
          if (errorData.code === 'RESUME_FAILED') {
            finishStream(cid)
            removeTask(cid)
            // 409 表示对话已取消/失败/重复请求，刷新消息显示真实状态，不自动重发
            // 用户会在消息气泡上看到「已取消」或「执行失败」状态及对应的重试按钮
            loadMessages(cid).then(() => {
              const lastAssistant = [...messages.value].reverse().find(m => m.role === 'assistant')
              const status = lastAssistant?.execution_status
              if (status === 'cancelled') {
                showToast('对话已取消，可点击「继续分析」重新执行', 'info')
              } else if (status === 'failed') {
                showToast('对话执行失败，可点击「重新生成」重试', 'info')
              } else if (status === 'completed') {
                showToast('任务已完成', 'success')
              } else {
                console.log('[ChatView] 恢复连接返回 409，当前状态:', status)
              }
            })
            return
          }
          const msgIndex = messages.value.findIndex(m =>
            m.role === 'assistant' && m.execution_status === 'streaming'
          )
          if (msgIndex >= 0) {
            loadMessages(cid).then(() => { showToast('任务状态已更新', 'info') })
          } else {
            messages.value.push({
              role: 'assistant',
              content: '恢复连接失败: ' + (errorData.message || '未知错误'),
              created_at: new Date().toISOString(),
            })
          }
        }
        finishStream(cid)
        removeTask(cid)
      },
    })
  })

  const state = streamStates.get(convId)
  if (state) {
    state.streamAbort = controller
  }
}

async function retryMessage(userMsg) {
  if (!userMsg || userMsg.role !== 'user' || sending.value) return
  const userMsgIndex = messages.value.indexOf(userMsg)
  const assistantMsg = messages.value.find(
    (m, idx) => idx > userMsgIndex && m.role === 'assistant'
  )
  if (assistantMsg) {
    const executionStatus = assistantMsg.execution_status || (assistantMsg.metadata?.execution_status)
    if (executionStatus === 'streaming') {
      if (confirm(`检测到上次执行中断，是否从断点继续？\n\n点击"确定"恢复执行，点击"取消"重新执行。`)) {
        handleResume()
        return
      }
    }
  }
  // 清除取消标记，避免新消息 streaming 期间刷新页面导致 resume 返回 409 孤立连接
  const convId = selectedConv.value?.id
  if (convId) {
    try {
      await clearCancelFlag(convId)
    } catch (e) {
      console.warn('清除取消标记失败:', e)
    }
  }
  inputText.value = userMsg.content
  handleSend()
}

async function continueAssistantMessage(msg) {
  const convId = selectedConv.value?.id
  if (!convId || sending.value) return
  // 清除取消标记（resume 接口对 cancel_requested=true 的对话返回 409）
  try {
    await clearCancelFlag(convId)
  } catch (e) {
    console.warn('清除取消标记失败:', e)
  }
  // 乐观更新本地消息状态为 streaming，让 tryResumeConversation 的 onAnswer 能定位到这条消息
  if (msg) {
    msg.execution_status = 'streaming'
    messages.value = [...messages.value]
  }
  tryResumeConversation(convId)
}

async function regenerateAssistantMessage(msg) {
  const convId = selectedConv.value?.id
  if (!convId || !msg?.id || sending.value) return
  try {
    const { data } = await retryConversationMessage(convId, msg.id)
    await loadMessages(convId)
    if (data.original_query) {
      sendMessageAndTrack(convId, data.original_query)
    }
  } catch (e) {
    showToast('重新生成失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

async function handleResume() {
  const convId = selectedConv.value?.id
  if (!convId || sending.value) return
  sending.value = true
  const controller = resumeConversationStream(convId, (event) => {
    routeStreamEvent(convId, event, {
      onAnswerChunk: (cid, data, state) => {
        if (selectedConv.value?.id !== cid) return
        const lastMsg = messages.value[messages.value.length - 1]
        if (lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming) {
          // 增量追加答案 + 持续更新思考过程
          lastMsg.content += data.content || ''
          if (state.streamingReasoning) lastMsg.reasoning = state.streamingReasoning
        } else {
          // 首个 chunk：push 占位消息（带已累积的思考过程）
          messages.value.push({
            role: 'assistant',
            content: data.content || '',
            reasoning: state.streamingReasoning || '',
            streaming: true,
            created_at: new Date().toISOString(),
          })
        }
        nextTick(() => scrollToBottom())
      },
      onAnswer: (cid, data, state) => {
        if (selectedConv.value?.id !== cid) return
        const allSpecResults = data.specialist_results || (state.completedSpecialists.length > 0 ? [...state.completedSpecialists] : [])
        const phaseAResults = allSpecResults.filter(s => !s.is_cross_review)
        const phaseBResults = allSpecResults.filter(s => s.is_cross_review)
        const lastMsg = messages.value[messages.value.length - 1]
        if (lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming) {
          // 流式已产出：用全文兜底覆盖 + 附加专家结果
          lastMsg.content = data.content || lastMsg.content
          lastMsg.streaming = false
          lastMsg.specialist_results = phaseAResults.length > 0 ? phaseAResults : null
          lastMsg.cross_review_results = phaseBResults.length > 0 ? phaseBResults : (state.completedCrossReviews.length > 0 ? [...state.completedCrossReviews] : null)
          lastMsg.tool_calls = state.currentToolCalls.length > 0 ? [...state.currentToolCalls] : null
          lastMsg.rag = state.currentToolCalls._ragSources ? { sources: state.currentToolCalls._ragSources } : null
        } else {
          // 未收到 chunk（simple chat / 流式未生效 / 回退）：走原逻辑 push
          messages.value.push({
            role: 'assistant',
            content: data.content,
            created_at: new Date().toISOString(),
            specialist_results: phaseAResults.length > 0 ? phaseAResults : null,
            cross_review_results: phaseBResults.length > 0 ? phaseBResults : (state.completedCrossReviews.length > 0 ? [...state.completedCrossReviews] : null),
            tool_calls: state.currentToolCalls.length > 0 ? [...state.currentToolCalls] : null,
            rag: state.currentToolCalls._ragSources ? { sources: state.currentToolCalls._ragSources } : null,
          })
        }
        nextTick(() => scrollToBottom())
      },
      onDone: (cid, data) => {
        if (selectedConv.value?.id === cid) {
          const state = getStreamState(cid)
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg && lastMsg.role === 'assistant' && state?.lastTiming) {
            lastMsg.duration_ms = state.lastTiming.duration_ms
            lastMsg.phase_timings = state.lastTiming.phase_timings
          }
        }
        finishStream(cid)
        sending.value = false
        loadConversations()
      },
      onError: (cid, data) => {
        if (data.code === 'RESUME_FAILED') {
          finishStream(cid)
          sending.value = false
          const lastUserMsg = [...messages.value].reverse().find(m => m.role === 'user')
          if (lastUserMsg) {
            showToast('对话已中断，正在重新执行...', 'info')
            inputText.value = lastUserMsg.content
            handleSend()
          }
          return
        }
        if (selectedConv.value?.id === cid) {
          messages.value.push({
            role: 'assistant',
            content: '恢复执行失败: ' + (data.message || '未知错误'),
            created_at: new Date().toISOString(),
          })
        }
        finishStream(cid)
        sending.value = false
      },
    })
  })
  startStream(convId, controller)
}

function isTaskRunning(convId) {
  return pendingTasks.value.some(t => t.convId === convId)
}

// ─── 消息评估 ───

function getEvalStatusClass(messageId) {
  const state = messageEvalStates.value[messageId]
  if (!state) return 'eval-status--none'
  if (state.loading) return 'eval-status--loading'
  if (state.score >= 80) return 'eval-status--good'
  if (state.score >= 60) return 'eval-status--ok'
  if (state.score > 0) return 'eval-status--bad'
  return 'eval-status--none'
}

async function toggleMessageEval(messageId) {
  const state = messageEvalStates.value[messageId]
  if (!state || (!state.evaluation && !state.loading)) {
    confirm.value = {
      visible: true,
      title: '质量评估',
      message: '是否对此消息进行质量评估？',
      danger: false,
      onConfirm: async () => {
        confirm.value.visible = false
        await triggerMessageEval(messageId)
      },
    }
    return
  }
  if (state.loading) {
    showToast('正在评估中，请稍候...', 'info')
    return
  }
  if (!messageEvalStates.value[messageId]) {
    messageEvalStates.value[messageId] = { score: 0, loading: false, expanded: true }
  } else {
    messageEvalStates.value[messageId].expanded = !messageEvalStates.value[messageId].expanded
  }
}

async function loadMessageEval(messageId) {
  if (!selectedConv.value?.id) return
  messageEvalStates.value[messageId] = { score: 0, loading: true, expanded: true }
  try {
    const { data } = await getConversationEvaluation(selectedConv.value.id, messageId)
    if (data.ok && data.evaluation) {
      messageEvalStates.value[messageId] = {
        score: data.evaluation.auto_score || 0,
        loading: false,
        expanded: true,
        evaluation: data.evaluation,
      }
    } else {
      messageEvalStates.value[messageId] = { score: 0, loading: false, expanded: true }
    }
  } catch (e) {
    messageEvalStates.value[messageId] = { score: 0, loading: false, expanded: true }
  }
}

async function triggerMessageEval(messageId) {
  if (!selectedConv.value?.id) return
  messageEvalStates.value[messageId] = { score: 0, loading: true, expanded: true }
  try {
    const { data } = await evaluateConversation(selectedConv.value.id, messageId)
    if (data.ok && data.evaluation) {
      messageEvalStates.value[messageId] = {
        score: data.evaluation.auto_score || 0,
        loading: false,
        expanded: true,
        evaluation: data.evaluation,
        evolution: data.evolution,
      }
      showToast('评估完成', 'success')
    } else {
      messageEvalStates.value[messageId] = { score: 0, loading: false, expanded: true }
      showToast('评估失败', 'error')
    }
  } catch (e) {
    messageEvalStates.value[messageId] = { score: 0, loading: false, expanded: true }
    showToast('评估失败', 'error')
  }
}

async function triggerLLMEval(messageId) {
  if (!selectedConv.value?.id) return
  messageEvalStates.value[messageId] = {
    ...messageEvalStates.value[messageId],
    loading: true,
    expanded: true,
  }
  try {
    const { data } = await evaluateConversationWithLLM(selectedConv.value.id, messageId)
    if (data.ok && data.evaluation) {
      const llmEval = data.evaluation
      messageEvalStates.value[messageId] = {
        score: llmEval.total_score || 0,
        loading: false,
        expanded: true,
        evaluation: {
          ...messageEvalStates.value[messageId]?.evaluation,
          llm_evaluation: llmEval,
        },
      }
      showToast('LLM 评估完成', 'success')
    } else {
      showToast('LLM 评估失败', 'error')
      messageEvalStates.value[messageId].loading = false
    }
  } catch (e) {
    showToast('LLM 评估失败: ' + (e.response?.data?.detail || e.message), 'error')
    messageEvalStates.value[messageId].loading = false
  }
}

// ─── 反馈 ───

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

function copyMessageId(msgId) {
  const text = String(msgId)
  navigator.clipboard.writeText(text).then(() => {
    showToast('已复制消息ID: ' + text, 'success')
  }).catch(() => {
    const input = document.createElement('input')
    input.value = text
    document.body.appendChild(input)
    input.select()
    document.execCommand('copy')
    document.body.removeChild(input)
    showToast('已复制消息ID: ' + text, 'success')
  })
}

async function saveMessageAsDecision(msg, index) {
  if (!selectedConv.value?.id || !msg?.id) return
  const previousUser = [...messages.value.slice(0, index)].reverse().find(m => m.role === 'user')
  confirm.value = {
    visible: true,
    title: '保存为决策草案',
    message: '将把这条 AI 回复保存到理财决策中枢，并自动生成检查清单和复盘日期。此操作不会执行任何交易。',
    danger: false,
    onConfirm: async () => {
      try {
        const { data } = await createDecisionFromChat({
          conversation_id: selectedConv.value.id,
          assistant_message_id: msg.id,
          user_message_id: previousUser?.id || null,
          target_type: 'portfolio',
          target_name: '',
          review_days: 30,
        })
        if (data?.ok) {
          showToast(`已保存为决策草案 #${data.id}`, 'success')
        } else {
          showToast('保存决策草案失败', 'error')
        }
      } catch (e) {
        showToast('保存决策草案失败: ' + (e.response?.data?.detail || e.message), 'error')
      } finally {
        confirm.value.visible = false
      }
    }
  }
}

// ─── Trace 详情 ───

async function toggleTraceDetail(msg, index) {
  const key = `${selectedConv.value?.id}_${index}`
  if (traceDetailVisible.value[key]) {
    traceDetailVisible.value[key] = false
    return
  }
  try {
    const { data } = await listTraces(selectedConv.value.id, 1)
    if (data.traces?.length > 0) {
      traceDetailData.value[key] = data.traces[0]
      traceDetailVisible.value[key] = true
    }
  } catch (e) {
    console.error('加载 trace 失败:', e)
  }
}

// ─── 格式化工具函数 ───

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

// ─── 进度轮询 ───

let pollingTimer = null
function startPollingProgress(convId) {
  stopPollingProgress()
  pollingTimer = setInterval(async () => {
    const state = getStreamState(convId)
    if (!state?.sending) {
      stopPollingProgress()
      return
    }
  }, 3000)
}

function stopPollingProgress() {
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}
</script>

<template>
  <div class="chat-page bg-grid">
    <!-- 移动端：对话列表切换按钮 -->
    <button class="mobile-sidebar-toggle" @click="showMobileSidebar = !showMobileSidebar" title="对话列表">
      <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
      </svg>
    </button>

    <!-- 对话侧边栏 -->
    <ChatSidebar
      :conversations="conversations"
      :selectedConv="selectedConv"
      :streamStates="streamStates"
      :isTaskRunning="isTaskRunning"
      :showMobileSidebar="showMobileSidebar"
      @select="selectConversation"
      @new="handleNewConversation"
      @delete="handleDeleteConversation"
      @update:showMobileSidebar="showMobileSidebar = $event"
    />

    <!-- 聊天区域 -->
    <div class="chat-area">
      <template v-if="selectedConv">
        <!-- 聊天头部 -->
        <div class="chat-header">
          <div class="chat-header-info">
            <span class="chat-agent-icon"><Icon name="bot" size="16" /></span>
            <span class="chat-agent-name editorial-title">投资分析助手</span>
          </div>
          <button class="btn-conv-id" @click="copyConvId" :title="'对话 #' + selectedConv.id">
            <span class="conv-id-text font-jet">#{{ selectedConv.id }}</span>
            <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <rect x="9" y="9" width="13" height="13" rx="2" stroke-width="2"/>
              <path stroke-width="2" d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
            </svg>
          </button>
        </div>

        <!-- 消息列表 -->
        <div ref="messagesContainer" class="messages-container">
          <ChatMessage
            v-for="(msg, i) in messages"
            :key="i"
            :msg="msg"
            :index="i"
            :convId="selectedConv?.id"
            :feedbackGiven="feedbackGiven"
            :specialistFeedback="specialistFeedback"
            :messageEvalStates="messageEvalStates"
            :traceDetailVisible="traceDetailVisible"
            :traceDetailData="traceDetailData"
            @feedback="handleFeedback"
            @specialist-feedback="handleSpecialistFeedback"
            @toggle-eval="toggleMessageEval"
            @trigger-eval="triggerMessageEval"
            @trigger-llm-eval="triggerLLMEval"
            @copy-message-id="copyMessageId"
            @toggle-trace="toggleTraceDetail"
            @save-decision="saveMessageAsDecision"
            @execute-trade="executeTradeFromChat"
            @retry="retryMessage"
            @resume="tryResumeConversation"
            @continue-analysis="continueAssistantMessage"
            @regenerate="regenerateAssistantMessage"
          />

          <!-- 流式状态指示器 -->
          <StreamIndicator v-if="currentStream?.sending" :currentStream="currentStream" />
        </div>

        <!-- 输入框 -->
        <ChatInput
          v-model:inputText="inputText"
          :sending="sending"
          :statusMessage="currentStream?.statusMessage"
          :agents="agents"
          @send="handleSend"
          @cancel="handleCancelStream"
          @images-ready="handleImagesReady"
        />
      </template>

      <!-- 空状态 -->
      <div v-else class="chat-empty bg-mesh">
        <div class="chat-empty-icon"><Icon name="chat" size="48" /></div>
        <h3 class="editorial-title-lg">选择或创建一个对话</h3>
        <p>选择左侧对话继续，或点击 + 创建新对话</p>
      </div>
    </div>
  </div>

  <!-- 反馈输入弹窗 -->
  <FeedbackModal
    :visible="feedbackModal.visible"
    :feedbackType="feedbackModal.feedbackType"
    :note="feedbackModal.note"
    @update:visible="feedbackModal.visible = $event"
    @update:note="feedbackModal.note = $event"
    @skip="feedbackModal.note = ''; submitFeedback()"
    @submit="submitFeedback"
  />

  <ConfirmDialog
    :visible="confirm.visible"
    :title="confirm.title"
    :message="confirm.message"
    :danger="confirm.danger"
    @confirm="() => confirm.onConfirm?.()"
    @cancel="confirm.visible = false"
  />
  <AppToast />
</template>

<style scoped>
.chat-page {
  display: flex;
  height: calc(100vh - 120px);
  height: calc(100dvh - 120px);
  gap: 0;
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
}

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
  padding: 0.85rem 1.5rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  position: relative;
  z-index: 2;
}
/* 底部渐变分割线 */
.chat-header::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--color-primary-border), transparent);
}

.chat-header-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.chat-agent-icon { font-size: 1.2rem; }
.chat-agent-name { font-size: 0.88rem; font-weight: 600; color: var(--color-text-primary); }

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
  color: var(--color-success);
  border-color: var(--color-success);
}

.conv-id-text {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

/* ── 消息列表 ── */
.messages-container {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding: 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
}

/* ── 空状态 ── */
.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  gap: 0.75rem;
  padding: 2rem;
}
.chat-empty-icon {
  font-size: 4rem;
  background: linear-gradient(135deg, var(--color-primary-400), var(--color-accent-400));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  filter: drop-shadow(0 2px 8px var(--color-primary-glow));
}
.chat-empty h3 {
  margin: 0;
  font-size: 1.1rem;
  color: var(--color-text-secondary);
  font-weight: 600;
}
.chat-empty p {
  margin: 0;
  font-size: 0.88rem;
  color: var(--color-text-muted);
  max-width: 260px;
  text-align: center;
  line-height: 1.6;
}

/* ── Transition ── */
.fade-enter-active { transition: opacity 0.2s; }
.fade-leave-active { transition: opacity 0.15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* ── 移动端菜单按钮 ── */
.mobile-sidebar-toggle {
  display: none;
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .chat-page {
    flex-direction: column;
    position: relative;
    flex: 1;
    min-height: 0;
    height: auto;
    border-radius: 0;
    border: none;
  }

  .mobile-sidebar-toggle {
    display: flex;
    align-items: center;
    justify-content: center;
    position: absolute;
    top: 0.5rem;
    left: 0.5rem;
    z-index: 51;
    width: 36px;
    height: 36px;
    border-radius: var(--radius-md);
    background: var(--glass-bg);
    backdrop-filter: blur(var(--glass-blur));
    -webkit-backdrop-filter: blur(var(--glass-blur));
    border: 1px solid var(--glass-border);
    color: var(--color-text-secondary);
    cursor: pointer;
  }

  .chat-area {
    flex: 1;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    height: calc(100vh - 120px);
    height: calc(100dvh - 120px);
  }

  .chat-header {
    padding-left: 2.5rem;
  }

  .messages-container {
    flex: 1;
    padding: 0.75rem;
    gap: 0.6rem;
  }
}
</style>
