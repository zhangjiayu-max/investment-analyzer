# Agent 架构升级设计稿 — 工具注册中心 / A2A 协议 / Plan & Execute

> 日期：2026-07-07  
> 范围：3 项架构升级的完整设计方案，含数据模型、代码改动、API、前端  
> 原则：每次升级可独立交付，渐进式增强，不破坏现有功能

---

## 一、总览

| 升级项 | 目标 | 优先级 | 复杂度 | 依赖 |
|--------|------|--------|--------|------|
| 1. 工具注册中心 + DB 配置 | 工具集中管理，agent 工具配置可动态调整 | P0 | 中 | 无 |
| 2. JSON 结构化输出 + A2A 协议 | Agent 间标准化通信，输出可解析可审计 | P1 | 高 | 升级1 |
| 3. Plan & Execute + ReAct 编排 | 先规划再执行，可审计可中断可重规划 | P1 | 高 | 升级1 |

---

## 二、升级 1：工具注册中心 + DB 配置

### 2.1 现状

```
tools/__init__.py          ← TOOLS 全局列表（33 个工具，硬编码）
agent/multi_agent.py       ← SPECIALIST_AGENTS 字典（硬编码每个 agent 的 tools 列表）
db/agents.py               ← load_specialist_agents() 从硬编码字典读取
db/core.py                 ← analysis_agents 表无 agent_key/tools 列
```

**问题**：
- 给某个 agent 加减工具需要改 `multi_agent.py` 重新部署
- 工具定义和 agent 绑定混在一起，无法在运行时热更新
- `analysis_agents` 表结构不完整，无法作为配置系统使用

### 2.2 目标

1. 启动时一次性初始化全局工具注册表（ToolRegistry 单例）
2. Agent 的工具配置存入数据库，支持运行时动态调整
3. `run_specialist()` 从数据库拉取 agent 的工具列表，按需过滤

### 2.3 数据模型变更

```sql
-- 扩展 analysis_agents 表
ALTER TABLE analysis_agents ADD COLUMN agent_key TEXT UNIQUE;
ALTER TABLE analysis_agents ADD COLUMN tools TEXT;  -- JSON 数组: ["query_valuation","search_knowledge"]

-- 新建工具注册表（审计用）
CREATE TABLE IF NOT EXISTS tool_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'general',
    enabled INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    registered_at TEXT DEFAULT (datetime('now','localtime'))
);
```

### 2.4 核心类设计

```python
# backend/tools/tool_registry.py（新建）

import json
from typing import Optional
from dataclasses import dataclass, field

@dataclass
class ToolDefinition:
    """单个工具定义"""
    name: str
    description: str
    parameters: dict
    category: str = "general"
    enabled: bool = True
    timeout: int = 30

class ToolRegistry:
    """全局工具注册中心 — 启动时初始化一次，运行时只读"""
    
    _instance: Optional["ToolRegistry"] = None
    _tools: dict[str, ToolDefinition] = {}
    _openai_tools: list[dict] = []  # 缓存 OpenAI function calling 格式
    
    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        if cls._instance is None:
            raise RuntimeError("ToolRegistry 未初始化，请先调用 ToolRegistry.initialize()")
        return cls._instance
    
    @classmethod
    def initialize(cls, db_path: str = None):
        """启动时调用一次，加载所有工具并同步到 DB"""
        if cls._instance is not None:
            return cls._instance
        
        cls._instance = cls()
        cls._instance._load_builtin_tools()
        if db_path:
            cls._instance._sync_to_db(db_path)
        return cls._instance
    
    def _load_builtin_tools(self):
        """从 tools/__init__.py 的 TOOLS 列表加载所有内置工具"""
        from tools import TOOLS  # 现有的全局工具列表
        for t in TOOLS:
            fn = t["function"]
            self._tools[fn["name"]] = ToolDefinition(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=fn.get("parameters", {}),
            )
        self._rebuild_openai_tools()
    
    def _sync_to_db(self, db_path: str):
        """将工具注册到 tool_registry 表（新增工具自动 INSERT）"""
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for name, td in self._tools.items():
            conn.execute(
                "INSERT OR IGNORE INTO tool_registry (name, category, description) VALUES (?, ?, ?)",
                (name, td.category, td.description)
            )
        conn.commit()
        conn.close()
    
    def _rebuild_openai_tools(self):
        """重建 OpenAI function calling 格式的工具列表"""
        self._openai_tools = []
        for td in self._tools.values():
            if td.enabled:
                self._openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": td.name,
                        "description": td.description,
                        "parameters": td.parameters,
                    },
                })
    
    def get_tools_for_agent(self, tool_names: list[str]) -> list[dict]:
        """根据 agent 的工具名列表，返回过滤后的 OpenAI 格式工具列表"""
        name_set = set(tool_names)
        return [t for t in self._openai_tools
                if t["function"]["name"] in name_set]
    
    def get_all_tools(self) -> list[dict]:
        """返回所有已启用的工具（OpenAI 格式）"""
        return self._openai_tools
    
    def list_tool_names(self) -> list[str]:
        """返回所有工具名称列表"""
        return [t["function"]["name"] for t in self._openai_tools]
    
    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)
    
    def enable_tool(self, name: str):
        td = self._tools.get(name)
        if td:
            td.enabled = True
            self._rebuild_openai_tools()
    
    def disable_tool(self, name: str):
        td = self._tools.get(name)
        if td:
            td.enabled = False
            self._rebuild_openai_tools()
```

