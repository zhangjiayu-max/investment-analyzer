import { ref, onUnmounted, getCurrentInstance } from 'vue'
import { getAsyncTaskStatus, listAsyncTasks } from '../api'
import { useTaskStore } from './useTaskStore'

const MAX_POLL_MS = 5 * 60 * 1000

/** 后台任务轮询：单在途，版本隔离；断网/等待超时保留后台任务凭据。 */
export function useAsyncTask(taskType) {
  const { getTask, setTask, clearTask, hasRunningTask } = useTaskStore()
  const taskState = ref('idle')
  const taskResult = ref(null)
  const taskError = ref('')
  const taskId = ref(null)
  let pollTimer = null
  let generation = 0
  let hydratedTerminal = ''

  function stopPolling() {
    generation++
    clearTimeout(pollTimer)
    pollTimer = null
  }
  function persist(callbacks = {}) {
    setTask(taskType, { taskId: taskId.value, state: taskState.value, result: taskResult.value, error: taskError.value, ...callbacks })
  }
  function hydrate(callbacks, restoring = false) {
    const key = `${taskId.value}:${taskState.value}`
    if (hydratedTerminal === key) return
    hydratedTerminal = key
    if (taskState.value === 'done') (restoring ? callbacks.onRestore || callbacks.onComplete : callbacks.onComplete)?.(taskResult.value)
    if (taskState.value === 'error') callbacks.onError?.(taskError.value)
  }
  function apply(stored, callbacks = {}, restoring = false) {
    taskId.value = stored.taskId || null
    taskState.value = stored.state || 'idle'
    taskResult.value = stored.result ?? null
    taskError.value = stored.error || ''
    persist(callbacks)
    if (taskState.value === 'running' && taskId.value) startPolling(callbacks)
    else hydrate(callbacks, restoring)
  }
  function restore(callbacks = {}) {
    const stored = getTask(taskType)
    if (!stored) return false
    stopPolling()
    // 回调属于当前页面，不能复用已卸载页面闭包。
    apply(stored, callbacks, true)
    return true
  }
  async function restoreFromServer(callbacks = {}) {
    stopPolling()
    const version = generation
    try {
      const { data } = await listAsyncTasks(taskType, 'running', 1)
      if (version !== generation) return false
      if (!Array.isArray(data?.tasks)) throw new Error('任务列表响应无效')
      const running = data.tasks[0]
      if (running) {
        apply({ taskId: running.id, state: 'running' }, callbacks, true)
        return true
      }
      const { data: latest } = await listAsyncTasks(taskType, '', 1)
      if (version !== generation) return false
      if (!Array.isArray(latest?.tasks)) throw new Error('任务列表响应无效')
      const terminal = latest.tasks[0]
      if (terminal && ['done', 'error'].includes(terminal.status)) {
        apply({ taskId: terminal.id, state: terminal.status, result: terminal.result, error: terminal.error_msg || terminal.error }, callbacks, true)
        return true
      }
      reset()
      return false
    } catch (_) {
      if (version !== generation) return false
      // 本地凭据不因网络故障过期，后端状态才决定任务是否结束。
      return restore(callbacks)
    }
  }
  async function start(triggerFn, callbacks = {}) {
    stopPolling()
    const version = generation
    hydratedTerminal = ''
    taskState.value = 'submitting'
    taskResult.value = null
    taskError.value = ''
    taskId.value = null
    try {
      const response = await triggerFn()
      if (version !== generation) return
      const data = response?.data ?? response
      if (!data?.task_id) throw new Error('未收到后台任务编号')
      apply({ taskId: data.task_id, state: 'running' }, callbacks)
    } catch (e) {
      if (version !== generation) return
      taskState.value = 'error'
      taskError.value = e.response?.data?.detail || e.message
      persist(callbacks)
      hydrate(callbacks)
    }
  }
  function startPolling(callbacks, interval = 3000) {
    stopPolling()
    const version = generation
    const id = taskId.value
    const startedAt = Date.now()
    const current = () => version === generation && taskId.value === id
    const poll = async () => {
      if (!current()) return
      try {
        const { data } = await getAsyncTaskStatus(id)
        if (!current()) return
        if (['done', 'error'].includes(data.status)) {
          taskState.value = data.status
          taskResult.value = data.result ?? null
          taskError.value = data.error_msg || data.error || (data.status === 'error' ? '分析失败' : '')
          persist(callbacks)
          stopPolling()
          hydrate(callbacks)
          return
        }
        taskError.value = Date.now() - startedAt >= MAX_POLL_MS ? '后台仍在执行，等待结果中' : ''
      } catch (_) {
        if (!current()) return
        taskError.value = '网络暂时中断，正在恢复任务状态'
      }
      if (current()) pollTimer = setTimeout(poll, Date.now() - startedAt >= MAX_POLL_MS ? 15000 : interval)
    }
    pollTimer = setTimeout(poll, interval)
  }
  function reset() {
    stopPolling()
    taskState.value = 'idle'
    taskResult.value = null
    taskError.value = ''
    taskId.value = null
    hydratedTerminal = ''
    clearTask(taskType)
  }
  if (getCurrentInstance()) onUnmounted(stopPolling)
  return { taskState, taskResult, taskError, taskId, start, stopPolling, reset, restore, restoreFromServer, hasRunningTask }
}
