# 路由系统 ADE 增强：复杂度独立判定 + 关键词扩展 + complex 下限补齐

**日期**：2026-07-18
**作者**：与用户协作设计
**影响范围**：`backend/agent/core/router.py`、`backend/agent/core/orchestrator.py`、`backend/scripts/verify_router_ade.py`、`backend/tests/test_router_complexity.py`

---

## 一、背景与问题诊断

### 1.1 现状数据（最近 10 条对话实测）

| 复杂度 | 对话数 | 平均专家数 | 当前上限 |
|---|---|---|---|
| simple | 1 | 1 | 1 |
| medium | 7 | 2.4 | 2 |
| complex | 2 | 2.0 | 4（从未触发）|

**没有任何一条对话调用过 4-5 个专家**，复杂问题反而专家更少（122 熊市判断=2，113 医疗器械估值=1）。

### 1.2 五大根因

1. **闭环反向约束（最严重）**：`router.py:357` 用"命中专家数反推复杂度"（len==1 → simple，>=3 → complex），再用复杂度截断专家数。导致"少命中的问题被强制保持少"，复杂问题因为关键词稀疏只命中 1-2 个，反推为 simple/medium，被锁死。
2. **max_specialists 默认值太保守**：medium=2 让大部分对话被砍到 2 个，router.py:323 的硬上限 5 形同虚设。
3. **保底专家占用名额**：风险管理师 9/10、资产配置师 7/10 几乎形成"保底组合"，已经占掉 3 个名额中的 2 个，留给主题专家的只剩 1 个 slot。
4. **complexity 与专家数脱钩**：complex 对话平均 2 个专家，medium 平均 2.6 个，复杂问题反而专家更少。
5. **主题专家错配**：
   - 122 熊市判断没选宏观策略师
   - 113 医疗器械估值只选了风险管理师，没选估值分析师
   - 117 医药政策利好没选行业基本面分析师
   - 116 买债券没选债券相关专家

### 1.3 用户决策

用户从五个改进方向（A/B/C/D/E）中选定 **A、D、E** 三个方向：
- **A**：修复闭环约束，复杂度独立判定
- **D**：扩展关键词覆盖
- **E**：complex 强制至少 4 个专家

复杂度判定方法：**规则计数法**（零 LLM 成本，可解释）
complex 补齐策略：**问题类型必选集**（基于 `_classify_question_type()` 结果）
max_specialists 语义调整：**仅 complex 改为下限**（simple/medium 保持上限语义）

---

## 二、设计详情

### 2.1 A 方向：复杂度独立判定

#### 2.1.1 新增函数

在 `backend/agent/core/router.py` 新增：

```python
def _classify_complexity_by_semantics(query: str, history_summary: str = "") -> str:
    """基于问题语义独立判定复杂度（不依赖命中专家数）。

    判定规则（按优先级）：
    1. complex: 满足以下任一
       - 多意图分隔符出现 ≥2 次（"和/跟/另外/同时/结合/以及"）
       - 命中 ≥2 个不同领域关键词组
       - 命中高风险关键词（清仓/满仓/梭哈/追涨/杀跌/恐慌/补仓/抄底/加杠杆）
       - 命中市场极端关键词（跌破/破位/熔断/股灾/熊市/崩盘/暴跌/新低/历史新低）
    2. medium: 命中 1 个领域 + 单一意图但需工具数据
    3. simple: 单一主题、无多意图、无高风险词

    开关：router.semantic_complexity_enabled（默认 true）
    关闭时回退到旧的 len(specialists) 反推逻辑。
    """
```

#### 2.1.2 关键修改

删除 `router.py:357` 的反推逻辑：

```python
# 旧代码（删除）
"complexity": "simple" if len(specialists_list) == 1 
               else ("complex" if len(specialists_list) >= 3 else "medium"),

# 新代码
"complexity": _classify_complexity_by_semantics(query, history_summary),
```

#### 2.1.3 领域关键词组定义

