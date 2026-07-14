# Agent 决策质量增强 — 自我反思 + 工具广播 + Agentic RAG

**日期**：2026-07-14
**目标**：提升理财决策质量，侧重交易决策准确性与分析深度
**策略**：质量优先，默认开启，通过模型路由控成本

---

## 一、背景与问题

当前系统已具备完整的 6 专家 + 6 阶段 Pipeline + 6 层上下文 + 3 层记忆架构，但 conv 112 分析暴露 3 个质量瓶颈：

1. **专家不自省**：估值分析师引用了来源不明的券商数据（工具返回"未找到"但 analysis 中却有 PB=1.21），专家生成后无自评机制
2. **工具结果不共享**：3 个专家各自查询相同估值数据（煤炭查 3 次、券商查 3 次），6 次调用中 4 次重复（67% 浪费）
3. **RAG 被动检索**：专家仅依赖预注入的 RAG 上下文，不会根据问题自主判断信息缺口并主动发起多轮检索

---

## 二、三大能力设计

### 模块 1：单专家自我反思（Self-Reflection）

**目标**：专家生成分析后自评，发现信息缺口或逻辑不足时主动补充，提升分析深度和交易决策准确性。

#### 2.1 触发位置

`agent/multi_agent.py:743`（answer 清理完毕、返回 dict 前）

#### 2.2 反思流程

```
专家生成 analysis
  ↓
反思评估器（轻量模型 deepseek-v4-flash）
  ↓ 输出 JSON
{
  "sufficient": bool,        // 信息是否充分
  "gaps": ["未验证基金代码", "未考虑持仓影响"],  // 信息缺口
  "confidence": 0.0-1.0,     // 置信度
  "need_retry": bool,        // 是否需要重试
  "issues": ["结论缺乏数据支撑"]  // 具体问题
}
  ↓ need_retry=True 且 gaps 非空
注入补充提示："你的分析存在以下不足：{gaps}，请补充"
  ↓ 最多重试 1 次
最终 analysis（附带 reflection_score、gaps_resolved 元数据）
```

#### 2.3 评估维度

| 维度 | 检查内容 | 交易决策场景示例 |
|------|---------|----------------|
| 数据充分性 | 关键数据是否有来源、是否引用了工具未返回的数据 | 券商 PB=1.21 是否来自工具（防止 conv 112 问题） |
| 逻辑严谨性 | 结论是否有依据支撑、推理链是否完整 | "BUY"建议是否有估值+趋势+风险多重支撑 |
| 可执行性 | 操作建议是否具体（金额/比例/触发条件） | 是否给出具体加仓金额和触发百分位 |
| 持仓感知 | 是否考虑了用户现有持仓和盈亏 | 加仓建议是否检查了该基金已超 25% 上限 |

#### 2.4 模型路由

两个 model map 新增 `"self_reflection"` 键：
- `_AGENT_MODEL_MAP_DEEPSEEK["self_reflection"] = "deepseek-v4-flash"`（轻量，控成本）
- `_AGENT_MODEL_MAP_MIMO["self_reflection"] = "mimo-v2.5-pro"`（需精确推理）

#### 2.5 成本控制

- 开关 `agent.self_reflection_enabled` 默认 `true`（质量优先）
- 单专家最多重试 1 次（避免无限循环）
- 反思用 flash 模型（成本为 pro 的 1/3-1/5）
- 反思 prompt 控制在 500 tokens 内（analysis 摘要 + 评估指令）

#### 2.6 返回结构扩展

`run_specialist()` 返回 dict 新增字段：
```python
{
    ...existing fields...,
    "self_reflection": {
        "sufficient": bool,
        "confidence": float,
        "gaps_identified": [...],
        "gaps_resolved": bool,      # 重试后是否补全
        "retry_count": int,         # 0 或 1
        "reflection_score": float,  # 综合反思得分 0-1
    }
}
```

---

### 模块 2：工具结果广播（Tool Broadcast）

**目标**：专家工具调用结果结构化写入黑板，后续专家直接引用，避免重复查询，提升多专家协同效率。

#### 2.1 触发位置

`agent/multi_agent.py:685-695`（工具结果追加到 llm_messages 后）

#### 2.2 广播机制

