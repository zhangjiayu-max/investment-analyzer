import { ref } from 'vue'

const toast = ref({ visible: false, message: '', type: 'info' })
let timer = null

export function useToast() {
  function showToast(message, type = 'info') {
    if (timer) clearTimeout(timer)
    toast.value = { visible: true, message, type }
    timer = setTimeout(() => {
      toast.value.visible = false
      timer = null
    }, 3000)
  }

  function hideToast() {
    if (timer) clearTimeout(timer)
    toast.value.visible = false
  }

  return { toast, showToast, hideToast }
}
