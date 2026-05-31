<script setup>
import { ref, watch } from 'vue'
import Sidebar from './components/Sidebar.vue'
import Home from './views/Home.vue'
import MobileApp from './components/MobileApp.vue'
import { useMobile } from './composables/useMobile'

const { isMobile } = useMobile()

const activePage = ref(localStorage.getItem('activePage') || 'articles')

watch(activePage, (val) => {
  localStorage.setItem('activePage', val)
})
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
</template>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
}

.app-main {
  flex: 1;
  margin-left: var(--sidebar-width);
  padding: 1.5rem 2rem;
  min-height: 100vh;
  transition: margin-left var(--transition-normal);
}
</style>
