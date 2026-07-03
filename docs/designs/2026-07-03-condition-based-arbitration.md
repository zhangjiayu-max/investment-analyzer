# 条件式仲裁设计稿 — 从"一个答案"到"判断框架"

**日期**: 2026-07-03
**版本**: v1.0
**前置**: 已有 arbitrator（`agent/multi_agent.py:run_arbitration`）+ 决策画布（`DecisionCanvas.vue`）

---

## 1. 问题定义

### 1.1 当前仲裁的局限

```
当前仲裁输出（一个答案）：

⚖️ 最终建议：建议降低博时恒乐仓位至15%
                 ↓
用户：知道了，但 WHY？
      - 为什么是这个结论？
      - riser_assessor 不是说 "持有" 吗？
      - 如果相信风控，我应该怎么做？
      - 如果相信配置，我又该怎么做？
      - 这个建议的置信度有多高？
```

**三个核心问题：**

| 问题 | 影响 |
|------|------|
| **分歧被隐藏** | 用户不知道专家们其实意见不一致 |
| **没有条件** | 推荐是绝对化的"做X"，而不是"什么条件下做X" |
| **无法学习** | 用户只得到一个答案，没有学到"投资决策的思考框架" |

### 1.2 目标

**同一笔 LLM 调用，同一个价格，输出更有价值的内容。**

```
当前（花费 1 次 LLM 调用）：
输出：一个答案 ✅

改造后（花费 1 次 LLM 调用）：
输出：一个答案 ✅ + 分歧分析 ✅ + 条件框架 ✅ + 关键变量 ✅

不加钱，多拿 3 样东西。
```

---

## 2. 方案：条件式仲裁输出

### 2.1 改造仲裁 prompt

**核心改动**：在仲裁 prompt 中增加一个结构化输出格式要求。

```python
# agent/multi_agent.py 修改 ARBITRATION_SYSTEM_PROMPT

ARBITRATION_PROMPT_V2 = """你是投资仲裁法官（Arbitration Agent），负责综合多位投资专家的分析结果，做出最终裁决。

## 新增核心要求：输出【条件判断框架】

除了最终的裁决建议外，你必须在回答中包含以下三块内容：

### 1. 分歧根源
指出各专家之间的核心分歧是什么，以及这个分歧**来自哪个关键变量**。
例：
"核心分歧在于对债市趋势的判断：
- 日报分析认为债市温度75°, 处于偏贵区间 → 建议减仓
- 宏观分析师认为利率处于下行通道 → 建议持有
  分歧变量：**债市温度 vs 利率趋势**
  → 如果你更相信温度指标，减仓
  → 如果你更相信趋势判断，持有"

### 2. 条件判断框架（必须）
不要只给一个结论。给出**什么条件下适用什么行动**的判断框架：
例：
"| 你的判断 | 对应行动 | 置信度 |
|----------|---------|--------|
| 相信债市温度偏高 | 减仓至15% | 75% |
| 相信利率在下行通道 | 持有，设止损线 | 70% |
| 不确定，想保守 | 减半仓（至22.5%） | 85% |"

### 3. 核心权衡（一句话总结关键变量）
例：
"最终判断取决于你更看重哪个变量：**当前估值（债市温度）vs 趋势判断（利率走向）**"

## 原有的职责保持不变
1. 审查分歧
2. 数据验证
3. 逻辑裁判
4. 最终建议

[原有的交易记录硬约束等保持不变]
"""
```

**改动量**：只改 prompt 文本，不增加任何 LLM 调用。**零成本。**

### 2.2 结构化输出解析

```python
# agent/multi_agent.py 新增解析函数

import re
import json


def _parse_arbitration_output(answer: str) -> dict:
    """
    解析仲裁输出，提取结构化字段。
    
    三种解析策略：
    1. 尝试 JSON 解析（如果 LLM 输出符合格式）
    2. 正则提取（如果 LLM 输出是 markdown 表格形式）
    3. 兜底：返回原始文本 + 标记"未结构化"
    """
    result = {
        "raw_answer": answer,
        "has_structured_sections": False,
        "divergence_analysis": "",
        "condition_framework": [],
        "key_variables": "",
        "final_recommendation": "",
    }
    
    answer_stripped = answer.strip()
    
    # === 策略1：尝试 JSON 解析 ===
    json_match = re.search(r'```(?:json)?\s*({.*?})\s*```', answer_stripped, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            result["has_structured_sections"] = True
            result["divergence_analysis"] = data.get("divergence", "")
            result["condition_framework"] = data.get("conditions", [])
            result["key_variables"] = data.get("key_variables", "")
            result["final_recommendation"] = data.get("recommendation", "")
            return result
        except (json.JSONDecodeError, TypeError):
            pass
    
    # === 策略2：正则提取 markdown 表格 ===
    # 提取条件判断框架表格
    table_pattern = r'\|(.+?)\|(.+?)\|(.+?)\|'
    table_matches = re.findall(table_pattern, answer_stripped)
    
    conditions = []
    for row in table_matches:
        cols = [c.strip() for c in row]
        if len(cols) >= 3:
            # 跳过表头行（"你的判断"、"对应行动"等）
            if any(kw in cols[0] for kw in ["你的判断", "条件", "变量", "---"]):
                continue
            conditions.append({
                "condition": cols[0],
                "action": cols[1],
                "confidence": cols[2] if len(cols) > 2 else "",
            })
    
    if conditions:
        result["has_structured_sections"] = True
        result["condition_framework"] = conditions
    
    # 提取"分歧变量：XXX"或"关键变量：XXX"等关键行
    var_patterns = [
        r'(?:分歧变量|关键变量|核心变量|核心权衡)[：:]\s*(.+?)(?:。|\n|$)',
        r'(?:取决于|区别在于)\s*(.+?)(?:。|\n|$)',
        r'**([^**]+)**',
    ]
    for pat in var_patterns:
        var_match = re.search(pat, answer_stripped)
        if var_match:
            result["key_variables"] = var_match.group(1).strip()
            break
    
    # 提取最终建议（最后一段非表格内容）
    sections = re.split(r'\n\s*\n', answer_stripped)
    if sections:
        last_meaningful = None
        for sec in reversed(sections):
            if len(sec) > 20 and not sec.startswith('|') and '|' not in sec:
                last_meaningful = sec.strip()
                break
        if last_meaningful:
            result["final_recommendation"] = last_meaningful[:500]
    
    return result
```

