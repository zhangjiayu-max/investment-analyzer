import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '../components/finance/StatusBadge.vue'

function mountBadge(props) {
  return mount(StatusBadge, {
    props,
    global: {
      stubs: {
        Icon: {
          props: ['name'],
          template: '<i class="icon-stub" :data-name="name"></i>',
        },
      },
    },
  })
}

describe('StatusBadge', () => {
  it('keeps existing valuation and market movement status labels', () => {
    expect(mountBadge({ status: 'undervalued' }).text()).toContain('低估')
    expect(mountBadge({ status: 'fair' }).text()).toContain('合理')
    expect(mountBadge({ status: 'overvalued' }).text()).toContain('偏高')
    expect(mountBadge({ status: 'risk' }).text()).toContain('风险')
    expect(mountBadge({ status: 'profit' }).text()).toContain('涨')
    expect(mountBadge({ status: 'loss' }).text()).toContain('跌')
  })

  it('renders generic task and informational status labels', () => {
    expect(mountBadge({ status: 'running' }).text()).toContain('进行中')
    expect(mountBadge({ status: 'done' }).text()).toContain('已完成')
    expect(mountBadge({ status: 'error' }).text()).toContain('失败')
    expect(mountBadge({ status: 'queued' }).text()).toContain('排队中')
    expect(mountBadge({ status: 'empty' }).text()).toContain('暂无')
    expect(mountBadge({ status: 'warning' }).text()).toContain('提醒')
    expect(mountBadge({ status: 'success' }).text()).toContain('正常')
    expect(mountBadge({ status: 'neutral' }).text()).toContain('未知')
  })

  it('allows custom text without changing status style', () => {
    const wrapper = mountBadge({ status: 'running', text: '后台分析中' })

    expect(wrapper.text()).toContain('后台分析中')
    expect(wrapper.find('.status-badge').classes()).toContain('sb-sm')
  })

  it('falls back to neutral for unknown statuses', () => {
    const wrapper = mountBadge({ status: 'unknown-status' })

    expect(wrapper.text()).toContain('未知')
  })
})
