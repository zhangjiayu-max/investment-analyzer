"""结构化共享黑板 — 专家执行时实时写入结论，后续专家可见。

设计要点：
- 替代现有 shared_blackboard（dict+全文摘要），改为结构化 BlackboardEntry
- 每条 entry 约 100 字，只含结论+信号+置信度+关键数据，不含全文分析
- max_entries=6，超出自动淘汰最早条目
- to_context_text() 生成注入专家上下文的黑板摘要（≤800 字）
- 与 message_protocol.AgentOutput 字段对齐，便于 A2A 协议复用

与现有代码的关系：
- 现有 orchestrator.py 的 shared_blackboard（line 4834）替换为 Blackboard 类
- 现有 _extract_structured_conclusion() 函数可复用，结果写入 BlackboardEntry
- 现有 _format_blackboard_summary() 替换为 Blackboard.to_context_text()
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class BlackboardEntry:
    """黑板条目 — 一个专家的结构化结论。

    字段与 AgentOutput 对齐，便于 A2A 协议直接复用。
    """
    agent_key: str
    agent_name: str
    conclusion: str = ""              # 一句话结论（≤100 字）
    action_signals: list = field(default_factory=list)
    # [{"type": "BUY", "target": "中证500", "confidence": 0.72, "urgency": "medium"}]
    confidence: float = 0.0           # 综合置信度 0-1
    key_data: dict = field(default_factory=dict)
    # {"沪深300:PE": 12.3, "沪深300:分位": 0.25}
    data_refs: list = field(default_factory=list)
    # 引用的数据源 [{"source": "index_valuations", "index_code": "000300"}]
    timestamp: float = 0.0
    # token 消耗（用于审计）
    tokens_used: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "agent_key": self.agent_key,
            "agent_name": self.agent_name,
            "conclusion": self.conclusion,
            "action_signals": self.action_signals,
            "confidence": self.confidence,
            "key_data": self.key_data,
            "data_refs": self.data_refs,
            "timestamp": self.timestamp,
            "tokens_used": self.tokens_used,
            "duration_ms": self.duration_ms,
        }


class Blackboard:
    """共享黑板 — 专家执行时实时写入，后续专家可见。

    用法：
        bb = Blackboard(max_entries=6)
        # 专家1完成后写入
        bb.write(BlackboardEntry(
            agent_key="valuation_analyst",
            agent_name="估值分析师",
            conclusion="沪深300当前PE 12.3，处于历史25%分位，估值偏低",
            action_signals=[{"type": "BUY", "target": "沪深300", "confidence": 0.72}],
            confidence=0.85,
            key_data={"沪深300:PE": 12.3, "沪深300:分位": 0.25},
        ))
        # 专家2执行前注入黑板摘要
        ctx = bb.to_context_text(max_chars=800)
        # 综合阶段收集所有关键数据
        all_data = bb.get_key_data()
    """

    def __init__(self, max_entries: int = 6):
        self.entries: list[BlackboardEntry] = []
        self.max_entries = max_entries
        # token 追踪：每个 agent 消耗
        self.tokens_used_by_agent: dict[str, int] = {}

    def write(self, entry: BlackboardEntry) -> None:
        """写入一条专家结论。"""
        if not entry.timestamp:
            entry.timestamp = time.time()
        self.entries.append(entry)
        # 容量保护：超出上限淘汰最早条目
        if len(self.entries) > self.max_entries:
            removed = self.entries.pop(0)
            logger.debug(
                f"[blackboard] 淘汰最早条目: {removed.agent_name} "
                f"(当前 {len(self.entries)}/{self.max_entries})"
            )
        # 记录 token
        if entry.tokens_used > 0:
            self.tokens_used_by_agent[entry.agent_key] = (
                self.tokens_used_by_agent.get(entry.agent_key, 0) + entry.tokens_used
            )
        logger.info(
            f"[blackboard] {entry.agent_name} 写入黑板 "
            f"(置信度={entry.confidence:.0%}, 当前 {len(self.entries)}/{self.max_entries})"
        )

    def to_context_text(self, max_chars: int = 800, exclude_agent: Optional[str] = None) -> str:
        """生成注入专家上下文的黑板摘要。

        Args:
            max_chars: 最大字符数，超出截断
            exclude_agent: 排除的 agent_key（避免专家看到自己的结论）

        Returns:
            黑板摘要文本，空字符串表示无条目
        """
        if not self.entries:
            return ""

        lines = ["## 已完成专家的结论（Blackboard）"]
        total = len(lines[0])
        for e in self.entries:
            if exclude_agent and e.agent_key == exclude_agent:
                continue
            line = self._format_entry(e)
            if total + len(line) > max_chars:
                # 截断，添加提示
                remaining = max_chars - total
                if remaining > 50:  # 至少能显示一部分
                    lines.append(line[:remaining] + "...")
                break
            lines.append(line)
            total += len(line)

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    def _format_entry(self, e: BlackboardEntry) -> str:
        """格式化单条条目为上下文文本。"""
        line = f"- [{e.agent_name}] (置信度{e.confidence:.0%}) {e.conclusion}"
        # 动作信号（最多2个）
        if e.action_signals:
            signals = []
            for s in e.action_signals[:2]:
                sig_str = f"{s.get('type', '')} {s.get('target', '')}"
                if s.get('confidence'):
                    sig_str += f"({s['confidence']:.0%})"
                signals.append(sig_str)
            line += " → " + ", ".join(signals)
        # 关键数据（最多3个）
        if e.key_data:
            data_items = list(e.key_data.items())[:3]
            data_str = "; ".join(f"{k}={v}" for k, v in data_items)
            line += f" | 数据: {data_str}"
        return line

    def get_key_data(self) -> dict:
        """收集所有专家引用的关键数据，供综合阶段使用。"""
        data = {}
        for e in self.entries:
            data.update(e.key_data)
        return data

    def get_action_signals(self) -> list[dict]:
        """收集所有专家的动作信号，供综合阶段仲裁使用。"""
        signals = []
        for e in self.entries:
            for s in e.action_signals:
                s_with_source = dict(s)
                s_with_source["source_agent"] = e.agent_key
                signals.append(s_with_source)
        return signals

    def get_entries_by_agent(self, agent_key: str) -> list[BlackboardEntry]:
        """获取指定 agent 的所有条目。"""
        return [e for e in self.entries if e.agent_key == agent_key]

    def find_conflicts(self) -> list[dict]:
        """检测动作信号冲突（如一个说买一个说卖）。

        Returns:
            冲突列表，每项 {"target": "中证500", "buy_agents": [...], "sell_agents": [...]}
        """
        target_signals: dict[str, dict[str, list[str]]] = {}
        for e in self.entries:
            for s in e.action_signals:
                target = s.get("target", "")
                sig_type = str(s.get("type", "")).upper()
                if not target or sig_type not in ("BUY", "SELL", "HOLD"):
                    continue
                if target not in target_signals:
                    target_signals[target] = {"BUY": [], "SELL": [], "HOLD": []}
                if sig_type in target_signals[target]:
                    target_signals[target][sig_type].append(e.agent_name)

        conflicts = []
        for target, sigs in target_signals.items():
            # 买和卖同时存在 = 冲突
            if sigs["BUY"] and sigs["SELL"]:
                conflicts.append({
                    "target": target,
                    "buy_agents": sigs["BUY"],
                    "sell_agents": sigs["SELL"],
                    "type": "buy_sell_conflict",
                })
        return conflicts

    # ── 统计 ──────────────────────────────

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def total_tokens(self) -> int:
        """所有写入黑板的专家总 token 消耗。"""
        return sum(self.tokens_used_by_agent.values())

    def stats(self) -> dict:
        return {
            "entry_count": self.entry_count,
            "max_entries": self.max_entries,
            "total_tokens": self.total_tokens,
            "tokens_by_agent": dict(self.tokens_used_by_agent),
            "agents": [e.agent_key for e in self.entries],
        }

    def clear(self) -> None:
        """清空黑板（新对话开始时调用）。"""
        self.entries.clear()
        self.tokens_used_by_agent.clear()


# ── 从专家结果提取黑板条目 ──────────────────────

def extract_entry_from_result(
    agent_key: str,
    agent_name: str,
    result: dict,
    tokens_used: int = 0,
    duration_ms: int = 0,
) -> BlackboardEntry:
    """从 specialist 执行结果中提取黑板条目。

    复用现有 _extract_structured_conclusion 的思路，但输出为 BlackboardEntry。

    Args:
        result: run_specialist 返回的 dict，包含 analysis/tool_calls/duration_ms 等
    """
    analysis = result.get("analysis", "") or ""
    conclusion = _extract_conclusion(analysis)
    action_signals = _extract_action_signals(analysis, agent_key)
    key_data = _extract_key_data(analysis, result.get("tool_calls", []))
    confidence = _estimate_confidence(analysis, action_signals)

    return BlackboardEntry(
        agent_key=agent_key,
        agent_name=agent_name,
        conclusion=conclusion,
        action_signals=action_signals,
        confidence=confidence,
        key_data=key_data,
        tokens_used=tokens_used,
        duration_ms=duration_ms or result.get("duration_ms", 0),
    )


def _extract_conclusion(analysis: str) -> str:
    """从分析文本中提取一句话结论。

    策略：
    1. 查找"结论"段落
    2. 查找加粗的第一句
    3. 退化为前 100 字
    """
    if not analysis:
        return ""

    # 策略1：查找"结论"段落
    conclusion_patterns = [
        r'结论[：:]\s*(.+?)(?:\n|$)',
        r'总结[：:]\s*(.+?)(?:\n|$)',
        r'综上[，,]?\s*(.+?)(?:\n|$)',
        r'###\s*结论\s*\n\s*(.+?)(?:\n|$)',
        r'##\s*结论\s*\n\s*(.+?)(?:\n|$)',
    ]
    for pat in conclusion_patterns:
        match = re.search(pat, analysis)
        if match:
            conclusion = match.group(1).strip()
            # 限制 100 字
            if len(conclusion) > 100:
                conclusion = conclusion[:97] + "..."
            return conclusion

    # 策略2：第一个加粗行
    bold_match = re.search(r'\*\*(.+?)\*\*', analysis)
    if bold_match:
        conclusion = bold_match.group(1).strip()
        if len(conclusion) > 100:
            conclusion = conclusion[:97] + "..."
        return conclusion

    # 策略3：前 100 字
    clean = re.sub(r'[#*\-\n]', ' ', analysis).strip()
    clean = re.sub(r'\s+', ' ', clean)
    return clean[:100] + ("..." if len(clean) > 100 else "")


def _extract_action_signals(analysis: str, agent_key: str) -> list[dict]:
    """从分析文本中提取动作信号。"""
    signals = []
    text = analysis

    # 买入信号
    if re.search(r'建议买入|可以买入|加仓|定投|BUY', text, re.IGNORECASE):
        # 尝试提取目标
        target_match = re.search(r'(沪深300|中证500|中证1000|创业板|科创50|[\d]{6})', text)
        target = target_match.group(1) if target_match else ""
        signals.append({
            "type": "BUY",
            "target": target,
            "confidence": 0.6,  # 默认置信度
            "urgency": "medium",
        })

    # 卖出信号
    if re.search(r'建议卖出|减仓|止盈|止损|SELL', text, re.IGNORECASE):
        target_match = re.search(r'(沪深300|中证500|中证1000|创业板|科创50|[\d]{6})', text)
        target = target_match.group(1) if target_match else ""
        signals.append({
            "type": "SELL",
            "target": target,
            "confidence": 0.6,
            "urgency": "medium",
        })

    # 持有信号
    if re.search(r'建议持有|继续持有|HOLD', text, re.IGNORECASE):
        target_match = re.search(r'(沪深300|中证500|中证1000|创业板|科创50|[\d]{6})', text)
        target = target_match.group(1) if target_match else ""
        signals.append({
            "type": "HOLD",
            "target": target,
            "confidence": 0.6,
            "urgency": "low",
        })

    return signals


def _extract_key_data(analysis: str, tool_calls: list) -> dict:
    """从分析文本和工具调用中提取关键数据点。"""
    key_data = {}

    # 从文本提取 PE/PB 值
    pe_matches = re.findall(r'(PE[_\s]*(?:TTM)?)\s*[：:是]=?\s*(\d+\.?\d*)', analysis, re.IGNORECASE)
    for label, value in pe_matches:
        key_data[f"PE:{label}"] = float(value)

    pb_matches = re.findall(r'(PB)\s*[：:是]=?\s*(\d+\.?\d*)', analysis, re.IGNORECASE)
    for label, value in pb_matches:
        key_data[f"PB:{label}"] = float(value)

    # 从文本提取分位值
    percentile_matches = re.findall(r'分位[数]?\s*[：:是]?\s*(\d+\.?\d*)\s*%', analysis)
    for i, value in enumerate(percentile_matches):
        key_data[f"分位{i+1}"] = f"{float(value)}%"

    # 从工具调用结果提取（简单提取）
    for tc in tool_calls[:5]:  # 最多看 5 个工具调用
        tc_name = tc.get("name", "")
        tc_result = tc.get("result_preview", "") or ""
        if tc_name in ("query_valuation", "get_index_valuation"):
            # 提取估值数据
            pe_match = re.search(r'["\']?pe["\']?\s*[:=]\s*(\d+\.?\d*)', tc_result, re.IGNORECASE)
            if pe_match:
                key_data[f"{tc_name}:pe"] = float(pe_match.group(1))

    return key_data


def _estimate_confidence(analysis: str, signals: list) -> float:
    """根据分析质量和信号数量估计置信度。"""
    confidence = 0.5  # 基础置信度

    # 有数据支撑 +0.2
    if re.search(r'\d+\.?\d*%|\d+\.?\d*元|PE\s*\d', analysis):
        confidence += 0.2

    # 有明确结论 +0.15
    if re.search(r'结论[：:]|综上[，,]?|建议', analysis):
        confidence += 0.15

    # 有动作信号 +0.1
    if signals:
        confidence += 0.1

    # 分析长度适中 +0.05（太短可能不深入，太长可能啰嗦）
    if 200 <= len(analysis) <= 2000:
        confidence += 0.05

    return min(confidence, 0.95)  # 上限 0.95


# ── 全局单例 ──────────────────────────────────

_global_blackboard: Optional[Blackboard] = None


def get_global_blackboard() -> Blackboard:
    """获取全局黑板实例（单次对话内复用）。"""
    global _global_blackboard
    if _global_blackboard is None:
        _global_blackboard = Blackboard()
    return _global_blackboard


def reset_global_blackboard() -> None:
    """重置全局黑板（新对话开始时调用）。"""
    global _global_blackboard
    _global_blackboard = None
