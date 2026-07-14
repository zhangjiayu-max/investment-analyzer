"""工具结果广播 — 专家工具调用结果结构化提取并写入黑板，供后续专家直接引用。

设计要点：
- 仅对白名单内的数据查询工具做结构化提取（query_valuation/search_knowledge 等）
- 提取是纯 Python 正则/JSON 解析，无 LLM 调用
- ToolBroadcastEntry 不占 BlackboardEntry 的 6 条配额，独立存储
- 后续专家在 to_context_text() 中看到"已有工具结果"区块，避免重复查询

集成方式（在 multi_agent.py 的 ReAct 循环中）：
    from agent.tool_broadcast import extract_broadcast, should_broadcast
    if should_broadcast(tool_name):
        entry = extract_broadcast(tool_name, args, result, agent_key, agent_name)
        if entry:
            blackboard.write_tool_broadcast(entry)
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── 可广播的工具白名单 ─────────────────────────
# 只广播数据查询类工具的结果，不广播长文本工具（如 fetch_article）
_BROADCASTABLE_TOOLS = {
    "query_valuation",
    "search_knowledge",
    "query_portfolio",
    "query_fund_info",
    "ttfund_fund_info",
    "ttfund_search",
    "ttfund_fund_manager",
    "ttfund_fund_nav",
    "ttfund_fund_condition",
    "get_index_valuation",
    "get_valuation_history",
    "get_bond_yield_curve",
    "get_bond_market_overview",
}


def should_broadcast(tool_name: str) -> bool:
    """判断工具是否需要广播。"""
    return tool_name in _BROADCASTABLE_TOOLS


@dataclass
class ToolBroadcastEntry:
    """工具广播条目 — 一个专家的工具调用结果摘要。"""
    tool_name: str
    query: str                    # 工具查询的主要参数（如 index_code/query/keyword）
    caller_agent: str             # 调用方 agent_key
    caller_agent_name: str        # 调用方 agent_name
    key_fields: dict = field(default_factory=dict)  # 结构化提取的关键字段
    summary: str = ""             # 一句话摘要（用于 to_context_text 展示）
    raw_result: str = ""          # 原始结果 JSON（截断到 2000 字，供需要时引用）
    timestamp: float = 0.0
    # 数据状态：success / data_missing / error
    status: str = "success"

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "query": self.query,
            "caller_agent": self.caller_agent,
            "caller_agent_name": self.caller_agent_name,
            "key_fields": self.key_fields,
            "summary": self.summary,
            "status": self.status,
            "timestamp": self.timestamp,
        }


def extract_broadcast(tool_name: str, args: dict, result: str,
                      agent_key: str, agent_name: str) -> Optional[ToolBroadcastEntry]:
    """从工具调用结果中提取结构化广播条目。

    Args:
        tool_name: 工具名称
        args: 工具参数
        result: 工具返回的 JSON 字符串
        agent_key: 调用方 agent_key
        agent_name: 调用方 agent_name

    Returns:
        ToolBroadcastEntry 或 None（提取失败时）
    """
    if not should_broadcast(tool_name):
        return None

    try:
        # 解析结果 JSON
        parsed = json.loads(result) if isinstance(result, str) else result
        if not isinstance(parsed, dict):
            return None

        # 提取查询参数
        query = _extract_query(tool_name, args)

        # 判断数据状态
        status = _detect_status(parsed)

        # 按工具类型提取关键字段
        key_fields = _extract_key_fields(tool_name, args, parsed)
        summary = _build_summary(tool_name, query, key_fields, status, parsed)

        return ToolBroadcastEntry(
            tool_name=tool_name,
            query=query,
            caller_agent=agent_key,
            caller_agent_name=agent_name,
            key_fields=key_fields,
            summary=summary,
            raw_result=result[:2000] if isinstance(result, str) else str(result)[:2000],
            timestamp=time.time(),
            status=status,
        )
    except Exception as e:
        logger.debug(f"[tool_broadcast] 提取失败 {tool_name}: {e}")
        return None


def _extract_query(tool_name: str, args: dict) -> str:
    """提取工具查询的主要参数。"""
    if not args:
        return ""
    # 优先级：index_code > keyword > query > fund_code > name
    for key in ("index_code", "index_name", "keyword", "query", "fund_code", "name", "symbol"):
        val = args.get(key)
        if val:
            return str(val)
    return str(args)[:50]


def _detect_status(parsed: dict) -> str:
    """检测数据状态。"""
    err = parsed.get("error")
    if err:
        err_lower = str(err).lower()
        if any(kw in err_lower for kw in ["未找到", "not found", "no data", "无数据"]):
            return "data_missing"
        return "error"
    # 检查空数据
    data = parsed.get("data")
    if data is None:
        # 有些工具直接返回字段，不是包在 data 里
        # 检查已知的列表型字段是否为空
        for list_key in ("results", "articles", "items", "holdings"):
            val = parsed.get(list_key)
            if val is not None:
                if isinstance(val, list) and len(val) == 0:
                    return "data_missing"
                # 有非空列表数据，视为成功
                break
        else:
            # 没有任何已知列表字段，检查是否有标量数据字段
            if not any(k in parsed for k in ("index_name", "fund_name", "PE", "PB", "total_value")):
                return "data_missing"
    elif isinstance(data, (list, dict)) and len(data) == 0:
        return "data_missing"
    return "success"


def _extract_key_fields(tool_name: str, args: dict, parsed: dict) -> dict:
    """按工具类型提取关键字段。"""
    fields = {}

    try:
        if tool_name == "query_valuation":
            # 估值查询：提取 index_name/PE/PB/percentile/snapshot_date
            data = parsed.get("data", parsed)
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                for k in ("index_name", "index_code", "PE", "PB", "pe_ttm", "pb",
                          "percentile", "snapshot_date", "current_point"):
                    val = data.get(k)
                    if val is not None:
                        fields[k] = val
            # 从 args 补充 index_name
            if "index_name" not in fields and args.get("index_name"):
                fields["index_name"] = args["index_name"]
            if "index_code" not in fields and args.get("index_code"):
                fields["index_code"] = args["index_code"]

        elif tool_name == "search_knowledge":
            # 知识检索：提取 top3 文档标题
            results = parsed.get("results", parsed.get("articles", []))
            if isinstance(results, list):
                top3 = []
                for item in results[:3]:
                    if isinstance(item, dict):
                        title = item.get("title", item.get("name", ""))
                        if title:
                            top3.append(str(title)[:50])
                if top3:
                    fields["top3_titles"] = top3
                    fields["total_hits"] = len(results)

        elif tool_name == "query_portfolio":
            # 持仓查询：提取总资产/持仓数/前3重仓
            data = parsed.get("data", parsed)
            if isinstance(data, dict):
                total = data.get("total_value", data.get("total_cost"))
                if total:
                    fields["total_value"] = total
                holdings = data.get("holdings", [])
                if isinstance(holdings, list):
                    fields["holding_count"] = len(holdings)
                    top3 = []
                    for h in holdings[:3]:
                        if isinstance(h, dict):
                            name = h.get("fund_name", h.get("fund_code", ""))
                            pct = h.get("weight", h.get("position_pct", ""))
                            if name:
                                top3.append(f"{name}({pct}%)" if pct else name)
                    if top3:
                        fields["top3_holdings"] = top3

        elif tool_name in ("query_fund_info", "ttfund_fund_info"):
            # 基金信息：提取名称/类型/规模
            data = parsed.get("data", parsed)
            if isinstance(data, dict):
                for k in ("fund_name", "fund_code", "fund_type", "scale", "management_fee"):
                    val = data.get(k)
                    if val is not None:
                        fields[k] = val
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                for k in ("fund_name", "fund_code", "fund_type", "scale"):
                    val = data[0].get(k)
                    if val is not None:
                        fields[k] = val

        elif tool_name == "ttfund_fund_manager":
            data = parsed.get("data", parsed)
            if isinstance(data, dict):
                for k in ("manager_name", "career_years", "scale", "return_rate"):
                    val = data.get(k)
                    if val is not None:
                        fields[k] = val

        elif tool_name in ("ttfund_search",):
            results = parsed.get("data", parsed.get("results", []))
            if isinstance(results, list):
                top3 = []
                for item in results[:3]:
                    if isinstance(item, dict):
                        name = item.get("fund_name", item.get("name", ""))
                        code = item.get("fund_code", item.get("code", ""))
                        if name:
                            top3.append(f"{name}({code})" if code else name)
                if top3:
                    fields["top3_funds"] = top3

        elif tool_name in ("get_bond_yield_curve", "get_bond_market_overview"):
            # 债券数据：提取关键收益率
            data = parsed.get("data", parsed)
            if isinstance(data, dict):
                for k in ("y10", "y2", "y10_y2_spread", "temperature", "market_temp"):
                    val = data.get(k)
                    if val is not None:
                        fields[k] = val

    except Exception as e:
        logger.debug(f"[tool_broadcast] 字段提取异常 {tool_name}: {e}")

    return fields


def _build_summary(tool_name: str, query: str, key_fields: dict,
                   status: str, parsed: dict) -> str:
    """构建一句话摘要，用于 to_context_text 展示。"""
    if status == "data_missing":
        return f"查询「{query}」未找到数据"
    if status == "error":
        err = parsed.get("error", "未知错误")
        return f"查询「{query}」失败: {str(err)[:50]}"

    # 成功：提取关键字段拼摘要
    parts = []
    for k, v in list(key_fields.items())[:4]:
        if k in ("top3_titles", "top3_holdings", "top3_funds"):
            parts.append(f"{k}: {','.join(str(x) for x in (v if isinstance(v, list) else [v]))}")
        elif isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        else:
            parts.append(f"{k}={v}")

    if parts:
        return f"查询「{query}」: {'; '.join(parts)}"
    return f"查询「{query}」完成"


def format_broadcasts_for_context(broadcasts: list, exclude_agent: str = None) -> str:
    """格式化工具广播列表为上下文文本。

    Args:
        broadcasts: ToolBroadcastEntry 列表
        exclude_agent: 排除的 agent_key（避免专家看到自己的工具调用）

    Returns:
        格式化的文本区块，空字符串表示无广播
    """
    if not broadcasts:
        return ""

    lines = []
    for b in broadcasts:
        if exclude_agent and b.caller_agent == exclude_agent:
            continue
        # 用调用方名称 + 工具 + 摘要
        lines.append(f"- {b.caller_agent_name}调用{b.tool_name}「{b.query}」: {b.summary}")

    if not lines:
        return ""

    return "## 已有工具结果（可直接引用，无需重复查询）\n" + "\n".join(lines)
