import { shallowMount, flushPromises } from '@vue/test-utils'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
vi.hoisted(() => { vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn() }) })
vi.mock('../api', async original => {
  const actual = await original()
  return { ...actual, listAlerts: vi.fn(), getUnreadAlertCount: vi.fn(), markAlertRead: vi.fn(), scanPortfolioAlerts: vi.fn(), acknowledgeAlert: vi.fn() }
})
import * as api from '../api'
import AlertCenter from '../components/task/AlertCenter.vue'
const wrappers=[]
const fact={latest_id:7,id:7,severity:'danger',alert_type:'loss_warning',related_fund_code:'001',title:'仍亏损',risk_status:'active',is_read:0}
beforeEach(() => {
  vi.clearAllMocks()
  api.listAlerts.mockResolvedValue({data:{alerts:[{...fact}],total:1,active_count:1,last_scan:null}})
  api.getUnreadAlertCount.mockResolvedValue({data:{count:1}})
  api.markAlertRead.mockResolvedValue({data:{ok:true}})
  api.acknowledgeAlert.mockResolvedValue({data:{ok:true}})
  vi.spyOn(api.dailyAdviceAPI,'getToday').mockResolvedValue({data:{signals:[]}})
})
afterEach(() => { wrappers.splice(0).forEach(w=>w.unmount()); vi.restoreAllMocks() })
async function page(){const w=shallowMount(AlertCenter);wrappers.push(w);await flushPromises();return w.vm.$.setupState}
it('loads every active risk including read notifications by default',async()=>{
  const state=await page()
  expect(state.activeTab).toBe('alerts')
  expect(api.listAlerts).toHaveBeenCalledWith(false,50,expect.objectContaining({active_only:true,offset:0}))
})
it('reading a risk preserves the active card',async()=>{
  const state=await page();await state.handleMarkAlertRead(7,'仍亏损','danger')
  expect(state.alerts).toHaveLength(1)
  expect(state.alerts[0].is_read).toBe(1)
})
it('keeps danger first even though its sorting index is zero',async()=>{
  const state=await page();state.alerts=[{severity:'info'},{severity:'danger'},{severity:'warning'}]
  expect(state.sortedAlerts.map(a=>a.severity)).toEqual(['danger','warning','info'])
})
it('always reloads after an incomplete scan and exposes failures',async()=>{
  const state=await page();api.listAlerts.mockClear()
  api.scanPortfolioAlerts.mockResolvedValue({data:{generated:0,updated:2,resolved:0,failed:1,skipped:1,status:'incomplete',coverage:{portfolio:{status:'failed'}}}})
  await state.handleScanAlerts()
  expect(api.listAlerts).toHaveBeenCalled()
  expect(state.alertScanResult.failed).toBe(1)
  expect(wrappers.at(-1).text()).toContain('检查未完成')
})
it('retains last successful facts and shows a read error',async()=>{
  const state=await page();api.listAlerts.mockRejectedValue(new Error('offline'))
  await state.loadAlerts()
  expect(state.alerts).toHaveLength(1)
  expect(state.alertsError).toContain('offline')
})
it('requires confirmation and review details to ignore an active risk',async()=>{
  const state=await page();state.handleAcknowledgeAlert(state.alerts[0],'ignored')
  expect(api.acknowledgeAlert).not.toHaveBeenCalled()
  expect(state.actionDialog.visible).toBe(true)
  state.actionDialog.reason='核对账单';state.actionDialog.review_date='2026-10-25'
  state.confirmRiskAction()
  expect(state.confirmState.visible).toBe(true)
  await state.confirmState.onConfirm()
  expect(api.acknowledgeAlert).toHaveBeenCalledWith(7,'ignored',{reason:'核对账单',review_date:'2026-10-25'})
  expect(state.alerts[0].risk_status).toBe('active')
})
it('shows review dates that have become due without hiding the risk',async()=>{
  const state=await page()
  state.alerts=[{...fact,acknowledged_status:'ignored',review_date:'2000-01-01',action_reason:'等待核对'}]
  await flushPromises()
  expect(wrappers.at(-1).text()).toContain('复查已到期')
  expect(state.sortedAlerts).toHaveLength(1)
})
