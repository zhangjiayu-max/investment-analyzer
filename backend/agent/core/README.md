# agent/core 架构定位（2026-10-15 固化）

## 双引擎职责划分

本目录存在两套对话编排引擎，**不是冗余，而是「主路径 + 兜底 + 共享库」的职责分层**。切勿试图合并或删除其中任何一个。

| 文件 | 角色 | 行数 | 说明 |
|------|------|------|------|
| `pipeline.py` | **主路径** | ~3513 | 6 阶段确定性流水线（preprocess → info_gather → planning → execution → reflection/debate/cross-review → synthesis → memory） |
| `orchestrator.py` | **兜底 + 共享库** | ~7091 | ReAct 循环；pipeline 失败/主动降级时接管；同时提供 pipeline 复用的共享函数 |
| `multi_agent.py` | **专家执行器** | ~1784 | `run_specialist` / `run_cross_review_opinion`，两引擎共用 |
| `router.py` | **路由** | ~1000+ | 关键词 → 专家路由，两引擎共用 |

## 调用链（关键）

```
对话入口 conversations.py
  └─ orchestrate_stream()  (orchestrator.py)
       ├─ 先试 run_pipeline()  (pipeline.py, pipeline_enabled=true)
       │    └─ 各 phase 复用 multi_agent.run_specialist()
       └─ degrade/error 时降级到 ReAct 逻辑（orchestrator.py 内）
```

- 开关：`orchestration_config.pipeline_enabled`（默认 `true`）
- 降级触发：pipeline 内部 `yield {"type":"degrade"}` 或 `{"type":"error"}`

## 反向依赖（刻意保留，不可删除）

`pipeline.py` 反向 import `orchestrator.py` 的共享函数：

| 函数 | 用途 |
|------|------|
| `_get_model_for_agent` | 成本路由：按 agent_key 选分级模型 |
| `_is_cost_routing_enabled` | 成本路由开关 |
| `MODEL` | 默认模型常量 |
| `enrich_query_with_article` / `detect_urls` | 链接抓取增强 |
| `get_orchestration_config` | 读编排配置 |
| `should_run_cross_review` | 交叉审阅统一决策树（R2 后） |

> 这些是「共享工具库」性质的反向依赖，非循环耦合（orchestrator 只在运行时 import pipeline）。

## orchestrator.py 独有职责（不可下沉到 pipeline）

这些能力只有 orchestrator 有，pipeline 缺失，是降级兜底存在的意义：

1. **人机确认环**：`wait_for_confirm` / `resolve_confirm`（交易类高置信操作需用户确认）
2. **自动评测**：`_schedule_auto_evaluation` / `_schedule_tool_eval`
3. **peer review**：`run_peer_review`（被 `routers/decision/decisions.py` 引用）
4. **checkpoint 断点恢复**：`_save_checkpoint` / `_load_checkpoint` / `_load_latest_checkpoint`
5. **取消/超时**：`CancelledError` / `_check_cancel` / `_check_timeout`

## 维护守则

1. **新增专家执行能力** → 改 `multi_agent.run_specialist`，两引擎自动继承。
2. **新增路由规则** → 改 `router.py`，两引擎共用。
3. **交叉审阅触发条件** → 只改 `should_run_cross_review`（orchestrator.py），pipeline Phase 3.8 已接入（R2）。
4. **不要**在 pipeline 里复制 orchestrator 的人机确认/断点恢复逻辑——那是兜底路径专属。
5. **不要**把 `pipeline.py` 和 `orchestrator.py` 物理合并——职责不同，合并只会制造 10000+ 行的巨型文件。
