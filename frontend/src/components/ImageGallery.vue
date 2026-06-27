<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { listGalleryRecords, uploadDdImage, listDdImages, listDdImageDates, deleteDdImage, parseAndSaveValuation, parseValuationBatch, parseDDImage, parseDDImageAsync, parseDDBatchAsync, getDDParseTask, pollDDParseTask, uploadValuationImage, listValuationImages, listValuationImageDates, deleteValuationImage, getSystemConfig, updateSystemConfig } from '../api'
import ConfirmDialog from './ConfirmDialog.vue'

// ── 并发限制工具函数 ──
async function asyncPool(limit, items, fn) {
  const results = []
  const executing = new Set()
  for (const item of items) {
    const p = fn(item).then(result => {
      executing.delete(p)
      return result
    })
    results.push(p)
    executing.add(p)
    if (executing.size >= limit) {
      await Promise.race(executing)
    }
  }
  return Promise.all(results)
}

// ── Tab 切换 ──
const activeTab = ref('gallery') // 'gallery' | 'dd'

// ── 组件卸载标志 ──
let isUnmounted = false

// ── 图片浏览 Tab ──
const records = ref([])
const searchQuery = ref('')
const loading = ref(false)
const sortBy = ref('date')
const previewImage = ref(null)
let searchTimer = null

async function loadRecords() {
  loading.value = true
  try {
    const { data } = await listGalleryRecords(searchQuery.value, 200)
    records.value = (data.records || []).filter(r => r.status === 'success')
  } catch (e) {
    console.error('Failed to load gallery:', e)
  } finally {
    loading.value = false
  }
}

function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(loadRecords, 300)
}

const sortedRecords = computed(() => {
  const list = [...records.value]
  if (sortBy.value === 'date') {
    list.sort((a, b) => (b.publish_time || '').localeCompare(a.publish_time || ''))
  } else {
    list.sort((a, b) => (a.index_name || a.index_code || '').localeCompare(b.index_name || b.index_code || ''))
  }
  return list
})

const groupedRecords = computed(() => {
  const groups = {}
  for (const r of sortedRecords.value) {
    const date = (r.publish_time || '').slice(0, 10) || '未知日期'
    if (!groups[date]) groups[date] = []
    groups[date].push(r)
  }
  return Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0]))
})

function imageUrl(path) {
  if (!path) return ''
  const marker = 'data/images/'
  const idx = path.indexOf(marker)
  if (idx !== -1) return `/static/images/${path.slice(idx + marker.length)}`
  return `/static/images/${path}`
}

function metricBadgeClass(mt) {
  if (!mt) return 'badge-neutral'
  if (mt.includes('市盈率')) return 'badge-info'
  if (mt.includes('市净率')) return 'badge-success'
  if (mt.includes('市销率')) return 'badge-warning'
  if (mt.includes('市销率')) return 'badge-warning'
  if (mt.includes('市现率')) return 'badge-purple'
  if (mt.includes('股息率')) return 'badge-orange'
  if (mt.includes('风险溢价')) return 'badge-pink'
  return 'badge-neutral'
}

function openPreview(url) { previewImage.value = url }
function closePreview() { previewImage.value = null }

// ── 估值图片上传 Tab ──
const viImages = ref([])
const viDates = ref([])
const viSelectedDate = ref('')
const viLoading = ref(false)
const viUploading = ref(false)
const viAutoParsing = ref(false) // 自动解析状态
const viFileInput = ref(null)
const viParsingPath = ref('')
const viParseResult = ref(null)
const viBatchParsing = ref(false)
const viBatchProgress = ref({ done: 0, total: 0 })

async function loadViDates() {
  try {
    const { data } = await listValuationImageDates()
    viDates.value = data.dates || []
  } catch (e) {
    console.error('Failed to load valuation image dates:', e)
  }
}

async function loadViImages() {
  viLoading.value = true
  try {
    const { data } = await listValuationImages(viSelectedDate.value || null)
    viImages.value = data.images || []
  } catch (e) {
    console.error('Failed to load valuation images:', e)
  } finally {
    viLoading.value = false
  }
}

async function handleViUpload(e) {
  const files = e.target.files
  if (!files || !files.length) return
  if (isUnmounted) return
  viUploading.value = true
  try {
    const uploadedPaths = []
    for (const file of files) {
      if (isUnmounted) break
      const { data } = await uploadValuationImage(file)
      if (data?.path) {
        uploadedPaths.push({ path: data.path, name: file.name })
      }
    }
    if (isUnmounted) return
    await loadViDates()
    viSelectedDate.value = ''
    await loadViImages()

    // 自动解析上传的图片
    if (uploadedPaths.length > 0 && !isUnmounted) {
      viAutoParsing.value = true
      showToast(`上传成功，开始自动解析 ${uploadedPaths.length} 张图片...`, 'info')
      let successCount = 0
      let failCount = 0
      for (const img of uploadedPaths) {
        if (isUnmounted) break
        try {
          // 自动判断图片类型
          const isDDImage = img.path.includes('dd_images') || img.name.includes('螺丝钉') || img.name.includes('dd')
          // TODO: DD 图片也应改用 parseDDImageAsync 异步模式
          const parseFn = isDDImage ? parseDDImage : parseAndSaveValuation
          await parseFn(img.path)
          successCount++
        } catch (parseErr) {
          console.error(`解析失败: ${img.name}`, parseErr)
          failCount++
        }
      }
      if (isUnmounted) return
      // 刷新列表（已解析的会从待解析列表消失）
      await loadViImages()
      await loadRecords()
      if (failCount > 0) {
        showToast(`解析完成：${successCount} 张成功，${failCount} 张失败`, 'warning')
      } else {
        showToast(`自动解析完成：${successCount} 张图片已识别`, 'success')
      }
    }
  } catch (e) {
    if (!isUnmounted) {
      console.error('Upload failed:', e)
      showToast('上传失败: ' + (e.response?.data?.detail || e.message), 'error')
    }
  } finally {
    viUploading.value = false
    viAutoParsing.value = false
    if (viFileInput.value) viFileInput.value.value = ''
  }
}

function triggerViUpload() {
  viFileInput.value?.click()
}

function confirmDeleteViImage(img) {
  confirm.value = {
    visible: true,
    title: '删除图片',
    message: `确定要删除「${img.name}」吗？删除后无法恢复。`,
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      try {
        await deleteValuationImage(img.path)
        await loadViDates()
        await loadViImages()
      } catch (e) {
        showToast('删除失败: ' + (e.response?.data?.detail || e.message), 'error')
      }
    }
  }
}

