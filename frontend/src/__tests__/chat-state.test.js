// Chat 状态管理测试
// 验证对话切换、stream 生命周期、多对话并发、进度事件等关键状态流

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref, reactive, computed, nextTick } from 'vue'

// ── 模拟 useStreamingState composable ──────────────────────────────────────

function createStreamState() {
  return {
    sending: true,
    streamStatus: '',
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
    streamAbort: null,
    currentPhase: '',
    phaseLabel: '',
    substep: '',
    progressPct: 0,
    progressDetail: null,
    totalPhases: 0,
    phaseIndex: 0,
  }
}

function createStreamingStateComposable() {
  const streamStates = reactive(new Map())

  function getStreamState(convId) {
    if (!convId) return null
    return streamStates.get(convId) || null
  }

  function startStream(convId) {
    streamStates.set(convId, createStreamState())
  }

  function handleStreamEvent(convId, event, callbacks = {}) {
    const state = streamStates.get(convId)
    if (!state) return false

    const { type, data } = event
    switch (type) {
      case 'status':
        state.streamStatus = data.message.includes('检索') ? 'searching' : 'thinking'
        state.statusMessage = data.message
        break
      case 'plan':
        state.executionPlan = data
        break
      case 'specialist_start':
        state.streamStatus = 'calling_specialist'
        state.activeSpecialists.push({ agent_key: data.agent_key, agent: data.agent })
        break
      case 'specialist_done':
        state.activeSpecialists = state.activeSpecialists.filter(s => s.agent_key !== data.agent_key)
        state.completedSpecialists.push({ agent_key: data.agent_key, agent: data.agent })
        break
      case 'answer':
        state.streamStatus = 'answering'
        if (callbacks.onAnswer) callbacks.onAnswer(convId, data, state)
        break
      case 'done':
        state.sending = false
        state.lastTiming = { duration_ms: data.duration_ms }
        if (callbacks.onDone) callbacks.onDone(convId, data)
        break
      case 'error':
        state.sending = false
        if (callbacks.onError) callbacks.onError(convId, data)
        break
      case 'progress':
        state.currentPhase = data.phase
        state.phaseLabel = data.phase_label || ''
        state.substep = data.substep || ''
        state.progressPct = data.pct || 0
        state.totalPhases = data.total_phases || 0
        state.phaseIndex = data.phase_index || 0
        break
      default:
        return false
    }
    return true
  }

  function finishStream(convId) {
    streamStates.delete(convId)
  }

  function cancelStream(convId) {
    streamStates.delete(convId)
  }

  const isAnyStreaming = computed(() => {
    for (const state of streamStates.values()) {
      if (state.sending) return true
    }
    return false
  })

  return { streamStates, getStreamState, startStream, handleStreamEvent, finishStream, cancelStream, isAnyStreaming }
}

// ── 模拟 ChatView 核心状态逻辑 ──────────────────────────────────────────────

function createChatState() {
  const streaming = createStreamingStateComposable()
  const selectedConv = ref(null)
  const messages = ref([])

  function selectConversation(conv) {
    selectedConv.value = conv
  }

  return {
    ...streaming,
    selectedConv,
    messages,
    selectConversation,
  }
}

// ── 测试 ──────────────────────────────────────────────────────────────────

describe('对话切换状态管理', () => {
  let state

  beforeEach(() => {
    state = createChatState()
  })

  it('初始状态正确', () => {
    expect(state.selectedConv.value).toBeNull()
    expect(state.messages.value).toEqual([])
    expect(state.streamStates.size).toBe(0)
    expect(state.isAnyStreaming.value).toBe(false)
  })

  it('选择对话后 selectedConv 更新', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    expect(state.selectedConv.value.id).toBe(1)
  })

  it('切换对话保留所有流式状态', () => {
    // 在对话 A 中开始流式
    state.selectConversation({ id: 1, title: '对话A' })
    state.startStream(1)
    state.handleStreamEvent(1, { type: 'status', data: { message: '分析中...' } })

    // 切换到对话 B
    state.selectConversation({ id: 2, title: '对话B' })

    // 对话 A 的状态应该保留
    const stateA = state.getStreamState(1)
    expect(stateA).not.toBeNull()
    expect(stateA.sending).toBe(true)
    expect(stateA.statusMessage).toBe('分析中...')
  })

  it('stream 事件路由到正确的对话状态', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    state.startStream(1)

    // 对话 1 的事件
    const handled1 = state.handleStreamEvent(1, { type: 'status', data: { message: '分析中' } })
    expect(handled1).toBe(true)
    expect(state.getStreamState(1).statusMessage).toBe('分析中')

    // 对话 1 的后续事件
    state.handleStreamEvent(1, { type: 'status', data: { message: '还在分析' } })
    expect(state.getStreamState(1).statusMessage).toBe('还在分析')
  })

  it('answer 事件触发 onAnswer 回调', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    state.startStream(1)

    let answerCalled = false
    state.handleStreamEvent(1, {
      type: 'answer',
      data: { content: '这是回复' },
    }, {
      onAnswer: (cid, data) => {
        answerCalled = true
        expect(cid).toBe(1)
        expect(data.content).toBe('这是回复')
      },
    })

    expect(answerCalled).toBe(true)
    expect(state.getStreamState(1).streamStatus).toBe('answering')
  })

  it('done 事件清除执行状态并触发回调', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    state.startStream(1)

    let doneCalled = false
    state.handleStreamEvent(1, { type: 'done', data: { duration_ms: 5000 } }, {
      onDone: (cid) => {
        doneCalled = true
        expect(cid).toBe(1)
      },
    })

    expect(doneCalled).toBe(true)
    expect(state.getStreamState(1).sending).toBe(false)
  })

  it('error 事件触发 onError 回调', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    state.startStream(1)

    let errorCalled = false
    state.handleStreamEvent(1, { type: 'error', data: { message: '超时' } }, {
      onError: (cid, data) => {
        errorCalled = true
        expect(data.message).toBe('超时')
      },
    })

    expect(errorCalled).toBe(true)
    expect(state.getStreamState(1).sending).toBe(false)
  })

  it('finishStream 清理状态条目', () => {
    state.startStream(1)
    expect(state.streamStates.has(1)).toBe(true)

    state.finishStream(1)
    expect(state.streamStates.has(1)).toBe(false)
  })

  it('cancelStream 清理状态条目', () => {
    state.startStream(1)
    expect(state.streamStates.has(1)).toBe(true)

    state.cancelStream(1)
    expect(state.streamStates.has(1)).toBe(false)
  })
})

