import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DecisionReviewList from '../components/dashboard/DecisionReviewList.vue'

function mountList(props = {}) {
  return mount(DecisionReviewList, {
    props: {
      reviews: [],
      loading: false,
      ...props,
    },
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

describe('DecisionReviewList', () => {
  it('renders nothing when there are no due reviews', () => {
    const wrapper = mountList()

    expect(wrapper.find('.review-panel').exists()).toBe(false)
  })

  it('renders due review cards with original decision context', () => {
    const wrapper = mountList({
      reviews: [
        {
          id: 21,
          decision_type: 'rebalance',
          target_name: '整体组合',
          summary: '前3持仓占比 70.2%，建议做一次组合集中度复盘',
          rationale: '先识别是否是主动集中配置',
          review_at: '2026-06-20',
          evidence_json: {
            data_points: [{ name: '前3持仓占比', value: '70.2%' }],
          },
        },
      ],
    })

    expect(wrapper.text()).toContain('待复盘')
    expect(wrapper.text()).toContain('整体组合')
    expect(wrapper.text()).toContain('前3持仓占比 70.2%')
    expect(wrapper.text()).toContain('2026-06-20')
  })

  it('emits submit-review when a user records an outcome', async () => {
    const wrapper = mountList({
      reviews: [
        {
          id: 22,
          decision_type: 'add',
          target_name: '零钱配置',
          summary: '现金占比 30%，可制定分批配置计划',
          evidence_json: {},
        },
      ],
    })

    await wrapper.find('[data-test="review-helpful"]').trigger('click')
    await wrapper.find('[data-test="review-neutral"]').trigger('click')
    await wrapper.find('[data-test="review-unhelpful"]').trigger('click')

    expect(wrapper.emitted('submit-review')).toEqual([
      [22, 'helpful'],
      [22, 'neutral'],
      [22, 'unhelpful'],
    ])
  })
})
