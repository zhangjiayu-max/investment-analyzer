import { ref } from 'vue'

// 跨页面待处理动作状态（模块级单例，所有组件共享）
const pendingChatPrefill = ref('')       // 预填到聊天输入框的问题
const pendingTradeAction = ref(null)     // 预填到持仓操作的动作
// pendingTradeAction 结构: { type: 'buy'|'sell', fund_code: string, fund_name?: string, amount?: number, shares?: number }

export function usePendingAction() {
  function setChatPrefill(text) {
    pendingChatPrefill.value = text
  }
  function clearChatPrefill() {
    pendingChatPrefill.value = ''
  }
  function setTradeAction(action) {
    pendingTradeAction.value = action
  }
  function clearTradeAction() {
    pendingTradeAction.value = null
  }
  return {
    pendingChatPrefill,
    pendingTradeAction,
    setChatPrefill,
    clearChatPrefill,
    setTradeAction,
    clearTradeAction,
  }
}