function confirmViParseImage(img) {
  confirm.value = {
    visible: true,
    title: '解析估值数据',
    message: `将使用 AI 识别「${img.name}」中的指数估值信息（PE/PB/百分位等），识别结果会自动存入估值库。`,
    danger: false,
    onConfirm: async () => {
      confirm.value.visible = false
      viParsingPath.value = img.path
      viParseResult.value = null
      try {
        // 自动判断图片类型：如果是螺丝钉估值表，调用 DD 解析
        const isDDImage = img.path.includes('dd_images') || img.name.includes('螺丝钉') || img.name.includes('dd')
        const parseFn = isDDImage ? parseDDImage : parseAndSaveValuation
        const { data } = await parseFn(img.path)
        viParseResult.value = { ok: true, data, name: img.name }
        viImages.value = viImages.value.filter(i => i.path !== img.path)
        await loadRecords()
      } catch (e) {
        viParseResult.value = { ok: false, message: e.response?.data?.detail || e.message, name: img.name }
      } finally {
        viParsingPath.value = ''
      }
    }
  }
}

function confirmViBatchParse(date, items) {
  confirm.value = {
    visible: true,
    title: '批量识别估值',
    message: `将并发识别「${date}」的 ${items.length} 张图片中的估值数据，是否继续？`,
    danger: false,
    onConfirm: async () => {
      confirm.value.visible = false
      viBatchParsing.value = true
      viBatchProgress.value = { done: 0, total: items.length }
      try {
        // 限制并发数为 3，避免占用所有浏览器连接
        const responses = await asyncPool(3, items, async (img) => {
          try {
            // 自动判断图片类型：如果是螺丝钉估值表，调用 DD 解析
            const isDDImage = img.path.includes('dd_images') || img.name.includes('螺丝钉') || img.name.includes('dd')
            // TODO: DD 图片也应改用 parseDDImageAsync 异步模式
          const parseFn = isDDImage ? parseDDImage : parseAndSaveValuation
            const result = await parseFn(img.path)
            return result
          } catch (e) {
            return { data: { ok: false, error: e.message } }
          } finally {
            viBatchProgress.value.done++
          }
        })
        const results = responses.map(r => r.data)
        const okCount = results.filter(r => r.ok).length
        const failCount = results.filter(r => !r.ok).length
        showToast(`批量识别完成：成功 ${okCount} 张${failCount > 0 ? '，失败 ' + failCount + ' 张' : ''}`, okCount > 0 ? 'success' : 'error')
        await loadViImages()
        await loadRecords()
      } catch (e) {
        showToast('批量识别失败: ' + (e.response?.data?.detail || e.message), 'error')
      } finally {
        viBatchParsing.value = false
      }
    }
  }
}

function confirmReParse(r) {
  confirm.value = {
    visible: true,
    title: '重新识别估值',
    message: `将重新使用 AI 识别「${r.index_name || r.image_path}」的估值数据，新结果会覆盖已有数据。`,
    danger: false,
    onConfirm: async () => {
      confirm.value.visible = false
      r._reparsing = true
      try {
        await parseAndSaveValuation(r.image_path)
        showToast('重新识别完成', 'success')
      } catch (e) {
        showToast('重新识别失败: ' + (e.response?.data?.detail || e.message), 'error')
      } finally {
        r._reparsing = false
      }
      await loadRecords()
    }
  }
}


const viGroupedImages = computed(() => {
  const groups = {}
  for (const img of viImages.value) {
    const date = img.date || '未知日期'
    if (!groups[date]) groups[date] = []
    groups[date].push(img)
  }
  return Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0]))
})

// ── 螺丝钉估值 Tab ──
const ddImages = ref([])
const ddDates = ref([])
const ddSelectedDate = ref('')
const ddLoading = ref(false)
const ddUploading = ref(false)
const fileInput = ref(null)
const parsingPath = ref('')
const parseResult = ref(null)
const ddBatchParsing = ref(false)
const ddBatchProgress = ref({ done: 0, total: 0 })
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

// ── 异步解析任务追踪 ──
// key: image_path, value: { taskId, status, pollCancel }
const ddParseTasks = ref({})
const ddBatchPollCancel = ref(null)

async function loadDdDates() {
  try {
    const { data } = await listDdImageDates()
    ddDates.value = data.dates || []
  } catch (e) {
    console.error('Failed to load dd dates:', e)
  }
}

async function loadDdImages() {
  ddLoading.value = true
  try {
    const { data } = await listDdImages(ddSelectedDate.value || null)
    ddImages.value = data.images || []
  } catch (e) {
    console.error('Failed to load dd images:', e)
  } finally {
    ddLoading.value = false
  }
}