### 2.5 代码改动

#### 2.5.1 `backend/app.py` — 启动时初始化

```python
# 在 startup 事件中（在 init_db() 之后）
from tools.tool_registry import ToolRegistry
from config import DB_PATH

ToolRegistry.initialize(db_path=DB_PATH)
logger.info(f"ToolRegistry 初始化完成，已注册 {len(ToolRegistry.get_instance().list_tool_names())} 个工具")
```

#### 2.5.2 `backend/db/agents.py` — 扩展数据库操作

```python
def load_specialist_agents() -> dict:
    """从数据库加载所有活跃的专家 Agent（含工具配置）"""
    from db.core import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT agent_key, name, description, icon, system_prompt, tools, model_type "
        "FROM analysis_agents WHERE is_active = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    
    if not rows:
        # 降级：从硬编码加载（兼容旧数据）
        return _load_from_hardcoded()
    
    agents = {}
    for r in rows:
        key = r["agent_key"]
        agents[key] = {
            "agent_key": key,
            "name": r["name"],
            "description": r["description"],
            "icon": r.get("icon", "robot"),
            "system_prompt": r["system_prompt"],
            "tools": json.loads(r["tools"] or "[]"),
            "model_type": r.get("model_type"),
        }
    return agents


def get_agent_tools(agent_key: str) -> list[str]:
    """获取指定 agent 的工具列表"""
    from db.core import _get_conn
    conn = _get_conn()
    row = conn.execute(
        "SELECT tools FROM analysis_agents WHERE agent_key = ? AND is_active = 1",
        (agent_key,)
    ).fetchone()
    conn.close()
    if row and row["tools"]:
        return json.loads(row["tools"])
    return []


def update_agent_tools(agent_key: str, tools: list[str]) -> bool:
    """更新 agent 的工具配置"""
    from db.core import _get_conn
    conn = _get_conn()
    conn.execute(
        "UPDATE analysis_agents SET tools = ?, updated_at = datetime('now','localtime') "
        "WHERE agent_key = ?",
        (json.dumps(tools, ensure_ascii=False), agent_key)
    )
    conn.commit()
    conn.close()
    return True
```

#### 2.5.3 `backend/agent/multi_agent.py` — 改用 ToolRegistry

```python
# 删除原来的第 425 行：
# agent_tools = [t for t in TOOLS if t["function"]["name"] in agent["tools"]]

# 替换为：
from tools.tool_registry import ToolRegistry
registry = ToolRegistry.get_instance()
agent_tools = registry.get_tools_for_agent(agent["tools"])
```

#### 2.5.4 `backend/agent/orchestrator.py` — `build_orchestrator_tools()`

`build_orchestrator_tools()` 不需要改动 —— 它动态生成 `consult_*` 工具，不是从 ToolRegistry 取。但 `execute_tool()` 的执行路径保持不变。

#### 2.5.5 `backend/db/core.py` — 添加迁移

```python
# 在 init_db() 中追加
def _migrate_analysis_agents():
    """迁移：为 analysis_agents 表添加 agent_key 和 tools 列"""
    conn = _get_conn()
    try:
        conn.execute("ALTER TABLE analysis_agents ADD COLUMN agent_key TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE analysis_agents ADD COLUMN tools TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE analysis_agents ADD COLUMN icon TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE analysis_agents ADD COLUMN model_type TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()
```

#### 2.5.6 数据迁移脚本 — 将硬编码 agent 同步到 DB

```python
# backend/scripts/migrate_agents_to_db.py（新建，一次性运行）

"""将硬编码的 SPECIALIST_AGENTS 同步到 analysis_agents 表"""
import json
from db.core import _get_conn
from agent.multi_agent import SPECIALIST_AGENTS

def migrate():
    conn = _get_conn()
    for key, info in SPECIALIST_AGENTS.items():
        existing = conn.execute(
            "SELECT id FROM analysis_agents WHERE agent_key = ?", (key,)
        ).fetchone()
        if existing:
            # 更新 tools 列
            conn.execute(
                "UPDATE analysis_agents SET tools = ?, updated_at = datetime('now','localtime') "
                "WHERE agent_key = ?",
                (json.dumps(info["tools"], ensure_ascii=False), key)
            )
            print(f"  更新: {key}")
        else:
            # 插入新行
            conn.execute(
                "INSERT INTO analysis_agents (agent_key, name, description, icon, system_prompt, tools, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)",
                (key, info["name"], info["description"], info.get("icon", ""),
                 info["system_prompt"], json.dumps(info["tools"], ensure_ascii=False))
            )
            print(f"  新增: {key}")
    conn.commit()
    conn.close()
    print("迁移完成")

if __name__ == "__main__":
    migrate()
```

