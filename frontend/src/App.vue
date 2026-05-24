<script setup>
import { ref, watch } from 'vue'
import Sidebar from './components/Sidebar.vue'
import Home from './views/Home.vue'

const activePage = ref(localStorage.getItem('activePage') || 'articles')

watch(activePage, (val) => {
  localStorage.setItem('activePage', val)
})
</script>

<template>
  <div class="app-layout">
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

@media (max-width: 768px) {
  .app-main {
    margin-left: 0;
    padding: 1rem;
    padding-bottom: 5rem;
  }
}
</style>