async function handleUpload(e) {
  const files = e.target.files
  if (!files || !files.length) return
  ddUploading.value = true
  try {
    for (const file of files) {
      await uploadDdImage(file)
    }
    await loadDdDates()
    ddSelectedDate.value = ''
    await loadDdImages()
  } catch (e) {
    console.error('Upload failed:', e)
    showToast('上传失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    ddUploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

function triggerUpload() {
  fileInput.value?.click()
}

function confirmDeleteDdImage(img) {
  confirm.value = {
    visible: true,
    title: '删除图片',
    message: `确定要删除「${img.name}」吗？删除后无法恢复。`,
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      try {
        await deleteDdImage(img.path)
        await loadDdDates()
        await loadDdImages()
      } catch (e) {
        showToast('删除失败: ' + (e.response?.data?.detail || e.message), 'error')
      }
    }
  }
}

function confirmParseImage(img) {
  confirm.value = {
    visible: true,
    title: '解析螺丝钉估值表',
    message: `将使用 AI 识别「${img.name}」中的多指数估值表格数据，识别结果会自动存入估值库。`,
    danger: false,
    onConfirm: async () => {
      confirm.value.visible = false
      parseResult.value = null
      try {
        const { data } = await parseDDImageAsync(img.path)
        const taskId = data.task_id
        // 存储任务状态
        ddParseTasks.value = { ...ddParseTasks.value, [img.path]: { taskId, status: data.status || 'pending' } }
        // 启动轮询
        const cancel = pollDDParseTask(taskId, (taskData) => {
          if (isUnmounted) return
          ddParseTasks.value = { ...ddParseTasks.value, [img.path]: { taskId, status: taskData.status } }
          if (taskData.status === 'done') {
            const result = taskData.result_json || {}
            parseResult.value = { ok: true, data: result, name: img.name }
            ddImages.value = ddImages.value.filter(i => i.path !== img.path)
            loadRecords()
            // 清理任务追踪
            const { [img.path]: _, ...rest } = ddParseTasks.value
            ddParseTasks.value = rest
            showToast(`「${img.name}」识别完成`, 'success')
          } else if (taskData.status === 'error') {
            parseResult.value = { ok: false, message: taskData.error_msg || '解析失败', name: img.name }
            const { [img.path]: _, ...rest } = ddParseTasks.value
            ddParseTasks.value = rest
            showToast(`「${img.name}」识别失败: ${taskData.error_msg || ''}`, 'error')
          }
        })
        // 存储取消函数
        ddParseTasks.value = { ...ddParseTasks.value, [img.path]: { ...ddParseTasks.value[img.path], pollCancel: cancel } }
      } catch (e) {
        showToast('提交解析任务失败: ' + (e.response?.data?.detail || e.message), 'error')
      }
    }
  }
}

function confirmDdBatchParse(date, items) {
  confirm.value = {
    visible: true,
    title: '批量识别螺丝钉估值表',
    message: `将并发识别「${date}」的 ${items.length} 张螺丝钉估值表，是否继续？`,
    danger: false,
    onConfirm: async () => {
      confirm.value.visible = false
      ddBatchParsing.value = true
      ddBatchProgress.value = { done: 0, total: items.length }
      try {
        const paths = items.map(img => img.path)
        const { data } = await parseDDBatchAsync(paths)
        const taskList = data.tasks || []
        const validTasks = taskList.filter(t => t.task_id)
        ddBatchProgress.value = { done: 0, total: validTasks.length }

        // 轮询所有任务
        let completed = 0
        let okCount = 0
        let failCount = 0
        const taskIds = validTasks.map(t => t.task_id)

        const pollAll = () => {
          if (isUnmounted) return
          let checked = 0
          for (const tid of taskIds) {
            getDDParseTask(tid).then(({ data: taskData }) => {
              if (taskData.status === 'done' || taskData.status === 'error') {
                completed++
                if (taskData.status === 'done') okCount++
                else failCount++
                ddBatchProgress.value = { ...ddBatchProgress.value, done: completed }
              }
              checked++
              if (checked === taskIds.length) {
                if (completed >= taskIds.length) {
                  // 全部完成
                  showToast(`批量识别完成：成功 ${okCount} 张${failCount > 0 ? '，失败 ' + failCount + ' 张' : ''}`, okCount > 0 ? 'success' : 'error')
                  loadDdImages()
                  ddBatchParsing.value = false
                } else {
                  // 继续轮询
                  ddBatchPollCancel.value = setTimeout(pollAll, 3000)
                }
              }
            }).catch(() => {
              checked++
              if (checked === taskIds.length && completed < taskIds.length) {
                ddBatchPollCancel.value = setTimeout(pollAll, 3000)
              }
            })
          }
        }
        // 首次轮询稍等一下
        ddBatchPollCancel.value = setTimeout(pollAll, 2000)
      } catch (e) {
        showToast('批量识别失败: ' + (e.message || e), 'error')
        ddBatchParsing.value = false
      }
    }
  }
}

const ddGroupedImages = computed(() => {
  const groups = {}
  for (const img of ddImages.value) {
    const date = img.date || '未知日期'
    if (!groups[date]) groups[date] = []
    groups[date].push(img)
  }
  return Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0]))
})

// ── 拖拽上传 ──
const isDragging = ref(false)
let dragCounter = 0

function handleDragEnter(e) {
  e.preventDefault()
  dragCounter++
  if (e.dataTransfer?.types?.includes('Files')) {
    isDragging.value = true
  }
}

function handleDragOver(e) {
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
}

function handleDragLeave(e) {
  e.preventDefault()
  dragCounter--
  if (dragCounter <= 0) {
    dragCounter = 0
    isDragging.value = false
  }
}

async function handleDrop(e) {
  e.preventDefault()
  dragCounter = 0
  isDragging.value = false

  const files = [...(e.dataTransfer?.files || [])].filter(f => f.type.startsWith('image/'))
  if (!files.length) return
  if (isUnmounted) return

  const isGallery = activeTab.value === 'gallery'
  const uploading = isGallery ? viUploading : ddUploading
  if (uploading.value) return

  uploading.value = true
  showToast(`正在上传 ${files.length} 张图片...`, 'info')
  try {
    const uploadFn = isGallery ? uploadValuationImage : uploadDdImage
    const uploadedPaths = []
    for (const file of files) {
      if (isUnmounted) break
      const { data } = await uploadFn(file)
      if (data?.path) {
        uploadedPaths.push({ path: data.path, name: file.name })
      }
    }
    if (isUnmounted) return
    if (isGallery) {
      await loadViDates()
      viSelectedDate.value = ''
      await loadViImages()
    } else {
      await loadDdDates()
      ddSelectedDate.value = ''
      await loadDdImages()
    }

    // 自动解析上传的图片
    if (uploadedPaths.length > 0 && isGallery && !isUnmounted) {
      viAutoParsing.value = true
      showToast(`上传成功，开始自动解析 ${uploadedPaths.length} 张图片...`, 'info')
      let successCount = 0
      let failCount = 0
      for (const img of uploadedPaths) {
        if (isUnmounted) break
        try {
          const isDDImage = img.path.includes('dd_images') || img.name.includes('螺丝钉') || img.name.includes('dd')
          // TODO: DD 图片也应改用 parseDDImageAsync 异步模式
          const parseFn = isDDImage ? parseDDImage : parseAndSaveValuation
          await parseFn(img.path)
          successCount++
        } catch (parseErr) {
          console.error(`解析失败: ${img.name}`, parseErr)
          failCount++
        }
      }
      if (isUnmounted) return
      await loadViImages()
      await loadRecords()
      if (failCount > 0) {
        showToast(`解析完成：${successCount} 张成功，${failCount} 张失败`, 'warning')
      } else {
        showToast(`自动解析完成：${successCount} 张图片已识别`, 'success')
      }
    } else if (!isUnmounted) {
      showToast(`${files.length} 张图片上传成功`, 'success')
    }
  } catch (e) {
    if (!isUnmounted) {
      console.error('Drop upload failed:', e)
      showToast('上传失败: ' + (e.response?.data?.detail || e.message), 'error')
    }
  } finally {
    uploading.value = false
    viAutoParsing.value = false
  }
}