### 2.6 API 变更

```python
# backend/app.py — 新增 2 个管理 API

@app.get("/api/admin/tools")
def list_tools():
    """列出所有可用工具（供前端管理页面使用）"""
    registry = ToolRegistry.get_instance()
    tools = registry.list_tool_names()
    return {"tools": tools, "total": len(tools)}

@app.get("/api/admin/agents/{agent_key}/tools")
def get_agent_tools_api(agent_key: str):
    """获取指定 agent 的工具配置"""
    tools = get_agent_tools(agent_key)
    all_tools = ToolRegistry.get_instance().list_tool_names()
    return {"agent_key": agent_key, "tools": tools, "available_tools": all_tools}

@app.put("/api/admin/agents/{agent_key}/tools")
def update_agent_tools_api(agent_key: str, tools: list[str]):
    """更新 agent 的工具配置"""
    # 验证工具名有效性
    valid_tools = set(ToolRegistry.get_instance().list_tool_names())
    invalid = [t for t in tools if t not in valid_tools]
    if invalid:
        raise HTTPException(400, f"无效工具名: {invalid}")
    update_agent_tools(agent_key, tools)
    return {"ok": True, "agent_key": agent_key, "tools": tools}
```

### 2.7 前端改动

在管理页面新增"Agent 工具配置"区域：
- 展示所有 agent 和当前工具配置
- 支持 checkbox 勾选/取消工具
- 保存后调用 `PUT /api/admin/agents/{agent_key}/tools`

### 2.8 风险与降级

- **ToolRegistry 未初始化**：`run_specialist()` 中 catch 异常，降级到硬编码 `TOOLS` 列表
- **DB 中无 agent 配置**：`load_specialist_agents()` 降级到 `SPECIALIST_AGENTS` 硬编码
- **工具不存在**：API 层校验工具名有效性，拒绝无效配置

---

## 三、升级 2：JSON 结构化输出 + A2A 协议

### 3.1 现状

```
run_specialist() 返回:
{
    "agent": "估值专家",
    "icon": "📊",
    "analysis": "根据当前PE分位30%...（纯文本markdown）",
    "tool_calls": [...],
    "duration_ms": 1234
}
```

**问题**：
- `analysis` 是纯文本，无法被其他 agent 解析引用
- 交叉审阅时 agent 只能看到文本，无法获取结构化数据（如置信度、数据来源）
- 无法做 A2A（Agent-to-Agent）定向通信

### 3.2 目标

1. 每个 agent 输出混合格式：结构化 JSON 头部 + 自然语言分析体
2. 定义标准 A2A 消息协议，支持 agent 间定向引用
3. 向后兼容：纯文本输出仍可正常工作

### 3.3 输出 Schema

```python
# backend/agent/message_protocol.py（新建）

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

class AgentRole(str, Enum):
    ANALYST = "analyst"       # 分析型专家
    ADVISOR = "advisor"       # 建议型专家（理财顾问）
    REVIEWER = "reviewer"     # 审阅型专家（反方观点、交叉审阅）
    ARBITRATOR = "arbitrator" # 仲裁法官

class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    ADD = "ADD"       # 加仓
    REDUCE = "REDUCE" # 减仓
    WATCH = "WATCH"   # 关注

@dataclass
class AgentOutput:
    """Agent 输出标准结构"""
    # 结构化元数据
    agent_key: str
    agent_name: str
    agent_role: AgentRole
    trace_id: str
    
    # 分析结论
    conclusion: str                    # 一句话结论（50字以内）
    action_signals: list[dict] = field(default_factory=list)
    # 例: [{"type": "BUY", "target": "中证500", "confidence": 0.72, "urgency": "medium"}]
    
    # 数据引用（用于 A2A 交叉验证）
    data_references: list[dict] = field(default_factory=list)
    # 例: [{"source": "index_valuations", "index_code": "000905", "metric": "PE_TTM", "value": 12.3}]
    
    # 置信度
    confidence: float = 0.0           # 0-1，整体置信度
    confidence_breakdown: dict = field(default_factory=dict)
    # 例: {"data_quality": 0.9, "analysis_depth": 0.7, "uncertainty": 0.3}
    
    # 分析体（自然语言）
    analysis_markdown: str = ""       # 完整的 markdown 分析文本
    
    # 工具调用记录
    tool_calls: list[dict] = field(default_factory=list)
    
    # 时间
    duration_ms: int = 0
    
    def to_dict(self) -> dict:
        return {
            "agent_key": self.agent_key,
            "agent_name": self.agent_name,
            "agent_role": self.agent_role.value,
            "trace_id": self.trace_id,
            "conclusion": self.conclusion,
            "action_signals": self.action_signals,
            "data_references": self.data_references,
            "confidence": self.confidence,
            "confidence_breakdown": self.confidence_breakdown,
            "analysis_markdown": self.analysis_markdown,
            "tool_calls": self.tool_calls,
            "duration_ms": self.duration_ms,
        }
    
    def to_a2a_message(self) -> dict:
        """转化为 A2A 协议消息"""
        return {
            "protocol": "a2a/v1",
            "from": self.agent_key,
            "from_role": self.agent_role.value,
            "trace_id": self.trace_id,
            "payload": {
                "conclusion": self.conclusion,
                "action_signals": self.action_signals,
                "data_references": self.data_references,
                "confidence": self.confidence,
                "confidence_breakdown": self.confidence_breakdown,
            },
            "body": self.analysis_markdown,
        }
```