```
专家 A 调用 query_valuation("煤炭")
  ↓ execute_tool 返回结果
轻量结构化提取
  ↓
ToolBroadcastEntry {
  tool_name: "query_valuation",
  query: "煤炭",
  caller_agent: "valuation_expert",
  key_fields: {"index_name": "中证煤炭", "PB": 1.85, "percentile": 0.80},
  raw_result: <原始JSON>,  # 供需要时完整引用
  timestamp: ...
}
  ↓ 写入 Blackboard.tool_broadcasts
后续专家 B 看到 to_context_text() 中：
  "已有工具结果：
   - 估值分析师查询了「煤炭」：PB=1.85, 百分位=80.04%
   - 基金分析师查询了「161725」：基金名称=招商中证白酒"
  ↓ 专家 B 不再重复查询煤炭估值
```

#### 2.3 结构化提取规则

按工具类型定制提取逻辑（`agent/tool_broadcast.py` 新建）：

| 工具 | 提取字段 |
|------|---------|
| query_valuation | index_name, PE, PB, percentile, snapshot_date |
| search_knowledge | top3 文档标题+摘要（每个 50 字） |
| query_portfolio | 总资产、持仓数、前 3 重仓 |
| query_fund_info | 基金名称、类型、规模 |
| ttfund_fund_info | 基金名称、经理、规模、费率 |
| 其他 | result 前 200 字（兜底） |

非白名单工具不广播（如 fetch_article 等长文本工具）。

#### 2.4 Blackboard 扩展

`agent/blackboard.py` 的 `Blackboard` 类新增：

```python
class Blackboard:
    def __init__(self, ...):
        ...
        self._tool_broadcasts: list[ToolBroadcastEntry] = []  # 新增
        self._max_broadcasts = 10  # 容量控制

    def write_tool_broadcast(self, entry: ToolBroadcastEntry):
        """写入工具广播（不占 BlackboardEntry 的 6 条配额）"""
        with self._lock:
            self._tool_broadcasts.append(entry)
            if len(self._tool_broadcasts) > self._max_broadcasts:
                self._tool_broadcasts.pop(0)  # FIFO 淘汰

    def get_tool_broadcasts(self, exclude_agent: str = None) -> list:
        """获取工具广播列表，可排除当前专家自己写的"""

    def to_context_text(self, exclude_agent: str = None) -> str:
        """扩展：在原有黑板摘要后追加工具广播区块"""
        ...原有逻辑...
        # 末尾追加
        broadcasts = self.get_tool_broadcasts(exclude_agent=exclude_agent)
        if broadcasts:
            text += "\n\n## 已有工具结果（可直接引用，无需重复查询）\n"
            for b in broadcasts:
                text += f"- {b.caller_agent_name}查询了「{b.query}」: {b.summary}\n"
        return text
```

#### 2.5 容量与性能

- tool_broadcasts 最多 10 条，FIFO 淘汰
- 结构化提取是纯 Python 正则/JSON 解析，无 LLM 调用
- to_context_text 增加约 200-400 字，在 Layer 5 的 800 字预算内

---

### 模块 3：Agentic RAG（主动检索）

**目标**：专家自主判断信息缺口，主动发起多轮检索，而非被动使用预注入的 RAG 上下文。

#### 2.1 核心改动

`agent/multi_agent.py:522-524` 的 `_ACTIVE_RETRIEVAL_INSTRUCTION` 在 pipeline 路径（`from_pipeline=True`）也注入。

当前逻辑（仅 ReAct 路径注入）：
```python
if "search_knowledge" in agent.get("tools", []):
    system_content += _ACTIVE_RETRIEVAL_INSTRUCTION
```

改为：
```python
if "search_knowledge" in agent.get("tools", []):
    system_content += _ACTIVE_RETRIEVAL_INSTRUCTION  # 所有路径都注入
```

#### 2.2 增强主动检索指令

扩展 `_ACTIVE_RETRIEVAL_INSTRUCTION` 为 3 阶段策略：

