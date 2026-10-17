import { beforeEach, describe, expect, it, vi } from 'vitest'

const listAsyncTasks = vi.fn()
const getAsyncTaskStatus = vi.fn()

vi.mock('../api', () => ({ listAsyncTasks, getAsyncTaskStatus }))

describe('useAsyncTask server reconciliation', () => {
  beforeEach(() => {
    vi.resetModules()
    listAsyncTasks.mockReset()
    getAsyncTaskStatus.mockReset()
  })

  it('clears an in-memory running task when the server has no running task', async () => {
    const { useAsyncTask } = await import('../composables/useAsyncTask.js')
    const { useTaskStore } = await import('../composables/useTaskStore.js')
    const store = useTaskStore()
    store.setTask('daily_report', { taskId: 42, state: 'running' })
    listAsyncTasks.mockResolvedValue({ data: { tasks: [] } })

    const task = useAsyncTask('daily_report')
    await task.restoreFromServer()

    expect(task.taskState.value).toBe('idle')
    expect(task.taskId.value).toBe(null)
    expect(store.getTask('daily_report')).toBe(null)
    expect(listAsyncTasks).toHaveBeenCalledWith('daily_report', 'running', 1)
  })

  it('restores the latest terminal server state instead of showing loading forever', async () => {
    const { useAsyncTask } = await import('../composables/useAsyncTask.js')
    listAsyncTasks
      .mockResolvedValueOnce({ data: { tasks: [] } })
      .mockResolvedValueOnce({ data: { tasks: [{ id: 7, status: 'error', error_msg: '模型超时' }] } })

    const task = useAsyncTask('hotspots_analysis')
    await task.restoreFromServer()

    expect(task.taskState.value).toBe('error')
    expect(task.taskError.value).toBe('模型超时')
    expect(task.taskId.value).toBe(7)
    expect(listAsyncTasks).toHaveBeenLastCalledWith('hotspots_analysis', '', 1)
  })

  it('keeps a background task recoverable beyond the local waiting deadline', async () => {
    vi.useFakeTimers()
    const { useAsyncTask } = await import('../composables/useAsyncTask.js')
    const task = useAsyncTask('daily_report')
    getAsyncTaskStatus.mockResolvedValue({ data: { status: 'running' } })

    await task.start(async () => ({ data: { task_id: 8 } }))
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 3000)

    expect(task.taskState.value).toBe('running')
    expect(task.taskId.value).toBe(8)
    expect(task.taskError.value).toContain('后台仍在执行')
    task.reset()
    vi.useRealTimers()
  })
})
