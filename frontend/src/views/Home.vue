<script setup>
import { ref } from 'vue'
import { createTask } from '../api'
import TaskList from '../components/TaskList.vue'
import TaskDetail from '../components/TaskDetail.vue'
import ArticleManagement from '../components/ArticleManagement.vue'
import ValuationHistory from '../components/ValuationHistory.vue'
import ImageGallery from '../components/ImageGallery.vue'
import ChatView from '../components/ChatView.vue'
import AuthorArticles from '../components/AuthorArticles.vue'
import LinkedArticles from '../components/LinkedArticles.vue'
import BondMarket from '../components/BondMarket.vue'
import RagAnalysis from '../components/RagAnalysis.vue'

const props = defineProps({
  activePage: String,
})
const emit = defineEmits(['navigate'])

const taskListRef = ref(null)
const currentTaskId = ref(null)
const url = ref('')
const submitting = ref(false)

async function onSubmit() {
  const trimmed = url.value.trim()
  if (!trimmed) return
  submitting.value = true
  try {
    const { data } = await createTask(trimmed)
    currentTaskId.value = data.task_id
    url.value = ''
    emit('navigate', 'analysis')
    taskListRef.value?.loadTasks()
  } catch (e) {
    alert('创建任务失败: ' + e.message)
  } finally {
    submitting.value = false
  }
}

function onSelectTask(taskId) {
  currentTaskId.value = taskId
  emit('navigate', 'analysis')
}

function onNewTask() {
  currentTaskId.value = null
}

function onBack() {
  currentTaskId.value = null
}
</script>

<template>
  <div class="home">
    <!-- AI 对话页 -->
    <div v-if="activePage === 'chat'" class="page-section">
      <ChatView />
    </div>

    <!-- 文章管理页 -->
    <div v-if="activePage === 'articles'" class="page-section">
      <ArticleManagement />
    </div>

    <!-- AI 分析页 -->
    <div v-if="activePage === 'analysis'" class="page-section">
      <div v-if="!currentTaskId" class="card analysis-card">
        <div class="analysis-header">
          <h2 class="page-title">AI 投资分析</h2>
          <p class="page-desc">粘贴公众号文章链接，AI 自动解读投资机会</p>
        </div>
        <form @submit.prevent="onSubmit" class="analysis-form">
          <input
            v-model="url"
            type="url"
            placeholder="https://mp.weixin.qq.com/s/..."
            class="input-field analysis-input"
            :disabled="submitting"
          />
          <button
            type="submit"
            :disabled="submitting || !url.trim()"
            class="btn-primary analysis-btn"
          >
            <svg v-if="submitting" class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/>
            </svg>
            {{ submitting ? '提交中...' : '开始分析' }}
          </button>
        </form>
        <p class="analysis-hint">支持微信公众号文章链接</p>

        <!-- 任务列表 -->
        <div class="task-list-section">
          <TaskList
            ref="taskListRef"
            @select="onSelectTask"
            @newTask="onNewTask"
          />
        </div>
      </div>

      <!-- 任务详情 -->
      <TaskDetail
        v-else
        :taskId="currentTaskId"
        @back="onBack"
      />
    </div>

    <!-- 估值数据页 -->
    <div v-if="activePage === 'valuation'" class="page-section">
      <h2 class="page-title">估值数据</h2>
      <p class="page-desc" style="margin-bottom: 1.5rem;">查看指数估值历史和分析</p>
      <ValuationHistory />
    </div>

    <!-- 图片浏览页 -->
    <div v-if="activePage === 'gallery'" class="page-section">
      <h2 class="page-title">图片浏览</h2>
      <p class="page-desc" style="margin-bottom: 1.5rem;">搜索和浏览所有已解析的估值图片</p>
      <ImageGallery />
    </div>

    <!-- 文章库页 -->
    <div v-if="activePage === 'author'" class="page-section">
      <AuthorArticles />
    </div>

    <!-- 链接文章页 -->
    <div v-if="activePage === 'linked'" class="page-section">
      <LinkedArticles />
    </div>

    <!-- RAG 分析页 -->
    <div v-if="activePage === 'rag'" class="page-section">
      <RagAnalysis />
    </div>

    <!-- 债市市场温度页 -->
    <div v-if="activePage === 'bond'" class="page-section">
      <BondMarket />
    </div>
  </div>
</template>

<style scoped>
.home {
  max-width: 1200px;
  margin: 0 auto;
}

.page-section {
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.page-title {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}

.page-desc {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  margin: 0.25rem 0 0 0;
}

/* Analysis card */
.analysis-card {
  padding: 0;
  overflow: hidden;
}

.analysis-header {
  text-align: center;
  padding: 2.5rem 2rem 1.5rem;
}

.analysis-form {
  display: flex;
  gap: 0.75rem;
  max-width: 640px;
  margin: 0 auto;
  padding: 0 2rem;
}

.analysis-input {
  flex: 1;
  padding: 0.75rem 1rem;
  font-size: 0.9rem;
}

.analysis-btn {
  padding: 0.75rem 1.5rem;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.analysis-hint {
  text-align: center;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin: 0.75rem 0 0 0;
  padding-bottom: 1.5rem;
}

.task-list-section {
  border-top: 1px solid var(--color-border);
}

@media (max-width: 640px) {
  .analysis-form {
    flex-direction: column;
    padding: 0 1.5rem;
  }
  .analysis-btn {
    justify-content: center;
  }
}
</style>
