"""结构化共享黑板 — 专家执行时实时写入结论，后续专家可见。

设计要点：
- 替代现有 shared_blackboard（dict+全文摘要），改为结构化 BlackboardEntry
- 每条 entry 约 300 字（M5：原 100→300，保留关键推理），只含结论+信号+置信度+关键数据
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
import threading
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
    conclusion: str = ""              # 结论（M5：≤300 字，保留关键推理）
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
    # P0-A: 风险否决（仅 risk_assessor 填写）
    # {"vetoed_action": "BUY", "target": "中证500", "reason": "最大回撤超30%",
    #  "severity": "high", "suggested_action": "HOLD"}
    risk_veto: Optional[dict] = None
    # P0-B: 对持仓的影响（所有专家可填，建议买卖时必填）
    # {"affected_holding": "中证500ETF", "action": "加仓", "suggested_change": "10%",
    #  "current_position_pct": 15.0, "post_change_pct": 25.0, "risk_check": "未超25%上限"}
    portfolio_impact: Optional[dict] = None
    # 结构化逐标的动作（优先于从长文本猜测）
    asset_actions: list = field(default_factory=list)
    portfolio_stance: str = "unknown"

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
            "risk_veto": self.risk_veto,
            "portfolio_impact": self.portfolio_impact,
            "asset_actions": self.asset_actions,
            "portfolio_stance": self.portfolio_stance,
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

    def __init__(self, max_entries: int = 6, max_broadcasts: int = 10):
        self.entries: list[BlackboardEntry] = []
        self.max_entries = max_entries
        # token 追踪：每个 agent 消耗
        self.tokens_used_by_agent: dict[str, int] = {}
        # P2: 并行写入时的线程安全锁
        self._lock = threading.Lock()
        # 工具结果广播（不占 entries 配额，独立存储）
        self._tool_broadcasts: list = []  # list[ToolBroadcastEntry]
        self.max_broadcasts = max_broadcasts

    def write(self, entry: BlackboardEntry) -> None:
        """写入一条专家结论（线程安全）。"""
        with self._lock:
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

    def write_tool_broadcast(self, entry) -> None:
        """写入工具结果广播（不占 entries 配额，线程安全）。

        Args:
            entry: ToolBroadcastEntry 实例
        """
        with self._lock:
            if not getattr(entry, "timestamp", 0):
                entry.timestamp = time.time()
            self._tool_broadcasts.append(entry)
            # FIFO 淘汰
            if len(self._tool_broadcasts) > self.max_broadcasts:
                self._tool_broadcasts.pop(0)
            logger.debug(
                f"[blackboard] 工具广播 {entry.caller_agent_name}"
                f"调用{entry.tool_name}「{entry.query}」"
                f"(当前 {len(self._tool_broadcasts)}/{self.max_broadcasts})"
            )

    def get_tool_broadcasts(self, exclude_agent: Optional[str] = None) -> list:
        """获取工具广播列表。

        Args:
            exclude_agent: 排除的 agent_key（避免专家看到自己的工具调用）

        Returns:
            ToolBroadcastEntry 列表
        """
        if not self._tool_broadcasts:
            return []
        if exclude_agent:
            return [b for b in self._tool_broadcasts if b.caller_agent != exclude_agent]
        return list(self._tool_broadcasts)

    def to_context_text(self, max_chars: int = 800, exclude_agent: Optional[str] = None) -> str:
        """生成注入专家上下文的黑板摘要（含工具广播区块）。

        Args:
            max_chars: 最大字符数，超出截断
            exclude_agent: 排除的 agent_key（避免专家看到自己的结论）

        Returns:
            黑板摘要文本，空字符串表示无条目
        """
        parts = []

        # 1. 专家结论区块（原有逻辑）
        if self.entries:
            lines = ["## 已完成专家的结论（Blackboard）"]
            total = len(lines[0])
            for e in self.entries:
                if exclude_agent and e.agent_key == exclude_agent:
                    continue
                line = self._format_entry(e)
                if total + len(line) > max_chars:
                    remaining = max_chars - total
                    if remaining > 50:
                        lines.append(line[:remaining] + "...")
                    break
                lines.append(line)
                total += len(line)
            if len(lines) > 1:
                parts.append("\n".join(lines))

        # 2. 工具广播区块（新增）
        if self._tool_broadcasts:
            from agent.infra.tool_broadcast import format_broadcasts_for_context
            broadcast_text = format_broadcasts_for_context(
                self._tool_broadcasts, exclude_agent=exclude_agent
            )
            if broadcast_text:
                parts.append(broadcast_text)

        return "\n\n".join(parts) if parts else ""

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
        # P0-A: 风险否决标注
        if e.risk_veto:
            v = e.risk_veto
            line += f" | ⚠️否决{v.get('vetoed_action','')} {v.get('target','')}: {v.get('reason','')}"
        # P0-B: 持仓影响标注
        if e.portfolio_impact:
            p = e.portfolio_impact
            line += f" | 持仓影响: {p.get('action','')} {p.get('affected_holding','')}"
            if p.get('suggested_change'):
                line += f"({p['suggested_change']})"
            if p.get('risk_check'):
                line += f" [{p['risk_check']}]"
        return line

    def get_vetoes(self) -> list[dict]:
        """P0-A: 返回所有风险否决，供综合阶段强制降级。"""
        vetoes = []
        for e in self.entries:
            if e.risk_veto:
                v = dict(e.risk_veto)
                v["source_agent"] = e.agent_key
                v["source_agent_name"] = e.agent_name
                vetoes.append(v)
        return vetoes

    def get_portfolio_impacts(self) -> list[dict]:
        """P0-B: 返回所有持仓影响标注，供综合阶段组合层面决策。"""
        impacts = []
        for e in self.entries:
            if e.portfolio_impact:
                p = dict(e.portfolio_impact)
                p["source_agent"] = e.agent_key
                p["source_agent_name"] = e.agent_name
                impacts.append(p)
        return impacts

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

        P3 修正：同一专家对同一标的的 REDUCE/HOLD 不算冲突（不同标的的信号）。
        只有不同专家对同一标的有相反方向（BUY vs SELL）才算真正冲突。

        Returns:
            冲突列表，每项 {"target": "中证500", "buy_agents": [...], "sell_agents": [...]}
        """
        # target_signals[target] = {"BUY": [agent_names], "SELL": [...], "HOLD": [...]}
        target_signals: dict[str, dict[str, list[str]]] = {}
        for e in self.entries:
            for s in e.action_signals:
                target = s.get("target", "")
                sig_type = str(s.get("type", "")).upper()
                if not target or sig_type not in ("BUY", "SELL", "HOLD", "REDUCE"):
                    continue
                # REDUCE 归类到 SELL 方向（减仓 = 卖出方向）
                if sig_type == "REDUCE":
                    sig_type = "SELL"
                if target not in target_signals:
                    target_signals[target] = {"BUY": [], "SELL": [], "HOLD": []}
                if sig_type in target_signals[target]:
                    # 去重：同一专家不重复加入
                    if e.agent_name not in target_signals[target][sig_type]:
                        target_signals[target][sig_type].append(e.agent_name)

        conflicts = []
        for target, sigs in target_signals.items():
            buy_set = set(sigs["BUY"])
            sell_set = set(sigs["SELL"])
            # P3: 只有不同专家才有冲突（同一专家同时买/卖不算）
            # 即 buy_set 和 sell_set 不能完全重合，且至少有一方来自不同专家
            if buy_set and sell_set:
                # 同一专家同时给出相反动作也必须保留为待澄清冲突；
                # 否则会把“条件买入/当前减仓”误折叠成无冲突。
                if buy_set and sell_set:
                    conflicts.append({
                        "target": target,
                        "buy_agents": list(buy_set),
                        "sell_agents": list(sell_set),
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

    # ── 序列化（用于 checkpoint 持久化）──────────────

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的 dict（用于 checkpoint 持久化）。

        注意：_tool_broadcasts 不持久化（运行时缓存，恢复时无需重建）。
        """
        return {
            "entries": [e.to_dict() for e in self.entries],
            "max_entries": self.max_entries,
            "tokens_used_by_agent": dict(self.tokens_used_by_agent),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Blackboard":
        """从 dict 重建 Blackboard 实例（用于 checkpoint 恢复）。

        Args:
            data: to_dict() 产出的 dict

        Returns:
            重建后的 Blackboard 实例，entries/tokens_used_by_agent 已恢复
        """
        if not data:
            return cls()
        bb = cls(max_entries=data.get("max_entries", 6))
        for e_dict in data.get("entries", []):
            try:
                bb.entries.append(BlackboardEntry(**e_dict))
            except Exception:
                # 防御：单条条目反序列化失败不应阻断整体恢复
                logger.warning(f"[blackboard] from_dict 条目反序列化失败，跳过: {e_dict}")
        bb.tokens_used_by_agent = dict(data.get("tokens_used_by_agent", {}))
        return bb


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
    from agent.infra.message_protocol import normalize_specialist_result
    structured = result.get("structured") or {}
    inner = structured.get("structured") or structured
    normalized = normalize_specialist_result(result)
    analysis = result.get("analysis") or result.get("analysis_markdown") or inner.get("analysis_markdown") or ""
    conclusion = _extract_conclusion(analysis)
    # 显式动作列表（包括空数组）是权威结果；拒绝后不能从全文重新激活。
    if "asset_actions" in result:
        structured_actions = result["asset_actions"]
        has_structured_actions = True
    elif "asset_actions" in inner:
        structured_actions = inner["asset_actions"]
        has_structured_actions = True
    else:
        structured_actions = result.get("action_signals") or inner.get("action_signals") or []
        has_structured_actions = bool(structured_actions)
    asset_actions = _normalize_asset_actions(structured_actions)
    action_signals = asset_actions if has_structured_actions else _extract_action_signals(analysis, agent_key)
    key_data = _extract_key_data(analysis, result.get("tool_calls", []))
    confidence = _estimate_confidence(analysis, action_signals, result=normalized)

    # P0-A: 风险否决提取（仅 risk_assessor + 开关开启）
    risk_veto = None
    try:
        from db.config import get_config_bool
        veto_enabled = get_config_bool("agent.risk_veto_enabled", True)
    except Exception:
        veto_enabled = True
    if veto_enabled and agent_key == "risk_assessor":
        risk_veto = _extract_risk_veto(analysis, action_signals)

    # P0-B: 持仓影响提取（开关开启 + 有动作信号）
    portfolio_impact = None
    try:
        impact_enabled = get_config_bool("agent.portfolio_impact_enabled", True)
    except Exception:
        impact_enabled = True
    if impact_enabled and action_signals:
        portfolio_impact = _extract_portfolio_impact(analysis, action_signals, asset_actions)

    return BlackboardEntry(
        agent_key=agent_key,
        agent_name=agent_name,
        conclusion=conclusion,
        action_signals=action_signals,
        confidence=confidence,
        key_data=key_data,
        tokens_used=tokens_used,
        duration_ms=duration_ms or result.get("duration_ms", 0),
        risk_veto=risk_veto,
        portfolio_impact=portfolio_impact,
        asset_actions=asset_actions,
        portfolio_stance=normalized.get("portfolio_stance", "unknown"),
        data_refs=result.get("data_references") or inner.get("data_references") or [],
    )


def _extract_conclusion(analysis: str) -> str:
    """从分析文本中提取结论（M5：长度 100→300，保留关键推理）。

    策略：
    1. 查找"结论"段落
    2. 查找加粗的第一句
    3. 退化为前 300 字
    """
    if not analysis:
        return ""

    # M5：结论长度上限 100 → 300，保留关键推理链条
    MAX_CONCLUSION_LEN = 300

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
            if len(conclusion) > MAX_CONCLUSION_LEN:
                conclusion = conclusion[:MAX_CONCLUSION_LEN - 3] + "..."
            return conclusion

    # 策略2：第一个加粗行
    bold_match = re.search(r'\*\*(.+?)\*\*', analysis)
    if bold_match:
        conclusion = bold_match.group(1).strip()
        if len(conclusion) > MAX_CONCLUSION_LEN:
            conclusion = conclusion[:MAX_CONCLUSION_LEN - 3] + "..."
        return conclusion

    # 策略3：前 300 字
    clean = re.sub(r'[#*\-\n]', ' ', analysis).strip()
    clean = re.sub(r'\s+', ' ', clean)
    return clean[:MAX_CONCLUSION_LEN] + ("..." if len(clean) > MAX_CONCLUSION_LEN else "")


def _extract_action_signals(analysis: str, agent_key: str) -> list[dict]:
    """从短句提取动作信号。

    这是兼容旧模型输出的最后回退路径。只接受带明确建议动词的句子，
    跳过否定、历史描述和没有明确标的的动作，避免把“禁止加仓”或任意
    六位数字（日期/指标）误认为交易动作。
    """
    signals = []
    text = analysis or ""
    # 按句切分，动作必须与建议/执行语义同句出现。
    sentences = re.split(r"[。！？\n;；]", text)
    target_re = re.compile(r"(沪深300|中证500|中证1000|创业板|科创50|(?:代码|基金)\s*[：:]?\s*(\d{6}))")
    rules = (
        ("BUY", re.compile(r"(?:建议|可以|考虑|执行|开始|继续)\s*(?:买入|加仓|建仓|定投|增持)")),
        ("SELL", re.compile(r"(?:建议|可以|考虑|执行|开始)\s*(?:卖出|减仓|清仓|赎回|减持)")),
        ("HOLD", re.compile(r"(?:建议|继续|保持)\s*持有|(?:建议|继续)\s*观望")),
    )
    neg_re = re.compile(r"(?:不建议|禁止|不要|无需|暂不|尚未|没有|未曾|避免)\s*(?:买入|加仓|建仓|定投|增持|卖出|减仓|清仓|赎回|持有)")
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or neg_re.search(sentence) or re.search(r"(?:如果|若|假设|当.+时|一旦|历史|此前|曾经|已经|已于)", sentence):
            continue
        matched = next(((kind, rule) for kind, rule in rules if rule.search(sentence)), None)
        if not matched:
            continue
        kind, _ = matched
        target_match = target_re.search(sentence)
        # 没有标的时不产生可执行信号；避免“首个六位数”误配。
        if not target_match:
            continue
        target = target_match.group(2) or target_match.group(1)
        signals.append({"type": kind, "target": target, "confidence": 0.45,
                        "urgency": "low" if kind == "HOLD" else "medium",
                        "source": "text_fallback"})
    return signals


def _normalize_asset_actions(actions) -> list[dict]:
    """规范化模型结构化逐标的动作，丢弃无标的/否定动作。"""
    if not isinstance(actions, list):
        return []
    out = []
    aliases = {"BUY": "BUY", "ADD": "BUY", "买入": "BUY", "加仓": "BUY", "增持": "BUY",
               "SELL": "SELL", "卖出": "SELL", "减仓": "SELL", "清仓": "SELL",
               "HOLD": "HOLD", "持有": "HOLD", "观望": "HOLD", "REDUCE": "SELL", "REDUCE_CONCENTRATION": "SELL"}
    for raw in actions:
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("target_code") or raw.get("target") or raw.get("asset") or raw.get("code") or "").strip()
        action = aliases.get(str(raw.get("type") or raw.get("action") or "").upper(),
                             aliases.get(str(raw.get("type") or raw.get("action") or "")))
        if not target or action not in {"BUY", "SELL", "HOLD"}:
            continue
        # 明确标记 conditional/negated/history 的动作不参与现时仲裁。
        if (raw.get("negated") or raw.get("historical") or raw.get("condition") or raw.get("conditions")
                or str(raw.get("status", "")).strip().lower() in {"conditional", "historical", "negated", "rejected"}):
            continue
        item = dict(raw)
        item.update({"type": action, "target": target})
        item.setdefault("source", "structured")
        out.append(item)
    return out


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


def _estimate_confidence(analysis: str, signals: list, *, result: dict | None = None) -> float:
    """基于可追溯证据估计置信度，禁止仅凭数字/“建议”把报告评为高置信。

    未验证的文本最多 0.6；只有工具证据、结构化置信度和完整状态同时存在时，
    才允许超过该上限。失败/截断/缺证据会再次降低上限。
    """
    result = result or {}
    analysis = analysis or ""
    tool_calls = result.get("tool_calls") or []
    if isinstance(tool_calls, dict):
        tool_calls = tool_calls.get("tool_calls") or []
    has_tool_evidence = isinstance(tool_calls, list) and any(
        isinstance(call, dict) and (call.get("result") or call.get("result_preview") or call.get("evidence_ref"))
        for call in tool_calls
    )
    structured_conf = result.get("confidence")
    try:
        structured_conf = float(structured_conf) if structured_conf is not None else None
    except (TypeError, ValueError):
        structured_conf = None
    status = str(result.get("status") or result.get("reflection_status") or "").lower()
    failed = status in {"failed", "error", "timeout", "truncated", "partial"} or bool(result.get("truncated"))
    has_refs = bool(result.get("data_references") or result.get("evidence_refs") or result.get("freshness"))

    confidence = 0.25
    if len(analysis) >= 200:
        confidence += 0.08
    if signals:
        confidence += 0.03
    if has_tool_evidence:
        confidence += 0.18
    if has_refs:
        confidence += 0.08
    if structured_conf is not None:
        # 模型自报分只作为弱证据，不能单独抬高上限。
        confidence += max(0.0, min(structured_conf, 1.0)) * 0.12

    fully_verified = has_tool_evidence and structured_conf is not None and has_refs and not failed
    cap = 0.82 if fully_verified else 0.60
    if not has_tool_evidence and structured_conf is None:
        cap = min(cap, 0.45)
    if failed:
        cap = min(cap, 0.35)
        confidence -= 0.10
    return round(max(0.0, min(confidence, cap)), 3)


# ── P0-A: 风险否决提取 ──────────────────────────

# 否决关键词（严重程度从高到低）
_VETO_KEYWORDS_HIGH = [
    "禁止加仓", "禁止买入", "不建议加仓", "不建议买入", "强烈建议不要",
    "风险过高", "极大风险", "严重高估", "危险",
]
_VETO_KEYWORDS_MEDIUM = [
    "不建议", "谨慎加仓", "谨慎买入", "风险较大", "需警惕",
]


def _extract_risk_veto(analysis: str, action_signals: list) -> Optional[dict]:
    """从风险专家分析文本中提取风险否决。

    仅当检测到否决关键词时返回 dict，否则返回 None。
    否决对象优先从 action_signals 的 BUY 信号取 target，退化为文本匹配。
    """
    if not analysis:
        return None

    text = analysis
    severity = None
    matched_reason = ""

    # 高严重度匹配
    for kw in _VETO_KEYWORDS_HIGH:
        if kw in text:
            severity = "high"
            matched_reason = kw
            break

    # 中严重度匹配
    if not severity:
        for kw in _VETO_KEYWORDS_MEDIUM:
            if kw in text:
                severity = "medium"
                matched_reason = kw
                break

    if not severity:
        return None

    # 提取被否决的标的：优先 BUY 信号的 target
    target = ""
    buy_signal = next((s for s in action_signals if s.get("type") == "BUY"), None)
    if buy_signal:
        target = buy_signal.get("target", "")
    if not target:
        # 退化：从文本匹配常见指数名
        target_match = re.search(r'(沪深300|中证500|中证1000|创业板|科创50|[\d]{6})', text)
        if target_match:
            target = target_match.group(1)

    # 提取更完整的理由（关键词前后 40 字）
    reason = matched_reason
    try:
        idx = text.find(matched_reason)
        if idx >= 0:
            start = max(0, idx - 20)
            end = min(len(text), idx + len(matched_reason) + 40)
            reason = text[start:end].replace("\n", " ").strip()
            if len(reason) > 80:
                reason = reason[:77] + "..."
    except Exception:
        pass

    return {
        "vetoed_action": "BUY",
        "target": target,
        "reason": reason,
        "severity": severity,
        "suggested_action": "HOLD",
    }


# ── P0-B: 持仓影响提取 ──────────────────────────

# 持仓影响动作关键词
_IMPACT_BUY_KEYWORDS = ["加仓", "买入", "定投", "建仓", "增持"]
_IMPACT_SELL_KEYWORDS = ["减仓", "卖出", "止盈", "止损", "减持", "清仓"]


def _extract_portfolio_impact(analysis: str, action_signals: list, asset_actions: list | None = None) -> Optional[dict]:
    """从分析文本和动作信号中提取持仓影响标注。

    返回结构化影响 dict，无法提取时返回 None。
    """
    if not analysis:
        return None

    text = analysis or ""
    source_actions = asset_actions or []
    # 确定动作类型；没有结构化 action 时仅输出标的方向，不读取全文数字。
    action = None
    if any(s.get("type") == "BUY" for s in source_actions):
        action = "加仓"
    elif any(s.get("type") == "SELL" for s in source_actions):
        action = "减仓"
    elif any(s.get("type") == "BUY" for s in action_signals):
        action = "加仓"
    elif any(s.get("type") in ("SELL", "REDUCE") for s in action_signals):
        action = "减仓"
    if not action:
        # 从 action_signals 推断
        if any(s.get("type") == "BUY" for s in action_signals):
            action = "加仓"
        elif any(s.get("type") in ("SELL", "REDUCE") for s in action_signals):
            action = "减仓"
        else:
            return None

    # 变动比例仅接受结构化字段；全文中的百分比可能是收益率/当前占比。
    suggested_change = ""
    structured_target = None
    for sig in source_actions:
        if sig.get("target") and sig.get("type") in {"BUY", "SELL"}:
            structured_target = sig
            change = sig.get("change_pct", sig.get("suggested_change_pct"))
            if isinstance(change, (int, float)):
                suggested_change = f"{change:g}%"
            elif isinstance(change, str) and re.fullmatch(r"\d+(?:\.\d+)?%?", change.strip()):
                suggested_change = change if change.strip().endswith("%") else f"{change.strip()}%"
            break

    # 提取受影响持仓
    affected = ""
    for s in (source_actions or action_signals):
        if s.get("target"):
            affected = s["target"]
            break
    if not affected:
        target_match = re.search(r'(沪深300|中证500|中证1000|创业板|科创50|(?:代码|基金)\s*[：:]?\s*\d{6})', text)
        if target_match:
            affected = target_match.group(1)
    if not affected:
        return None  # 无法确定标的，不生成影响标注

    # 没有同一快照提供的当前占比时保持未知，绝不填 0 再计算“变动后占比”。
    current_pct = None
    for s in source_actions:
        if s.get("target") == affected and isinstance(s.get("current_position_pct"), (int, float)):
            current_pct = float(s["current_position_pct"])
            break

    # 计算变动后占比
    post_pct = None
    denominator = None
    if structured_target:
        denominator = structured_target.get("denominator") or structured_target.get("pct_denominator")
    if suggested_change and current_pct is not None and denominator:
        try:
            change_val = float(re.search(r'(\d+(?:\.\d+)?)', suggested_change).group(1))
            if action == "加仓":
                post_pct = current_pct + change_val
            else:  # 减仓
                post_pct = max(0, current_pct - change_val)
        except Exception:
            post_pct = None

    # 风险检查
    risk_check = "待持仓快照核验"
    if post_pct is not None:
        risk_check = "已根据同一持仓快照计算"

    return {
        "affected_holding": affected,
        "action": action,
        "suggested_change": suggested_change,
        "current_position_pct": round(current_pct, 1) if current_pct is not None else None,
        "post_change_pct": round(post_pct, 1) if post_pct is not None else None,
        "denominator": denominator,
        "risk_check": risk_check,
    }


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
