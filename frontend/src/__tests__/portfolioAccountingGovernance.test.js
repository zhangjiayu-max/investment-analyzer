import { shallowMount, flushPromises } from '@vue/test-utils'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
vi.hoisted(() => { vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() }) })
vi.mock('../api', async original => {
  const actual = await original()
  return { ...Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === 'function' ? vi.fn(async () => ({ data: {} })) : value])), previewTransactionNav: vi.fn() }
})
import * as api from '../api'
import Portfolio from '../components/portfolio/PortfolioManagement.vue'
const wrappers = []
beforeEach(() => {
  vi.clearAllMocks()
  api.getPortfolioSummary.mockResolvedValue({ data: { holdings: [], total_value: 100, cash_balance: 50 } })
  api.listPendingTransactions.mockResolvedValue({ data: { transactions: [] } })
  vi.spyOn(api.dailyAdviceAPI, 'getSignals').mockResolvedValue({ data: { signals: [] } })
  vi.spyOn(api.dailyAdviceAPI, 'getToday').mockResolvedValue({ data: {} })
  vi.stubGlobal('alert', vi.fn())
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.restoreAllMocks() })
async function page() { const w = shallowMount(Portfolio); wrappers.push(w); await flushPromises(); return w.vm.$.setupState }
it('retrieves a NAV preview without confirming or changing the transaction', async () => {
  const s = await page(); s.confirmTxData = { id: 9, transaction_type: 'buy' }
  api.previewTransactionNav.mockResolvedValue({ data: { confirmed_price: 1.25, preview_only: true } })
  await s.fetchAutoNav()
  expect(api.previewTransactionNav).toHaveBeenCalledWith(9)
  expect(api.autoConfirmTransaction).not.toHaveBeenCalled()
  expect(api.confirmTransaction).not.toHaveBeenCalled()
  expect(s.confirmTxPrice).toBe(1.25)
})
it('requires an explicit funding source and sends the selected household cash account', async () => {
  const s = await page(); s.newBuyForm = { fund_code: '000001', fund_name: '测试', amount: 100, account: '小鱼儿', funding_source: '' }
  await s.submitNewBuy()
  expect(api.createPortfolioTransaction).not.toHaveBeenCalled()
  s.newBuyForm.funding_source = 'internal'
  await s.submitNewBuy()
  expect(api.createPortfolioTransaction).toHaveBeenCalledWith(expect.objectContaining({ funding_source: 'internal', cash_account: '小鱼儿' }))
})
it('reloads canonical holdings after CSV import without requesting prices', async () => {
  const s = await page(); api.getPortfolioSummary.mockClear(); api.refreshPortfolioPrice.mockClear()
  api.importPortfolioCsv.mockResolvedValue({ data: { ok: true, imported: 1, skipped: 0 } })
  await s.handleImportCsv({ target: { files: [{}], value: 'file' } }); await flushPromises()
  expect(api.getPortfolioSummary).toHaveBeenCalled()
  expect(api.refreshPortfolioPrice).not.toHaveBeenCalled()
})
it('keeps the last pending list and exposes its loading error', async () => {
  const s = await page(); s.pendingTxs = [{ id: 8 }]
  api.listPendingTransactions.mockRejectedValue(new Error('offline'))
  await s.loadPendingTxs()
  expect(s.pendingTxs).toEqual([{ id: 8 }])
  expect(s.pendingTxError).toContain('offline')
})
it('separates holding unrealized gain and cumulative investment gain in the summary', async () => {
  const s = await page(); s.summary = { holdings: [], total_profit: null, profit_rate: null, total_value: 100, cash_balance: 50, valuation_complete: false,
    performance: { cumulative_investment_profit: 9, realized_profit: 5, cash_dividends: 4, fees: 1, xirr: { value: null, reason: '历史不足' } } }
  await flushPromises()
  expect(wrappers.at(-1).text()).toContain('持仓浮盈亏')
  expect(wrappers.at(-1).text()).toContain('累计投资收益')
  expect(wrappers.at(-1).text()).toContain('净值覆盖不足')
})
