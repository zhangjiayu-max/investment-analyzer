import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'
import CapabilityCenter from '../components/agent/CapabilityCenter.vue'
import ShadowModePage from '../components/agent/ShadowModePage.vue'
import DataHealthDashboard from '../components/quality/DataHealthDashboard.vue'
import * as api from '../api'
vi.mock('../api', () => Object.fromEntries([
  'getCapabilitiesOverview', 'getCapabilitiesTools', 'getUnexposedMcp', 'getIntegrationGuide',
  'getCapabilitiesStats', 'getCapabilitiesRecent', 'debugTool', 'getShadowConfigs', 'createShadowConfig',
  'toggleShadowConfig', 'deleteShadowConfig', 'getShadowRuns', 'getShadowStats', 'getDataHealth',
].map(name => [name, vi.fn(async () => ({data:{}}))])))
const wrappers=[]
function mount(component) { const w=shallowMount(component); wrappers.push(w); return w }
beforeEach(() => { vi.clearAllMocks(); api.getShadowConfigs.mockResolvedValue({data:{configs:[],enabled:false}}) })
afterEach(() => wrappers.splice(0).forEach(w=>w.unmount()))
it('loads recent tool items as array and filters disabled debug tools', async () => {
  api.getCapabilitiesTools.mockResolvedValue({data:{tools:[{name:'disabled',exposed:true,enabled:false},{name:'read',exposed:true,enabled:true}]}})
  api.getCapabilitiesRecent.mockResolvedValue({data:{items:[{id:4,tool_name:'read',success:true}]}})
  const w=mount(CapabilityCenter); await flushPromises()
  await w.vm.expandStatTool('read')
  expect(w.vm.toolRecentMap.read.records).toEqual([{id:4,tool_name:'read',success:true}])
  expect(w.vm.exposedTools.map(t=>t.name)).toEqual(['read'])
})
it('requires confirmation before write debug and passes explicit confirmation', async () => {
  api.getCapabilitiesTools.mockResolvedValue({data:{tools:[{name:'write',exposed:true,enabled:true,effect:'write',parameters:{}}]}})
  const w=mount(CapabilityCenter); await flushPromises(); w.vm.selectDebugTool('write')
  await w.vm.runDebug()
  expect(api.debugTool).not.toHaveBeenCalled()
  expect(w.vm.debugConfirm).toBe(true)
  await w.vm.runDebug(true)
  expect(api.debugTool).toHaveBeenCalledWith('write',{},true)
})
it('keeps shadow config on server failure and confirms deletion', async () => {
  const config={id:9,name:'candidate',is_active:1,agent_type:'chat'}
  api.getShadowConfigs.mockResolvedValue({data:{configs:[config],enabled:false}})
  api.toggleShadowConfig.mockRejectedValue(new Error('server failed'))
  const w=mount(ShadowModePage); await flushPromises()
  w.vm.toggleConfig(config)
  expect(api.toggleShadowConfig).not.toHaveBeenCalled()
  await w.vm.applyAction()
  expect(config.is_active).toBe(1)
  expect(w.vm.pending).not.toBeNull()
  w.vm.deleteConfig(9)
  expect(api.deleteShadowConfig).not.toHaveBeenCalled()
})
it('shows unavailable scores as missing and global disabled state', async () => {
  api.getShadowStats.mockResolvedValue({data:{total_runs:1,valid_runs:0,avg_prod_score:null,avg_shadow_score:null,shadow_win_rate:null}})
  const w=mount(ShadowModePage); await flushPromises()
  expect(w.text()).toContain('全局 Shadow 已关闭')
  expect(w.vm.formatScore(null)).toBe('—')
  expect(w.text()).toContain('有效 0')
})
it('keeps reference content available while showing missing target coverage', async () => {
  api.getDataHealth.mockResolvedValue({data:{health:{knowledge:{label:'知识库',count:3,stale_days:100,status:'available'},holdings:{label:'持仓',count:2,status:'partial',coverage:{fresh:1,expected:2,items:[{key:'missing',label:'缺失基金',status:'missing'}]}}}}})
  const w=mount(DataHealthDashboard); await flushPromises()
  expect(w.vm.greenCount).toBe(1)
  expect(w.vm.yellowCount).toBe(1)
  expect(w.text()).toContain('缺失基金')
  expect(w.text()).toContain('缺少数据')
})
