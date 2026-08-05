import { ref, onUnmounted } from 'vue'
import { getAsyncTaskStatus, listAsyncTasks } from '../api'
import { useTaskStore } from './useTaskStore'

/**
 * 通用异步任务 composable
 *
 * 用法：
 * const { taskState, taskResult, taskError, start, stopPolling, reset, restore, restoreFromServer } = useAsyncTask('hotspots_analysis')
 *
 * await start(triggerHotspotsAnalysis, {
 *   onComplete: (result) => { ... },
 *   onError: (err) => { ... },
 * })
 *
 * // 页面加载时从后端恢复执行中任务(切页/刷新后按钮显示"执行中")
 * await restoreFromServer({
 *   onComplete: (result) => { ... },
 *   onError: (err) => { ... },
 * })
 */
export function useAsyncTask(taskType) {
  const { getTask, setTask, updateTask, clearTask, hasRunningTask } = useTaskStore()

  const taskState = ref('idle')  // idle | submitting | running | done | error
  const taskResult = ref(null)
  const taskError = ref('')
  const taskId = ref(null)
  let pollTimer = null

  // 恢复已有任务状态（页面切回来时,从内存 store 恢复）
  function restore() {
    const stored = getTask(taskType)
    if (!stored) return false

    taskState.value = stored.state || 'idle'
    taskResult.value = stored.result || null
    taskError.value = stored.error || ''
    taskId.value = stored.taskId || null

    if (stored.state === 'running' && stored.taskId) {
      startPolling(stored.onComplete, stored.onError)
      return true
    }
    return true
  }

  /**
   * 从后端恢复执行中任务状态(页面加载/刷新后调用)
   * 查询 async_tasks 表是否有该 task_type 的 running 任务
   * 有则恢复 taskState=running 并开始轮询,实现"切页/刷新回来按钮显示执行中"
   */
  async function restoreFromServer({ onComplete, onError } = {}) {
    // 先尝试从内存 store 恢复(切页场景,store 仍在)
    if (restore()) {
      // 内存有 running 任务,但 onComplete/onError 可能丢失(组件重建),用新的覆盖
      if (taskState.value === 'running' && taskId.value) {
        setTask(taskType, { onComplete, onError })
        startPolling(onComplete, onError)
      }
      return true
    }

    // 内存无任务,查后端是否有 running 任务(刷新场景)
    try {
      const { data } = await listAsyncTasks(taskType, 'running', 1)
      const runningTask = data?.tasks?.[0]
      if (runningTask) {
        taskId.value = runningTask.id
        taskState.value = 'running'
        taskResult.value = null
        taskError.value = ''
        setTask(taskType, {
          taskId: runningTask.id,
          state: 'running',
          result: null,
          error: '',
          onComplete,
          onError,
        })
        startPolling(onComplete, onError)
        return true
      }
    } catch (e) {
      // 查询失败静默处理,不影响页面正常加载
      console.warn(`[useAsyncTask] restoreFromServer 查询 ${taskType} running 任务失败:`, e)
    }
    return false
  }

  async function start(triggerFn, { onComplete, onError } = {}) {
    taskState.value = 'submitting'
    taskResult.value = null
    taskError.value = ''
    try {
      const { data } = await triggerFn()
      taskId.value = data.task_id
      taskState.value = 'running'
      setTask(taskType, {
        taskId: data.task_id,
        state: 'running',
        result: null,
        error: '',
        onComplete,
        onError,
      })
      startPolling(onComplete, onError)
    } catch (e) {
      taskState.value = 'error'
      taskError.value = e.response?.data?.detail || e.message
      setTask(taskType, { state: 'error', error: taskError.value })
    }
  }

  function startPolling(onComplete, onError, interval = 3000) {
    stopPolling()
    pollTimer = setInterval(async () => {
      if (!taskId.value) return
      try {
        const { data } = await getAsyncTaskStatus(taskId.value)
        if (data.status === 'done') {
          taskState.value = 'done'
          taskResult.value = data.result
          updateTask(taskType, { state: 'done', result: data.result })
          stopPolling()
          onComplete?.(data.result)
        } else if (data.status === 'error') {
          taskState.value = 'error'
          taskError.value = data.error || '分析失败'
          updateTask(taskType, { state: 'error', error: taskError.value })
          stopPolling()
          onError?.(taskError.value)
        }
      } catch (e) {
        taskState.value = 'error'
        taskError.value = e.message
        updateTask(taskType, { state: 'error', error: e.message })
        stopPolling()
        onError?.(e.message)
      }
    }, interval)
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }

  function reset() {
    stopPolling()
    taskState.value = 'idle'
    taskResult.value = null
    taskError.value = ''
    taskId.value = null
    clearTask(taskType)
  }

  // 组件卸载时自动清理轮询定时器
  onUnmounted(() => {
    stopPolling()
  })

  return { taskState, taskResult, taskError, taskId, start, stopPolling, reset, restore, restoreFromServer, hasRunningTask }
}
