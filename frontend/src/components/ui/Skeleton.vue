<!-- 通用骨架屏：支持多种形状和 shimmer 动画 -->
<script setup>
defineProps({
  /** 'text' | 'title' | 'circle' | 'rect' | 'card' */
  variant: { type: String, default: 'text' },
  width: { type: String, default: '' },
  height: { type: String, default: '' },
  count: { type: Number, default: 1 },
  rounded: { type: Boolean, default: false },
})

const presets = {
  text: { width: '100%', height: '0.875rem' },
  title: { width: '60%', height: '1.5rem' },
  circle: { width: '48px', height: '48px' },
  rect: { width: '100%', height: '120px' },
  card: { width: '100%', height: '180px' },
}
</script>

<template>
  <div
    v-for="i in count"
    :key="i"
    class="skeleton-wrap"
    :class="[`skeleton-${variant}`, { 'skeleton-rounded': rounded }]"
    :style="{ width: width || presets[variant]?.width, height: height || presets[variant]?.height }"
  ></div>
</template>

<style scoped>
.skeleton-wrap {
  background: linear-gradient(90deg,
    var(--color-surface-hover, #f0f0f0) 25%,
    var(--color-surface, #e0e0e0) 50%,
    var(--color-surface-hover, #f0f0f0) 75%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.5s ease-in-out infinite;
  border-radius: var(--radius-sm);
}

.skeleton-circle { border-radius: 50%; }
.skeleton-rounded { border-radius: var(--radius-lg); }
.skeleton-card { border-radius: var(--radius-md); }

.skeleton-wrap + .skeleton-wrap { margin-top: 0.5rem; }

@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
