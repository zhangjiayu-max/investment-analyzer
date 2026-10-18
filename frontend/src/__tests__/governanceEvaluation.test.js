import { beforeEach, afterEach, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
vi.mock('../api', () => Object.fromEntries([
  'listEvalCases','createEvalCase','updateEvalCase','deleteEvalCase','runEvalCase','runEvalBatch',
  'listEvalRuns','getEvalRunDetail','getEvalStats','listAgents','batchConvertBadCases','extractEvalFromConversations','runAutoRegression',
  'listPromptVersions','savePromptVersion','activatePromptVersion','comparePrompt','listDailyReports','triggerDailyEval','autoGenerateEvalCases',
  'getAsyncTaskStatus','listAsyncTasks',
].map(key => [key, vi.fn()])))
vi.mock('../composables/useTracking', () => ({ trackFeatureClick:vi.fn(),trackFeatureComplete:vi.fn() }))
import * as api from '../api'
import { trackFeatureComplete } from '../composables/useTracking'
import EvalSuite from '../components/quality/EvalSuitePage.vue'
const wrappers=[]
beforeEach(() => {
  vi.clearAllMocks(); vi.useFakeTimers()
  vi.stubGlobal('matchMedia', () => ({ matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn() }))
  vi.stubGlobal('localStorage', { getItem:()=>null,setItem:vi.fn(),removeItem:vi.fn() })
  api.listAsyncTasks.mockResolvedValue({data:{tasks:[]}})
  api.listEvalCases.mockResolvedValue({data:{cases:[{id:1,name:'隔离评测',analysis_type:'ai',input_params:'{}'}]}})
  api.listEvalRuns.mockResolvedValue({data:{runs:[]}})
  api.getEvalStats.mockResolvedValue({data:{}})
  api.listAgents.mockResolvedValue({data:{agents:[]}})
  api.listPromptVersions.mockResolvedValue({data:{versions:[]}})
  api.listDailyReports.mockResolvedValue({data:{reports:[]}})
})
afterEach(() => { wrappers.splice(0).forEach(w=>w.unmount());vi.useRealTimers() })
function render(){const w=mount(EvalSuite,{global:{stubs:{teleport:true}}});wrappers.push(w);return w}
it('single eval submits once and completion tracking waits for server terminal state',async()=>{
  api.runEvalCase.mockResolvedValue({data:{task_id:9901,status:'running'}})
  api.getAsyncTaskStatus.mockResolvedValue({data:{status:'done',result:{ok:true,case_id:1,run_id:2}}})
  const w=render();await flushPromises()
  await w.vm.$.setupState.doRun({id:1,name:'隔离评测'});await flushPromises()
  expect(api.runEvalCase).toHaveBeenCalledWith(1)
  expect(trackFeatureComplete).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(3100);await flushPromises()
  expect(trackFeatureComplete).toHaveBeenCalledWith('eval_run','eval-suite')
})
it('all cases submit one durable batch instead of racing a single-task composable',async()=>{
  api.runEvalBatch.mockResolvedValue({data:{task_id:9902,status:'running'}})
  const w=render();await flushPromises()
  await w.vm.$.setupState.runAll();await flushPromises()
  expect(api.runEvalBatch).toHaveBeenCalledWith([1])
  expect(api.runEvalCase).not.toHaveBeenCalled()
  expect(trackFeatureComplete).not.toHaveBeenCalled()
})
it('daily evaluation remains pending until its report arrives',async()=>{
  api.triggerDailyEval.mockResolvedValue({data:{task_id:9903,status:'running'}})
  api.getAsyncTaskStatus.mockResolvedValue({data:{status:'done',result:{report:{total_cases:2,avg_score:20}}}})
  const w=render();await flushPromises()
  await w.vm.$.setupState.doTriggerDailyEval();await flushPromises()
  expect(w.vm.$.setupState.dailyRunning).toBe(true)
  expect(api.listDailyReports).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(3100);await flushPromises()
  expect(api.listDailyReports).toHaveBeenCalled()
  expect(w.vm.$.setupState.dailyRunning).toBe(false)
})
