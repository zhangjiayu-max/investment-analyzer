/**
 * 任务追踪 composable
 *
 * 功能：
 * 1. 持久化正在运行的任务到 localStorage
 * 2. 轮询检查任务状态
 * 3. 任务完成时触发通知
 */

import { ref, onMounted, onUnmounted } from 'vue'

const STORAGE_KEY = 'pending_tasks'
const POLL_INTERVAL = 10000 // 10 秒轮询一次

export function useTaskTracker() {
  // 正在运行的任务列表
  const pendingTasks = ref([])

  // 刚完成的任务（用于通知）
  const completedTask = ref(null)

  // 轮询定时器
  let pollTimer = null

  // 从 localStorage 加载任务
  function loadTasks() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored) {
        pendingTasks.value = JSON.parse(stored)
      }
    } catch (e) {
      console.error('加载任务列表失败:', e)
      pendingTasks.value = []
    }
  }

  // 保存任务到 localStorage
  function saveTasks() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(pendingTasks.value))
    } catch (e) {
      console.error('保存任务列表失败:', e)
    }
  }

  // 添加任务
  function addTask(convId, messageId, title = '') {
    const existing = pendingTasks.value.find(t => t.convId === convId)
    if (existing) {
      // 更新现有任务
      existing.messageId = messageId
      existing.title = title || existing.title
      existing.addedAt = Date.now()
    } else {
      // 添加新任务
      pendingTasks.value.push({
        convId,
        messageId,
        title: title || `对话 #${convId}`,
        addedAt: Date.now(),
        lastChecked: null,
        status: 'streaming',
      })
    }
    saveTasks()
  }

  // 移除任务
  function removeTask(convId) {
    pendingTasks.value = pendingTasks.value.filter(t => t.convId !== convId)
    saveTasks()
  }

  // 检查单个任务状态
  async function checkTaskStatus(task) {
    try {
      const response = await fetch(`/api/conversations/tasks/${task.convId}/status`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const data = await response.json()
      task.lastChecked = Date.now()
      task.status = data.status
      task.progress = data.progress || 0
      task.completedSpecialists = data.completed_specialists || 0
      task.totalSpecialists = data.total_specialists || 0

      // 如果任务完成或失败，触发通知
      if (data.status === 'completed' || data.status === 'failed') {
        completedTask.value = {
          convId: task.convId,
          messageId: task.messageId,
          title: task.title,
          status: data.status,
          completedAt: Date.now(),
        }
        // 从待处理列表中移除
        removeTask(task.convId)
        return true // 表示任务完成
      }

      return false // 表示任务仍在进行
    } catch (e) {
      console.error(`检查任务状态失败 (convId: ${task.convId}):`, e)
      // 网络错误时保留任务，继续轮询
      return false
    }
  }

  // 检查所有任务状态
  async function checkAllTasks() {
    if (pendingTasks.value.length === 0) {
      return
    }

    const tasksToCheck = [...pendingTasks.value]
    for (const task of tasksToCheck) {
      await checkTaskStatus(task)
    }
    saveTasks()
  }

  // 从后端获取正在运行的任务（用于恢复状态）
  async function fetchRunningTasks() {
    try {
      const response = await fetch('/api/conversations/tasks/running')
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const data = await response.json()
      const runningTasks = data.tasks || []

      const backendIds = new Set(runningTasks.map(t => t.conversation_id))

      // 删除后端已不存在的任务（同步前后端状态）
      pendingTasks.value = pendingTasks.value.filter(t => backendIds.has(t.convId))

      // 添加后端有但本地没有的任务
      for (const task of runningTasks) {
        const existing = pendingTasks.value.find(t => t.convId === task.conversation_id)
        if (!existing) {
          pendingTasks.value.push({
            convId: task.conversation_id,
            messageId: task.message_id,
            title: task.conversation_title || `对话 #${task.conversation_id}`,
            addedAt: Date.now(),
            lastChecked: null,
            status: 'streaming',
          })
        }
      }
      saveTasks()
    } catch (e) {
      console.error('获取运行中任务失败:', e)
    }
  }

  // 清除完成通知
  function clearCompletedTask() {
    completedTask.value = null
  }

  // 启动轮询
  function startPolling() {
    if (pollTimer) {
      return
    }
    pollTimer = setInterval(checkAllTasks, POLL_INTERVAL)
  }

  // 停止轮询
  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // 初始化
  onMounted(async () => {
    loadTasks()
    await fetchRunningTasks()
    startPolling()
  })

  // 清理
  onUnmounted(() => {
    stopPolling()
  })

  return {
    pendingTasks,
    completedTask,
    addTask,
    removeTask,
    checkAllTasks,
    fetchRunningTasks,
    clearCompletedTask,
    startPolling,
    stopPolling,
  }
}