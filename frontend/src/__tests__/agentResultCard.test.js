import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentResultCard from '../components/ui/AgentResultCard.vue'

function mountCard(props = {}) {
  return mount(AgentResultCard, {
    props: {
      title: 'AI 全景诊断',
      agent: '全景诊断分析师',
      status: 'done',
      content: '## 结论\n保持均衡配置。',
      ...props,
    },
    global: {
      stubs: {
        Icon: {
          props: ['name'],
          template: '<i class="icon-stub" :data-name="name"></i>',
        },
        QualityFeedback: {
          props: ['targetType', 'targetId', 'simple', 'caller'],
          emits: ['submitted'],
          template: '<button class="quality-feedback-stub" @click="$emit(\'submitted\', { rating: \'helpful\' })">反馈</button>',
        },
      },
    },
  })
}

describe('AgentResultCard', () => {
  beforeEach(() => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders markdown content and metadata for done state', () => {
    const wrapper = mountCard({ tokenUsage: 1234, recordId: 7, updatedAt: '2026-06-19 10:00', source: '持仓快照' })

    expect(wrapper.text()).toContain('AI 全景诊断')
    expect(wrapper.text()).toContain('全景诊断分析师')
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('1234 tokens')
    expect(wrapper.text()).toContain('#7')
    expect(wrapper.text()).toContain('2026-06-19 10:00')
    expect(wrapper.text()).toContain('持仓快照')
    expect(wrapper.find('.markdown-body').html()).toContain('<h2')
  })

  it('renders running state', () => {
    const wrapper = mountCard({ status: 'running', content: '' })

    expect(wrapper.text()).toContain('后台分析中')
    expect(wrapper.text()).toContain('任务已提交，可以切换页面')
  })

  it('renders empty state', () => {
    const wrapper = mountCard({ status: 'empty', content: '' })

    expect(wrapper.text()).toContain('暂无分析结果')
  })

  it('renders error state and emits retry', async () => {
    const wrapper = mountCard({ status: 'error', error: '模型超时' })

    expect(wrapper.text()).toContain('模型超时')
    await wrapper.find('.agent-result-card__retry').trigger('click')

    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('copies content to clipboard and emits copied event', async () => {
    const wrapper = mountCard({ content: '可复制的分析内容' })

    await wrapper.find('.agent-result-card__copy').trigger('click')

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('可复制的分析内容')
    expect(wrapper.emitted('copied')).toHaveLength(1)
  })

  it('toggles collapsed state when collapsible', async () => {
    const wrapper = mountCard({ collapsible: true, defaultExpanded: true })

    expect(wrapper.find('.agent-result-card__body').exists()).toBe(true)
    await wrapper.find('.agent-result-card__toggle').trigger('click')

    expect(wrapper.find('.agent-result-card__body').exists()).toBe(false)
    expect(wrapper.emitted('toggle-expanded')[0][0]).toEqual(false)
  })

  it('forwards quality feedback submission', async () => {
    const wrapper = mountCard({ targetType: 'portfolio_analysis', targetId: 7 })

    await wrapper.find('.quality-feedback-stub').trigger('click')

    expect(wrapper.emitted('feedback-submitted')[0][0]).toEqual({ rating: 'helpful' })
  })
})
