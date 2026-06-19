import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AsyncStateBlock from '../components/ui/AsyncStateBlock.vue'

function mountBlock(props = {}, slots = {}) {
  return mount(AsyncStateBlock, {
    props,
    slots,
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

describe('AsyncStateBlock', () => {
  it('renders loading state', () => {
    const wrapper = mountBlock({ state: 'loading', title: '加载持仓', description: '正在获取数据' })

    expect(wrapper.find('.async-state-block--loading').exists()).toBe(true)
    expect(wrapper.text()).toContain('加载持仓')
    expect(wrapper.text()).toContain('正在获取数据')
  })

  it('renders empty state and emits action', async () => {
    const wrapper = mountBlock({ state: 'empty', title: '暂无持仓', actionText: '去添加' })

    expect(wrapper.find('.async-state-block--empty').exists()).toBe(true)
    await wrapper.find('.async-state-block__action').trigger('click')

    expect(wrapper.emitted('action')).toHaveLength(1)
  })

  it('renders error state and emits retry', async () => {
    const wrapper = mountBlock({ state: 'error', title: '加载失败', description: '网络异常', retryText: '重新加载' })

    expect(wrapper.find('.async-state-block--error').exists()).toBe(true)
    await wrapper.find('.async-state-block__retry').trigger('click')

    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('renders default slot for normal state', () => {
    const wrapper = mountBlock({ state: 'ready' }, { default: '<div class="ready-content">内容已加载</div>' })

    expect(wrapper.find('.ready-content').text()).toBe('内容已加载')
    expect(wrapper.find('.async-state-block__panel').exists()).toBe(false)
  })
})
