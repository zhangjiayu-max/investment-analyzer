<script setup>
import { nextTick, onBeforeUnmount, onDeactivated, ref, useId } from 'vue'
const props = defineProps({ label: { type: String, required: true }, content: { type: String, required: true }, iconOnly: { type: Boolean, default: false } })
const id = `term-tooltip-${useId()}`
const anchor = ref(null)
const bubble = ref(null)
const visible = ref(false)
const positioned = ref(false)
const position = ref({ position: 'fixed', top: '0px', left: '0px' })
let pinned = false
let focused = false
const margin = 12
const gap = 8
const clamp = (value, min, max) => Math.min(Math.max(value, min), Math.max(min, max))
function removeListeners() {
  document.removeEventListener('keydown', onKeydown)
  document.removeEventListener('pointerdown', onOutside)
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', hide)
}
function hide() {
  visible.value = false; positioned.value = false; pinned = false
  removeListeners()
}
function onKeydown(event) { if (event.key === 'Escape') { event.preventDefault(); hide() } }
function onOutside(event) { if (!anchor.value?.contains(event.target) && !bubble.value?.contains(event.target)) hide() }
function onScroll(event) { if (!(event.target instanceof Node) || !bubble.value?.contains(event.target)) hide() }
async function show() {
  if (visible.value) return
  visible.value = true
  document.addEventListener('keydown', onKeydown)
  document.addEventListener('pointerdown', onOutside)
  window.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', hide)
  await nextTick()
  if (!visible.value || !anchor.value || !bubble.value) return
  const rect = anchor.value.getBoundingClientRect()
  const tip = bubble.value.getBoundingClientRect()
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const width = Math.min(tip.width, Math.max(0, viewportWidth - margin * 2))
  const height = Math.min(tip.height, Math.max(0, viewportHeight - margin * 2))
  const above = rect.top - height - gap
  const below = rect.bottom + gap
  const preferAbove = above >= margin || (below + height > viewportHeight - margin && rect.top > viewportHeight - rect.bottom)
  position.value = {
    position: 'fixed',
    left: `${clamp(rect.left + (rect.width - width) / 2, margin, viewportWidth - width - margin)}px`,
    top: `${clamp(preferAbove ? above : below, margin, viewportHeight - height - margin)}px`,
  }
  positioned.value = true
}
function toggle() { if (pinned) hide(); else { pinned = true; show() } }
function leave() { if (!pinned && !focused) hide() }
function focus() { focused = true; show() }
function blur() { focused = false; hide() }
onDeactivated(() => { focused = false; hide() })
onBeforeUnmount(hide)
</script>
<template>
  <span ref="anchor" class="term-tooltip-label" @pointerenter="show" @pointerleave="leave">
    <slot v-if="!iconOnly">{{ label }}</slot>
    <button type="button" class="term-help-button" :aria-label="`${label}说明`" :aria-describedby="visible ? id : undefined" :aria-expanded="visible" @focus="focus" @blur="blur" @click.stop="toggle"><slot name="trigger">?</slot></button>
    <Teleport to="body">
      <div v-if="visible" :id="id" ref="bubble" role="tooltip" class="term-tooltip-bubble" :style="{ ...position, visibility: positioned ? 'visible' : 'hidden' }">{{ content }}</div>
    </Teleport>
  </span>
</template>
<style scoped>
.term-tooltip-label { display: inline-flex; align-items: center; gap: .35em; }
.term-help-button { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; width: 16px; height: 16px; padding: 0; border: 1px solid var(--color-text-muted); border-radius: 50%; color: var(--color-text-muted); background: transparent; cursor: help; font-family: inherit; font-size: 11px; font-weight: 500; line-height: 1; }
.term-help-button:hover { color: var(--color-text-primary); border-color: var(--color-primary); }
.term-help-button:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 3px; }
.term-tooltip-bubble { z-index: var(--z-lightbox, 9999); width: min(320px, calc(100vw - 24px)); max-width: calc(100vw - 24px); max-height: calc(100vh - 24px); box-sizing: border-box; overflow-y: auto; overflow-wrap: anywhere; padding: .65rem .85rem; background: var(--color-bg-card); border: 1px solid var(--color-border); border-radius: var(--radius-md); box-shadow: var(--shadow-lg); color: var(--color-text-secondary); font-size: .78rem; font-weight: 400; line-height: 1.7; white-space: pre-line; }
</style>
