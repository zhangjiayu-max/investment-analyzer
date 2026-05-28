<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { listGalleryRecords, uploadDdImage, listDdImages, listDdImageDates, deleteDdImage, parseAndSaveValuation, uploadValuationImage, listValuationImages, listValuationImageDates, deleteValuationImage } from '../api'
import ConfirmDialog from './ConfirmDialog.vue'

// ── Tab 切换 ──
const activeTab = ref('gallery') // 'gallery' | 'dd'

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
const viFileInput = ref(null)
const viParsingPath = ref('')
const viParseResult = ref(null)

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
  viUploading.value = true
  try {
    for (const file of files) {
      await uploadValuationImage(file)
    }
    await loadViDates()
    viSelectedDate.value = ''
    await loadViImages()
  } catch (e) {
    console.error('Upload failed:', e)
    alert('上传失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    viUploading.value = false
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
        alert('删除失败: ' + (e.response?.data?.detail || e.message))
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
        const { data } = await parseAndSaveValuation(img.path)
        viParseResult.value = { ok: true, data, name: img.name }

        // 解析成功：从待解析移除，重新加载已解析列表
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
const parsingPath = ref('')  // 正在解析的图片路径
const parseResult = ref(null)  // 解析结果弹窗
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

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
    alert('上传失败: ' + (e.response?.data?.detail || e.message))
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
        alert('删除失败: ' + (e.response?.data?.detail || e.message))
      }
    }
  }
}

function confirmParseImage(img) {
  confirm.value = {
    visible: true,
    title: '解析估值数据',
    message: `将使用 AI 识别「${img.name}」中的指数估值信息（PE/PB/百分位等），识别结果会自动存入估值库。`,
    danger: false,
    onConfirm: async () => {
      confirm.value.visible = false
      parsingPath.value = img.path
      parseResult.value = null
      try {
        const { data } = await parseAndSaveValuation(img.path)
        parseResult.value = { ok: true, data, name: img.name }
      } catch (e) {
        parseResult.value = { ok: false, message: e.response?.data?.detail || e.message, name: img.name }
      } finally {
        parsingPath.value = ''
      }
    }
  }
}

// 按日期分组 dd 图片
const ddGroupedImages = computed(() => {
  const groups = {}
  for (const img of ddImages.value) {
    const date = img.date || '未知日期'
    if (!groups[date]) groups[date] = []
    groups[date].push(img)
  }
  return Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0]))
})

// ── 生命周期 ──
onMounted(() => {
  loadRecords()
  loadViDates()
  loadViImages()
  loadDdDates()
  loadDdImages()
})