// ── 粘贴上传 ──
async function handlePaste(e) {
  const items = e.clipboardData?.items
  if (!items) return
  const imageFiles = []
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) {
        const ext = item.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png'
        const named = new File([file], `paste-${Date.now()}.${ext}`, { type: item.type })
        imageFiles.push(named)
      }
    }
  }
  if (!imageFiles.length) return
  e.preventDefault()
  if (isUnmounted) return

  const isGallery = activeTab.value === 'gallery'
  const uploading = isGallery ? viUploading : ddUploading
  if (uploading.value) return

  uploading.value = true
  showToast(`正在粘贴上传 ${imageFiles.length} 张图片...`, 'info')
  try {
    const uploadFn = isGallery ? uploadValuationImage : uploadDdImage
    const uploadedPaths = []
    for (const file of imageFiles) {
      if (isUnmounted) break
      const { data } = await uploadFn(file)
      if (data?.path) {
        uploadedPaths.push({ path: data.path, name: file.name })
      }
    }
    if (isUnmounted) return
    if (isGallery) {
      await loadViDates()
      viSelectedDate.value = ''
      await loadViImages()
    } else {
      await loadDdDates()
      ddSelectedDate.value = ''
      await loadDdImages()
    }

    // 自动解析上传的图片
    if (uploadedPaths.length > 0 && isGallery && !isUnmounted) {
      viAutoParsing.value = true
      showToast(`粘贴上传成功，开始自动解析 ${uploadedPaths.length} 张图片...`, 'info')
      let successCount = 0
      let failCount = 0
      for (const img of uploadedPaths) {
        if (isUnmounted) break
        try {
          const isDDImage = img.path.includes('dd_images') || img.name.includes('螺丝钉') || img.name.includes('dd')
          // TODO: DD 图片也应改用 parseDDImageAsync 异步模式
          const parseFn = isDDImage ? parseDDImage : parseAndSaveValuation
          await parseFn(img.path)
          successCount++
        } catch (parseErr) {
          console.error(`解析失败: ${img.name}`, parseErr)
          failCount++
        }
      }
      if (isUnmounted) return
      await loadViImages()
      await loadRecords()
      if (failCount > 0) {
        showToast(`解析完成：${successCount} 张成功，${failCount} 张失败`, 'warning')
      } else {
        showToast(`自动解析完成：${successCount} 张图片已识别`, 'success')
      }
    } else if (!isUnmounted) {
      showToast(`${imageFiles.length} 张图片粘贴上传成功`, 'success')
    }
  } catch (e) {
    if (!isUnmounted) {
      console.error('Paste upload failed:', e)
      showToast('粘贴上传失败: ' + (e.response?.data?.detail || e.message), 'error')
    }
  } finally {
    uploading.value = false
    viAutoParsing.value = false
  }
}

// ── Toast 通知 ──
const toasts = ref([])
let toastId = 0

function showToast(message, type = 'info') {
  const id = ++toastId
  toasts.value.push({ id, message, type })
  setTimeout(() => {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }, 3000)
}

// ── 视觉模型切换 ──
const visionProvider = ref('ollama')
const visionSwitching = ref(false)

async function loadVisionProvider() {
  try {
    const { data } = await getSystemConfig('vision.provider')
    visionProvider.value = data.value || 'ollama'
  } catch (e) {
    console.error('Failed to load vision provider:', e)
  }
}

async function switchVisionProvider(provider) {
  if (provider === visionProvider.value || visionSwitching.value) return
  visionSwitching.value = true
  try {
    await updateSystemConfig('vision.provider', provider)
    visionProvider.value = provider
  } catch (e) {
    console.error('Failed to switch vision provider:', e)
  } finally {
    visionSwitching.value = false
  }
}

// ── 生命周期 ──
onMounted(() => {
  loadRecords()
  loadViDates()
  loadViImages()
  loadDdDates()
  loadDdImages()
  loadVisionProvider()
  document.addEventListener('paste', handlePaste)
})

onUnmounted(() => {
  // 设置卸载标志，阻止异步操作继续
  isUnmounted = true
  if (searchTimer) clearTimeout(searchTimer)
  document.removeEventListener('paste', handlePaste)
  loading.value = false
  viLoading.value = false
  viUploading.value = false
  viAutoParsing.value = false
  ddLoading.value = false
  ddUploading.value = false
  viParsingPath.value = ''
  parsingPath.value = ''
  // 取消所有轮询
  for (const task of Object.values(ddParseTasks.value)) {
    if (task.pollCancel) task.pollCancel()
  }
  if (ddBatchPollCancel.value) clearTimeout(ddBatchPollCancel.value)
})

watch(activeTab, (tab) => {
  if (tab === 'gallery') {
    loadViDates()
    loadViImages()
  } else if (tab === 'dd') {
    loadDdDates()
    loadDdImages()
  }
})
</script>

