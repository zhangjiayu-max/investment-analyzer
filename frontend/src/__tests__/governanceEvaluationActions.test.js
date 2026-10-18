import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'
import http from '../api/http'
import '../api/interceptors'
import QualityDashboard from '../components/quality/QualityDashboard.vue'
import BadCasePage from '../components/quality/BadCasePage.vue'

vi.mock('../composables/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../composables/useAsyncTask', () => ({ useAsyncTask: () => ({
  taskState:{ value:'idle' }, start:vi.fn(), restoreFromServer:async()=>false,
}) }))
const wrappers=[]
let calls=[]
beforeEach(()=>{
  calls=[]
  http.defaults.adapter=async config=>{
    calls.push(config.url)
    let data={cases:[],scores:[],trend:[],agents:[],items:[],evaluations:[],suggestions:[],alerts:[],by_cause:[],today:{},by_severity:{}}
    if (config.url.includes('/suggestions/')) data=config.url.endsWith('/accept') ? {case_id:99} : null
    if (config.url.endsWith('/from-bad-case')) data={id:99,name:'真实转化结果',created:true}
    // ResponseWrapper 已删除 ok，axios实际拦截器会解包此协议。
    return {data:{code:0,message:'ok',data},status:200,statusText:'OK',headers:{},config}
  }
})
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());delete http.defaults.adapter})
function render(component){const w=shallowMount(component);wrappers.push(w);return w}
it('accept and reject remove processed suggestions after actual response unwrapping',async()=>{
  const w=render(QualityDashboard);await flushPromises()
  w.vm.$.setupState.evalSuggestions=[{id:1,name:'接受项'},{id:2,name:'拒绝项'}]
  await w.vm.$.setupState.handleAcceptSuggestion(1)
  await w.vm.$.setupState.handleRejectSuggestion(2)
  expect(w.vm.$.setupState.evalSuggestions).toEqual([])
  expect(calls).toContain('/eval/suggestions/1/accept')
  expect(calls).toContain('/eval/suggestions/2/reject')
})
it('bad case conversion recognizes an unwrapped successful id without ok',async()=>{
  const { useToast }=await import('../composables/useToast')
  const showToast=vi.fn()
  vi.spyOn((await import('../composables/useToast')),'useToast').mockReturnValue({showToast})
  const w=render(BadCasePage);await flushPromises()
  await w.vm.$.setupState.doConvertToEval({source:'chat',id:3})
  expect(calls).toContain('/eval/cases/from-bad-case')
  expect(showToast).toHaveBeenCalledWith('已创建评测用例「真实转化结果」','success')
})