### 3.4 A2A 消息协议

```python
# backend/agent/message_protocol.py（续）

@dataclass
class A2AMessage:
    """Agent-to-Agent 标准消息"""
    protocol: str = "a2a/v1"
    msg_id: str = ""
    from_agent: str = ""
    from_role: str = ""
    to_agent: str = ""          # 空 = 广播给所有 agent
    msg_type: str = "analysis"  # analysis | review | question | answer | alert
    trace_id: str = ""
    payload: dict = field(default_factory=dict)
    body: str = ""              # 自然语言内容
    timestamp: str = ""
    reply_to: str = ""          # 回复某条消息的 msg_id
    
    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "msg_id": self.msg_id,
            "from": self.from_agent,
            "from_role": self.from_role,
            "to": self.to_agent,
            "type": self.msg_type,
            "trace_id": self.trace_id,
            "payload": self.payload,
            "body": self.body,
            "timestamp": self.timestamp,
            "reply_to": self.reply_to,
        }


class A2AMessageBus:
    """A2A 消息总线 — 在编排过程中收集和分发 agent 间消息"""
    
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.messages: list[A2AMessage] = []
        self._msg_counter = 0
    
    def send(self, msg: A2AMessage) -> str:
        """发送消息，返回 msg_id"""
        self._msg_counter += 1
        msg.msg_id = f"{self.trace_id}-msg-{self._msg_counter}"
        msg.timestamp = datetime.now().isoformat()
        self.messages.append(msg)
        return msg.msg_id
    
    def get_for_agent(self, agent_key: str) -> list[A2AMessage]:
        """获取发给指定 agent 的消息（包括广播）"""
        return [m for m in self.messages
                if m.to_agent == "" or m.to_agent == agent_key]
    
    def get_replies(self, msg_id: str) -> list[A2AMessage]:
        """获取某条消息的所有回复"""
        return [m for m in self.messages if m.reply_to == msg_id]
    
    def to_context_text(self, agent_key: str) -> str:
        """将相关消息转化为文本上下文，注入给 agent"""
        relevant = self.get_for_agent(agent_key)
        if not relevant:
            return ""
        lines = ["## 其他专家的分析结论（A2A）"]
        for m in relevant[-5:]:  # 最近 5 条
            lines.append(f"- [{m.from_agent}] {m.payload.get('conclusion', '')}")
            if m.payload.get("action_signals"):
                signals = ", ".join(
                    f"{s['type']} {s.get('target','')}({s.get('confidence','?')})"
                    for s in m.payload["action_signals"]
                )
                lines.append(f"  信号: {signals}")
        return "\n".join(lines)
```

### 3.5 LLM Prompt 改造

