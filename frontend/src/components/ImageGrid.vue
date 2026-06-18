<script setup>
import { ref } from 'vue'
import { parseAndSaveValuation } from '../api'

const props = defineProps({ images: Array })
const emit = defineEmits(['parsed'])

const lightbox = ref(null)
const parsingImage = ref(null)
const parseResult = ref(null)
const parseError = ref(null)

function open(img) { lightbox.value = img.url }
function close() { lightbox.value = null }

async function onParse(img) {
  parsingImage.value = img.local_path
  parseResult.value = null
  parseError.value = null
  try {
    const { data } = await parseAndSaveValuation(img.local_path)
    parseResult.value = data
    emit('parsed', data)
  } catch (e) {
    parseError.value = '解析失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    parsingImage.value = null
  }
}

function percentileClass(p) {
  if (p == null) return ''
  if (p < 30) return 'val-low'
  if (p <= 70) return 'val-mid'
  return 'val-high'
}

function percentileColor(p) {
  if (p == null) return 'neutral'
  if (p < 30) return 'success'
  if (p <= 70) return 'warning'
  return 'danger'
}
</script>

<template>
  <div class="image-grid">
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
      <div v-for="(img, i) in images" :key="i" class="image-item">
        <div @click="open(img)" class="image-thumb">
          <img :src="img.url" :alt="`图片 ${i + 1}`" loading="lazy" />
          <div class="image-overlay">
            <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7"/>
            </svg>
          </div>
        </div>
        <button
          @click.stop="onParse(img)"
          :disabled="parsingImage === img.local_path"
          class="parse-btn"
        >
          <svg v-if="parsingImage === img.local_path" class="spinner" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/></svg>
          {{ parsingImage === img.local_path ? '解析中...' : '解析估值' }}
        </button>
      </div>
    </div>

    <!-- Parse Result -->
    <div v-if="parseResult" class="parse-result success">
      <h4>解析成功</h4>
      <div class="result-grid">
        <div class="result-item">
          <span class="result-label">指数</span>
          <span class="result-value">{{ parseResult.index_name || '-' }}</span>
        </div>
        <div class="result-item">
          <span class="result-label">代码</span>
          <span class="result-value">{{ parseResult.index_code || '-' }}</span>
        </div>
        <div class="result-item">
          <span class="result-label">指标</span>
          <span class="result-value">{{ parseResult.metric_type || '-' }}</span>
        </div>
        <div class="result-item">
          <span class="result-label">当前值</span>
          <span class="result-value">{{ parseResult.current_value ?? '-' }}</span>
        </div>
        <div class="result-item">
          <span class="result-label">分位点</span>
          <span :class="['result-value', percentileClass(parseResult.percentile)]">
            {{ parseResult.percentile != null ? parseResult.percentile + '%' : '-' }}
          </span>
        </div>
        <div class="result-item">
          <span class="result-label">点位</span>
          <span class="result-value">{{ parseResult.current_point ?? '-' }}</span>
        </div>
      </div>
    </div>

    <div v-if="parseError" class="parse-result error">{{ parseError }}</div>

    <!-- Lightbox -->
    <Teleport to="body">
      <div v-if="lightbox" @click="close" class="lightbox">
        <img :src="lightbox" />
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.image-grid {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.image-item {
  position: relative;
}

.image-thumb {
  aspect-ratio: 1;
  border-radius: var(--radius-md);
  overflow: hidden;
  cursor: pointer;
  background: var(--color-bg-input);
  position: relative;
}

.image-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s ease;
}

.image-thumb:hover img {
  transform: scale(1.05);
}

.image-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0,0,0,0);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  transition: background 0.2s;
}

.image-overlay svg {
  opacity: 0;
  transition: opacity 0.2s;
}

.image-thumb:hover .image-overlay {
  background: rgba(0,0,0,0.3);
}

.image-thumb:hover .image-overlay svg {
  opacity: 1;
}

.parse-btn {
  position: absolute;
  bottom: 0.5rem;
  right: 0.5rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.75rem;
  background: var(--color-primary-600);
  color: white;
  font-size: 0.72rem;
  font-weight: 500;
  border-radius: var(--radius-sm);
  opacity: 0;
  transition: all var(--transition-fast);
  box-shadow: 0 2px 6px rgba(201, 168, 76, 0.4);
}

.image-item:hover .parse-btn {
  opacity: 1;
}

.parse-btn:hover {
  background: var(--color-primary-700);
}

/* 移动端：始终显示操作按钮 */
@media (max-width: 768px) {
  .image-overlay {
    background: rgba(0,0,0,0.15);
  }

  .image-overlay svg {
    opacity: 0.8;
  }

  .parse-btn {
    opacity: 1;
    padding: 0.5rem 0.8rem;
    font-size: 0.8rem;
  }

  .image-item .parse-btn {
    opacity: 1;
  }
}

.parse-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.parse-result {
  padding: 1rem;
  border-radius: var(--radius-md);
  font-size: 0.8rem;
}

.parse-result.success {
  background: rgba(16, 185, 129, 0.08);
  border: 1px solid rgba(16, 185, 129, 0.2);
}

.parse-result.success h4 {
  font-weight: 600;
  color: #059669;
  margin: 0 0 0.5rem 0;
  font-size: 0.85rem;
}

.parse-result.error {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.2);
  color: var(--color-danger);
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 0.75rem;
}

.result-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.result-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.result-value {
  font-weight: 600;
  color: var(--color-text-primary);
  font-size: 0.875rem;
}

.val-low { color: #059669; }
.val-mid { color: #d97706; }
.val-high { color: #dc2626; }

.lightbox {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.85);
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  cursor: zoom-out;
}

.lightbox img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: var(--radius-md);
}
</style>
