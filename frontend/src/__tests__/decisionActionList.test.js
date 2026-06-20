import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DecisionActionList from '../components/dashboard/DecisionActionList.vue'

function mountList(props = {}) {
  return mount(DecisionActionList, {
    props: {
      decisions: [],
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

describe('DecisionActionList', () => {
  it('renders empty state when there are no decisions', () => {
    const wrapper = mountList()

    expect(wrapper.find('.decision-empty').exists()).toBe(true)
    expect(wrapper.text()).toContain('今日暂无需要处理的行动')
  })

  it('renders decision evidence and action rows', () => {
    const wrapper = mountList({
      decisions: [
        {
          id: 8,
          status: 'proposed',
          decision_type: 'watch',
          target_name: '沪深300',
          summary: '低估进入观察',
          rationale: 'PE 百分位处于低位',
          evidence_json: {
            data_points: [{ name: 'PE百分位', value: '18%', as_of: '2026-06-20' }],
          },
          actions: [{ id: 2, title: '设置低估提醒', status: 'todo' }],
        },
      ],
    })

    expect(wrapper.text()).toContain('沪深300')
    expect(wrapper.text()).toContain('低估进入观察')
    expect(wrapper.text()).toContain('PE百分位 18%')
    expect(wrapper.text()).toContain('设置低估提醒')
  })

  it('renders suitability context and counter arguments when provided', () => {
    const wrapper = mountList({
      decisions: [
        {
          id: 11,
          status: 'proposed',
          decision_type: 'add',
          target_name: '零钱配置',
          summary: '现金占比 26%，可制定低估指数分批配置计划',
          rationale: '现金占比偏高，且沪深300处于低估区间',
          evidence_json: {
            data_points: [{ name: '现金占比', value: '26%' }],
            portfolio_context: {
              opportunity_names: '沪深300、中证500',
            },
            missing_data: ['执行前需补充备用金目标'],
            counter_arguments: ['先确认 3-6 个月备用金'],
          },
          suitability_json: {
            notes: ['适合作为资金计划，不直接生成一次性买入指令'],
          },
          actions: [],
        },
      ],
    })

    expect(wrapper.text()).toContain('适配')
    expect(wrapper.text()).toContain('沪深300、中证500')
    expect(wrapper.text()).toContain('待确认')
    expect(wrapper.text()).toContain('执行前需补充备用金目标')
    expect(wrapper.text()).toContain('反方提醒')
    expect(wrapper.text()).toContain('先确认 3-6 个月备用金')
    expect(wrapper.text()).toContain('适合作为资金计划')
  })

  it('emits status-change when user chooses a decision action', async () => {
    const wrapper = mountList({
      decisions: [
        {
          id: 9,
          status: 'proposed',
          decision_type: 'rebalance',
          target_type: 'portfolio',
          summary: '检查组合再平衡',
          evidence_json: {},
          actions: [],
        },
      ],
    })

    await wrapper.find('[data-test="accept-decision"]').trigger('click')
    await wrapper.find('[data-test="defer-decision"]').trigger('click')
    await wrapper.find('[data-test="reject-decision"]').trigger('click')

    expect(wrapper.emitted('status-change')).toEqual([
      [9, 'accepted'],
      [9, 'deferred'],
      [9, 'rejected'],
    ])
  })

  it('emits complete-action when an action item is clicked', async () => {
    const wrapper = mountList({
      decisions: [
        {
          id: 10,
          status: 'accepted',
          decision_type: 'watch',
          target_name: '中证500',
          summary: '继续观察',
          evidence_json: {},
          actions: [{ id: 4, title: '明日复查估值', status: 'todo' }],
        },
      ],
    })

    await wrapper.find('[data-test="complete-action"]').trigger('click')

    expect(wrapper.emitted('complete-action')).toEqual([[10, 4]])
  })
})