<template>
  <div
    class="gallery-page"
    @dragenter="handleDragEnter"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
    @drop="handleDrop"
  >
    <!-- 拖拽覆盖层 -->
    <Transition name="dropzone">
      <div v-if="isDragging" class="drop-overlay">
        <div class="drop-content">
          <div class="drop-icon-ring">
            <svg class="drop-icon" width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
            </svg>
          </div>
          <p class="drop-text">释放鼠标上传图片</p>
          <p class="drop-sub">支持 JPG、PNG、GIF 格式</p>
        </div>
      </div>
    </Transition>

    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'gallery' }]" @click="activeTab = 'gallery'">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
        雷牛牛估值
      </button>
      <button :class="['tab-btn', { active: activeTab === 'dd' }]" @click="activeTab = 'dd'">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
        螺丝钉估值
      </button>
      <div class="vision-switch">
        <span class="vision-label">视觉模型</span>
        <button
          :class="['vision-btn', { active: visionProvider === 'ollama' }]"
          :disabled="visionSwitching"
          @click="switchVisionProvider('ollama')"
        >Ollama</button>
        <button
          :class="['vision-btn', { active: visionProvider === 'mimo' }]"
          :disabled="visionSwitching"
          @click="switchVisionProvider('mimo')"
        >MiMo</button>
      </div>
    </div>

    <!-- ═══ 估值图片 Tab ═══ -->
    <template v-if="activeTab === 'gallery'">
      <div class="section-header">
        <span class="section-title">待解析图片</span>
        <span class="toolbar-count">共 {{ viImages.length }} 张</span>
      </div>
      <div class="toolbar">
        <input ref="viFileInput" type="file" accept="image/*" multiple @change="handleViUpload" class="hidden-input" />
        <button class="btn-upload" @click="triggerViUpload" :disabled="viUploading || viAutoParsing">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
          {{ viAutoParsing ? '解析中...' : viUploading ? '上传中...' : '上传估值图片' }}
        </button>
        <div class="upload-hint">
          <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          拖拽图片到页面 或 Ctrl+V 粘贴
        </div>
        <select v-model="viSelectedDate" @change="loadViImages" class="date-select">
          <option value="">全部日期</option>
          <option v-for="d in viDates" :key="d.date" :value="d.date">{{ d.date }} ({{ d.count }})</option>
        </select>
      </div>

      <div v-if="viLoading" class="loading-state">
        <div class="spinner-lg"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="!viImages.length" class="empty-state">
        <div class="empty-icon-float">
          <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
          </svg>
        </div>
        <p>暂无估值图片</p>
        <p class="empty-sub">点击上方按钮上传，或直接拖拽/粘贴图片</p>
      </div>

      <template v-else>
        <div v-for="[date, items] in viGroupedImages" :key="date" class="date-group">
          <div class="date-header">
            <span class="date-label">{{ date }}</span>
            <span class="date-count">{{ items.length }} 张</span>
            <button class="btn-batch-parse" @click="confirmViBatchParse(date, items)" :disabled="viBatchParsing" title="批量识别该日期下所有图片">
              <span v-if="viBatchParsing" class="spinner-sm"></span>
              <svg v-else width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              {{ viBatchParsing ? '识别中...' : '批量识别' }}
            </button>
          </div>
          <div class="gallery-grid">
            <div v-for="(img, idx) in items" :key="img.path" class="gallery-card" :style="{ animationDelay: `${idx * 40}ms` }">
              <div class="gallery-thumb" @click="openPreview(img.url)">
                <img :src="img.url" loading="lazy" />
                <div class="thumb-overlay">
                  <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7"/></svg>
                </div>
                <button class="btn-delete-img" @click.stop="confirmDeleteViImage(img)" title="删除此图片">
                  <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                </button>
              </div>
              <div class="gallery-info">
                <div class="gallery-index">{{ img.name }}</div>
                <button class="btn-parse-img" @click.stop="confirmViParseImage(img)" :disabled="viParsingPath === img.path" title="AI 识别图片中的估值数据并存入数据库">
                  <span v-if="viParsingPath === img.path" class="spinner-sm"></span>
                  <svg v-else width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                  </svg>
                  {{ viParsingPath === img.path ? '识别中...' : '识别估值' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- 已解析的图片库 -->
      <div class="section-divider"></div>
      <div class="section-header">
        <span class="section-title">已解析图片</span>
        <span class="toolbar-count">共 {{ records.length }} 条</span>
      </div>
      <div class="toolbar">
        <input
          v-model="searchQuery"
          @input="onSearchInput"
          placeholder="搜索指数名称、代码、指标类型..."
          class="input-field gallery-search"
        />
        <div class="sort-toggle">
          <button :class="['sort-btn', { active: sortBy === 'date' }]" @click="sortBy = 'date'">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
            按日期
          </button>
          <button :class="['sort-btn', { active: sortBy === 'name' }]" @click="sortBy = 'name'">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12"/></svg>
            按名称
          </button>
        </div>
      </div>

      <div v-if="loading" class="loading-state">
        <div class="spinner-lg"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="!records.length" class="empty-state">
        <div class="empty-icon-float">
          <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
          </svg>
        </div>
        <p>{{ searchQuery ? '无匹配结果' : '暂无已解析的图片' }}</p>
      </div>

      <template v-else>
        <template v-if="sortBy === 'date'">
          <div v-for="[date, items] in groupedRecords" :key="date" class="date-group">
            <div class="date-header">
              <span class="date-label">{{ date }}</span>
              <span class="date-count">{{ items.length }} 张</span>
            </div>
            <div class="gallery-grid">
              <div v-for="(r, idx) in items" :key="r.id" class="gallery-card" :style="{ animationDelay: `${idx * 40}ms` }" @click="openPreview(imageUrl(r.image_path))">
                <div class="gallery-thumb">
                  <img :src="imageUrl(r.image_path)" loading="lazy" />
                  <div class="thumb-overlay">
                    <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7"/></svg>
                  </div>
                </div>
                <div class="gallery-info">
                  <div class="gallery-index">{{ r.index_name || r.index_code || '未识别' }}</div>
                  <span v-if="r.metric_type" :class="['badge', 'badge-sm', metricBadgeClass(r.metric_type)]">{{ r.metric_type }}</span>
                  <div class="gallery-value" v-if="r.current_value != null">
                    {{ r.current_value }}
                    <span v-if="r.percentile != null" class="gallery-pct">({{ r.percentile }}%)</span>
                  </div>
                </div>
                <button class="btn-reparse" @click.stop="confirmReParse(r)" :disabled="r._reparsing" title="重新识别">
                  <span v-if="r._reparsing" class="spinner-xs"></span>
                  <svg v-else width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                  {{ r._reparsing ? '识别中...' : '重新识别' }}
                </button>
              </div>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="gallery-grid">
            <div v-for="(r, idx) in sortedRecords" :key="r.id" class="gallery-card" :style="{ animationDelay: `${idx * 40}ms` }" @click="openPreview(imageUrl(r.image_path))">
              <div class="gallery-thumb">
                <img :src="imageUrl(r.image_path)" loading="lazy" />
                <div class="thumb-overlay">
                  <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7"/></svg>
                </div>
              </div>
              <div class="gallery-info">
                <div class="gallery-index">{{ r.index_name || r.index_code || '未识别' }}</div>
                <span v-if="r.metric_type" :class="['badge', 'badge-sm', metricBadgeClass(r.metric_type)]">{{ r.metric_type }}</span>
                <div class="gallery-value" v-if="r.current_value != null">
                  {{ r.current_value }}
                  <span v-if="r.percentile != null" class="gallery-pct">({{ r.percentile }}%)</span>
                </div>
                <div class="gallery-date">{{ (r.publish_time || '').slice(0, 10) }}</div>
                <button class="btn-reparse" @click.stop="confirmReParse(r)" :disabled="r._reparsing" title="重新识别">
                  <span v-if="r._reparsing" class="spinner-xs"></span>
                  <svg v-else width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                  {{ r._reparsing ? '识别中...' : '重新识别' }}
                </button>
              </div>
            </div>
          </div>
        </template>
      </template>
    </template>

    <!-- ═══ 螺丝钉估值 Tab ═══ -->
    <template v-if="activeTab === 'dd'">
      <div class="dd-toolbar">
        <div class="dd-actions">
          <input ref="fileInput" type="file" accept="image/*" multiple @change="handleUpload" class="hidden-input" />
          <button class="btn-upload" @click="triggerUpload" :disabled="ddUploading">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
            {{ ddUploading ? '上传中...' : '上传图片' }}
          </button>
          <div class="upload-hint">
            <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            拖拽图片到页面 或 Ctrl+V 粘贴
          </div>
          <select v-model="ddSelectedDate" @change="loadDdImages" class="date-select">
            <option value="">全部日期</option>
            <option v-for="d in ddDates" :key="d.date" :value="d.date">{{ d.date }} ({{ d.count }})</option>
          </select>
        </div>
        <span class="toolbar-count">共 {{ ddImages.length }} 张</span>
      </div>

      <div v-if="ddLoading" class="loading-state">
        <div class="spinner-lg"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="!ddImages.length" class="empty-state">
        <div class="empty-icon-float">
          <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
          </svg>
        </div>
        <p>暂无螺丝钉估值图片</p>
        <p class="empty-sub">点击上方按钮上传，或直接拖拽/粘贴图片</p>
      </div>

      <template v-else>
        <div v-for="[date, items] in ddGroupedImages" :key="date" class="date-group">
          <div class="date-header">
            <span class="date-label">{{ date }}</span>
            <span class="date-count">{{ items.length }} 张</span>
            <button class="btn-batch-parse" @click="confirmDdBatchParse(date, items)" :disabled="ddBatchParsing" title="批量识别该日期下所有图片">
              <span v-if="ddBatchParsing" class="spinner-sm"></span>
              <svg v-else width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              {{ ddBatchParsing ? `识别中 ${ddBatchProgress.done}/${ddBatchProgress.total}...` : '批量识别' }}
            </button>
          </div>
          <div class="gallery-grid">
            <div v-for="(img, idx) in items" :key="img.path" :class="['gallery-card', { parsed: img.parsed }]" :style="{ animationDelay: `${idx * 40}ms` }">
              <div class="gallery-thumb" @click="openPreview(img.url)">
                <img :src="img.url" loading="lazy" />
                <div class="thumb-overlay">
                  <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7"/></svg>
                </div>
                <button class="btn-delete-img" @click.stop="confirmDeleteDdImage(img)" title="删除此图片">
                  <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                </button>
              </div>
              <div class="gallery-info">
                <div class="gallery-index">{{ img.name }}</div>
                <span v-if="img.parsed" class="parsed-badge">
                  <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>
                  已识别
                </span>
                <button v-else class="btn-parse-img" @click.stop="confirmParseImage(img)" :disabled="!!ddParseTasks[img.path]" title="AI 识别图片中的估值数据并存入数据库">
                  <span v-if="ddParseTasks[img.path]" class="spinner-sm"></span>
                  <svg v-else width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                  </svg>
                  {{ ddParseTasks[img.path] ? '识别中...' : '识别估值' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </template>
    </template>

    <!-- Lightbox -->
    <Teleport to="body">
      <Transition name="lightbox">
        <div v-if="previewImage" class="lightbox" @click.self="closePreview">
          <button @click="closePreview" class="lightbox-close">
            <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
          <img :src="previewImage" />
        </div>
      </Transition>
    </Teleport>

    <!-- Toast 通知 -->
    <Teleport to="body">
      <div class="toast-container">
        <TransitionGroup name="toast">
          <div v-for="t in toasts" :key="t.id" :class="['toast', `toast-${t.type}`]">
            <svg v-if="t.type === 'success'" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <svg v-else-if="t.type === 'error'" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <svg v-else width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <span>{{ t.message }}</span>
          </div>
        </TransitionGroup>
      </div>
    </Teleport>

    <!-- Confirm Dialog -->
    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :danger="confirm.danger"
      @cancel="confirm.visible = false"
      @confirm="() => confirm.onConfirm?.()"
    />

    <!-- Parse Result Modal (螺丝钉) -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="parseResult" class="modal-overlay" @click.self="parseResult = null">
          <div class="modal-box" style="max-width:500px">
            <h3 class="modal-title">{{ parseResult.ok ? '解析成功' : '解析失败' }}</h3>
            <div v-if="parseResult.ok" class="parse-result-content">
              <p class="parse-file">{{ parseResult.name }}</p>
              <!-- 螺丝钉估值表：多指数数据 -->
              <div v-if="parseResult.data.data && parseResult.data.data.length > 0" class="parse-data">
                <div v-if="parseResult.data.update_date" class="parse-row">
                  <span class="parse-label">更新日期</span>
                  <span>{{ parseResult.data.update_date }}</span>
                </div>
                <div v-if="parseResult.data.market_temperature != null" class="parse-row">
                  <span class="parse-label">市场温度</span>
                  <span>{{ parseResult.data.market_temperature }}</span>
                </div>
                <div class="parse-row">
                  <span class="parse-label">识别指数</span>
                  <span>{{ parseResult.data.count }} 个</span>
                </div>
                <div class="dd-index-list">
                  <div v-for="(item, idx) in parseResult.data.data.slice(0, 10)" :key="idx" class="dd-index-item">
                    <span class="dd-index-name">{{ item.index_name || '未知' }}</span>
                    <span v-if="item.pe" class="dd-index-val">PE {{ item.pe }}</span>
                    <span v-if="item.pe_percentile" class="dd-index-val">{{ item.pe_percentile }}%</span>
                    <span v-if="item.valuation_status" :class="['dd-index-status', item.valuation_status === '低估' ? 'low' : item.valuation_status === '高估' ? 'high' : '']">{{ item.valuation_status }}</span>
                  </div>
                  <div v-if="parseResult.data.data.length > 10" class="dd-index-more">
                    还有 {{ parseResult.data.data.length - 10 }} 个指数...
                  </div>
                </div>
              </div>
              <!-- 单指数估值图 -->
              <div v-else class="parse-data">
                <div v-if="parseResult.data.index_name" class="parse-row">
                  <span class="parse-label">指数</span>
                  <span>{{ parseResult.data.index_name }}</span>
                </div>
                <div v-if="parseResult.data.metric_type" class="parse-row">
                  <span class="parse-label">指标</span>
                  <span>{{ parseResult.data.metric_type }}</span>
                </div>
                <div v-if="parseResult.data.current_value != null" class="parse-row">
                  <span class="parse-label">当前值</span>
                  <span>{{ parseResult.data.current_value }}</span>
                </div>
                <div v-if="parseResult.data.percentile != null" class="parse-row">
                  <span class="parse-label">百分位</span>
                  <span>{{ parseResult.data.percentile }}%</span>
                </div>
              </div>
              <p class="parse-hint">数据已存入估值库，可前往「估值数据」页面查看</p>
            </div>
            <div v-else class="parse-error">
              <p>{{ parseResult.message }}</p>
            </div>
            <div class="modal-actions">
              <button class="btn btn-primary" @click="parseResult = null">确定</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Parse Result Modal (估值图片) -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="viParseResult" class="modal-overlay" @click.self="viParseResult = null">
          <div class="modal-box" style="max-width:420px">
            <h3 class="modal-title">{{ viParseResult.ok ? '解析成功' : '解析失败' }}</h3>
            <div v-if="viParseResult.ok" class="parse-result-content">
              <p class="parse-file">{{ viParseResult.name }}</p>
              <div class="parse-data">
                <div v-if="viParseResult.data.index_name" class="parse-row">
                  <span class="parse-label">指数</span>
                  <span>{{ viParseResult.data.index_name }}</span>
                </div>
                <div v-if="viParseResult.data.metric_type" class="parse-row">
                  <span class="parse-label">指标</span>
                  <span>{{ viParseResult.data.metric_type }}</span>
                </div>
                <div v-if="viParseResult.data.current_value != null" class="parse-row">
                  <span class="parse-label">当前值</span>
                  <span>{{ viParseResult.data.current_value }}</span>
                </div>
                <div v-if="viParseResult.data.percentile != null" class="parse-row">
                  <span class="parse-label">百分位</span>
                  <span>{{ viParseResult.data.percentile }}%</span>
                </div>
              </div>
              <p class="parse-hint">数据已存入估值库，可前往「估值数据」页面查看</p>
            </div>
            <div v-else class="parse-error">
              <p>{{ viParseResult.message }}</p>
            </div>
            <div class="modal-actions">
              <button class="btn btn-primary" @click="viParseResult = null">确定</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.gallery-page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  position: relative;
}

/* ── 拖拽覆盖层 ── */
.drop-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(99, 102, 241, 0.08);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.drop-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
}

.drop-icon-ring {
  width: 96px;
  height: 96px;
  border-radius: 50%;
  border: 3px dashed var(--color-primary-400);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-primary-500);
  animation: drop-ring-pulse 1.5s ease-in-out infinite;
}

@keyframes drop-ring-pulse {
  0%, 100% { transform: scale(1); border-color: var(--color-primary-400); opacity: 0.8; }
  50% { transform: scale(1.08); border-color: var(--color-primary-500); opacity: 1; }
}

.drop-icon {
  animation: drop-icon-bounce 1.5s ease-in-out infinite;
}

@keyframes drop-icon-bounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-6px); }
}

