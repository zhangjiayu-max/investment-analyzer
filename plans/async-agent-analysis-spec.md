# Agent 分析功能全异步化改造设计稿

## 日期
2026-06-20

## 背景
当前项目中所有 Agent 分析功能的触发方式分为两类：
1. **已异步**：panorama/deep-dive/trade-review/fund-analysis（后端 `asyncio.create_task` + 前端轮询状态）
2. **同步阻塞**：hotspots-analysis、daily-report/regenerate、bond-recommend、rebalancing、diversification-ai-summary、portfolio-ai、market-intelligence、eval-case-run、index-deep-analysis（前端设置 60-300 秒超时等待 HTTP 响应）

**核心问题：** 同步阻塞类分析在等待 LLM 返回期间（通常 30-120 秒），前端 axios 连接被占住，用户切换到其他页面时，新页面的数据请求排在阻塞连接后面，导致其他页面一直转圈。

## 目标
1. 所有 Agent 分析触发改为异步：立即返回 task_id，后台执行，前端轮询状态
2. 分析执行期间用户可自由切换页面，其他页面正常加载数据
3. 评测（eval）页面同样改为异步触发

## 改造范围

### 需要改造的同步 API（9 个）

| # | API 端点 | 前端函数 | 超时 | 后端 caller |
|---|---------|---------|------|------------|
| 1 | `GET /dashboard/hotspots-analysis` | `getHotspotsAnalysis()` | 90s | hotspots_analysis |
| 2 | `POST /dashboard/daily-report/regenerate` | `regenerateDailyReport()` | 120s | daily_report |
| 3 | `POST /bond/ai-recommend` | `getBondRecommend()` | 120s | bond_recommend |
| 4 | `GET /portfolio/rebalancing` | `getRebalancingSuggestion()` | - | rebalancing |
| 5 | `POST /portfolio/analysis/diversification/ai-summary` | `runDiversificationAiSummary()` | 120s | diversification_ai |
| 6 | `POST /portfolio/analysis/ai` | `runPortfolioAiAnalysis()` | 300s | portfolio_ai |
| 7 | `GET /market-intelligence/overview` | `getMarketIntelligenceOverview()` | 120s | market_intelligence |
| 8 | `POST /eval/cases/{id}/run` | `runEvalCase()` | 600s | eval_case |
| 9 | `POST /analysis/run` (index) | `runAnalysis()` | 30s | index_deep_analysis |

### 已异步的 API（保持不变，仅统一前端体验）

| # | API 端点 | 前端函数 | 状态查询 |
|---|---------|---------|---------|
| A | `POST /portfolio/analysis/panorama` | `runPanoramaAnalysis()` | `GET /panorama/{id}/status` |
| B | `POST /portfolio/analysis/deep-dive/{id}` | `runDeepDiveAnalysis()` | `GET /{id}/status` |
| C | `POST /portfolio/analysis/trade-review` | `runTradeReview()` | `GET /{id}/status` |
| D | `POST /portfolio/analysis/fund-analysis` | `runFundAnalysis()` | `GET /{id}/status` |

## 技术方案

### 统一异步任务表

新建 `async_tasks` 表，作为所有异步分析任务的统一状态跟踪：

```sql
CREATE TABLE IF NOT EXISTS async_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,        -- hotspots / daily_report / bond_recommend / ...
    caller TEXT,                    -- LLM caller 标识
    status TEXT DEFAULT 'running',  -- running / done / error
    result TEXT DEFAULT '',         -- JSON 序列化的分析结果
    error_msg TEXT DEFAULT '',
    token_usage INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
```

### 后端改造模式

每个同步 API 改为统一三步模式：

```python
# 1. 创建任务记录，立即返回 task_id
@router.post("/api/dashboard/hotspots-analysis")
async def trigger_hotspots_analysis():
    task_id = create_async_task("hotspots_analysis", caller="hotspots_analysis")
    task = asyncio.create_task(_run_hotspots_async(task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}

# 2. 后台执行，更新状态
async def _run_hotspots_async(task_id: int):
    try:
        result = ...  # 原 LLM 调用逻辑
        update_async_task(task_id, status="done", result=json.dumps(result), token_usage=tokens)
    except Exception as e:
        update_async_task(task_id, status="error", error_msg=str(e))

# 3. 新增状态查询端点
@router.get("/api/async-tasks/{task_id}/status")
async def get_async_task_status(task_id: int):
    task = get_async_task(task_id)
    return {
        "task_id": task_id,
        "status": task["status"],
        "result": json.loads(task["result"]) if task["result"] else None,
        "error": task["error_msg"],
    }
```