describe('多对话并发执行', () => {
  let state

  beforeEach(() => {
    state = createChatState()
  })

  it('两个对话可以同时有活跃的 stream', () => {
    state.startStream(1)
    state.startStream(2)

    expect(state.streamStates.size).toBe(2)
    expect(state.isAnyStreaming.value).toBe(true)
  })

  it('切换对话保留另一个对话的 stream 状态', () => {
    state.startStream(1)
    state.handleStreamEvent(1, { type: 'status', data: { message: '对话1分析中' } })

    state.startStream(2)
    state.handleStreamEvent(2, { type: 'status', data: { message: '对话2分析中' } })

    // 两个对话的状态独立
    expect(state.getStreamState(1).statusMessage).toBe('对话1分析中')
    expect(state.getStreamState(2).statusMessage).toBe('对话2分析中')
  })

  it('对话 A 的事件不影响对话 B 的状态', () => {
    state.startStream(1)
    state.startStream(2)

    state.handleStreamEvent(1, { type: 'specialist_start', data: { agent_key: 'analyst', agent: '分析师' } })

    // 对话 1 有专家活动
    expect(state.getStreamState(1).activeSpecialists).toHaveLength(1)
    // 对话 2 没有专家活动
    expect(state.getStreamState(2).activeSpecialists).toHaveLength(0)
  })

  it('一个对话完成不影响另一个', () => {
    state.startStream(1)
    state.startStream(2)

    state.handleStreamEvent(1, { type: 'done', data: { duration_ms: 3000 } })

    // 对话 1 完成
    expect(state.getStreamState(1).sending).toBe(false)
    // 对话 2 仍在执行
    expect(state.getStreamState(2).sending).toBe(true)
    // isAnyStreaming 仍为 true
    expect(state.isAnyStreaming.value).toBe(true)
  })

  it('所有对话完成后 isAnyStreaming 为 false', () => {
    state.startStream(1)
    state.startStream(2)

    state.handleStreamEvent(1, { type: 'done', data: { duration_ms: 3000 } })
    state.handleStreamEvent(2, { type: 'done', data: { duration_ms: 5000 } })

    expect(state.isAnyStreaming.value).toBe(false)
  })

  it('getStreamState 返回 null 对于不存在的对话', () => {
    expect(state.getStreamState(999)).toBeNull()
    expect(state.getStreamState(null)).toBeNull()
  })
})

describe('进度事件处理', () => {
  let state

  beforeEach(() => {
    state = createChatState()
  })

  it('progress 事件更新阶段状态', () => {
    state.startStream(1)

    state.handleStreamEvent(1, {
      type: 'progress',
      data: {
        phase: 'clarification',
        phase_index: 1,
        total_phases: 5,
        phase_label: '理解问题',
        pct: 10,
      },
    })

    const s = state.getStreamState(1)
    expect(s.currentPhase).toBe('clarification')
    expect(s.phaseLabel).toBe('理解问题')
    expect(s.progressPct).toBe(10)
    expect(s.totalPhases).toBe(5)
    expect(s.phaseIndex).toBe(1)
  })

  it('progress 事件更新 substep', () => {
    state.startStream(1)

    state.handleStreamEvent(1, {
      type: 'progress',
      data: {
        phase: 'specialists',
        phase_label: '专家分析',
        substep: '估值专家 分析中 (2/4)',
        pct: 50,
      },
    })

    const s = state.getStreamState(1)
    expect(s.substep).toBe('估值专家 分析中 (2/4)')
    expect(s.progressPct).toBe(50)
  })

  it('进度百分比正确存储', () => {
    state.startStream(1)

    state.handleStreamEvent(1, { type: 'progress', data: { pct: 0 } })
    expect(state.getStreamState(1).progressPct).toBe(0)

    state.handleStreamEvent(1, { type: 'progress', data: { pct: 50 } })
    expect(state.getStreamState(1).progressPct).toBe(50)

    state.handleStreamEvent(1, { type: 'progress', data: { pct: 100 } })
    expect(state.getStreamState(1).progressPct).toBe(100)
  })

  it('阶段转换正确跟踪', () => {
    state.startStream(1)

    // 阶段 1
    state.handleStreamEvent(1, { type: 'progress', data: { phase: 'clarification', phase_index: 1, total_phases: 5, pct: 10 } })
    expect(state.getStreamState(1).currentPhase).toBe('clarification')

    // 阶段 2
    state.handleStreamEvent(1, { type: 'progress', data: { phase: 'rag', phase_index: 2, total_phases: 5, pct: 25 } })
    expect(state.getStreamState(1).currentPhase).toBe('rag')

    // 阶段 3
    state.handleStreamEvent(1, { type: 'progress', data: { phase: 'specialists', phase_index: 4, total_phases: 5, pct: 50 } })
    expect(state.getStreamState(1).currentPhase).toBe('specialists')
  })
})