```python
# backend/agent/multi_agent.py — 在 system prompt 中注入结构化输出指令

_A2A_OUTPUT_INSTRUCTION = """
## 输出格式要求

你的分析需要同时输出两部分：

### 1. 结构化结论（JSON，放在分析文本最前面）
```json
{
  "conclusion": "一句话结论（50字以内）",
  "action_signals": [
    {"type": "BUY|SELL|HOLD|ADD|REDUCE|WATCH", "target": "标的名称", "confidence": 0.0-1.0, "urgency": "high|medium|low"}
  ],
  "data_references": [
    {"source": "数据来源表名", "metric": "指标名", "value": "数值"}
  ],
  "confidence": 0.0-1.0,
  "confidence_breakdown": {
    "data_quality": 0.0-1.0,
    "analysis_depth": 0.0-1.0,
    "uncertainty": 0.0-1.0
  }
}
```

### 2. 详细分析（Markdown，放在 JSON 后面）
正常的分析文本，包括数据解读、逻辑推理、风险提示等。

注意：
- JSON 必须放在代码块 ```json 中
- 如果某个字段不适用，用空数组 [] 或 0.0
- 置信度 confidence 不要盲目给高分，有不确定因素时如实降低
"""
```

### 3.6 解析层

```python
# backend/agent/message_protocol.py（续）

import re
import json

def parse_agent_output(raw_text: str, agent_key: str, agent_name: str,
                       agent_role: str, trace_id: str, tool_calls: list,
                       duration_ms: int) -> AgentOutput:
    """解析 agent 的 LLM 输出，提取结构化 JSON 和自然语言"""
    
    # 尝试提取 JSON 块
    json_match = re.search(r'```json\s*\n(.*?)\n```', raw_text, re.DOTALL)
    structured = {}
    if json_match:
        try:
            structured = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            logger.warning(f"[{agent_key}] JSON 解析失败，使用纯文本模式")
    
    # 提取自然语言分析体（去掉 JSON 块）
    analysis_md = raw_text
    if json_match:
        analysis_md = raw_text[:json_match.start()] + raw_text[json_match.end():]
    analysis_md = analysis_md.strip()
    
    return AgentOutput(
        agent_key=agent_key,
        agent_name=agent_name,
        agent_role=AgentRole(agent_role),
        trace_id=trace_id,
        conclusion=structured.get("conclusion", ""),
        action_signals=structured.get("action_signals", []),
        data_references=structured.get("data_references", []),
        confidence=structured.get("confidence", 0.0),
        confidence_breakdown=structured.get("confidence_breakdown", {}),
        analysis_markdown=analysis_md,
        tool_calls=tool_calls,
        duration_ms=duration_ms,
    )


def agent_outputs_to_a2a_context(outputs: list[AgentOutput],
                                  target_agent_key: str) -> str:
    """将多个 agent 输出转化为目标 agent 的 A2A 上下文文本"""
    bus = A2AMessageBus(trace_id="composite")
    for ao in outputs:
        msg = A2AMessage(
            from_agent=ao.agent_key,
            from_role=ao.agent_role.value,
            to_agent=target_agent_key,
            msg_type="analysis",
            trace_id=ao.trace_id,
            payload={
                "conclusion": ao.conclusion,
                "action_signals": ao.action_signals,
                "confidence": ao.confidence,
            },
            body=ao.analysis_markdown,
        )
        bus.send(msg)
    return bus.to_context_text(target_agent_key)
```

### 3.7 `run_specialist()` 改动

```python
# backend/agent/multi_agent.py — 在 run_specialist() 末尾替换 return

# 原有:
# return {"agent": agent["name"], "icon": agent["icon"], "analysis": answer, ...}

# 替换为:
from agent.message_protocol import parse_agent_output, AgentRole

ROLE_MAP = {
    "wealth_advisor": "advisor",
    "behavior_coach": "advisor",
    "counter_argument": "reviewer",
    "valuation_analyst": "analyst",
    "risk_assessor": "analyst",
    "market_analyst": "analyst",
    "macro_strategist": "analyst",
    "fund_analyst": "analyst",
    "asset_allocator": "analyst",
    "article_expert": "analyst",
}

output = parse_agent_output(
    raw_text=answer,
    agent_key=agent_key,
    agent_name=agent["name"],
    agent_role=ROLE_MAP.get(agent_key, "analyst"),
    trace_id=trace_id,
    tool_calls=tool_calls_log,
    duration_ms=int((time.time() - start_time) * 1000),
)

return {
    "agent": output.agent_name,
    "agent_key": output.agent_key,
    "icon": agent["icon"],
    "analysis": output.analysis_markdown,  # 向后兼容
    "structured": output.to_dict(),        # 新增：结构化数据
    "a2a": output.to_a2a_message(),        # 新增：A2A 协议消息
    "tool_calls": output.tool_calls,
    "duration_ms": output.duration_ms,
}
```

### 3.8 交叉审阅增强

```python
# backend/agent/orchestrator.py — 交叉审阅时注入 A2A 上下文

# 原有: peer_analyses = {sr["agent_key"]: sr["analysis"] for sr in specialist_results}

# 替换为:
from agent.message_protocol import agent_outputs_to_a2a_context

# 为每个审阅专家构建 A2A 上下文
if sr.get("structured"):
    a2a_context = agent_outputs_to_a2a_context(
        [AgentOutput(**s["structured"]) for s in specialist_results if s.get("structured")],
        target_agent_key=sr["agent_key"]
    )
    # 注入到 system prompt 中
    extra_context = a2a_context
```

### 3.9 前端展示

- 结构化信号（BUY/SELL/HOLD）在消息卡片中以标签形式展示
- 置信度以进度条或色标展示（绿/黄/红）
- 数据引用可点击跳转到数据详情页
- 纯文本分析保持现有渲染方式

### 3.10 风险与降级

- **JSON 解析失败**：降级为纯文本模式，`structured` 字段为空
- **LLM 不输出 JSON**：`parse_agent_output()` 安全处理，返回空结构化字段
- **向后兼容**：`analysis` 字段保留纯文本，旧前端不受影响

---

## 四、升级 3：Plan & Execute + ReAct 编排

### 4.1 现状

```
Orchestrator ReAct Loop (max 3-6 turns)
  ├── turn 1: LLM 决定调用 consult_valuation_analyst
  │   └── Specialist ReAct Loop (max 3 turns)
  ├── turn 2: LLM 决定调用 consult_fund_analyst
  │   └── Specialist ReAct Loop (max 3 turns)
  └── turn 3: LLM 综合 → 交叉审阅 → 仲裁 → 输出
```

**问题**：
- 没有显式计划，Orchestrator 可能第一轮就调用了错误的专家
- 不可审计（不知道为什么会调用这些专家）
- 不可中断恢复（如果第二步失败，第一步结果也丢失）
- 无法重规划（新信息出现后不能调整策略）

### 4.2 目标

1. Phase 0：生成显式分析计划（JSON）
2. Phase 1：按计划执行，每个 step 调用对应专家
3. 支持重规划：执行中发现新信息，可更新计划
4. 每个 step 可中断恢复（配合升级1的检查点）
5. 执行完成后进入交叉审阅 + 仲裁

### 4.3 计划 Schema

```python
# backend/agent/plan_executor.py（新建）

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class PlanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLANNED = "replanned"

class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"

@dataclass
class PlanStep:
    """单个执行步骤"""
    step_id: int
    agent_key: str           # 要调用的专家
    agent_name: str
    query: str               # 发给专家的具体问题
    depends_on: list[int] = field(default_factory=list)  # 依赖的 step_id 列表
    status: StepStatus = StepStatus.PENDING
    result: Optional[dict] = None
    error: str = ""
    duration_ms: int = 0

@dataclass
class AnalysisPlan:
    """分析计划"""
    plan_id: str
    trace_id: str
    user_query: str
    refined_query: str
    complexity: str            # simple | medium | complex
    reasoning: str             # 规划理由（LLM 生成）
    steps: list[PlanStep]
    status: PlanStatus = PlanStatus.PENDING
    created_at: str = ""
    updated_at: str = ""
    
    @property
    def completed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == StepStatus.DONE]
    
    @property
    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status in (StepStatus.PENDING, StepStatus.FAILED)]
    
    @property
    def next_step(self) -> Optional[PlanStep]:
        """获取下一个可执行的步骤（依赖已满足）"""
        for s in self.steps:
            if s.status != StepStatus.PENDING:
                continue
            # 检查依赖是否全部完成
            deps_met = all(
                any(ds.step_id == dep_id and ds.status == StepStatus.DONE
                    for ds in self.steps)
                for dep_id in s.depends_on
            )
            if deps_met:
                return s
        return None
    
    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "trace_id": self.trace_id,
            "user_query": self.user_query,
            "refined_query": self.refined_query,
            "complexity": self.complexity,
            "reasoning": self.reasoning,
            "steps": [
                {
                    "step_id": s.step_id,
                    "agent_key": s.agent_key,
                    "agent_name": s.agent_name,
                    "query": s.query,
                    "depends_on": s.depends_on,
                    "status": s.status.value,
                    "duration_ms": s.duration_ms,
                }
                for s in self.steps
            ],
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
```

### 4.4 Plan 生成 Prompt

```python
_PLAN_GENERATION_PROMPT = """
## 任务：为以下用户问题生成分析计划

