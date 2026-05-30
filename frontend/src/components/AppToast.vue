<script setup>
import { useToast } from '../composables/useToast'

const { toast, hideToast } = useToast()
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="toast.visible" class="toast-container" @click="hideToast">
        <div :class="['toast', `toast-${toast.type}`]">
          <svg v-if="toast.type === 'success'" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
          </svg>
          <svg v-else-if="toast.type === 'error'" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
          <svg v-else width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
          <span>{{ toast.message }}</span>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