```python
_COMPLEXITY_DOMAIN_GROUPS = [
    {"估值", "PE", "PB", "百分位", "低估", "高估", "百分位低", "百分位高", 
     "历史低位", "历史高位", "低估区间", "高估区间"},
    {"风险", "回撤", "止损", "亏损", "最大回撤", "清仓", "满仓", "梭哈", 
     "追涨", "杀跌", "恐慌", "补仓", "抄底", "加杠杆"},
    {"配置", "仓位", "股债", "比例", "再平衡", "持仓", "分散", "穿透", "集中度"},
    {"市场", "大盘", "行情", "走势", "牛市", "熊市", "跌破", "破位", "熔断", 
     "股灾", "崩盘", "暴跌", "新低", "历史新低", "千股跌停"},
    {"宏观", "经济", "利率", "政策", "利好", "利空", "GDP", "PMI", "社融", 
     "M2", "CPI", "通胀", "降准", "降息"},
    {"基金", "选基", "基金分析", "基金经理", "净值", "年化收益"},
    {"债券", "国债", "利率债", "信用债", "可转债", "债基", "纯债", "短债"},
    {"医药", "医疗", "白酒", "食品饮料", "新能源", "半导体", "银行", "军工", "房地产"},
]

_MULTI_INTENT_SEPARATORS = ["和", "跟", "另外", "同时", "结合", "以及", "又", "再"]

_MARKET_EXTREME_KEYWORDS = {
    "跌破", "破位", "熔断", "股灾", "熊市", "崩盘", "暴跌", 
    "新低", "历史新低", "千股跌停"
}
```

#### 2.1.4 判定算法

```python
def _classify_complexity_by_semantics(query, history_summary=""):
    enabled = get_config_bool("router.semantic_complexity_enabled", True)
    if not enabled:
        return None  # 调用方回退到旧逻辑
    
    # 1. 多意图分隔符计数
    sep_count = sum(query.count(sep) for sep in _MULTI_INTENT_SEPARATORS)
    if sep_count >= 2:
        return "complex"
    
    # 2. 命中领域数
    hit_domains = sum(1 for group in _COMPLEXITY_DOMAIN_GROUPS 
                      if any(kw in query for kw in group))
    if hit_domains >= 2:
        return "complex"
    
    # 3. 高风险关键词
    if any(kw in query for kw in _HIGH_RISK_ACTION_KEYWORDS):
        return "complex"
    
    # 4. 市场极端关键词
    if any(kw in query for kw in _MARKET_EXTREME_KEYWORDS):
        return "complex"
    
    # 5. 单领域 → medium；零领域 → simple
    if hit_domains == 1:
        return "medium"
    return "simple"
```

---

### 2.2 D 方向：关键词覆盖扩展

#### 2.2.1 新增关键词规则

在 `router.py:57-99` 的 `_KEYWORD_ROUTES` 中插入：

```python
# D-1: 市场极端关键词（强制升级 complex）
(["跌破", "破位", "熔断", "股灾", "熊市", "崩盘", "暴跌", "新低", 
  "历史新低", "千股跌停"],
 ["market_analyst", "macro_strategist", "risk_assessor"]),

# D-2: 债券主题（当前缺失，补充）
(["债券", "国债", "利率债", "信用债", "可转债", "债基", "纯债", "短债"],
 ["fund_analyst", "macro_strategist", "valuation_expert"]),

# D-3: 政策利好（强化，补充行业基本面）
(["利好", "利空", "刺激政策", "补贴", "减税", "降准", "降息", "政策受益"],
 ["macro_strategist", "industry_fundamentalist"]),

# D-4: 估值查询增强（补口语化表达）
(["百分位低", "百分位高", "历史低位", "历史高位", "低估区间", "高估区间"],
 ["valuation_expert"]),

# D-5: 补仓抄底场景增强
(["抄底", "补仓时机", "加仓时机", "分批建仓", "左侧交易"],
 ["allocation_advisor", "risk_assessor", "valuation_expert"]),
```

#### 2.2.2 修改现有规则

```python
# 原：归因问题缺少行业视角
(["为什么涨", "为什么跌", "原因", "驱动", "归因", "怎么回事", "为何"],
 ["macro_strategist", "market_analyst"]),
# 改为：
(["为什么涨", "为什么跌", "原因", "驱动", "归因", "怎么回事", "为何"],
 ["macro_strategist", "market_analyst", "industry_fundamentalist"]),

# 原：医药问题缺少行业基本面视角
(["医药", "医疗", "生物医药", "创新药", "中药"], 
 ["macro_strategist", "valuation_expert", "fund_analyst"]),
# 改为：
(["医药", "医疗", "生物医药", "创新药", "中药"], 
 ["macro_strategist", "valuation_expert", "fund_analyst", "industry_fundamentalist"]),
```

---

### 2.3 E 方向：complex 强制下限 + 问题类型必选集

#### 2.3.1 新增 min_specialists 配置