用户问题：{user_query}
优化后问题：{refined_query}
复杂度：{complexity}

可用专家列表：
{available_specialists}

### 输出格式（严格 JSON）
```json
{{
  "reasoning": "规划理由：为什么选择这些专家，为什么是这个顺序",
  "steps": [
    {{
      "step_id": 1,
      "agent_key": "valuation_analyst",
      "query": "发给专家的具体问题（包含用户问题中的关键信息）",
      "depends_on": []
    }}
  ]
}}
```

### 规划原则
1. 估值分析先于买卖建议（先看估值再看行动）
2. 宏观分析作为背景，可以与估值并行
3. 风险评估在配置建议之前
4. simple 问题 1-2 个专家，medium 2-4 个，complex 3-6 个
5. 无关专家不要加（如用户问估值就别加文章解读）
6. depends_on 表示该步骤依赖前面步骤的结果，大多数步骤可以并行
"""
```

### 4.5 Plan 生成函数

```python
# backend/agent/plan_executor.py（续）

def generate_plan(
    user_query: str,
    refined_query: str,
    complexity: str,
    available_specialists: list[dict],
    trace_id: str,
) -> AnalysisPlan:
    """调用 LLM 生成分析计划"""
    import uuid
    from datetime import datetime
    
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"
    
    # 构建专家列表文本
    specialist_lines = []
    for s in available_specialists:
        specialist_lines.append(
            f"- {s['agent_key']}: {s['name']} — {s.get('description', '')}"
        )
    specialists_text = "\n".join(specialist_lines)
    
    prompt = _PLAN_GENERATION_PROMPT.format(
        user_query=user_query,
        refined_query=refined_query,
        complexity=complexity,
        available_specialists=specialists_text,
    )
    
    from llm_service import call_llm
    response = call_llm(
        caller="plan_generator",
        trace_id=trace_id,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=2000,
    )
    
    content = response.choices[0].message.content or ""
    
    # 解析 JSON
    import re
    json_match = re.search(r'```json\s*\n(.*?)\n```', content, re.DOTALL)
    if json_match:
        plan_data = json.loads(json_match.group(1))
    else:
        # 降级：所有专家顺序执行
        plan_data = {
            "reasoning": "Plan 解析失败，降级为全量顺序执行",
            "steps": [
                {"step_id": i+1, "agent_key": s["agent_key"],
                 "query": refined_query, "depends_on": []}
                for i, s in enumerate(available_specialists)
            ],
        }
    
    steps = [
        PlanStep(
            step_id=s["step_id"],
            agent_key=s["agent_key"],
            agent_name=next(
                (sp["name"] for sp in available_specialists
                 if sp["agent_key"] == s["agent_key"]), s["agent_key"]
            ),
            query=s["query"],
            depends_on=s.get("depends_on", []),
        )
        for s in plan_data["steps"]
    ]
    
    return AnalysisPlan(
        plan_id=plan_id,
        trace_id=trace_id,
        user_query=user_query,
        refined_query=refined_query,
        complexity=complexity,
        reasoning=plan_data.get("reasoning", ""),
        steps=steps,
        status=PlanStatus.PENDING,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )


def execute_plan(
    plan: AnalysisPlan,
    prebuilt_context: str,
    budget: dict,
    cancel_event,
    progress_callback=None,
) -> tuple[list[dict], list[dict]]:
    """
    按计划执行，返回 (specialist_results, all_tool_calls)
    
    执行策略：
    - 按 step_id 顺序执行
    - 无依赖的步骤可并行（ThreadPoolExecutor）
    - 有依赖的步骤等待依赖完成后执行
    - 步骤失败不阻塞（标记为 FAILED，继续执行其他步骤）
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from agent.multi_agent import run_specialist
    
    plan.status = PlanStatus.RUNNING
    specialist_results = []
    all_tool_calls = []
    
    while plan.pending_steps:
        # 收集所有可并行执行的步骤
        parallel_steps = []
        next_step = plan.next_step
        if next_step is None:
            break
        
        # 收集所有同依赖级别的步骤
        for s in plan.steps:
            if s.status != StepStatus.PENDING:
                continue
            if s.depends_on == next_step.depends_on:
                parallel_steps.append(s)
        
        # 并行执行
        if len(parallel_steps) == 1:
            step = parallel_steps[0]
            step.status = StepStatus.RUNNING
            if progress_callback:
                progress_callback(step.step_id, step.agent_name, "running")
            try:
                result = run_specialist(
                    agent_key=step.agent_key,
                    query=step.query,
                    prebuilt_context=prebuilt_context,
                    trace_id=plan.trace_id,
                )
                step.result = result
                step.status = StepStatus.DONE
                step.duration_ms = result.get("duration_ms", 0)
                specialist_results.append(result)
                all_tool_calls.extend(result.get("tool_calls", []))
                if progress_callback:
                    progress_callback(step.step_id, step.agent_name, "done")
            except Exception as e:
                step.status = StepStatus.FAILED
                step.error = str(e)
                if progress_callback:
                    progress_callback(step.step_id, step.agent_name, "failed")
        else:
            # 多步骤并行
            with ThreadPoolExecutor(max_workers=len(parallel_steps)) as executor:
                future_map = {}
                for step in parallel_steps:
                    step.status = StepStatus.RUNNING
                    future = executor.submit(
                        run_specialist,
                        agent_key=step.agent_key,
                        query=step.query,
                        prebuilt_context=prebuilt_context,
                        trace_id=plan.trace_id,
                    )
                    future_map[future] = step
                
                for future in as_completed(future_map):
                    step = future_map[future]
                    try:
                        result = future.result()
                        step.result = result
                        step.status = StepStatus.DONE
                        step.duration_ms = result.get("duration_ms", 0)
                        specialist_results.append(result)
                        all_tool_calls.extend(result.get("tool_calls", []))
                    except Exception as e:
                        step.status = StepStatus.FAILED
                        step.error = str(e)
    
    plan.status = PlanStatus.COMPLETED
    plan.updated_at = datetime.now().isoformat()
    
    return specialist_results, all_tool_calls
