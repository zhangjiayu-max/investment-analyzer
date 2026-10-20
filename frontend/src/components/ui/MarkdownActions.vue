<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { copyToClipboard } from '../../composables/useClipboard'
import { downloadMarkdown, toMarkdown } from '../../utils/markdownExport'

const props = defineProps({
  content: { type: [String, Object, Array], default: '' },
  title: { type: String, default: '分析报告' },
  createdAt: { type: String, default: '' },
  recordId: { type: [String, Number], default: null },
  disabled: { type: Boolean, default: false },
  showCopy: { type: Boolean, default: true },
  showExport: { type: Boolean, default: true },
})
const emit = defineEmits(['copied', 'exported'])
const markdown = computed(() => toMarkdown(props.content))
const feedback = ref('')
let timer
function reset() { clearTimeout(timer); feedback.value = '' }
function notify(message) {
  reset()
  feedback.value = message
  timer = setTimeout(reset, 2000)
}
watch(markdown, reset)
onBeforeUnmount(reset)

function copy() {
  if (props.disabled || !markdown.value) return
  const text = markdown.value
  copyToClipboard(text, {
    onSuccess: () => { notify('已复制'); emit('copied', text) },
    onError: () => notify('复制失败，请手动选择正文'),
  })
}
function download() {
  if (props.disabled || !markdown.value) return
  try {
    if (downloadMarkdown(markdown.value, props)) {
      notify('已导出')
      emit('exported')
    }
  } catch { notify('导出失败，请重试') }
}
</script>

<template>
  <span v-if="markdown" class="markdown-actions" @click.stop>
    <button v-if="showCopy" type="button" class="markdown-actions__copy" title="复制 Markdown 原文" :disabled="disabled" @click.stop="copy">
      <Icon name="copy" size="13" /> 复制 MD
    </button>
    <button v-if="showExport" type="button" class="markdown-actions__export" title="导出为 Markdown 文件" :disabled="disabled" @click.stop="download">
      <Icon name="download" size="13" /> 导出 MD
    </button>
    <span v-if="feedback" class="markdown-actions__feedback" role="status" aria-live="polite">{{ feedback }}</span>
  </span>
</template>

<style scoped>
.markdown-actions { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; max-width: 100%; }
.markdown-actions button { display: inline-flex; align-items: center; gap: 0.25rem; padding: 0.25rem 0.45rem; border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: var(--color-bg-input); color: var(--color-text-secondary); font: inherit; font-size: 0.72rem; white-space: nowrap; cursor: pointer; }
.markdown-actions button:hover:not(:disabled) { color: var(--color-primary); border-color: var(--color-primary-border); }
.markdown-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
.markdown-actions__feedback { font-size: 0.72rem; color: var(--color-text-secondary); }
</style>