修改 `orchestrator.py:2234-2266` 的 `get_context_config()`：

| complexity | max_specialists（上限，保持） | min_specialists（新增下限） |
|---|---|---|
| simple | 1 | 1 |
| medium | 2 | 2 |
| complex | 4 | **4** |

#### 2.3.2 截断逻辑修改

```python
# orchestrator.py 现有截断逻辑改为：
max_spec = context_config.get("max_specialists", 4)
min_spec = context_config.get("min_specialists", 1)

# P1: 截断到上限（保持原逻辑）
if len(specialists) > max_spec:
    logger.info(f"specialists 超出上限({len(specialists)} > {max_spec})，截断")
    specialists = specialists[:max_spec]

# P2: complex 下限补齐（E 方向核心新增）
if (complexity == "complex" 
    and len(specialists) < min_spec
    and get_config_bool("router.complex_min_specialists_enabled", True)):
    needed = min_spec - len(specialists)
    question_type = route_result.get("question_type", "generic")
    mandatory_pool = _QUESTION_TYPE_MANDATORY.get(question_type, 
                                                   _QUESTION_TYPE_MANDATORY["generic"])
    # 排除已在列表中的 + 过滤禁用专家
    candidates = [s for s in mandatory_pool 
                  if s not in specialists and _is_specialist_enabled(s)]
    # 按 DB eval 分数排序（高分优先）
    candidates = _sort_by_eval_score(candidates)
    # 补入
    padded = candidates[:needed]
    specialists += padded
    logger.info(f"complex 下限补齐: question_type={question_type}, "
                f"补入 {padded} (共 {len(specialists)} 个专家)")
```

#### 2.3.3 问题类型必选集

```python
_QUESTION_TYPE_MANDATORY = {
    # 操作类：补仓/抄底/止盈 → 配置+风险+行为+估值
    "action": ["allocation_advisor", "risk_assessor", 
               "behavioral_advisor", "valuation_expert"],
    # 归因类：为什么涨/跌 → 宏观+市场+行业基本面
    "attribution": ["macro_strategist", "market_analyst", 
                    "industry_fundamentalist"],
    # 预测类：未来走势 → 估值+市场+宏观
    "prediction": ["valuation_expert", "market_analyst", "macro_strategist"],
    # 对比类：A vs B → 基金+估值
    "comparison": ["fund_analyst", "valuation_expert"],
    # 通用兜底：常驻保底组合
    "generic": ["risk_assessor", "allocation_advisor", 
                "market_analyst", "valuation_expert"],
}
```

#### 2.3.4 候选排序逻辑

```python
def _sort_by_eval_score(candidates: list[str]) -> list[str]:
    """按 DB eval 分数排序候选专家（高分优先）。"""
    try:
        from db.eval import get_agent_eval_scores
        scores = get_agent_eval_scores(days=7)
        # avg_score 高的排前；无 eval 数据的排最后（不阻塞补齐）
        return sorted(candidates, 
                      key=lambda s: scores.get(s, {}).get("avg_score", 50), 
                      reverse=True)
    except Exception:
        return candidates


def _is_specialist_enabled(specialist_key: str) -> bool:
    """检查专家是否启用（受 _filter_disabled_specialists 同款开关控制）。"""
    enabled_map = {
        "industry_fundamentalist": "agent.industry_fundamentalist_enabled",
        "behavioral_advisor": "agent.behavioral_advisor_enabled",
    }
    config_key = enabled_map.get(specialist_key)
    if not config_key:
        return True
    return get_config_bool(config_key, False)
```

#### 2.3.5 配置开关

```yaml
# router_config.yaml 新增
complex_min_specialists_enabled: true   # 总开关
complex_min_specialists: 4              # 下限值（默认 4，可调到 5）
```

DB 配置示例：
```sql
INSERT INTO system_config (key, value) VALUES 
  ('router.complex_min_specialists_enabled', 'true'),
  ('router.complex_min_specialists', '4');
```

---

### 2.4 触发场景示例

| 对话 | 问题 | 语义复杂度 | 路由命中 | 原行为 | 新行为 |
|---|---|---|---|---|---|
| 122 | "沪深跌破3700 熊市" | complex（市场极端+高风险） | market+macro+risk（3） | 3 个 | 补 behavioral → 4 个 |
| 113 | "医疗涨 医疗器械涨幅少" | complex（多领域） | fund+valuation+risk（3） | 1 个（误判 simple） | 4 个 |
| 117 | "医药利好政策" | complex（政策+高风险"利好"） | macro+industry+risk（3） | 2 个 | 补 valuation+market → 4-5 个 |
| 116 | "闲置资金买债券可行吗" | medium（单领域） | fund+macro+valuation（3） | 3 个 | 2-3 个（medium 上限 2，命中已超） |