```
## 主动检索策略（Agentic RAG）

### 阶段 1：信息缺口判断
分析用户问题，列出你需要的关键信息清单。对照已提供的上下文和工具结果，
明确标注哪些信息已有、哪些还缺失。

### 阶段 2：主动检索
对每个缺失信息，使用 search_knowledge 工具主动检索：
- 第一轮：用核心关键词检索
- 若结果不足，第二轮：换同义词或扩展词检索
- 每个信息缺口最多 2 轮检索

### 阶段 3：充分性自检
检索完成后，判断信息是否充分：
- 充分 → 开始分析
- 仍不足 → 在分析中明确标注"该维度数据不足"，不要编造

### 检索场景（强制）
- 估值判断：必须检索当前估值百分位+历史对比
- 市场情绪：必须检索近期资金流向+市场温度
- 周期位置：必须检索宏观经济数据+周期分析

### 不检索场景
- 买卖操作：用 query_portfolio 查持仓
- 基金质量：用 ttfund_fund_info 查基金详情
```

#### 2.3 反思联动

模块 1 的反思评估器输出 `gaps` 时，若包含信息缺口（如"未检索煤炭板块政策"），触发专家主动检索补全：

```
反思发现 gaps=["未检索煤炭板块近期政策"]
  ↓
注入提示："你的分析缺少煤炭板块近期政策信息，请用 search_knowledge 检索"
  ↓
专家 ReAct 循环自动调用 search_knowledge("煤炭 政策")
  ↓
重新生成 analysis
```

#### 2.4 查询改写联动

复用 `agent/query_rewriter.py` 的 `expand_query()` 展开多子查询，提升召回：
- 主动检索时，专家传入的 query 会被 expand_query 展开为 2-3 个子查询
- 每个子查询独立检索，结果合并

#### 2.5 成本控制

- 复用已有 search_knowledge 工具，走工具缓存（TTL 5 分钟）
- 每专家最多 2 轮主动检索
- 检索结果写入黑板广播（模块 2），后续专家不重复检索

---

## 三、三模块协同流程

```
用户提问（交易决策类）
  ↓
Pipeline 阶段 4：专家并行执行
  ↓
专家 A（估值分析师）执行：
  ├─ [模块3] 主动检索：判断信息缺口 → search_knowledge("煤炭 估值") + search_knowledge("券商 分位")
  ├─ [模块2] 工具结果广播：煤炭PB=1.85、券商未找到 → 写入黑板
  ├─ 生成 analysis
  └─ [模块1] 自我反思：自评 → 数据充分、逻辑严谨 → 通过
专家 B（基金分析师）并行执行：
  ├─ [模块2] 读取黑板：看到煤炭PB已查、券商未找到 → 不重复查煤炭
  ├─ [模块3] 主动检索：只查券商（黑板显示未找到）→ search_knowledge("证券公司 指数 估值")
  ├─ 生成 analysis
  └─ [模块1] 自我反思：发现"未验证基金代码" → 重试补充
专家 C（风险管理师）并行执行：
  ├─ [模块2] 读取黑板：煤炭PB=1.85、券商估值已补全 → 直接引用
  ├─ 生成 analysis（含风险否决约束）
  └─ [模块1] 自我反思：通过
  ↓
Pipeline 阶段 5：综合
  ├─ 3 个专家的 reflection_score 汇总
  ├─ 黑板工具广播提供数据来源追溯
  └─ 综合回复附带"分析质量评估"
```

---

## 四、改造范围

### 4.1 新建文件

| 文件 | 作用 |
|------|------|
| `agent/self_reflection.py` | 反思评估器（评估维度+LLM 调用+JSON 解析） |
| `agent/tool_broadcast.py` | 工具广播结构化提取（ToolBroadcastEntry+提取规则） |
| `backend/tests/test_self_reflection.py` | 反思评估器单元测试 |
| `backend/tests/test_tool_broadcast.py` | 工具广播提取单元测试 |

### 4.2 修改文件

