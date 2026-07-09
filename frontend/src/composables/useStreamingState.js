/**
 * Per-conversation streaming state management.
 *
 * 核心设计：每个对话有独立的流式状态，支持多对话同时执行。
 * 用 reactive(new Map()) 存储，Vue 自动追踪变化。
 */

import { reactive, computed, onUnmounted } from 'vue'

// 每个对话的流式状态结构
function createStreamState() {
  return {
    sending: true,
    streamStatus: '',        // 'searching' | 'thinking' | 'calling_tool' | 'calling_specialist' | 'cross_reviewing' | 'answering'
    statusMessage: '',
    executionPlan: null,
    currentToolCalls: [],
    activeSpecialists: [],
    completedSpecialists: [],
    crossReviewSpecialists: [],
    completedCrossReviews: [],
    elapsedMs: 0,
    elapsedTimer: null,
    lastTiming: null,
    streamAbort: null,       // AbortController
    // 进度信息（Phase C）
    currentPhase: '',
    phaseLabel: '',
    substep: '',
    progressPct: 0,
    progressDetail: null,
    totalPhases: 0,
    phaseIndex: 0,
    // 流式增量累积（阶段二：思考过程 + 答案逐字流）
    streamingContent: '',
    streamingReasoning: '',
  }
}

// 全局状态映射（模块级单例，组件卸载后仍存活）
const streamStates = reactive(new Map())

/**
 * 获取指定对话的流式状态
 * @param {number} convId
 * @returns {object|null}
 */
function getStreamState(convId) {
  if (!convId) return null
  return streamStates.get(convId) || null
}

/**
 * 开始一个新的流式执行
 * @param {number} convId
 * @param {AbortController} abortController
 */
function startStream(convId, abortController) {
  // 如果已有流，先中止旧的（防止覆盖导致 AbortController 孤立）
  const existing = streamStates.get(convId)
  if (existing) {
    if (existing.streamAbort) {
      existing.streamAbort.abort()
    }
    if (existing.elapsedTimer) {
      clearInterval(existing.elapsedTimer)
    }
  }

  const state = createStreamState()
  state.streamAbort = abortController

  // 启动计时器
  state.elapsedTimer = setInterval(() => {
    state.elapsedMs += 1000
  }, 1000)

  streamStates.set(convId, state)
}

/**
 * 处理流式事件，路由到正确的对话状态
 * @param {number} convId
 * @param {{type: string, data: object}} event
 * @param {object} callbacks - 回调函数集合
 * @param {function} callbacks.onAnswer - 收到最终回答时回调 (convId, data)
 * @param {function} callbacks.onDone - 流完成时回调 (convId, data)
 * @param {function} callbacks.onError - 出错时回调 (convId, data)
 * @returns {boolean} 是否处理了事件
 */
function handleStreamEvent(convId, event, callbacks = {}) {
  const state = streamStates.get(convId)
  if (!state) return false

  const { type, data } = event

  switch (type) {
    case 'status':
      state.streamStatus = data.message.includes('检索') ? 'searching' : 'thinking'
      state.statusMessage = data.message
      break

    case 'phase':
      // 澄清/路由/分析阶段 — 实时显示阶段信息（含澄清原因）
      state.streamStatus = 'thinking'
      state.statusMessage = data.message
      break

    case 'plan':
      state.executionPlan = data
      // 显示澄清原因和改写后的问题（如果有的话）
      if (data.reason) {
        const parts = [data.reason]
        if (data.refined_query) {
          parts.push(`理解为：${data.refined_query}`)
        }
        state.statusMessage = parts.join(' | ')
      }
      break

    case 'rag_sources':
      state.currentToolCalls._ragSources = data.sources
      break

    case 'tool_call':
      state.streamStatus = 'calling_tool'
      state.currentToolCalls.push({
        name: data.name,
        arguments: data.arguments,
        result_preview: data.result_preview,
        expanded: false,
      })
      break

    case 'specialist_start':
      state.streamStatus = 'calling_specialist'
      state.activeSpecialists.push({
        agent_key: data.agent_key,
        agent: data.agent,
        icon: data.icon,
        status: 'running',
      })
      break

    case 'specialist_done':
      state.activeSpecialists = state.activeSpecialists.filter(s => s.agent_key !== data.agent_key)
      state.completedSpecialists.push({
        agent_key: data.agent_key,
        agent: data.agent,
        icon: data.icon,
        analysis: data.analysis,
        status: data.status || 'success',
        error: data.error || false,
        duration_ms: data.duration_ms,
        expanded: false,
      })
      break

    case 'cross_review_start':
      state.streamStatus = 'cross_reviewing'
      state.crossReviewSpecialists.push({
        agent_key: data.agent_key,
        agent: data.agent,
        icon: data.icon,
        status: 'running',
      })
      break

    case 'cross_review_done':
      state.crossReviewSpecialists = state.crossReviewSpecialists.filter(s => s.agent_key !== data.agent_key)
      state.completedCrossReviews.push({
        agent_key: data.agent_key,
        agent: data.agent,
        icon: data.icon,
        analysis: data.analysis,
        duration_ms: data.duration_ms,
        expanded: false,
      })
      break

    case 'reasoning_chunk':
      // 思考过程增量（thinking mode）
      state.streamStatus = 'thinking'
      state.streamingReasoning += data.content || ''
      if (callbacks.onReasoning) {
        callbacks.onReasoning(convId, data, state)
      }
      break

    case 'answer_chunk':
      // 答案增量（逐字流）
      state.streamStatus = 'answering'
      state.streamingContent += data.content || ''
      if (callbacks.onAnswerChunk) {
        callbacks.onAnswerChunk(convId, data, state)
      }
      break

    case 'answer':
      state.streamStatus = 'answering'
      // 回调通知 ChatView 添加消息
      if (callbacks.onAnswer) {
        callbacks.onAnswer(convId, data, state)
      }
      break

    case 'clarification':
      // 交互式澄清：通知 ChatView 展示澄清问题 + 选项
      state.streamStatus = 'clarifying'
      state.statusMessage = data.question || '需要更多信息'
      if (callbacks.onClarification) {
        callbacks.onClarification(convId, data, state)
      }
      break

    case 'done':
      _finishStream(convId, state, data)
      if (callbacks.onDone) {
        callbacks.onDone(convId, data)
      }
      break

    case 'error':
      _finishStream(convId, state, null)
      if (callbacks.onError) {
        callbacks.onError(convId, data)
      }
      break

    case 'progress':
      state.currentPhase = data.phase
      state.phaseLabel = data.phase_label || ''
      state.substep = data.substep || ''
      state.progressPct = data.pct || 0
      state.progressDetail = data.detail || null
      state.totalPhases = data.total_phases || 0
      state.phaseIndex = data.phase_index || 0
      break

    case 'title_updated':
      // 标题更新事件，通知前端刷新对话列表
      if (callbacks.onTitleUpdated) {
        callbacks.onTitleUpdated(convId, data.title)
      }
      break

    default:
      return false
  }

  return true
}

