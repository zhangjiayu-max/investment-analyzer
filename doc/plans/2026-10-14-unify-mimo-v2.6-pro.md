# 全系统模型统一为 mimo-v2.6-pro 设计稿

日期：2026-10-14
状态：待评审

## 目标

1. 系统所有 LLM 调用（对话 / 编排 / 专家 / 仲裁 / 辅助 / RAG 评估 / 魔鬼代言人 / 蒸馏脚本）统一使用小米 MiMo `mimo-v2.6-pro`。
2. 视觉模型（估值图片 / 六亿图片 / 钉钉图片解析、PDF OCR）同样使用 `mimo-v2.6-pro`。
3. 删除不再使用的模型与配置：DeepSeek 全系列、Qwen 全系列、Ollama 本地视觉路径。

## 现状梳理

### 当前模型分布（代码 + DB 实际生效值）

| 用途 | 当前值 | 来源 |
|------|--------|------|
| 主对话/编排/专家（MIMO 档） | mimo-v2.5-pro | `orchestrator._AGENT_MODEL_MAP_MIMO` |
| market/macro/article 三专家 | **deepseek-v4-flash**（跨 provider 残留） | 同上 |
| 仲裁 | mimo-v2.5-pro（DB `arbitration.model` 为空走默认） | llm_service |
| 分级常量 STRONG/ANALYSIS/AUX | mimo-v2.5-pro | `llm_service.py` |
| RAG 评估 | mimo-v2.5-pro | `rag_evaluator.py` |
| 魔鬼代言人 | mimo-v2.5-pro | DB `agent.devil_advocate_model` |
| **视觉（实际生效）** | **qwen3.7-plus**（DB `vision.provider=qwen`） | DB system_config |
| 保守模式（conservative） | mimo-v2.5-pro / deepseek-v4-flash 兜底 | orchestrator / cost_tracker |
| 蒸馏脚本 | 继承 `get_llm_config()` / `get_vision_config()` | distill*.py |

### 待删除的死代码/配置

- `config.py`：`DEEPSEEK_*`(3)、`ARBITRATION_*`(3)、`QWEN_*`(6)、`LLM_PROVIDER` 多 provider 分支
- `llm_service.py`：Qwen/DeepSeek 分支、独立仲裁客户端、Token Plan Qwen 启动提示
- `orchestrator.py`：`_AGENT_MODEL_MAP_DEEPSEEK`、`_AGENT_MODEL_MAP_QWEN`、`_is_model_compatible` 多 provider 逻辑
- `db/config.py` 默认值：`vision.ollama.*`(3)、`vision.qwen.*`(3)
- `cost_tracker.py` / `db/token_usage.py` / `TokenUsagePage.vue`：deepseek/qwen 计费条目
- `.env` / `.env.keys`：DEEPSEEK/ARBITRATION/QWEN 相关行
- DB 存量：`vision.qwen.*`（含一个 Qwen API Key）、`vision.ollama.*`
- `image_parser.py` ollama 解析路径 + `tests/test_image_parser_ollama.py`
- `ImageGallery.vue` 视觉 provider 切换 UI（只保留 mimo）

## 改动方案

### 1. backend/config.py（简化约 60 行）

- 删除 `DEEPSEEK_*`、`ARBITRATION_*`、`QWEN_*`、`LLM_PROVIDER`
- `MIMO_MODEL` / `MIMO_PLAN_MODEL` 默认值 → `mimo-v2.6-pro`
- `VISION_API_KEY/BASE_URL/MODEL` 默认继承 MIMO，`VISION_MODEL` 默认 `mimo-v2.6-pro`
- `get_llm_config()`：套餐 key 优先 → 普通 key（无 provider 分支）
- `get_llm_fallback_config()`：保持返回 None（沿用"失败直接异常"策略）
- `get_vision_config()` → 直接返回 MIMO 视觉配置；`get_vision_config_db()` 只读 `vision.mimo.*`
- `IMAGE_PARSER_MODEL_TYPE` 默认 `"deepseek"` → `"mimo"`

### 2. backend/services/llm/llm_service.py

- 删除 QWEN/ARBITRATION 导入与分支、Qwen Token Plan 启动提示
- `MODEL_STRONG = MODEL_ANALYSIS = MODEL_AUX = mimo-v2.6-pro`（直接等于 `MODEL`）
- 仲裁不再使用独立客户端：`_arbitration_client = client`，`_arbitration_model = MODEL`

### 3. backend/agent/core/orchestrator.py

- 删除 `_AGENT_MODEL_MAP_DEEPSEEK`、`_AGENT_MODEL_MAP_QWEN`，`AGENT_MODEL_MAP` 别名同步删除
- `_AGENT_MODEL_MAP_MIMO` 11 个角色全部 → `mimo-v2.6-pro`（含 market/macro/article 三专家，消除 deepseek 残留）
- `_get_model_for_agent()`：去 provider 分支；conservative 默认 `mimo-v2.6-pro`
- `_is_model_compatible()`：仅校验 `mimo` 前缀

