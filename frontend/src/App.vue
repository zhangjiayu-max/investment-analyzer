<script setup>
import { ref, watch } from 'vue'
import Sidebar from './components/Sidebar.vue'
import Home from './views/Home.vue'
import MobileApp from './components/MobileApp.vue'
import TickerBar from './components/finance/TickerBar.vue'
import TaskNotification from './components/TaskNotification.vue'
import { useMobile } from './composables/useMobile'
import { useTaskTracker } from './composables/useTaskTracker'

const { isMobile } = useMobile()
const { completedTask, clearCompletedTask } = useTaskTracker()

const activePage = ref(localStorage.getItem('activePage') || 'articles')
const showKyc = ref(false)

watch(activePage, (val) => {
  localStorage.setItem('activePage', val)
})

// 任务完成时跳转到对话页面
function handleViewResult(convId) {
  activePage.value = 'chat'
  // 可以通过事件总线或 store 传递 convId 给 Chat 组件
  clearCompletedTask()
}
</script>

<template>
  <!-- 移动端：独立布局 -->
  <MobileApp v-if="isMobile" />

  <!-- 桌面端：交易终端布局（侧栏 + 行情顶栏 + 主区） -->
  <div v-else class="app-layout">
    <Sidebar :activePage="activePage" @navigate="activePage = $event" />
    <div class="app-content">
      <TickerBar @open-kyc="showKyc = true" />
      <main class="app-main">
        <Home :activePage="activePage" @navigate="activePage = $event" />
      </main>
    </div>
  </div>

  <!-- 任务完成通知 -->
  <TaskNotification
    :task="completedTask"
    @view="handleViewResult"
    @close="clearCompletedTask"
  />
</template>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
  min-height: 100dvh;
}

.app-content {
  flex: 1;
  margin-left: var(--sidebar-width);
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  min-height: 100dvh;
}

.app-main {
  flex: 1;
  padding: 1.25rem 1.75rem;
  transition: padding var(--transition-normal);
  overflow-x: hidden;
  background: linear-gradient(180deg, var(--color-bg) 0%, var(--color-bg) 100%);
  position: relative;
}

/* 微妙背景纹理 */
.app-main::before {
  content: '';
  position: fixed;
  top: 40px;
  left: var(--sidebar-width);
  right: 0;
  bottom: 0;
  background-image: radial-gradient(circle at 30% 20%, var(--color-primary-bg-weak) 0%, transparent 50%);
  pointer-events: none;
  z-index: 0;
  opacity: 0.6;
}

.app-main > * {
  position: relative;
  z-index: 1;
}
</style>
