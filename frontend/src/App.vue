<script setup>
import { ref, watch } from 'vue'
import Sidebar from './components/Sidebar.vue'
import Home from './views/Home.vue'
import MobileApp from './components/MobileApp.vue'
import TaskNotification from './components/TaskNotification.vue'
import { useMobile } from './composables/useMobile'
import { useTaskTracker } from './composables/useTaskTracker'

const { isMobile } = useMobile()
const { completedTask, clearCompletedTask } = useTaskTracker()

const activePage = ref(localStorage.getItem('activePage') || 'articles')

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

  <!-- 桌面端：原有布局 -->
  <div v-else class="app-layout">
    <Sidebar :activePage="activePage" @navigate="activePage = $event" />
    <main class="app-main">
      <Home :activePage="activePage" @navigate="activePage = $event" />
    </main>
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

.app-main {
  flex: 1;
  margin-left: var(--sidebar-width);
  padding: 1.5rem 2rem;
  min-height: 100vh;
  min-height: 100dvh;
  transition: margin-left var(--transition-normal);
  overflow-x: hidden;
}
</style>