### 统一状态查询端点

一个通用端点查询所有异步任务状态：

```
GET /api/async-tasks/{task_id}/status
```

返回：
```json
{
    "task_id": 123,
    "status": "running | done | error",
    "result": { ... },  // done 时返回分析结果
    "error": ""          // error 时返回错误信息
}
```

### 前端改造

#### 1. 新增通用异步任务 composable

`frontend/src/composables/useAsyncTask.js`：

```javascript
import { ref } from 'vue'
import { getAsyncTaskStatus } from '../api'

export function useAsyncTask() {
  const taskState = ref('idle')  // idle | submitting | running | done | error
  const taskResult = ref(null)
  const taskError = ref('')
  const taskId = ref(null)
  let pollTimer = null

  async function start(triggerFn, { onComplete, onError } = {}) {
    taskState.value = 'submitting'
    taskResult.value = null
    taskError.value = ''
    try {
      const { data } = await triggerFn()
      taskId.value = data.task_id
      taskState.value = 'running'
      startPolling(onComplete, onError)
    } catch (e) {
      taskState.value = 'error'
      taskError.value = e.response?.data?.detail || e.message
    }
  }

  function startPolling(onComplete, onError, interval = 3000) {
    stopPolling()
    pollTimer = setInterval(async () => {
      try {
        const { data } = await getAsyncTaskStatus(taskId.value)
        if (data.status === 'done') {
          taskState.value = 'done'
          taskResult.value = data.result
          stopPolling()
          onComplete?.(data.result)
        } else if (data.status === 'error') {
          taskState.value = 'error'
          taskError.value = data.error
          stopPolling()
          onError?.(data.error)
        }
      } catch (e) {
        taskState.value = 'error'
        taskError.value = e.message
        stopPolling()
        onError?.(e.message)
      }
    }, interval)
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }

  function reset() {
    stopPolling()
    taskState.value = 'idle'
    taskResult.value = null
    taskError.value = ''
    taskId.value = null
  }

  return { taskState, taskResult, taskError, taskId, start, stopPolling, reset }
}
```

#### 2. 前端 API 改造

每个同步函数改为不设超时、立即返回：

```javascript
// 改前
export function getHotspotsAnalysis() {
  return api.get('/dashboard/hotspots-analysis', { timeout: 90000 })
}

// 改后
export function triggerHotspotsAnalysis() {
  return api.post('/dashboard/hotspots-analysis')
}

export function getAsyncTaskStatus(taskId) {
  return api.get(`/async-tasks/${taskId}/status`)
}
```

#### 3. 各页面组件改造

**Dashboard.vue - 热点分析：**
```javascript
const { taskState: hotspotState, taskResult: hotspotsResult, start: startHotspots } = useAsyncTask()

function confirmHotspots() {
  startHotspots(triggerHotspotsAnalysis, {
    onComplete: (result) => {
      hotspotsAnalysis.value = result
      autoVerifyRecommendations()
      loadRecHistory()
    }
  })
}
```

**Dashboard.vue - 全景诊断：** 已有轮询逻辑，改为统一 composable

**Dashboard.vue - 日报重新生成：** 改为 useAsyncTask

**PortfolioManagement.vue - 持仓AI分析/分散度AI/调仓建议：** 改为 useAsyncTask

**MarketIntelligence.vue - 概览分析：** 改为 useAsyncTask

**EvalSuitePage.vue - 执行测试用例：** 改为 useAsyncTask

**ValuationHistory.vue - 指数深度分析：** 已有轮询，统一为 useAsyncTask

**BondMarket.vue - 债券推荐：** 改为 useAsyncTask

#### 4. 跨页面任务感知

