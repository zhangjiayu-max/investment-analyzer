<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  leftActions: {
    type: Array,
    default: () => []
  },
  rightActions: {
    type: Array,
    default: () => []
  },
  disabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['action'])

const cardRef = ref(null)
const startX = ref(0)
const currentX = ref(0)
const isDragging = ref(false)
const startY = ref(0)
const isVerticalScroll = ref(false)

const maxLeftSwipe = computed(() => {
  return props.rightActions.reduce((sum, action) => sum + (action.width || 70), 0)
})

const maxRightSwipe = computed(() => {
  return props.leftActions.reduce((sum, action) => sum + (action.width || 70), 0)
})

function getActionWidth(action) {
  return action.width || 70
}

function handleTouchStart(e) {
  if (props.disabled) return
  const touch = e.touches[0]
  startX.value = touch.clientX
  startY.value = touch.clientY
  isDragging.value = true
  isVerticalScroll.value = false
}

function handleTouchMove(e) {
  if (!isDragging.value || props.disabled) return
  const touch = e.touches[0]
  const deltaX = touch.clientX - startX.value
  const deltaY = touch.clientY - startY.value

  if (!isVerticalScroll.value && Math.abs(deltaY) > Math.abs(deltaX) * 1.5) {
    isVerticalScroll.value = true
    resetPosition()
    return
  }

  if (isVerticalScroll.value) return

  e.preventDefault()

  let newX = deltaX
  if (newX > maxRightSwipe.value) newX = maxRightSwipe.value
  if (newX < -maxLeftSwipe.value) newX = -maxLeftSwipe.value

  currentX.value = newX
}

function handleTouchEnd() {
  if (!isDragging.value) return
  isDragging.value = false

  const threshold = 40

  if (currentX.value > threshold) {
    currentX.value = maxRightSwipe.value
  } else if (currentX.value < -threshold) {
    currentX.value = -maxLeftSwipe.value
  } else {
    resetPosition()
  }
}

function resetPosition() {
  currentX.value = 0
}

function handleAction(action) {
  emit('action', action)
  resetPosition()
}

onMounted(() => {
  document.addEventListener('touchmove', handleTouchMove, { passive: false })
  document.addEventListener('touchend', handleTouchEnd)
})

onUnmounted(() => {
  document.removeEventListener('touchmove', handleTouchMove)
  document.removeEventListener('touchend', handleTouchEnd)
})

const cardStyle = computed(() => ({
  transform: `translateX(${currentX.value}px)`,
  transition: isDragging.value ? 'none' : 'transform 0.25s cubic-bezier(0.34, 1.2, 0.64, 1)'
}))
</script>

<template>
  <div class="swipe-card-container">
    <div class="swipe-actions swipe-actions-left">
      <button
        v-for="(action, index) in leftActions"
        :key="index"
        @click="handleAction(action)"
        class="swipe-action-btn"
        :style="{ width: getActionWidth(action) + 'px', background: action.color }"
      >
        <span class="swipe-action-icon">{{ action.icon }}</span>
        <span class="swipe-action-label">{{ action.label }}</span>
      </button>
    </div>

    <div
      ref="cardRef"
      class="swipe-card"
      :style="cardStyle"
      @touchstart="handleTouchStart"
    >
      <slot></slot>
    </div>

    <div class="swipe-actions swipe-actions-right">
      <button
        v-for="(action, index) in rightActions"
        :key="index"
        @click="handleAction(action)"
        class="swipe-action-btn"
        :style="{ width: getActionWidth(action) + 'px', background: action.color }"
      >
        <span class="swipe-action-icon">{{ action.icon }}</span>
        <span class="swipe-action-label">{{ action.label }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.swipe-card-container {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-lg);
  margin-bottom: 0.5rem;
}

.swipe-actions {
  position: absolute;
  top: 0;
  bottom: 0;
  display: flex;
  z-index: 1;
}

.swipe-actions-left {
  left: 0;
  flex-direction: row;
}

.swipe-actions-right {
  right: 0;
  flex-direction: row-reverse;
}

.swipe-action-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: white;
  border: none;
  cursor: pointer;
  transition: opacity var(--transition-fast);
  min-width: 60px;
}

.swipe-action-btn:active {
  opacity: 0.8;
}

.swipe-action-icon {
  font-size: 1.25rem;
  margin-bottom: 0.25rem;
}

.swipe-action-label {
  font-size: 0.65rem;
  font-weight: 600;
}

.swipe-card {
  position: relative;
  z-index: 2;
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
}
</style>