### 4. 其余后端文件

| 文件 | 改动 |
|------|------|
| `agent/eval/rag_evaluator.py` | 直接返回 `mimo-v2.6-pro`，删除 provider 分支 |
| `agent/infra/self_reflection.py` | 更新 deepseek 注释 |
| `infra/cost_tracker.py` | 计费表只保留 mimo 条目 + `mimo-v2.6-pro`；conservative 默认 `deepseek-v4-flash` → `mimo-v2.6-pro` |
| `db/token_usage.py` | 计费表同上 |
| `db/config.py` | 删 `vision.ollama.*`/`vision.qwen.*` 默认值；`vision.mimo.model` → `mimo-v2.6-pro`；`agent.devil_advocate_model` → `mimo-v2.6-pro`；`arbitration.model` 描述更新；`vision.provider` 描述改为固定 mimo |
| `services/content/image_parser.py` | 删除 ollama 解析路径（约 `_resolve_vision` 中 ollama 分支） |
| `tests/test_image_parser_ollama.py` | 删除该文件（ollama 路径整体移除）或改写为 mimo 切换测试 |
| `scripts/run_eval_suite.py` | 报告文案 `mimo-v2.5-pro` → `mimo-v2.6-pro` |
| `scripts/distill.py` / `distill_period.py` | 无代码改动（自动继承新配置） |

### 5. 前端

| 文件 | 改动 |
|------|------|
| `components/quality/TokenUsagePage.vue` | 计费表只保留 mimo + `mimo-v2.6-pro` |
| `components/knowledge/ImageGallery.vue` | visionProvider 默认 `'qwen'` → `'mimo'`；移除 provider 切换 UI（如有 qwen/ollama 选项） |

前端改动后重新 `npm run build`，sw.js 缓存版本号 +1（ia-vXX 规范）。

### 6. 环境变量

- `.env.keys`：删除 `DEEPSEEK_API_KEY`、`ARBITRATION_API_KEY`、`QWEN_API_KEY`、`QWEN_VISION_API_KEY`
- `.env`：删除 `LLM_PROVIDER`、`DEEPSEEK_*`、`ARBITRATION_*`、`QWEN_*`；`MIMO_MODEL` / `MIMO_PLAN_MODEL` / `VISION_MODEL` → `mimo-v2.6-pro`

### 7. DB 存量迁移（关键，代码默认值不影响已入库数据）

```sql
UPDATE system_config SET value='mimo'          WHERE key='vision.provider';
UPDATE system_config SET value='mimo-v2.6-pro' WHERE key='vision.mimo.model';
UPDATE system_config SET value='mimo-v2.6-pro' WHERE key='agent.devil_advocate_model';
DELETE FROM system_config WHERE key LIKE 'vision.qwen.%' OR key LIKE 'vision.ollama.%';
-- cost_routing.*_model 当前均为空（走代码默认），无需迁移
```

## 风险与待确认

1. **mimo-v2.6-pro 视觉能力**：当前视觉用的是 `mimo-v2.5`（非 pro）。历史教训：窄列识别（指数温度/PE/PB 百分位）需要强视觉模型。改完后需用一张六亿估值图实测识别效果，若 pro 不支持多模态需回退视觉档为 `mimo-v2.6`（非 pro）。
2. **token-plan 套餐端点兼容性**：`token-plan-cn.xiaomimimo.com` 是否已支持 `mimo-v2.6-pro` 模型名，不支持会 404。改完后先跑一次对话冒烟测试。
3. **mimo-v2.6-pro 单价**：成本表暂沿用 v2.5 费率（input 0.3 / output 1.2 元/百万 token），如有新费率请提供。
4. **三专家模型升级成本**：market/macro/article 从 deepseek-v4-flash 升到 mimo-v2.6-pro，单次调用成本略升，但消除了跨 provider 依赖，符合统一目标。

## 验证清单

1. 后端启动无 ImportError/配置缺失
2. 对话冒烟：主对话 + 一次多专家分析（验证编排/专家/仲裁链路）
3. 视觉冒烟：六亿估值图重新识别一张，核对温度/PE/PB 百分位三列
4. 前端构建 + TokenUsagePage/ImageGallery 显示正常
5. `grep -rn "qwen\|deepseek" backend/` 仅剩历史数据兼容注释（如有）
6. 推送 git

## 回滚方案

所有改动在一个 git commit 中，DB 迁移前备份 `valuations.db` 到 `backups/`，回滚 = `git revert` + 恢复 DB 备份。