用户在 A 页面触发分析后切换到 B 页面，再切回 A 页面时，任务状态应自动恢复：

- `useAsyncTask` 内部通过全局 task store（pinia 或 reactive 对象）共享任务状态
- 页面 `onMounted` 时检查是否有运行中的任务，自动恢复轮询
- KeepAlive 保持页面状态不丢失

新建 `frontend/src/composables/useTaskStore.js`：

```javascript
import { reactive } from 'vue'

const tasks = reactive({})  // key: task_type, value: { taskId, state, result, error }

export function useTaskStore() {
  function setTask(type, data) {
    tasks[type] = data
  }
  function getTask(type) {
    return tasks[type]
  }
  function clearTask(type) {
    delete tasks[type]
  }
  return { tasks, setTask, getTask, clearTask }
}
```

#### 5. 前端 AgentResultCard 状态显示

利用已有的 `AgentResultCard.vue` 组件（已有 running/done/error 状态），统一展示异步分析进度：
- `submitting` → "提交中..."
- `running` → "后台分析中，可切换页面"（带进度条动画）
- `done` → 展示分析结果
- `error` → 展示错误信息 + 重试按钮

## 改造清单

### 后端文件

| 文件 | 改动 |
|------|------|
| `backend/db/__init__.py` | 新增 async_tasks 建表 + 导出 |
| `backend/db/tasks.py` | 新增 create_async_task / update_async_task / get_async_task |
| `backend/routers/dashboard.py` | hotspots-analysis 改异步（POST 触发 + 后台执行） |
| `backend/routers/dashboard.py` | daily-report/regenerate 改异步 |
| `backend/routers/portfolio.py` | rebalancing / diversification-ai-summary / portfolio-ai 改异步 |
| `backend/routers/bond.py` | bond-recommend 改异步 |
| `backend/routers/market_intelligence.py` | overview 改异步 |
| `backend/routers/eval.py` | case run 改异步 |
| `backend/routers/analysis.py` | index analysis 已异步，统一用 async_tasks 表 |
| `backend/app.py` | 新增 `GET /api/async-tasks/{task_id}/status` 通用状态查询路由 |

### 前端文件

| 文件 | 改动 |
|------|------|
| `frontend/src/composables/useAsyncTask.js` | **新建** 通用异步任务 composable |
| `frontend/src/composables/useTaskStore.js` | **新建** 全局任务状态 store |
| `frontend/src/api/index.js` | 9 个函数改名（加 trigger 前缀），去掉超时；新增 getAsyncTaskStatus |
| `frontend/src/components/Dashboard.vue` | hotspots/panorama/daily-report/rebalance 改用 useAsyncTask |
| `frontend/src/components/PortfolioManagement.vue` | portfolio-ai/diversification-ai 改用 useAsyncTask |
| `frontend/src/components/MarketIntelligence.vue` | overview 改用 useAsyncTask |
| `frontend/src/components/EvalSuitePage.vue` | case run 改用 useAsyncTask |
| `frontend/src/components/ValuationHistory.vue` | index analysis 统一用 useAsyncTask |
| `frontend/src/components/BondMarket.vue` | bond recommend 改用 useAsyncTask |

## 实施顺序

1. **后端基础设施**：async_tasks 表 + CRUD + 通用状态查询端点
2. **后端逐个改造**：按上表 9 个 API 逐个改为异步（每个保持原有业务逻辑不变，只是包装层变了）
3. **前端基础设施**：useAsyncTask composable + useTaskStore
4. **前端逐个改造**：先改 Dashboard（用户最常用），再改其他页面
5. **测试验证**：每个页面触发分析后切换到其他页面，确认其他页面正常加载

## 关键约束

- 不改变任何分析的业务逻辑（prompt、数据收集、结果保存等）
- 不改变前端展示组件（AgentResultCard、AsyncStateBlock 等）
- 已有的轮询函数（pollPanoramaStatus 等）保留兼容，新逻辑统一走 useAsyncTask
- 后台任务异常不崩溃 FastAPI 进程（已有 `_background_tasks.discard` 模式）
- 任务状态持久化到 SQLite，进程重启后可查询（但后台任务会丢失，前端显示 error）
