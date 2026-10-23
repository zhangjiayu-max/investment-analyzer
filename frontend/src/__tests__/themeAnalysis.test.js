import { shallowMount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Theme from '../components/portfolio/ThemeAnalysis.vue'
vi.mock('../api', () => ({ getThemeAnalysis: vi.fn() }))
vi.mock('../composables/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../composables/useChartTheme', () => ({
  useChartTheme: () => ({
    theme: { borderColor: '#ccc', titleColor: '#333', fontMono: 'mono', textColor: '#333' },
    isDark: { value: false },
  }),
}))
vi.mock('../composables/useLazyChart', () => ({ useLazyChart: vi.fn() }))
import { getThemeAnalysis } from '../api'

const wrappers = []
const ctx = {
  summary: {
    holding_count: 4, total_value: 100000, total_cost: 110000, total_pnl: -10000,
    total_pnl_rate: -0.09, cash_balance: 20000, equity_value: 50000, equity_pnl: -1234,
    theme_count: 2, effective_theme_count: 1.8, hhi: 0.55, hhi_level: 'high',
    hhi_thresholds: { moderate: 0.12, high: 0.18 }, top3_weight: 1,
    fragment_count: 1, fragment_threshold: 10000, fragment_total_value: 5000,
    fragment_max_impact_pct: 0.167, duplicate_fund_count: 1, duplicate_index_count: 1,
  },
  themes: [
    { theme: '大消费', value: 30000, weight: 0.6, pnl: -8000, pnl_rate: -0.2, is_defensive: false, funds: [] },
    { theme: '港股科技互联网', value: 20000, weight: 0.4, pnl: -434, pnl_rate: -0.02, is_defensive: false, funds: [] },
    { theme: '债券', value: 50000, weight: 0.5, pnl: 500, pnl_rate: 0.01, is_defensive: true, funds: [] },
  ],
  duplicates: {
    cross_account: [{
      fund_code: '161725', fund_name: '招商中证白酒指数(LOF)A', theme: '大消费',
      total_value: 54157, total_pnl: -5000, keep_hint: '建议合并至「花无缺」（现值 ¥30,000 最大），另一账户卖出后归拢',
      positions: [{ account: '花无缺', value: 30000 }, { account: '小鱼儿', value: 24157 }],
    }],
    same_index: [{
      index_code: 'HSTECH', index_name: '恒生科技指数', theme: '港股科技互联网', total_value: 25000,
      keep_hint: '建议保留「易方达恒生科技ETF联接A」（现值 ¥15,000 规模最大），合并前比较各基金管理费率',
      funds: [{ fund_name: '易方达恒生科技ETF联接A', account: '小鱼儿' }],
    }],
  },
  fragments: [{
    fund_code: '012725', fund_name: '国泰中证畜牧养殖ETF联接C', account: '小鱼儿',
    theme: '畜牧农业', value: 5000, cost: 6000, pnl: -1000, pnl_rate: -0.1667,
  }],
  advice: ['前三大主题（大消费）占权益 100%，超过 50% 预警线，新增资金应避开这些主题'],
}

beforeEach(() => { vi.clearAllMocks(); getThemeAnalysis.mockResolvedValue({ data: structuredClone(ctx) }) })
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
async function mount() {
  const w = shallowMount(Theme)
  wrappers.push(w)
  await flushPromises()
  return w
}

it('renders the theme column for fragment positions (regression: blank column)', async () => {
  const w = await mount()
  expect(w.text()).toContain('畜牧农业')
})

it('labels the pnl chart with equity-only pnl, not total pnl', async () => {
  const w = await mount()
  expect(w.text()).toContain('权益主题合计 ¥-1,234') // equity_pnl 口径（不含债券 +500）
  expect(w.text()).not.toContain('权益主题合计 ¥-10,000')
})

it('colors profit red and loss green (A-share convention)', async () => {
  const w = await mount()
  // 亏损 -1,000（碎片行）→ neg（绿）；类映射：正数 pos、负数 neg
  const neg = w.find('.frag-row .neg')
  expect(neg.exists()).toBe(true)
  expect(neg.text()).toContain('-¥1,000')
  const s = w.vm.$.setupState
  expect(s.pnlClass(100)).toBe('pos')
  expect(s.pnlClass(-1)).toBe('neg')
})

it('shows keep-hint guidance for duplicated holdings', async () => {
  const w = await mount()
  expect(w.text()).toContain('建议合并至「花无缺」')
  expect(w.text()).toContain('建议保留「易方达恒生科技ETF联接A」')
})

it('trusts backend hhi_level and thresholds instead of re-deriving with local cutoffs', async () => {
  const w = await mount()
  expect(w.text()).toContain('高度集中')
  // 后端阈值展示为一份口径
  expect(w.text()).toContain('0.55')
})

it('renders dynamic fragment impact instead of hardcoded 0.2%', async () => {
  const w = await mount()
  expect(w.text()).toContain('0.167%')
  expect(w.text()).not.toContain('不足 0.2%')
})