/**
 * 内部：完成流式执行，清理计时器
 */
function _finishStream(convId, state, data) {
  if (state.elapsedTimer) {
    clearInterval(state.elapsedTimer)
    state.elapsedTimer = null
  }
  if (data) {
    state.lastTiming = {
      duration_ms: data.duration_ms,
      phase_timings: data.phase_timings,
    }
  }
  state.sending = false
}

/**
 * 完成并移除流式状态（消息已保存到 DB 后调用）
 * @param {number} convId
 * @param {boolean} abort - 是否真正中止 SSE 连接（默认 true）
 *   - true: 切走页面/页面不可见时，中止前端 SSE fetch（后端任务不受影响，仍会跑完并落库）
 *   - false: 流自然结束（done/error 事件已收到），仅清理本地状态
 */
function finishStream(convId, abort = true) {
  const state = streamStates.get(convId)
  if (state) {
    if (state.elapsedTimer) {
      clearInterval(state.elapsedTimer)
      state.elapsedTimer = null
    }
    if (state.streamAbort) {
      // 真正中止 SSE fetch，避免切走后旧连接残留 + 切回时新旧 SSE 并发
      // sendMessageStream 内部已捕获 AbortError，不会报错
      if (abort) state.streamAbort.abort()
      state.streamAbort = null
    }
    streamStates.delete(convId)
  }
}

/**
 * 取消指定对话的流式执行
 * @param {number} convId
 * @param {function} notifyBackend - 通知后端取消的函数 (convId) => Promise
 */
function cancelStream(convId, notifyBackend) {
  const state = streamStates.get(convId)
  if (!state) return

  // 通知后端
  if (notifyBackend) {
    notifyBackend(convId).catch(() => {})
  }

  // 中止 SSE
  if (state.streamAbort) {
    state.streamAbort.abort()
    state.streamAbort = null
  }

  // 清理计时器
  if (state.elapsedTimer) {
    clearInterval(state.elapsedTimer)
    state.elapsedTimer = null
  }

  // 移除状态
  streamStates.delete(convId)
}

/**
 * 是否有任何对话在执行中
 */
const isAnyStreaming = computed(() => {
  for (const state of streamStates.values()) {
    if (state.sending) return true
  }
  return false
})

/**
 * 获取所有正在执行的对话 ID
 */
const streamingConvIds = computed(() => {
  const ids = []
  for (const [convId, state] of streamStates.entries()) {
    if (state.sending) ids.push(convId)
  }
  return ids
})

export function useStreamingState() {
  onUnmounted(() => {
    // 清理所有未完成的流计时器
    for (const [convId, state] of streamStates) {
      if (state.elapsedTimer) {
        clearInterval(state.elapsedTimer)
        state.elapsedTimer = null
      }
      if (state.streamAbort) {
        state.streamAbort.abort()
        state.streamAbort = null
      }
    }
    streamStates.clear()
  })

  return {
    streamStates,
    getStreamState,
    startStream,
    handleStreamEvent,
    finishStream,
    cancelStream,
    isAnyStreaming,
    streamingConvIds,
  }
}
