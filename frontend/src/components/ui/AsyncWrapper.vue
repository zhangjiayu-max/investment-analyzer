<script setup>
/**
 * AsyncWrapper — 统一的加载/错误/空状态包装器
 * 
 * 使用方式：
 * <AsyncWrapper :loading="loading" :error="error" :empty="!data?.length">
 *   <YourComponent />
 * </AsyncWrapper>
 */
const props = defineProps({
  loading: { type: Boolean, default: false },
  error: { type: [String, Error, null], default: null },
  empty: { type: Boolean, default: false },
  errorTitle: { type: String, default: '加载失败' },
  emptyTitle: { type: String, default: '暂无数据' },
  emptyDescription: { type: String, default: '' },
  showRetry: { type: Boolean, default: false },
})

const emit = defineEmits(['retry'])

function getErrorMessage(err) {
  if (!err) return ''
  if (typeof err === 'string') return err
  return err.message || '未知错误'
}
</script>

<template>
  <!-- Loading 状态 -->
  <div v-if="loading" class="async-wrapper async-wrapper--loading">
    <div class="spinner-lg"></div>
    <p class="async-loading-text">加载中...</p>
  </div>

  <!-- 错误状态 -->
  <div v-else-if="error" class="async-wrapper async-wrapper--error">
    <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-.833-2.694-.833-3.464 0L3.34 16c-.77.833.192 2.5 1.732 3z"/>
    </svg>
    <p class="async-error-title">{{ errorTitle }}</p>
    <p class="async-error-message">{{ getErrorMessage(error) }}</p>
    <button v-if="showRetry" class="btn-secondary btn-sm" @click="$emit('retry')">
      重试
    </button>
  </div>

  <!-- 空状态 -->
  <div v-else-if="empty" class="async-wrapper async-wrapper--empty">
    <svg width="40" height="40" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="opacity: 0.3;">
      <circle cx="12" cy="12" r="9"/>
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v8"/>
    </svg>
    <p class="async-empty-title">{{ emptyTitle }}</p>
    <p v-if="emptyDescription" class="async-empty-desc">{{ emptyDescription }}</p>
  </div>

  <!-- 正常内容 -->
  <slot v-else />
</template>

<style scoped>
.async-wrapper {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  min-height: 120px;
  text-align: center;
}

.async-loading-text {
  margin-top: 0.75rem;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.async-error-title {
  font-weight: 600;
  color: var(--color-danger);
  margin-bottom: 0.25rem;
}

.async-error-message {
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  max-width: 360px;
  word-break: break-word;
}

.async-empty-title {
  font-weight: 500;
  color: var(--color-text-muted);
  margin-bottom: 0.25rem;
}

.async-empty-desc {
  color: var(--color-text-muted);
  font-size: 0.82rem;
}
</style>
