import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AIActionButton from '../components/ui/AIActionButton.vue'

describe('AIActionButton', () => {
  it('emits the native click event so callers can use modifiers', async () => {
    const wrapper = mount(AIActionButton, {
      props: {
        label: '开始分析',
        agent: '测试分析师',
      },
      global: {
        stubs: {
          Icon: true,
        },
      },
    })

    await wrapper.find('button').trigger('click')

    const emitted = wrapper.emitted('click')
    expect(emitted).toHaveLength(1)
    expect(emitted[0][0]).toBeInstanceOf(MouseEvent)
  })
})