---

## 三、验证方案

### 3.1 单元测试（必须覆盖）

新增 `backend/tests/test_router_complexity.py`，覆盖：

```python
# A 方向：复杂度独立判定
def test_semantic_complexity_simple():
    """单主题无多意图 → simple"""
    assert _classify_complexity_by_semantics("沪深300现在估值多少", "") == "simple"

def test_semantic_complexity_complex_multi_intent():
    """多意图分隔符 ≥2 → complex"""
    assert _classify_complexity_by_semantics(
        "大盘跌破3700，熊市了，要不要补仓和止损", "") == "complex"

def test_semantic_complexity_complex_high_risk():
    """命中高风险关键词 → complex"""
    assert _classify_complexity_by_semantics("现在能抄底吗", "") == "complex"

def test_semantic_complexity_complex_market_extreme():
    """D 方向：市场极端关键词 → complex"""
    assert _classify_complexity_by_semantics("沪深指数跌破3700 熊市", "") == "complex"

def test_semantic_complexity_disabled_fallback():
    """开关关闭时返回 None，调用方回退到旧逻辑"""
    # 配置 router.semantic_complexity_enabled=false
    assert _classify_complexity_by_semantics("沪深300估值", "") is None

# E 方向：complex 下限补齐
def test_complex_min_specialists_padding():
    """complex 问题命中 3 个 → 补齐到 4"""
    route_result = {"specialists": ["market_analyst", "risk_assessor", "macro_strategist"],
                    "complexity": "complex", "question_type": "attribution"}
    padded = _apply_min_specialists(route_result)
    assert len(padded["specialists"]) == 4
    assert "industry_fundamentalist" in padded["specialists"]

def test_complex_min_specialists_skip_disabled():
    """禁用的专家不补入（industry_fundamentalist_enabled=false）"""
    # 配置 industry_fundamentalist_enabled=false
    # 验证补的是 generic 池的下一个候选

def test_complex_min_specialists_disabled_switch():
    """complex_min_specialists_enabled=false 时不补齐"""
    # 验证回退到仅 max_spec 截断

# D 方向：关键词扩展
def test_keyword_market_extreme_route():
    """跌破/熊市 → market+macro+risk"""
    route = _rule_route("沪深跌破3700 熊市", "", "")
    assert "market_analyst" in route["specialists"]
    assert "macro_strategist" in route["specialists"]
    assert "risk_assessor" in route["specialists"]

def test_keyword_bond_route():
    """债券 → fund+macro+valuation"""
    route = _rule_route("闲置资金买债券可行吗", "", "")
    assert "fund_analyst" in route["specialists"]
    assert "macro_strategist" in route["specialists"]
```

### 3.2 路由层验证脚本

新增 `backend/scripts/verify_router_ade.py`：

```python
test_cases = [
    ("沪深跌破3700 熊市", "complex", 
     ["market_analyst", "macro_strategist", "risk_assessor"], 4),
    ("医疗涨 医疗器械涨幅少", "complex", 
     ["fund_analyst", "valuation_expert"], 4),
    ("医药利好政策", "complex", 
     ["macro_strategist", "industry_fundamentalist"], 4),
    ("闲置资金买债券", "medium", 
     ["fund_analyst", "macro_strategist"], 2),
    ("沪深300估值多少", "simple", 
     ["valuation_expert"], 1),
]
for query, expected_complex, expected_contains, expected_count in test_cases:
    route = router.route(query, "", "")
    assert route["complexity"] == expected_complex, \
        f"复杂度错误: {query} → {route['complexity']} (期望 {expected_complex})"
    for agent in expected_contains:
        assert agent in route["specialists"], \
            f"缺少专家: {query} → {route['specialists']} (期望包含 {agent})"
    assert len(route["specialists"]) >= expected_count, \
        f"专家数不足: {query} → {len(route['specialists'])} (期望 ≥{expected_count})"
print("✅ 路由验证全部通过")
```

### 3.3 真实对话验证（上线后观测）

