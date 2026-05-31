import { ref, onMounted, onUnmounted } from 'vue'

const isMobile = ref(false)
let initialized = false

function update() {
  isMobile.value = window.innerWidth <= 768
}

export function useMobile() {
  if (!initialized) {
    update()
    initialized = true
  }

  onMounted(() => {
    window.addEventListener('resize', update)
  })

  onUnmounted(() => {
    window.removeEventListener('resize', update)
  })

  return { isMobile }
}
