import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatMessage from '../components/chat/ChatMessage.vue'

function mountMessage(status, extra = {}) {
  return mount(ChatMessage, {
    props: {
      msg: {
        id: 12,
        role: 'assistant',
        content: '分析内容',
        execution_status: status,
        created_at: '2026-06-23 10:00:00',
        ...extra,
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

  it('shows generated pending review for degraded reports and allows retry without resume', async () => {
    const wrapper = mountMessage('degraded', {
      completion_status: 'degraded', quality_status: 'degraded',
      delivery_reasons: ['专家输出截断', '交叉审查因预算不足未完成'],
    })
    expect(wrapper.text()).toContain('分析已生成，部分核验未完成')
    expect(wrapper.text()).toContain('专家输出截断')
    expect(wrapper.text()).toContain('交叉审查因预算不足未完成')
    expect(wrapper.text()).not.toContain('执行失败')
    expect(wrapper.text()).not.toContain('恢复连接')
    expect(wrapper.text()).not.toContain('继续分析')
    expect(wrapper.find('.status-degraded').exists()).toBe(true)
    await wrapper.find('button[title="重新生成"]').trigger('click')
    expect(wrapper.emitted('regenerate')).toHaveLength(1)
  })

  it('normalizes legacy failed plus degraded metadata without changing report content', () => {
    const wrapper = mountMessage('failed', { metadata: {
      execution_status: 'failed', completion_status: 'degraded', quality_status: 'degraded',
      quality_issues: ['cross_review_budget_exhausted'],
    } })
    expect(wrapper.text()).toContain('分析已生成，部分核验未完成')
    expect(wrapper.text()).not.toContain('执行失败')
    expect(wrapper.text()).toContain('分析内容')
  })

  it('labels expert-only recovery without claiming synthesis completed', () => {
    const wrapper = mountMessage('failed', { metadata: {
      execution_status: 'failed', completion_status: 'degraded', recovery_type: 'experts_only',
    } })
    expect(wrapper.text()).toContain('仅专家结果')
    expect(wrapper.text()).toContain('综合报告未生成')
    expect(wrapper.text()).not.toContain('分析已生成，部分核验未完成')
  })

  it('retains export actions and labels a truncated expert in a degraded report', () => {
    const wrapper = mountMessage('degraded', {
      completion_status: 'degraded', specialist_results: [{ agent_key: 'asset', agent: '配置师',
        analysis: '尚未完成的专家原稿', status: 'degraded', completion_status: 'truncated' }],
    })
    expect(wrapper.text()).toContain('输出截断')
    expect(wrapper.text()).toContain('导出 MD')
    expect(wrapper.text()).not.toContain('执行失败')
  })

  it.each(['专家预算已用尽，未执行追加分析。', '专家执行超时，本轮未取得完整分析。'])(
    'does not display internal failure placeholders as generated analysis: %s', (content) => {
      const wrapper = mountMessage('failed', { content, completion_status: 'degraded' })
      expect(wrapper.text()).toContain('执行失败')
      expect(wrapper.text()).not.toContain('分析已生成，部分核验未完成')
    },
  )
})