1. **conv 122 复现**：问"沪深跌破3700 熊市"，确认 4 个专家参与 + 包含宏观策略师
2. **对话 113 复现**：问"医疗器械估值"，确认 complex + 4 个专家 + 包含估值分析师
3. **对话 117 复现**：问"医药政策利好"，确认 4 个专家 + 包含行业基本面分析师
4. **数据观测**：`agent_runs` 表近 10 条对话平均专家数从 2.4 提升到 3.2+

---

## 四、灰度回滚方案

### 4.1 三层开关

| 层级 | 开关 | 默认 | 回滚方式 |
|---|---|---|---|
| L1 总开关 | `router.semantic_complexity_enabled` | true | 关闭即回退到旧反推逻辑 |
| L2 complex 下限 | `router.complex_min_specialists_enabled` | true | 关闭即不补齐，仅按 max 截断 |
| L3 关键词扩展 | 无独立开关 | 走 `router.enabled` | 修改 `_KEYWORD_ROUTES` 即可回滚单条规则 |

### 4.2 紧急回滚 SQL

```sql
-- 全部回滚到旧行为
INSERT INTO system_config (key, value) VALUES 
  ('router.semantic_complexity_enabled', 'false'),
  ('router.complex_min_specialists_enabled', 'false')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;
```

执行后立即生效（配置缓存 60s TTL），无需重启后端。

---

## 五、性能成本评估

| 指标 | 改动前 | 改动后 | 增量 |
|---|---|---|---|
| 路由耗时 | ~5ms | ~6ms | +1ms（多一次语义判定） |
| complex 对话专家数 | 2-3 | 4-5 | +2 |
| complex 对话 token 成本 | ~8K | ~16K | +100% |
| medium 对话专家数 | 2 | 2-3 | +0-1 |
| simple 对话专家数 | 1 | 1 | 0 |

**token 成本控制**：complex 问题占比约 20%，整体 token 增量约 +20%，可接受。

---

## 六、影响范围

### 6.1 修改文件清单

| 文件 | 改动类型 | 改动量 |
|---|---|---|
| `backend/agent/core/router.py` | 新增 `_classify_complexity_by_semantics`、扩展 `_KEYWORD_ROUTES`、删除反推逻辑 | ~80 行 |
| `backend/agent/core/orchestrator.py` | 修改 `get_context_config()` 加 min_specialists、修改截断逻辑加补齐 | ~50 行 |
| `backend/tests/test_router_complexity.py` | 新建单元测试 | ~150 行 |
| `backend/scripts/verify_router_ade.py` | 新建验证脚本 | ~60 行 |

### 6.2 不影响的范围

- 不修改专家 prompt
- 不修改专家执行逻辑（multi_agent.py）
- 不修改 RAG 检索逻辑
- 不修改工具调用逻辑
- 不修改前端

### 6.3 兼容性

- 配置开关默认 true 启用新逻辑
- 关闭开关立即回退到旧行为
- 数据库无需迁移（min_specialists 走 rag_config/system_config 表）

---

## 七、实施步骤

1. **A 方向实现**：`_classify_complexity_by_semantics` + 删除反推逻辑
2. **D 方向实现**：扩展 `_KEYWORD_ROUTES` + 修改现有规则
3. **E 方向实现**：`_QUESTION_TYPE_MANDATORY` + `_apply_min_specialists` + orchestrator 截断逻辑修改
4. **单元测试**：`test_router_complexity.py` 全部通过
5. **路由验证脚本**：`verify_router_ade.py` 全部通过
6. **DB 配置写入**：`router.semantic_complexity_enabled=true`、`router.complex_min_specialists_enabled=true`、`router.complex_min_specialists=4`
7. **后端重启** + **真实对话验证**
8. **提交推送 git**（按项目规范自动推送）

---

## 八、验收标准

- [ ] 单元测试 100% 通过
- [ ] 路由验证脚本 5 个测试用例全部通过
- [ ] conv 122 复现：问"沪深跌破3700 熊市"→ 4 个专家 + 包含宏观策略师
- [ ] 对话 113 复现：问"医疗器械估值"→ complex + 4 个专家 + 包含估值分析师
- [ ] 对话 117 复现：问"医药政策利好"→ 4 个专家 + 包含行业基本面分析师
- [ ] `agent_runs` 表近 10 条对话平均专家数从 2.4 提升到 3.2+
- [ ] 关闭 `router.semantic_complexity_enabled` 后立即回退到旧行为
- [ ] 关闭 `router.complex_min_specialists_enabled` 后立即回退到旧行为
- [ ] 提交推送 git，重启后端，前端无需重启