.drop-text {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--color-primary-600);
  margin: 0;
}

.drop-sub {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin: 0;
}

.dropzone-enter-active { transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1); }
.dropzone-leave-active { transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
.dropzone-enter-from { opacity: 0; }
.dropzone-enter-from .drop-icon-ring { transform: scale(0.7); }
.dropzone-leave-to { opacity: 0; }

/* ── Tab 栏 ── */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 2px solid var(--color-border);
  align-items: center;
}

.vision-switch {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding-right: 0.25rem;
}

.vision-label {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  font-weight: 500;
  white-space: nowrap;
}

.vision-btn {
  padding: 0.25rem 0.55rem;
  font-size: 0.68rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.vision-btn.active {
  background: var(--color-primary-50);
  color: var(--color-primary-600);
  border-color: var(--color-primary-300);
}

.vision-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.7rem 1.35rem;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-secondary);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
}

.tab-btn::after {
  content: '';
  position: absolute;
  bottom: -2px;
  left: 50%;
  width: 0;
  height: 2px;
  background: var(--color-primary-500);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  transform: translateX(-50%);
}

.tab-btn:hover {
  color: var(--color-text-primary);
  background: var(--color-bg-hover);
}

.tab-btn.active {
  color: var(--color-primary-600);
  border-bottom-color: transparent;
}

