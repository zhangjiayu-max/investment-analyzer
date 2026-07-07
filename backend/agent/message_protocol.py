"""A2A 消息协议 — Agent 间标准化通信。

定义：
- AgentOutput: 每个 agent 的结构化输出
- A2AMessage: Agent-to-Agent 标准消息
- A2AMessageBus: 消息总线（收集和分发 agent 间消息）
- parse_agent_output(): 从 LLM 原始文本解析结构化输出
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    ANALYST = "analyst"       # 分析型专家
    ADVISOR = "advisor"       # 建议型专家（理财顾问）
    REVIEWER = "reviewer"     # 审阅型专家（反方观点、交叉审阅）
    ARBITRATOR = "arbitrator" # 仲裁法官


# agent_key → AgentRole 映射
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


# ── Agent 结构化输出 ──────────────────────────────


@dataclass
class AgentOutput:
    """Agent 输出标准结构。"""

    agent_key: str
    agent_name: str
    agent_role: AgentRole
    trace_id: str

    # 分析结论
    conclusion: str = ""                    # 一句话结论（50字以内）
    action_signals: list[dict] = field(default_factory=list)
    # 例: [{"type": "BUY", "target": "中证500", "confidence": 0.72, "urgency": "medium"}]

    # 数据引用（用于 A2A 交叉验证）
    data_references: list[dict] = field(default_factory=list)
    # 例: [{"source": "index_valuations", "index_code": "000905", "metric": "PE_TTM", "value": 12.3}]

    # 置信度
    confidence: float = 0.0
    confidence_breakdown: dict = field(default_factory=dict)
    # 例: {"data_quality": 0.9, "analysis_depth": 0.7, "uncertainty": 0.3}

    # 分析体（自然语言）
    analysis_markdown: str = ""

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
        """转化为 A2A 协议消息。"""
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


# ── A2A 消息协议 ──────────────────────────────────


@dataclass
class A2AMessage:
    """Agent-to-Agent 标准消息。"""

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
    """A2A 消息总线 — 在编排过程中收集和分发 agent 间消息。"""

    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.messages: list[A2AMessage] = []
        self._msg_counter = 0

    def send(self, msg: A2AMessage) -> str:
        """发送消息，返回 msg_id。"""
        self._msg_counter += 1
        msg.msg_id = f"{self.trace_id}-msg-{self._msg_counter}"
        msg.timestamp = datetime.now().isoformat()
        self.messages.append(msg)
        return msg.msg_id

    def get_for_agent(self, agent_key: str) -> list[A2AMessage]:
        """获取发给指定 agent 的消息（包括广播）。"""
        return [m for m in self.messages
                if m.to_agent == "" or m.to_agent == agent_key]

    def get_replies(self, msg_id: str) -> list[A2AMessage]:
        """获取某条消息的所有回复。"""
        return [m for m in self.messages if m.reply_to == msg_id]

    def to_context_text(self, agent_key: str) -> str:
        """将相关消息转化为文本上下文，注入给 agent。"""
        relevant = self.get_for_agent(agent_key)
        if not relevant:
            return ""
        lines = ["## 其他专家的分析结论（A2A）"]
        for m in relevant[-5:]:  # 最近 5 条
            lines.append(f"- [{m.from_agent}] {m.payload.get('conclusion', '')}")
            if m.payload.get("action_signals"):
                signals = ", ".join(
                    f"{s.get('type', '?')} {s.get('target', '')}({s.get('confidence', '?')})"
                    for s in m.payload["action_signals"]
                )
                lines.append(f"  信号: {signals}")
        return "\n".join(lines)


# ── 解析层 ────────────────────────────────────────


def parse_agent_output(raw_text: str, agent_key: str, agent_name: str,
                       agent_role: str, trace_id: str, tool_calls: list,
                       duration_ms: int) -> AgentOutput:
    """解析 agent 的 LLM 输出，提取结构化 JSON 和自然语言。

    降级策略：JSON 解析失败时返回空结构化字段，analysis_markdown 保留原文。
    """
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
        confidence=float(structured.get("confidence", 0.0)),
        confidence_breakdown=structured.get("confidence_breakdown", {}),
        analysis_markdown=analysis_md,
        tool_calls=tool_calls,
        duration_ms=duration_ms,
    )


def agent_outputs_to_a2a_context(outputs: list[AgentOutput],
                                  target_agent_key: str) -> str:
    """将多个 agent 输出转化为目标 agent 的 A2A 上下文文本。"""
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


# ── A2A 输出格式指令（注入到 agent system prompt） ──


_A2A_OUTPUT_INSTRUCTION = """
## A2A 输出格式要求

你的分析需要同时输出两部分：

### 1. 结构化结论（JSON，放在分析文本最前面，用 ```json 包裹）
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


def get_a2a_output_instruction() -> str:
    """获取 A2A 输出格式指令，用于注入 agent system prompt。"""
    return _A2A_OUTPUT_INSTRUCTION