### 2.3 结果注入决策画布

```python
# orchestrator.py 中调用 run_arbitration 后，增加解析步骤

# 当前代码（简化）：
arb_result = run_arbitration(refined_query, specialist_results, rag_context)

# 增强后：
arb_result = run_arbitration(refined_query, specialist_results, rag_context)
arb_parsed = _parse_arbitration_output(arb_result.get("analysis", ""))
arb_result["parsed_arbitration"] = arb_parsed

# 保存到 analysis_conclusions 表，供决策画布使用
if arb_parsed.get("has_structured_sections"):
    from db.analysis_conclusions import save_analysis_conclusion
    for cond in arb_parsed.get("condition_framework", []):
        save_analysis_conclusion(
            source_system="ai_dialogue",
            source_type="arbitrator",
            target_subject=cond.get("condition", ""),
            action=_infer_action_from_condition(cond),
            summary=cond.get("action", ""),
            key_variables=arb_parsed.get("key_variables", ""),
            confidence=_parse_confidence(cond.get("confidence", "50%")),
        )
```

### 2.4 前端展示增强

在 `DecisionCanvas.vue` 的关注区展示条件框架：

```
当前展示（关注区）：
⚠️ 某基金是否应减仓？
  日报说：减仓
  AI对话说：持有

增强后展示（关注区）：
⚠️ 某基金是否应减仓？
  核心分歧：债市温度(75°) vs 利率趋势(下行)
  
  ┌────────────────────────────────────┐
  │ 你的判断             对应行动  置信度 │
  ├────────────────────────────────────┤
  │ 相信债市温度偏高      减仓至15%  75%  │
  │ 相信利率在下行通道    持有设止损  70%  │
  │ 不想冒险             减半仓     85%  │
  └────────────────────────────────────┘
  
  关键变量：当前估值 vs 趋势判断
```

```vue
<!-- DecisionCanvas.vue 新增组件 -->
<template>
  <div v-if="conditions.length" class="condition-framework">
    <div class="section-title">🧩 条件判断框架</div>
    <div v-if="keyVariable" class="key-variable">
      关键变量：<strong>{{ keyVariable }}</strong>
    </div>
    <table class="condition-table">
      <thead>
        <tr>
          <th>你的判断</th>
          <th>对应行动</th>
          <th>置信度</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="c in conditions" :key="c.condition">
          <td>{{ c.condition }}</td>
          <td><span class="action-tag">{{ c.action }}</span></td>
          <td>
            <span :class="confidenceLevel(c.confidence)">
              {{ c.confidence }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
```

---

## 3. 触发条件

条件式仲裁不是每次都需要。以下情况触发**条件式输出**：

```python
def should_use_conditional_arbitration(specialist_results: list) -> bool:
    """
    条件框架输出的触发条件。
    
    规则：当专家结论存在实质分歧时启用。
    """
    if len(specialist_results) < 2:
        return False
    
    # 检查方向分歧
    actions = set()
    for sr in specialist_results:
        action = _detect_action(sr.get("analysis", ""))
        if action:
            actions.add(action)
    
    # 同时存在 bullish 和 bearish 方向 → 有分歧 → 启用
    has_bullish = any(a in {"buy", "increase", "hold"} for a in actions)
    has_bearish = any(a in {"sell", "decrease", "clear"} for a in actions)
    
    return has_bullish and has_bearish
```

如果专家们意见一致，仲裁者按原来的方式输出单一建议即可。**不增加复杂度。**

---

## 4. 数据模型扩展