| 文件 | 改动内容 |
|------|---------|
| `agent/multi_agent.py` | 1. line 522-524：pipeline 路径也注入主动检索指令<br>2. line 685-695：工具结果广播提取+写入黑板<br>3. line 743 后：自我反思插入点<br>4. 扩展 `_ACTIVE_RETRIEVAL_INSTRUCTION` 为 3 阶段策略<br>5. 返回 dict 新增 self_reflection 字段 |
| `agent/blackboard.py` | 1. 新增 ToolBroadcastEntry dataclass<br>2. Blackboard 类新增 tool_broadcasts 列表+write_tool_broadcast+get_tool_broadcasts<br>3. to_context_text 末尾追加工具广播区块 |
| `agent/orchestrator.py` | 1. `_AGENT_MODEL_MAP_DEEPSEEK` 新增 `"self_reflection": "deepseek-v4-flash"`<br>2. `_AGENT_MODEL_MAP_MIMO` 新增 `"self_reflection": "mimo-v2.5-pro"` |
| `db/__init__.py` | orchestration_config 新增 `self_reflection_enabled=true` |
| `db/config.py` | system_config 新增 `agent.self_reflection_enabled=true` |

### 4.3 不改动的文件

- `agent/context_builder.py`：6 层架构不变，工具广播通过 Layer 5 黑板传递
- `agent/pipeline.py`：6 阶段流程不变，反思在专家内部完成
- `tools/__init__.py`：工具定义不变，复用已有 search_knowledge 和工具缓存
- `agent/query_rewriter.py`：复用已有 expand_query，不改动

---

## 五、配置项

| 配置键 | 表 | 默认值 | 说明 |
|--------|-----|--------|------|
| `self_reflection_enabled` | orchestration_config | `true` | 自我反思开关（质量优先） |
| `self_reflection_max_retry` | orchestration_config | `1` | 反思后最大重试次数 |
| `tool_broadcast_enabled` | orchestration_config | `true` | 工具广播开关 |
| `tool_broadcast_max_entries` | orchestration_config | `10` | 广播列表容量 |
| `agentic_rag_enabled` | orchestration_config | `true` | Agentic RAG 主动检索开关 |
| `agentic_rag_max_rounds` | orchestration_config | `2` | 每专家最大主动检索轮数 |

---

## 六、测试方案

### 6.1 单元测试

**`test_self_reflection.py`**：
- 反思评估器 JSON 解析容错
- 各评估维度判定逻辑
- gaps 为空时不重试
- need_retry=True 时触发重试
- 重试后 gaps_resolved 标记

**`test_tool_broadcast.py`**：
- query_valuation 结果结构化提取
- search_knowledge 结果 top3 提取
- 非白名单工具不广播
- FIFO 淘汰机制
- to_context_text 追加广播区块

### 6.2 集成验证

复现 conv 112 场景（"可买煤炭/券商/银行等低估值指数基金吗"）：
- **工具调用次数**：期望从 6 次 → 2-3 次（广播复用）
- **专家自评**：估值分析师应发现券商数据来源不明
- **主动检索**：基金分析师应主动检索券商指数估值
- **最终回复**：不再出现来源不明的数据

### 6.3 质量指标对比

| 指标 | 改造前（conv 112） | 改造后目标 |
|------|-------------------|-----------|
| 工具重复调用率 | 67%（4/6） | <20% |
| 数据来源可追溯 | 否（券商数据来源不明） | 是（黑板广播记录） |
| 专家自评覆盖率 | 0% | 100%（交易决策类） |
| 主动检索轮次 | 0（纯被动） | 每专家 1-2 轮 |
| 反思发现缺口 | 0 | ≥1/次（交易决策类） |

---

## 七、风险与降级

### 7.1 反思 LLM 调用失败

- 降级：跳过反思，直接返回原 analysis
- 标记：返回 dict 中 `self_reflection=None`，`status` 不变

### 7.2 工具广播提取失败

- 降级：跳过该工具的广播，不影响专家正常执行
- 日志：warning 级别

### 7.3 主动检索超时

- 降级：复用已有工具超时机制（30s），超时后继续分析
- 在 analysis 中标注"部分信息检索超时"

### 7.4 成本超预算

- 若 token 消耗超过预算，自动关闭反思（`self_reflection_enabled=false`）
- 通过 `cost_tracker` 监控，超阈值时动态降级

---

## 八、实施顺序

1. **模块 2：工具广播**（无 LLM 调用，最安全，先上）
2. **模块 3：Agentic RAG**（复用已有工具，改动小）
3. **模块 1：自我反思**（新增 LLM 调用，最后上）
4. **集成测试**：复现 conv 112 场景验证
5. **提交推送+重启**

每个模块独立可开关，互不依赖，可分批上线。
