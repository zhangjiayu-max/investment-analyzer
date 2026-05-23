<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { listGalleryRecords } from '../api'

const records = ref([])
const searchQuery = ref('')
const loading = ref(false)
const sortBy = ref('date') // 'date' | 'name'
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

// 按日期分组
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

function openPreview(path) { previewImage.value = imageUrl(path) }
function closePreview() { previewImage.value = null }

onMounted(loadRecords)
</script>

<template>
  <div class="gallery-page">
    <!-- Toolbar -->
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
      <span class="toolbar-count">共 {{ records.length }} 张</span>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-state">
      <div class="spinner-lg"></div>
      <span>加载中...</span>
    </div>

    <!-- Empty -->
    <div v-else-if="!records.length" class="empty-state">
      <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
      </svg>
      <p>{{ searchQuery ? '无匹配结果' : '暂无已解析的图片' }}</p>
    </div>

    <!-- Gallery -->
    <template v-else>
      <template v-if="sortBy === 'date'">
        <div v-for="[date, items] in groupedRecords" :key="date" class="date-group">
          <div class="date-header">
            <span class="date-label">{{ date }}</span>
            <span class="date-count">{{ items.length }} 张</span>
          </div>
          <div class="gallery-grid">
            <div v-for="r in items" :key="r.id" class="gallery-card" @click="openPreview(r.image_path)">
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
          <div v-for="r in sortedRecords" :key="r.id" class="gallery-card" @click="openPreview(r.image_path)">
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
  </div>
</template>

<style scoped>
.gallery-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

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
}

.gallery-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
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