```sql
-- 新增：条件框架记录表
CREATE TABLE IF NOT EXISTS arbitration_conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arbitration_id INTEGER REFERENCES analysis_conclusions(id),
    condition TEXT NOT NULL,          -- '相信债市温度偏高'
    action TEXT NOT NULL,             -- '减仓至15%'
    confidence REAL DEFAULT 0.7,      -- 置信度
    key_variable TEXT,                -- '债市温度 vs 利率趋势'
    user_selected INTEGER DEFAULT 0, -- 用户最终选择了哪个条件
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 新增：用户选择追踪
CREATE TABLE IF NOT EXISTS user_decision_choices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id INTEGER REFERENCES arbitration_conditions(id),
    user_action TEXT,                 -- 用户实际执行了什么
    outcome TEXT,                     -- 执行结果（后续追踪）
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```

**这些表做什么的？**

| 表 | 用途 |
|------|------|
| `arbitration_conditions` | 记录仲裁者给出的多个条件选项 |
| `user_decision_choices` | 记录用户最终选了哪个选项、执行了什么操作 |

长期来看，这些数据可以回馈到**准确率统计**——"在条件A下，建议B的准确率是X%"。

---

## 5. 改动点汇总

| 位置 | 改动 | 行数 | 成本变化 |
|------|------|------|---------|
| `agent/multi_agent.py` | 修改仲裁 prompt + 新增解析函数 | ~120 行 | 零（同一笔 LLM） |
| `agent/orchestrator.py` | 调用处增加解析 + 存入结论表 | ~20 行 | 零 |
| `frontend/src/components/DecisionCanvas.vue` | 新增条件框架展示 | ~80 行 | 零 |
| `db/__init__.py` | 新增 2 张表 | ~20 行 | 零 |
| 总计 | | **~240 行** | **零成本** |

---

## 6. 效果对比

### 改造前（用户看到）：

```
⚖️ 最终建议：建议降低博时恒乐仓位至15%

用户：好的...（但不知道为什么，下次遇到类似情况还是不会判断）
```

### 改造后（用户看到）：

```
⚖️ 最终建议：建议降低博时恒乐仓位至15%

🧩 核心分歧
日报分析认为债市温度75°偏贵 → 减仓
宏观分析师认为利率下行 → 持有
→ 分歧变量：**债市温度 vs 利率趋势**

🧩 条件判断框架
| 你的判断              | 对应行动    | 置信度 |
|----------------------|------------|-------|
| 相信债市温度偏高       | 减仓至15%  | 75%   |
| 相信利率在下行通道     | 持有设止损  | 70%   |
| 不确定，想保守         | 减半仓     | 85%   |

📖 核心权衡：这次决策教会你——估值高位不一定是卖出点，趋势向下才是。
下次遇到类似情况，先问自己：驱动市场的核心变量是估值还是趋势？
```

### 对比维度

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| LLM 调用次数 | 1 次 | 1 次（不变） |
| 用户能学到什么 | 得到一个答案 | 学到一套判断框架 |
| 下次类似场景 | 还是不知道怎么做 | 有框架可复用 |
| 对决策的信任 | "谁对谁错？" | "分歧来自XX变量，我理解了" |
| 数据积累 | 无 | 可追踪用户选择 → 回馈准确率 |

---

## 7. 实现步骤

### Step 1：修改仲裁 prompt

```python
# 替换 ARBITRATION_SYSTEM_PROMPT
# 在末尾增加结构化输出要求
# 核心：多输出一个"条件判断框架"表格
```

**行数**：~30 行 prompt 修改
**测试方法**：手动触发一次仲裁，看输出是否包含表格

### Step 2：新增解析函数

```python
# _parse_arbitration_output()
# 3 种解析策略：JSON → 正则 → 兜底
```

**行数**：~80 行
**测试方法**：用样本输出测试 3 种解析路径是否都覆盖

### Step 3：orchestrator 集成

```python
# 在 run_arbitration 后调用解析
# 将条件存入 analysis_conclusions
```

**行数**：~20 行

### Step 4：前端展示

```vue
# DecisionCanvas.vue 新增条件框架组件
# 关注区改版：从纯文本改为表格展示
```

**行数**：~80 行

### Step 5：可选 — 用户选择追踪

```sql
# 新增 2 张表
# 在用户点击某个条件时记录选择
```

**行数**：~20 行 SQL + ~20 行前端

---

## 8. 与已有功能的关系

```
现有系统         条件式仲裁的接入点
────────────────────────────────────
multi_agent.py    改 prompt + 增加解析
orchestrator.py   调用后处理
analysis_conclusions.py 存入条件（自动化）
DecisionCanvas.vue     展示条件框架（新增组件）
BadCasePage.vue   如果仲裁输出质量差 → 自动捕获
```

**不修改**：
- `router.py`（路由不受影响）
- `cost_tracker.py`（成本不变）
- `validator.py`（验证逻辑不变）
- `query_rewriter.py`（改写逻辑不变）

---

## 9. 风险与应对

| 风险 | 应对 |
|------|------|
| LLM 输出格式不统一 | 3 层解析兜底，最差回退到原始文本 |
| 条件框架分散用户注意力 | 折叠展示，默认展开 |
| 用户不习惯多选项 | 保持"最终建议"显眼位置，条件框架作为辅助信息 |
| 条件太多（>5） | 只展示置信度最高的前 3 个条件 |