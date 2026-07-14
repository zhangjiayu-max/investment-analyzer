<script setup>
import { ref } from 'vue'

const props = defineProps({
  loading: Boolean,
})

const emit = defineEmits(['analyze'])
const url = ref('')

function submit() {
  const trimmed = url.value.trim()
  if (!trimmed) return
  emit('analyze', trimmed)
}

function onPaste(e) {
  // 粘贴后自动提交
  setTimeout(() => {
    if (url.value.trim().includes('mp.weixin.qq.com')) {
      submit()
    }
  }, 100)
}
</script>

<template>
  <div class="card url-input-card bg-mesh editorial-card">
    <h2 class="url-title editorial-title-lg">输入公众号文章链接</h2>
    <form @submit.prevent="submit" class="url-form">
      <input
        v-model="url"
        type="url"
        placeholder="https://mp.weixin.qq.com/s/..."
        class="input-field url-field"
        :disabled="loading"
        @paste="onPaste"
      />
      <button
        type="submit"
        :disabled="loading || !url.trim()"
        class="btn-primary url-btn"
      >
        {{ loading ? '分析中...' : '开始分析' }}
      </button>
    </form>
    <p class="url-hint">
      支持微信公众号文章链接，粘贴后自动开始分析
    </p>
  </div>
</template>

<style scoped>
.url-input-card {
  padding: 1.5rem;
}

.url-title {
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-primary);
  margin: 0 0 0.75rem;
}

.url-form {
  display: flex;
  gap: 0.75rem;
}

.url-field {
  flex: 1;
  padding: 0.75rem 1rem;
  font-size: 0.9rem;
}

.url-btn {
  padding: 0.75rem 1.5rem;
  white-space: nowrap;
}

.url-hint {
  margin: 0.5rem 0 0;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
</style>
