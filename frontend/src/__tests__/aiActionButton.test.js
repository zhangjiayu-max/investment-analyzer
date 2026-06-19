import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AIActionButton from '../components/ui/AIActionButton.vue'

function mountButton(props = {}) {
  return mount(AIActionButton, {
    props: {
      label: '开始分析',
      agent: '测试分析师',
      ...props,
    },
    global: {
      stubs: {
        Icon: {
          props: ['name', 'size'],
          template: '<i class="icon-stub" :data-name="name" :data-size="size"></i>',
        },
      },
    },
  })
}

describe('AIActionButton', () => {
  it('renders the original label and icon in idle state and only disables when disabled prop is true', () => {
    const wrapper = mountButton({ icon: 'sparkles' })

    expect(wrapper.find('.ai-action-button__label').text()).toBe('开始分析')
    expect(wrapper.find('.icon-stub').attributes('data-name')).toBe('sparkles')
    expect(wrapper.find('button').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('button').classes()).not.toContain('is-loading')

    const disabledWrapper = mountButton({ icon: 'sparkles', disabled: true })
    expect(disabledWrapper.find('.ai-action-button__label').text()).toBe('开始分析')
    expect(disabledWrapper.find('.icon-stub').attributes('data-name')).toBe('sparkles')
    expect(disabledWrapper.find('button').attributes('disabled')).toBeDefined()
  })

  it('emits the native click event so callers can use modifiers', async () => {
    const wrapper = mountButton()

    await wrapper.find('button').trigger('click')

    const emitted = wrapper.emitted('click')
    expect(emitted).toHaveLength(1)
    expect(emitted[0][0]).toBeInstanceOf(MouseEvent)
  })

  it('shows the responsible agent in a hover tooltip', () => {
    const wrapper = mountButton({ agent: '全景诊断分析师' })

    expect(wrapper.find('.ai-action-button__tooltip').text()).toBe('全景诊断分析师')
  })

  it('maps submitting state to submitting text, hourglass icon, loading animation, and disabled button', () => {
    const wrapper = mountButton({ state: 'submitting' })

    expect(wrapper.find('.ai-action-button__label').text()).toBe('提交中...')
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.icon-stub').attributes('data-name')).toBe('hourglass')
    expect(wrapper.find('button').classes()).toContain('is-loading')
  })

  it('maps queued state to background queued text with check icon', () => {
    const wrapper = mountButton({ state: 'queued' })

    expect(wrapper.find('.ai-action-button__label').text()).toBe('已提交后台分析')
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.icon-stub').attributes('data-name')).toBe('check')
  })

  it('maps running state to analysis text, hourglass icon, loading animation, and remains disabled', () => {
    const wrapper = mountButton({ state: 'running' })

    expect(wrapper.find('.ai-action-button__label').text()).toBe('分析中...')
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.icon-stub').attributes('data-name')).toBe('hourglass')
    expect(wrapper.find('button').classes()).toContain('is-loading')
  })

  it('maps error state to retry text and refresh icon without disabling the button', async () => {
    const wrapper = mountButton({ state: 'error' })

    expect(wrapper.find('.ai-action-button__label').text()).toBe('重试分析')
    expect(wrapper.find('.icon-stub').attributes('data-name')).toBe('refresh')
    expect(wrapper.find('button').attributes('disabled')).toBeUndefined()

    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('keeps loading prop backward compatible with running state and loading animation', () => {
    const wrapper = mountButton({ loading: true })

    expect(wrapper.find('.ai-action-button__label').text()).toBe('分析中...')
    expect(wrapper.find('.icon-stub').attributes('data-name')).toBe('hourglass')
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    expect(wrapper.find('button').classes()).toContain('is-loading')
  })
})
