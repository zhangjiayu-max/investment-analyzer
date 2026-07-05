import { ref, onUnmounted } from 'vue'
import { getAsyncTaskStatus } from '../api'
import { useTaskStore } from './useTaskStore'

/**
 * 通用异步任务 composable
 *
 * 用法：
 * const { taskState, taskResult, taskError, start, stopPolling, reset } = useAsyncTask('hotspots_analysis')
 *
 * await start(triggerHotspotsAnalysis, {
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

  // 恢复已有任务状态（页面切回来时）
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

  return { taskState, taskResult, taskError, taskId, start, stopPolling, reset, restore, hasRunningTask }
}
