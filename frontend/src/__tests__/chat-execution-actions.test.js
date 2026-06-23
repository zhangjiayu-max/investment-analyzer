import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatMessage from '../components/chat/ChatMessage.vue'

function mountMessage(status) {
  return mount(ChatMessage, {
    props: {
      msg: {
        id: 12,
        role: 'assistant',
        content: '分析内容',
        execution_status: status,
        created_at: '2026-06-23 10:00:00',
      },
      index: 1,
      convId: 3,
      feedbackGiven: {},
      specialistFeedback: {},
      messageEvalStates: {},
      traceDetailVisible: {},
      traceDetailData: {},
    },
    global: {
      stubs: {
        Icon: { props: ['name'], template: '<i :data-name="name"></i>' },
        TraceDetail: true,
        ReasoningPanel: true,
      },
    },
  })
}

describe('ChatMessage execution actions', () => {
  it('shows resume connection for streaming messages', async () => {
    const wrapper = mountMessage('streaming')

    expect(wrapper.text()).toContain('恢复连接')
    await wrapper.find('button[title="恢复连接"]').trigger('click')
    expect(wrapper.emitted('resume')).toEqual([[3]])
  })

  it('shows continue and regenerate for cancelled messages', async () => {
    const wrapper = mountMessage('cancelled')

    expect(wrapper.text()).toContain('继续分析')
    expect(wrapper.text()).toContain('重新生成')
    await wrapper.find('button[title="继续分析"]').trigger('click')
    await wrapper.find('button[title="重新生成"]').trigger('click')
    expect(wrapper.emitted('continue-analysis')[0][0].id).toBe(12)
    expect(wrapper.emitted('regenerate')[0][0].id).toBe(12)
  })

  it('shows regenerate for failed messages', async () => {
    const wrapper = mountMessage('failed')

    expect(wrapper.text()).toContain('重新生成')
    await wrapper.find('button[title="重新生成"]').trigger('click')
    expect(wrapper.emitted('regenerate')[0][0].execution_status).toBe('failed')
  })
})
