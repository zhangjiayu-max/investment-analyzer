import { ref, watch } from 'vue'

// 桌面与移动布局共用页面和通知目标，不依赖具体布局是否已挂载。
const activePage = ref(localStorage.getItem('activePage') || 'articles')
const pendingConversationId = ref(null)
watch(activePage, page => localStorage.setItem('activePage', page))
export function useAppNavigation() {
  function openConversation(convId) {
    pendingConversationId.value = convId
    activePage.value = 'chat'
  }
  function clearConversationTarget(convId) {
    if (pendingConversationId.value === convId) pendingConversationId.value = null
  }
  return { activePage, pendingConversationId, openConversation, clearConversationTarget }
}