.tab-btn.active::after {
  width: 100%;
}

/* ── 工具栏 ── */
.toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.gallery-search {
  flex: 1;
  min-width: 200px;
  max-width: 400px;
}

.sort-toggle {
  display: flex;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.sort-btn {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.45rem 0.85rem;
  font-size: 0.82rem;
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border: none;
  cursor: pointer;
  transition: all 0.15s;
}

.sort-btn:first-child { border-right: 1px solid var(--color-border); }
.sort-btn:hover { background: var(--color-bg-hover); }
.sort-btn.active { background: var(--color-primary-50); color: var(--color-primary-600); }

.toolbar-count {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

/* ── 螺丝钉工具栏 ── */
.dd-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.dd-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.hidden-input {
  display: none;
}

.btn-upload {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  font-size: 0.8rem;
  font-weight: 500;
  color: white;
  background: linear-gradient(135deg, var(--color-primary-600), var(--color-primary-500));
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.btn-upload::before {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent);
  transition: left 0.5s ease;
}

.btn-upload:hover::before {
  left: 100%;
}

.btn-upload:hover {
  background: linear-gradient(135deg, var(--color-primary-700), var(--color-primary-600));
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3);
  transform: translateY(-1px);
}

.btn-upload:active {
  transform: translateY(0);
}

.btn-upload:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.btn-upload:disabled::before { display: none; }

.upload-hint {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  padding: 0.3rem 0.6rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
  border: 1px dashed var(--color-border);
}

.date-select {
  padding: 0.45rem 0.75rem;
  font-size: 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  cursor: pointer;
  outline: none;
  transition: border-color 0.2s;
}

.date-select:focus {
  border-color: var(--color-primary-500);
}

/* ── 分段标题 ── */
.section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.section-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.section-divider {
  height: 1px;
  background: var(--color-border);
  margin: 1rem 0 0.5rem;
}

/* Loading & Empty */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.empty-state p { font-size: 0.875rem; margin: 0; }
.empty-sub { font-size: 0.75rem !important; color: var(--color-text-tertiary); }

.empty-icon-float {
  color: var(--color-text-tertiary);
  animation: empty-float 3s ease-in-out infinite;
}

@keyframes empty-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

/* Date Groups */
.date-group {
  margin-bottom: 0.5rem;
}

.date-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 0;
  margin-bottom: 0.6rem;
  border-bottom: 1px solid var(--color-border-light);
}

.date-label {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.date-count {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  background: var(--color-bg-input);
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
}

/* Gallery Grid */
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 1.25rem;
}

.gallery-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  animation: card-in 0.4s cubic-bezier(0.4, 0, 0.2, 1) both;
}

@keyframes card-in {
  from {
    opacity: 0;
    transform: translateY(12px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.gallery-card:hover {
  border-color: var(--color-primary-300);
  box-shadow: 0 8px 24px -4px rgba(99, 102, 241, 0.12), 0 4px 8px -2px rgba(0, 0, 0, 0.06);
  transform: translateY(-3px);
}

.gallery-thumb {
  width: 100%;
  height: 140px;
  overflow: hidden;
  background: var(--color-bg-input);
  position: relative;
  cursor: pointer;
}

.gallery-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}

.gallery-card:hover .gallery-thumb img {
  transform: scale(1.06);
}

.thumb-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.25);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  opacity: 0;
  transition: opacity 0.25s ease;
}

.gallery-card:hover .thumb-overlay {
  opacity: 1;
}

