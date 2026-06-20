import { reactive } from 'vue'

// 全局任务状态 store — 按 task_type 区分，跨页面共享
const tasks = reactive({})

export function useTaskStore() {
  function setTask(type, data) {
    tasks[type] = { ...data, updatedAt: Date.now() }
  }

  function getTask(type) {
    return tasks[type] || null
  }

  function updateTask(type, patch) {
    if (tasks[type]) {
      Object.assign(tasks[type], patch, { updatedAt: Date.now() })
    }
  }

  function clearTask(type) {
    delete tasks[type]
  }

  function hasRunningTask(type) {
    const t = tasks[type]
    return t && (t.state === 'submitting' || t.state === 'running')
  }

  return { tasks, setTask, getTask, updateTask, clearTask, hasRunningTask }
}