```

### 4.6 `orchestrate()` 改造

```python
# backend/agent/orchestrator.py — 在 orchestrate() 中替换原有 ReAct Loop

# 原有 Phase A（约 2930-3055 行）替换为：

# Phase 0: 生成分析计划
specialists = load_specialist_agents()
available = [
    {"agent_key": k, "name": v["name"], "description": v.get("description", "")}
    for k, v in specialists.items()
]

from agent.plan_executor import generate_plan, execute_plan

plan = generate_plan(
    user_query=query,
    refined_query=refined_query,
    complexity=complexity,
    available_specialists=available,
    trace_id=trace_id,
)

# SSE 推送计划给前端
yield {"type": "plan", "plan": plan.to_dict()}

# Phase 1: 执行计划
specialist_results, all_tool_calls = execute_plan(
    plan=plan,
    prebuilt_context=prebuilt_context,
    budget=budget,
    cancel_event=cancel_event,
    progress_callback=lambda step_id, name, status: (
        yield {"type": "plan_progress", "step_id": step_id, "agent": name, "status": status}
    ),
)

# Phase 2: 交叉审阅 + 仲裁（保持不变）
# ... 原有交叉审阅逻辑 ...
```

### 4.7 重规划支持

```python
# backend/agent/plan_executor.py（续）

def should_replan(plan: AnalysisPlan, new_info: str) -> bool:
    """判断是否需要重规划"""
    # 场景1：超过 30% 步骤失败
    if plan.completed_steps and len(plan.pending_steps) > len(plan.steps) * 0.3:
        return True
    
    # 场景2：执行结果中发现新标的（如用户持仓中有未分析的基金）
    import re
    new_targets = re.findall(r'建议分析[：:]\s*(.+?)[\n。]', new_info)
    if new_targets:
        return True
    
    return False