onUnmounted(() => {
  // 重置所有加载状态
  loading.value = false
  viLoading.value = false
  viUploading.value = false
  ddLoading.value = false
  ddUploading.value = false
  viParsingPath.value = ''
  parsingPath.value = ''
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
  <div class="gallery-page">
    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'gallery' }]" @click="activeTab = 'gallery'">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
        估值图片
      </button>
      <button :class="['tab-btn', { active: activeTab === 'dd' }]" @click="activeTab = 'dd'">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
        螺丝钉估值
      </button>
    </div>

    <!-- ═══ 估值图片 Tab ═══ -->
    <template v-if="activeTab === 'gallery'">
      <!-- 上传的估值图片 -->
      <div class="section-header">
        <span class="section-title">待解析图片</span>
        <span class="toolbar-count">共 {{ viImages.length }} 张</span>
      </div>
      <div class="toolbar">
        <input ref="viFileInput" type="file" accept="image/*" multiple @change="handleViUpload" class="hidden-input" />
        <button class="btn-upload" @click="triggerViUpload" :disabled="viUploading">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
          {{ viUploading ? '上传中...' : '上传估值图片' }}
        </button>
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
        <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
        </svg>
        <p>暂无估值图片，点击上方按钮上传</p>
      </div>

      <template v-else>
        <div v-for="[date, items] in viGroupedImages" :key="date" class="date-group">
          <div class="date-header">
            <span class="date-label">{{ date }}</span>
            <span class="date-count">{{ items.length }} 张</span>
          </div>
          <div class="gallery-grid">
            <div v-for="img in items" :key="img.path" class="gallery-card">
              <div class="gallery-thumb" @click="openPreview(img.url)">
                <img :src="img.url" loading="lazy" />
                <button class="btn-delete-img" @click.stop="confirmDeleteViImage(img)" title="删除此图片">✕</button>
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
        <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
        </svg>
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
              <div v-for="r in items" :key="r.id" class="gallery-card" @click="openPreview(imageUrl(r.image_path))">
                <div class="gallery-thumb">
                  <img :src="imageUrl(r.image_path)" loading="lazy" />
                </div>
                <div class="gallery-info">
                  <div class="gallery-index">{{ r.index_name || r.index_code || '未识别' }}</div>
                  <span v-if="r.metric_type" :class="['badge', 'badge-sm', metricBadgeClass(r.metric_type)]">{{ r.metric_type }}</span>
                  <div class="gallery-value" v-if="r.current_value != null">
                    {{ r.current_value }}
                    <span v-if="r.percentile != null" class="gallery-pct">({{ r.percentile }}%)</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="gallery-grid">
            <div v-for="r in sortedRecords" :key="r.id" class="gallery-card" @click="openPreview(imageUrl(r.image_path))">
              <div class="gallery-thumb">
                <img :src="imageUrl(r.image_path)" loading="lazy" />
              </div>
              <div class="gallery-info">
                <div class="gallery-index">{{ r.index_name || r.index_code || '未识别' }}</div>
                <span v-if="r.metric_type" :class="['badge', 'badge-sm', metricBadgeClass(r.metric_type)]">{{ r.metric_type }}</span>
                <div class="gallery-value" v-if="r.current_value != null">
                  {{ r.current_value }}
                  <span v-if="r.percentile != null" class="gallery-pct">({{ r.percentile }}%)</span>
                </div>
                <div class="gallery-date">{{ (r.publish_time || '').slice(0, 10) }}</div>
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
        <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
        </svg>
        <p>暂无螺丝钉估值图片，点击上方按钮上传</p>
      </div>

      <template v-else>
        <div v-for="[date, items] in ddGroupedImages" :key="date" class="date-group">
          <div class="date-header">
            <span class="date-label">{{ date }}</span>
            <span class="date-count">{{ items.length }} 张</span>
          </div>
          <div class="gallery-grid">
            <div v-for="img in items" :key="img.path" class="gallery-card">
              <div class="gallery-thumb" @click="openPreview(img.url)">
                <img :src="img.url" loading="lazy" />
                <button class="btn-delete-img" @click.stop="confirmDeleteDdImage(img)" title="删除此图片">✕</button>
              </div>
              <div class="gallery-info">
                <div class="gallery-index">{{ img.name }}</div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </template>

    <!-- Lightbox -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="previewImage" class="lightbox" @click.self="closePreview">
          <button @click="closePreview" class="lightbox-close">
            <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
          <img :src="previewImage" />
        </div>
      </Transition>
    </Teleport>

    <!-- Confirm Dialog -->
    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :danger="confirm.danger"
      @cancel="confirm.visible = false"
      @confirm="confirm.onConfirm"
    />

    <!-- Parse Result Modal (螺丝钉) -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="parseResult" class="modal-overlay" @click.self="parseResult = null">
          <div class="modal-box" style="max-width:420px">
            <h3 class="modal-title">{{ parseResult.ok ? '解析成功' : '解析失败' }}</h3>
            <div v-if="parseResult.ok" class="parse-result-content">
              <p class="parse-file">{{ parseResult.name }}</p>
              <div class="parse-data">
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
  gap: 1rem;
}

/* ── Tab 栏 ── */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 2px solid var(--color-border);
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 1.2rem;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-secondary);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  cursor: pointer;
  transition: all 0.15s;
}

.tab-btn:hover {
  color: var(--color-text-primary);
  background: var(--color-bg-hover);
}

.tab-btn.active {
  color: var(--color-primary-600);
  border-bottom-color: var(--color-primary-500);
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
  padding: 0.4rem 0.75rem;
  font-size: 0.8rem;
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
  transition: all 0.15s;
}

.btn-upload:hover {
  background: linear-gradient(135deg, var(--color-primary-700), var(--color-primary-600));
}

.btn-upload:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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

/* Date Groups */
.date-group {
  margin-bottom: 0.5rem;
}

.date-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0;
  margin-bottom: 0.5rem;
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
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.gallery-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  cursor: pointer;
  transition: all 0.2s;
}

.gallery-card:hover {
  border-color: var(--color-primary-300);
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
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
}

.btn-delete-img {
  position: absolute;
  top: 0.4rem;
  right: 0.4rem;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.65rem;
  color: white;
  background: rgba(0, 0, 0, 0.45);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  opacity: 0;
  transition: all var(--transition-fast);
}

.gallery-card:hover .btn-delete-img {
  opacity: 1;
}

.btn-delete-img:hover {
  color: white;
  background: var(--color-danger);
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
  transition: all var(--transition-fast);
  margin-top: 0.25rem;
  width: fit-content;
}

.btn-parse-img:hover:not(:disabled) {
  background: var(--color-primary-100);
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
  padding: 0.75rem;
}

.parse-row {
  display: flex;
  justify-content: space-between;
  padding: 0.3rem 0;
  font-size: 0.85rem;
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

.gallery-info {
  padding: 0.6rem 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.gallery-index {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gallery-value {
  font-size: 0.75rem;
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

/* Lightbox */
.lightbox {
  position: fixed;
  inset: 0;
  z-index: var(--z-lightbox);
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.lightbox-close {
  position: absolute;
  top: 1rem;
  right: 1.5rem;
  background: none;
  border: none;
  color: white;
  cursor: pointer;
  z-index: 10000;
  padding: 0.5rem;
}

.lightbox img {
  max-width: 95vw;
  max-height: 90vh;
  object-fit: contain;
  border-radius: var(--radius-md);
}

/* Transitions */
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
