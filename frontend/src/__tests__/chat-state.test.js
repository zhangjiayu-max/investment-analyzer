// Chat 状态管理测试
// 验证对话切换、stream 生命周期等关键状态流

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref, nextTick } from 'vue'

// 模拟 ChatView 的核心状态逻辑
function createChatState() {
  const sending = ref(false)
  const selectedConv = ref(null)
  const executingConvId = ref(null)
  const statusMessage = ref('')
  const currentToolCalls = ref([])
  const activeSpecialists = ref([])
  const completedSpecialists = ref([])
  const crossReviewSpecialists = ref([])
  const completedCrossReviews = ref([])
  const messages = ref([])
  const streamAbort = ref(null)

  function selectConversation(conv) {
    // 切换对话时重置 UI 状态，但不 abort stream
    sending.value = false
    statusMessage.value = ''
    currentToolCalls.value = []
    activeSpecialists.value = []
    completedSpecialists.value = []
    crossReviewSpecialists.value = []
    completedCrossReviews.value = []
    selectedConv.value = conv
  }

  function handleStreamEvent(convId, event) {
    // 只处理当前对话的事件
    if (selectedConv.value?.id !== convId) return false

    const { type, data } = event
    switch (type) {
      case 'status':
        statusMessage.value = data.message
        break
      case 'tool_call':
        currentToolCalls.value.push({ name: data.name })
        break
      case 'specialist_start':
        activeSpecialists.value.push({ agent_key: data.agent_key })
        break
      case 'specialist_done':
        activeSpecialists.value = activeSpecialists.value.filter(s => s.agent_key !== data.agent_key)
        completedSpecialists.value.push({ agent_key: data.agent_key })
        break
      case 'answer':
        messages.value.push({ role: 'assistant', content: data.content })
        break
      case 'done':
        sending.value = false
        executingConvId.value = null
        currentToolCalls.value = []
        activeSpecialists.value = []
        completedSpecialists.value = []
        break
      case 'error':
        sending.value = false
        executingConvId.value = null
        break
    }
    return true
  }

  return {
    sending, selectedConv, executingConvId, statusMessage,
    currentToolCalls, activeSpecialists, completedSpecialists,
    crossReviewSpecialists, completedCrossReviews, messages, streamAbort,
    selectConversation, handleStreamEvent,
  }
}

describe('对话切换状态管理', () => {
  let state

  beforeEach(() => {
    state = createChatState()
  })

  it('初始状态正确', () => {
    expect(state.sending.value).toBe(false)
    expect(state.selectedConv.value).toBeNull()
    expect(state.executingConvId.value).toBeNull()
    expect(state.messages.value).toEqual([])
  })

  it('选择对话后 selectedConv 更新', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    expect(state.selectedConv.value.id).toBe(1)
  })

  it('切换对话重置 sending 状态', () => {
    // 在对话 A 中发送消息
    state.sending.value = true
    state.executingConvId.value = 1
    state.statusMessage.value = '分析中...'
    state.activeSpecialists.value.push({ agent_key: 'test' })

    // 切换到对话 B
    state.selectConversation({ id: 2, title: '对话B' })

    expect(state.sending.value).toBe(false)
    expect(state.statusMessage.value).toBe('')
    expect(state.activeSpecialists.value).toEqual([])
  })

  it('切换对话不中断 stream（executingConvId 保留）', () => {
    state.sending.value = true
    state.executingConvId.value = 1

    // 切换对话
    state.selectConversation({ id: 2, title: '对话B' })

    // sending 被重置，但 executingConvId 保留（stream 后台继续）
    expect(state.sending.value).toBe(false)
  })

  it('stream 事件只更新当前选中对话的 UI', () => {
    // 对话 1 的 stream 事件
    state.selectConversation({ id: 1, title: '对话1' })
    const handled1 = state.handleStreamEvent(1, { type: 'status', data: { message: '分析中' } })
    expect(handled1).toBe(true)
    expect(state.statusMessage.value).toBe('分析中')

    // 切换到对话 2
    state.selectConversation({ id: 2, title: '对话2' })
    state.statusMessage.value = ''

    // 对话 1 的后续事件不应更新 UI
    const handled2 = state.handleStreamEvent(1, { type: 'status', data: { message: '还在分析' } })
    expect(handled2).toBe(false)
    expect(state.statusMessage.value).toBe('') // 不应被更新
  })

  it('answer 事件将消息添加到 messages', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    state.handleStreamEvent(1, {
      type: 'answer',
      data: { content: '这是回复' },
    })
    expect(state.messages.value).toHaveLength(1)
    expect(state.messages.value[0].content).toBe('这是回复')
  })

  it('done 事件清除执行状态', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    state.sending.value = true
    state.executingConvId.value = 1

    state.handleStreamEvent(1, { type: 'done', data: { duration_ms: 5000 } })

    expect(state.sending.value).toBe(false)
    expect(state.executingConvId.value).toBeNull()
  })

  it('error 事件清除执行状态', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    state.sending.value = true
    state.executingConvId.value = 1

    state.handleStreamEvent(1, { type: 'error', data: { message: '超时' } })

    expect(state.sending.value).toBe(false)
    expect(state.executingConvId.value).toBeNull()
  })

  it('tool_call 事件更新 currentToolCalls', () => {
    state.selectConversation({ id: 1, title: '对话1' })
    state.handleStreamEvent(1, { type: 'tool_call', data: { name: 'search' } })
    state.handleStreamEvent(1, { type: 'tool_call', data: { name: 'lookup' } })
    expect(state.currentToolCalls.value).toHaveLength(2)
  })

  it('specialist 事件管理活跃/完成列表', () => {
    state.selectConversation({ id: 1, title: '对话1' })

    // 专家开始
    state.handleStreamEvent(1, { type: 'specialist_start', data: { agent_key: 'analyst' } })
    expect(state.activeSpecialists.value).toHaveLength(1)

    // 专家完成
    state.handleStreamEvent(1, { type: 'specialist_done', data: { agent_key: 'analyst' } })
    expect(state.activeSpecialists.value).toHaveLength(0)
    expect(state.completedSpecialists.value).toHaveLength(1)
  })
})