def replan(plan: AnalysisPlan, new_info: str, trace_id: str) -> AnalysisPlan:
    """基于新信息重规划，保留已完成步骤，追加新步骤"""
    from datetime import datetime
    
    new_prompt = (
        f"原始计划已完成 {len(plan.completed_steps)} 个步骤。\n"
        f"新发现信息：{new_info}\n"
        f"请给出需要追加的分析步骤（JSON steps 数组，step_id 从 {len(plan.steps)+1} 开始）"
    )
    
    # ... 调用 LLM 生成新步骤，追加到 plan.steps ...
    
    plan.status = PlanStatus.REPLANNED
    plan.updated_at = datetime.now().isoformat()
    return plan
```

### 4.8 SSE 流式事件

```python
# 新增 SSE 事件类型

# 计划生成完成
{"type": "plan", "plan": {"plan_id": "...", "steps": [...], "reasoning": "..."}}

# 步骤进度
{"type": "plan_progress", "step_id": 1, "agent": "估值分析师", "status": "running"}
{"type": "plan_progress", "step_id": 1, "agent": "估值分析师", "status": "done"}

# 计划重规划
{"type": "plan_replan", "reason": "发现新标的", "new_steps": [...]}

# 步骤失败（不阻塞）
{"type": "plan_step_failed", "step_id": 2, "agent": "基金分析师", "error": "LLM 超时"}
```

### 4.9 前端改动

- 在对话流中展示"分析计划"卡片（可折叠）
- 实时显示每个步骤的执行状态（pending/running/done/failed）
- 失败步骤显示红色标记 + 重试按钮
- 重规划时追加新步骤到计划卡片

### 4.10 风险与降级

- **Plan 生成失败**：降级为原有的 ReAct Loop（`_fallback_orchestrate()`）
- **步骤失败**：不阻塞，标记 FAILED 继续执行
- **死循环保护**：每个 Specialist 仍是 ReAct（max 3 turns），计划步骤数上限 8

---

## 五、实施顺序

```
Phase 1（升级1，独立交付）
  ├── 1.1 DB migration（analysis_agents 加列 + tool_registry 建表）
  ├── 1.2 ToolRegistry 类实现
  ├── 1.3 数据迁移脚本（硬编码 → DB）
  ├── 1.4 run_specialist() 改用 ToolRegistry
  ├── 1.5 管理 API + 前端
  └── 验证：agent 工具调用正常

Phase 2（升级2，依赖升级1）
  ├── 2.1 AgentOutput + A2AMessage 数据结构
  ├── 2.2 parse_agent_output() 解析层
  ├── 2.3 run_specialist() 输出结构化
  ├── 2.4 交叉审阅注入 A2A 上下文
  ├── 2.5 前端结构化展示
  └── 验证：agent 输出 JSON 可解析，置信度合理

Phase 3（升级3，依赖升级1）
  ├── 3.1 AnalysisPlan + PlanStep 数据结构
  ├── 3.2 generate_plan() LLM 规划
  ├── 3.3 execute_plan() 并行执行
  ├── 3.4 orchestrate() 改造
  ├── 3.5 SSE 流式事件 + 前端
  └── 验证：计划可生成、可执行、可展示
```

---

## 六、测试要点

### 升级1 测试
- [ ] ToolRegistry.initialize() 正常加载所有工具
- [ ] get_tools_for_agent(["query_valuation"]) 返回正确的工具子集
- [ ] DB 迁移不破坏现有数据
- [ ] 数据迁移脚本正确同步 agent 配置
- [ ] 降级：DB 无 agent 配置时回退到硬编码

### 升级2 测试
- [ ] parse_agent_output() 正确解析 ```json 块
- [ ] parse_agent_output() 无 JSON 时降级为纯文本
- [ ] AgentOutput.to_a2a_message() 输出正确格式
- [ ] A2AMessageBus 消息收发正确
- [ ] 交叉审阅能正确注入 A2A 上下文

### 升级3 测试
- [ ] generate_plan() 为 simple 查询生成 1-2 步
- [ ] generate_plan() 为 complex 查询生成 3-6 步
- [ ] execute_plan() 并行执行无依赖步骤
- [ ] execute_plan() 串行执行有依赖步骤
- [ ] 步骤失败不阻塞其他步骤
- [ ] 降级：plan 生成失败时回退到 ReAct

---

## 七、与现有设计稿的关系

| 已有设计稿 | 本设计稿关联 |
|------------|-------------|
| 2026-06-25-多Agent系统6项增强设计稿.md | 升级3 的检查点复用其状态机设计 |
| 2026-06-25-多Agent系统对标成熟框架增强分析.md | 升级3 对标 LangGraph 的 Plan & Execute |
| 2026-07-06-对话与AI分析增强-design.md | 升级2 的 A2A 协议增强对话质量 |
| 2026-05-29-specialist-agents-db-migration.md | 升级1 完成其未完成的 DB 迁移 |