.btn-delete-img {
  position: absolute;
  top: 0.4rem;
  right: 0.4rem;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  opacity: 0;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  transform: scale(0.8);
}

.gallery-card:hover .btn-delete-img {
  opacity: 1;
  transform: scale(1);
}

.btn-delete-img:hover {
  background: var(--color-danger);
  transform: scale(1.1) !important;
}

/* 移动端：始终显示删除按钮和操作提示 */
@media (max-width: 768px) {
  .thumb-overlay {
    opacity: 1;
    background: rgba(0, 0, 0, 0.1);
  }

  .btn-delete-img {
    opacity: 1;
    transform: scale(1);
    width: 36px;
    height: 36px;
  }

  .gallery-thumb {
    height: 120px;
  }
}

.btn-parse-img {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.25rem 0.5rem;
  font-size: 0.7rem;
  font-weight: 500;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  border: 1px solid var(--color-primary-200);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  margin-top: 0.25rem;
  width: fit-content;
}

.btn-parse-img:hover:not(:disabled) {
  background: var(--color-primary-100);
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(99, 102, 241, 0.15);
}

.btn-parse-img:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-parse-img .spinner-sm {
  width: 12px;
  height: 12px;
  border-width: 1.5px;
}

.btn-reparse {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.25rem 0.5rem;
  font-size: 0.7rem;
  font-weight: 500;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  border: 1px solid var(--color-primary-200);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  margin-top: 0.25rem;
  width: fit-content;
}

.btn-reparse:hover:not(:disabled) {
  background: var(--color-primary-100);
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(99, 102, 241, 0.15);
}

.btn-reparse:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-reparse .spinner-xs {
  width: 10px;
  height: 10px;
  border-width: 1.5px;
}

.btn-batch-parse {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-left: auto;
  padding: 0.2rem 0.5rem;
  font-size: 0.7rem;
  font-weight: 500;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  border: 1px solid var(--color-primary-200);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.btn-batch-parse:hover:not(:disabled) {
  background: var(--color-primary-100);
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(99, 102, 41, 0.15);
}

.btn-batch-parse:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-batch-parse .spinner-sm {
  width: 12px;
  height: 12px;
  border-width: 1.5px;
}

.parse-result-content {
  margin: 0.75rem 0;
}

.parse-file {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-bottom: 0.75rem;
}

.parse-data {
  background: var(--color-bg-hover);
  border-radius: var(--radius-md);
  padding: 0.85rem 1rem;
}

.parse-row {
  display: flex;
  justify-content: space-between;
  padding: 0.4rem 0;
  font-size: 0.85rem;
  line-height: 1.6;
}

.parse-row + .parse-row {
  border-top: 1px solid var(--color-border);
}

.parse-label {
  color: var(--color-text-muted);
}

.parse-hint {
  font-size: 0.75rem;
  color: var(--color-primary);
  margin-top: 0.75rem;
}

.parse-error p {
  color: var(--color-danger);
  font-size: 0.85rem;
}

/* 螺丝钉估值表 - 指数列表 */
.dd-index-list {
  margin-top: 0.5rem;
  border-top: 1px solid var(--color-border);
  padding-top: 0.5rem;
}

.dd-index-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0;
  font-size: 0.82rem;
  border-bottom: 1px dashed var(--color-border-light);
}

.dd-index-name {
  flex: 1;
  font-weight: 500;
  color: var(--color-text-primary);
}

.dd-index-val {
  color: var(--color-text-secondary);
  font-size: 0.75rem;
}

.dd-index-status {
  font-size: 0.7rem;
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  font-weight: 500;
}

.dd-index-status.low {
  background: rgba(16, 185, 129, 0.1);
  color: #10b981;
}

.dd-index-status.high {
  background: rgba(239, 68, 68, 0.1);
  color: #ef4444;
}

.dd-index-more {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  text-align: center;
  padding: 0.3rem 0;
}

.gallery-info {
  padding: 0.75rem 0.85rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.gallery-index {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gallery-value {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
}

.gallery-pct {
  color: var(--color-text-muted);
  font-size: 0.7rem;
}

.gallery-date {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.badge-sm {
  font-size: 0.6rem;
  padding: 0.05rem 0.35rem;
  align-self: flex-start;
}

.badge-purple { background: rgba(139, 92, 246, 0.1); color: #7c3aed; }
.badge-orange { background: rgba(249, 115, 22, 0.1); color: #ea580c; }
.badge-pink { background: rgba(236, 72, 153, 0.1); color: #db2777; }

/* 已识别徽章 */
.parsed-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.7rem;
  font-weight: 600;
  color: #16a34a;
  background: rgba(22, 163, 74, 0.08);
  padding: 0.15rem 0.5rem;
  border-radius: 9999px;
  align-self: flex-start;
}

.gallery-card.parsed {
  border-left: 3px solid #22c55e;
}

/* Lightbox */
.lightbox {
  position: fixed;
  inset: 0;
  z-index: var(--z-lightbox);
  background: rgba(0, 0, 0, 0.88);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.lightbox-close {
  position: absolute;
  top: 1rem;
  right: 1.5rem;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  cursor: pointer;
  z-index: 10000;
  padding: 0.5rem;
  border-radius: var(--radius-md);
  transition: all 0.2s;
}

.lightbox-close:hover {
  background: rgba(255, 255, 255, 0.2);
  transform: rotate(90deg);
}

.lightbox img {
  max-width: 95vw;
  max-height: 90vh;
  object-fit: contain;
  border-radius: var(--radius-md);
}

.lightbox-enter-active { transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
.lightbox-leave-active { transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
.lightbox-enter-from { opacity: 0; }
.lightbox-enter-from img { transform: scale(0.92); }
.lightbox-leave-to { opacity: 0; }
.lightbox-leave-to img { transform: scale(0.95); }

/* Toast 通知 */
.toast-container {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 10001;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 1rem;
  border-radius: var(--radius-md);
  font-size: 0.8rem;
  font-weight: 500;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12), 0 1px 3px rgba(0, 0, 0, 0.08);
  pointer-events: auto;
  backdrop-filter: blur(8px);
}

.toast-success {
  background: rgba(22, 163, 74, 0.9);
  color: white;
}

.toast-error {
  background: rgba(220, 38, 38, 0.9);
  color: white;
}

.toast-info {
  background: rgba(99, 102, 241, 0.9);
  color: white;
}

.toast-enter-active { transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
.toast-leave-active { transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1); }
.toast-enter-from { opacity: 0; transform: translateX(40px) scale(0.95); }
.toast-leave-to { opacity: 0; transform: translateX(20px) scale(0.95); }

/* Transitions */
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
