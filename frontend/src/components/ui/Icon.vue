<script setup>
/**
 * Icon 统一图标组件
 * 
 * 使用方式：<Icon name="dashboard" size="20" />
 * 
 * 支持的图标名称（持续扩展）：
 *   导航类: dashboard, chat, articles, valuation, gallery, portfolio, admin, token, config, fire, author, bond, evolution, link, rag, bug, check, chart, book
 *   操作类: search, plus, minus, close, chevron-down, chevron-up, chevron-left, chevron-right, refresh, send, more, settings
 *   状态类: success, warning, error, info, loading/spinner
 */
const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 20 },
  color: { type: String, default: '' }, // 覆盖颜色，空则继承当前文字色
})

const iconSize = computed(() => {
  const s = Number(props.size) || 20
  return `${s}px`
})
</script>

<template>
  <svg
    class="icon"
    :class="[`icon--${name}`]"
    :width="iconSize"
    :height="iconSize"
    :fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    :style="color ? undefined : undefined"
    :stroke="color || 'currentColor'"
  >
    <!-- dashboard -->
    <template v-if="name === 'dashboard'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-2-2V6a2 2 0 00-2-2h-2m-6 0a1 1 0 002 2h2a2 2 0 012 2v4a1 1 0 002 2m-6 0h6"/>
    </template>
    <!-- chat -->
    <template v-else-if="name === 'chat'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
    </template>
    <!-- articles -->
    <template v-else-if="name === 'articles'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 002-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"/>
    </template>
    <!-- valuation -->
    <template v-else-if="name === 'valuation'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002 2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
    </template>
    <!-- gallery -->
    <template v-else-if="name === 'gallery'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
    </template>
    <!-- portfolio -->
    <template v-else-if="name === 'portfolio'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M2 7l10-4 10 4-10 4-10-4zM2 17l10 4 10-4M2 12l10 4 10-4"/>
    </template>
    <!-- admin -->
    <template v-else-if="name === 'admin'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.75 17L9 20l-1 1h8l-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
    </template>
    <!-- token (复用 valuation 图标) -->
    <template v-else-if="name === 'token'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002 2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
    </template>
    <!-- config -->
    <template v-else-if="name === 'config'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c-.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573-1.066c-.996.608-2.296.07-2.572-1.065c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996-.608 2.296-.07 2.572 1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
    </template>
    <!-- fire / hot -->
    <template v-else-if="name === 'fire'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.879 16.121A3 3 0 1012.015 11L11 14H9c0 .768.293 1.536.879 2.121z"/>
    </template>
    <!-- author / book -->
    <template v-else-if="name === 'author' || name === 'book'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13"/>
    </template>
    <!-- bond / trend -->
    <template v-else-if="name === 'bond'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
    </template>
    <!-- evolution / lightning -->
    <template v-else-if="name === 'evolution'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 10V3L4 14h7v7l9-11h-7z"/>
    </template>
    <!-- link -->
    <template v-else-if="name === 'link'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/>
    </template>
    <!-- rag / search -->
    <template v-else-if="name === 'rag' || name === 'search'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
    </template>
    <!-- bug -->
    <template v-else-if="name === 'bug'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8V4m3 2l-3 3m0 6l3 3M8 12h8m-5 4h2"/>
    </template>
    <!-- check -->
    <template v-else-if="name === 'check'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
    </template>
    <!-- chart (复用 valuation 图标) -->
    <template v-else-if="name === 'chart'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002 2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
    </template>
    <!-- chevron-down -->
    <template v-else-if="name === 'chevron-down'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
    </template>
    <!-- chevron-up -->
    <template v-else-if="name === 'chevron-up'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/>
    </template>
    <!-- chevron-right -->
    <template v-else-if="name === 'chevron-right'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
    </template>
    <!-- refresh / spinner -->
    <template v-else-if="name === 'refresh'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
    </template>
    <!-- close / x -->
    <template v-else-if="name === 'close'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
    </template>
    <!-- plus / add -->
    <template v-else-if="name === 'plus'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
    </template>
    <!-- minus / remove -->
    <template v-else-if="name === 'minus'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14"/>
    </template>
    <!-- send -->
    <template v-else-if="name === 'send'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M22 2L11 13"/>
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M22 2l-20 20"/>
    </template>
    <!-- more / dots -->
    <template v-else-if="name === 'more'">
      <circle cx="12" cy="12" r="1" fill="currentColor"/>
      <circle cx="12" cy="5" r="1" fill="currentColor"/>
      <circle cx="19" cy="12" r="1" fill="currentColor"/>
      <circle cx="12" cy="19" r="1" fill="currentColor"/>
      <circle cx="5" cy="12" r="1" fill="currentColor"/>
    </template>
    <!-- warning / alert -->
    <template v-else-if="name === 'warning'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-.833-2.694-.833-3.464 0L3.34 16c-.77.833.192 2.5 1.732 3z"/>
    </template>
    <!-- info -->
    <template v-else-if="name === 'info'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-2v-2m0-4h-2m0-4h4m0 4h2v2m0 4h.01M12 3a9 9 0 100 18 9 9 0 000-18z"/>
    </template>
    <!-- success / check-circle -->
    <template v-else-if="name === 'success'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
    </template>
    <!-- error / x-circle -->
    <template v-else-if="name === 'error'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 4l4-4m4 4v3a2 2 0 01-2 2h-2a2 2 0 01-2-2v-3"/>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/>
    </template>
    <!-- eye / view -->
    <template v-else-if="name === 'eye'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M1 12s4-8 9-8 9 9-4 8 9 9 4 8 9 9-4 8-9 8-9 8-4-4-4-4-4-4m4 4a5 5 0 017 7 5 5 0 007-10 0z"/>
    </template>
    <!-- download -->
    <template v-else-if="name === 'download'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16v1.5a2.5 2.5 0 015 5v1.5m0-8V10m0 0a2.5 2.5 0 015 5v5m0 0h7.5m-7.5 0H1m0-9V9.5a2.5 2.5 0 014.5 5V15"/>
    </template>
    <!-- upload -->
    <template v-else-if="name === 'upload'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 15v4a2 2 0 01-2 2H7.5m2 0h6M4 15l4 4"/>
    </template>
    <!-- external-link -->
    <template v-else-if="name === 'external-link'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M18 13v6a2 2 0 002 2h3m-3 0V5a2 2 0 002 2v4m0 0a2.5 2.5 0 015 5v3m0-5h-3m3-3h3"/>
      <polyline points="M18 13 13 21 21 21 13" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"/>
    </template>
    <!-- clock / time -->
    <template v-else-if="name === 'clock'">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/><polyline points="12 6 12.001 12 18 12 18" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"/>
    </template>
    <!-- sun (亮色模式) -->
    <template v-else-if="name === 'sun'">
      <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707"/>
    </template>
    <!-- moon (暗色模式) -->
    <template v-else-if="name === 'moon'">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
    </template>
    <!-- fallback for unknown icons -->
    <template v-else>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <text x="12" y="16" text-anchor="middle" font-size="10" fill="currentColor">{{ name?.charAt(0)?.toUpperCase() || '?' }}</text>
    </template>
  </svg>
</template>

<style scoped>
.icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
</style>
