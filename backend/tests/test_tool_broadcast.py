"""工具结果广播模块测试 — 结构化提取 + 黑板集成。

覆盖：
- should_broadcast 白名单判断
- extract_broadcast 各类工具的结构化提取（估值/知识/持仓/基金/债券）
- _detect_status 数据状态检测（success/data_missing/error）
- format_broadcasts_for_context 上下文格式化
- Blackboard.write_tool_broadcast / get_tool_broadcasts / to_context_text 集成
"""
import json
import time
import pytest

from agent.tool_broadcast import (
    should_broadcast,
    extract_broadcast,
    format_broadcasts_for_context,
    ToolBroadcastEntry,
)


# ── should_broadcast ──────────────────────────────

def test_should_broadcast_whitelist():
    assert should_broadcast("query_valuation") is True
    assert should_broadcast("search_knowledge") is True
    assert should_broadcast("query_portfolio") is True
    assert should_broadcast("ttfund_fund_info") is True


def test_should_broadcast_not_in_whitelist():
    assert should_broadcast("fetch_article") is False
    assert should_broadcast("unknown_tool") is False
    assert should_broadcast("") is False


# ── extract_broadcast: query_valuation ─────────────

def test_extract_valuation_success():
    """估值查询成功：提取 index_name/PE/PB/percentile。"""
    args = {"index_code": "000300", "index_name": "沪深300"}
    result = json.dumps({
        "data": {
            "index_name": "沪深300",
            "index_code": "000300",
            "PE": 12.5,
            "PB": 1.3,
            "percentile": 25.0,
            "snapshot_date": "2026-07-14",
        }
    })
    entry = extract_broadcast("query_valuation", args, result, "valuation_expert", "估值专家")
    assert entry is not None
    assert entry.tool_name == "query_valuation"
    assert entry.caller_agent == "valuation_expert"
    assert entry.status == "success"
    assert entry.key_fields.get("PE") == 12.5
    assert entry.key_fields.get("index_name") == "沪深300"
    assert "沪深300" in entry.summary or "000300" in entry.summary


def test_extract_valuation_data_missing():
    """估值查询未找到数据：status=data_missing。"""
    args = {"index_code": "999999", "index_name": "未知指数"}
    result = json.dumps({"error": "未找到该指数的估值数据"})
    entry = extract_broadcast("query_valuation", args, result, "valuation_expert", "估值专家")
    assert entry is not None
    assert entry.status == "data_missing"
    assert "未找到" in entry.summary


def test_extract_valuation_error():
    """估值查询异常：status=error。"""
    args = {"index_code": "000300"}
    result = json.dumps({"error": "API 超时"})
    entry = extract_broadcast("query_valuation", args, result, "valuation_expert", "估值专家")
    assert entry is not None
    assert entry.status == "error"
    assert "API 超时" in entry.summary


# ── extract_broadcast: search_knowledge ────────────

def test_extract_search_knowledge_success():
    """知识检索：提取 top3 文档标题。"""
    args = {"query": "估值 安全边际"}
    result = json.dumps({
        "results": [
            {"title": "聪明的投资者", "content": "..."},
            {"title": "证券分析", "content": "..."},
            {"title": "漫步华尔街", "content": "..."},
            {"title": "第四篇不需要提取", "content": "..."},
        ]
    })
    entry = extract_broadcast("search_knowledge", args, result, "risk_expert", "风险专家")
    assert entry is not None
    assert entry.status == "success"
    assert entry.key_fields.get("total_hits") == 4
    assert len(entry.key_fields.get("top3_titles", [])) == 3


def test_extract_search_knowledge_empty():
    """知识检索无结果：status=data_missing。"""
    args = {"query": "不存在的内容"}
    result = json.dumps({"results": []})
    entry = extract_broadcast("search_knowledge", args, result, "risk_expert", "风险专家")
    assert entry is not None
    assert entry.status == "data_missing"


# ── extract_broadcast: query_portfolio ─────────────

def test_extract_portfolio_success():
    """持仓查询：提取总资产/持仓数/前3重仓。"""
    args = {}
    result = json.dumps({
        "data": {
            "total_value": 100000,
            "holdings": [
                {"fund_name": "基金A", "weight": 30},
                {"fund_name": "基金B", "weight": 25},
                {"fund_name": "基金C", "weight": 20},
                {"fund_name": "基金D", "weight": 15},
            ]
        }
    })
    entry = extract_broadcast("query_portfolio", args, result, "allocation_advisor", "配置专家")
    assert entry is not None
    assert entry.status == "success"
    assert entry.key_fields.get("total_value") == 100000
    assert entry.key_fields.get("holding_count") == 4
    assert len(entry.key_fields.get("top3_holdings", [])) == 3


# ── extract_broadcast: 边界情况 ────────────────────

def test_extract_non_broadcastable_tool_returns_none():
    """非白名单工具不广播。"""
    entry = extract_broadcast("fetch_article", {}, "{}", "agent", "专家")
    assert entry is None


def test_extract_invalid_json_returns_none():
    """无效 JSON 返回 None。"""
    entry = extract_broadcast("query_valuation", {}, "not a json", "agent", "专家")
    assert entry is None


def test_extract_non_dict_result_returns_none():
    """非 dict 结果返回 None。"""
    entry = extract_broadcast("query_valuation", {}, "[1, 2, 3]", "agent", "专家")
    assert entry is None


# ── format_broadcasts_for_context ──────────────────

def test_format_broadcasts_empty():
    assert format_broadcasts_for_context([]) == ""


def test_format_broadcasts_excludes_caller():
    """排除自己的工具调用，避免自引用。"""
    entries = [
        ToolBroadcastEntry(
            tool_name="query_valuation", query="沪深300",
            caller_agent="valuation_expert", caller_agent_name="估值专家",
            summary="PE=12.5", timestamp=time.time(),
        ),
        ToolBroadcastEntry(
            tool_name="search_knowledge", query="估值",
            caller_agent="risk_expert", caller_agent_name="风险专家",
            summary="找到3篇", timestamp=time.time(),
        ),
    ]
    # 排除 valuation_expert
    text = format_broadcasts_for_context(entries, exclude_agent="valuation_expert")
    assert "估值专家" not in text
    assert "风险专家" in text
    assert "已有工具结果" in text


def test_format_broadcasts_all_excluded_returns_empty():
    """全部被排除时返回空字符串。"""
    entries = [
        ToolBroadcastEntry(
            tool_name="query_valuation", query="沪深300",
            caller_agent="valuation_expert", caller_agent_name="估值专家",
            summary="PE=12.5", timestamp=time.time(),
        ),
    ]
    text = format_broadcasts_for_context(entries, exclude_agent="valuation_expert")
    assert text == ""


# ── Blackboard 工具广播集成 ────────────────────────

def test_blackboard_write_and_get_broadcast():
    """黑板写入工具广播并读取。"""
    from agent.blackboard import Blackboard
    bb = Blackboard(max_broadcasts=5)
    entry = ToolBroadcastEntry(
        tool_name="query_valuation", query="沪深300",
        caller_agent="valuation_expert", caller_agent_name="估值专家",
        key_fields={"PE": 12.5}, summary="PE=12.5", timestamp=time.time(),
    )
    bb.write_tool_broadcast(entry)
    broadcasts = bb.get_tool_broadcasts()
    assert len(broadcasts) == 1
    assert broadcasts[0].tool_name == "query_valuation"


def test_blackboard_broadcast_fifo_eviction():
    """超过 max_broadcasts 时 FIFO 淘汰最早条目。"""
    from agent.blackboard import Blackboard
    bb = Blackboard(max_broadcasts=3)
    for i in range(5):
        entry = ToolBroadcastEntry(
            tool_name="query_valuation", query=f"指数{i}",
            caller_agent="valuation_expert", caller_agent_name="估值专家",
            summary=f"PE={i}", timestamp=time.time() + i,
        )
        bb.write_tool_broadcast(entry)
    broadcasts = bb.get_tool_broadcasts()
    assert len(broadcasts) == 3
    # 最早的两条被淘汰，保留最后3条
    queries = [b.query for b in broadcasts]
    assert "指数0" not in queries
    assert "指数1" not in queries
    assert "指数4" in queries


def test_blackboard_get_broadcasts_exclude_agent():
    """get_tool_broadcasts 排除指定 agent。"""
    from agent.blackboard import Blackboard
    bb = Blackboard()
    bb.write_tool_broadcast(ToolBroadcastEntry(
        tool_name="query_valuation", query="沪深300",
        caller_agent="valuation_expert", caller_agent_name="估值专家",
        summary="PE=12.5", timestamp=time.time(),
    ))
    bb.write_tool_broadcast(ToolBroadcastEntry(
        tool_name="search_knowledge", query="估值",
        caller_agent="risk_expert", caller_agent_name="风险专家",
        summary="找到3篇", timestamp=time.time(),
    ))
    # 排除 valuation_expert
    filtered = bb.get_tool_broadcasts(exclude_agent="valuation_expert")
    assert len(filtered) == 1
    assert filtered[0].caller_agent == "risk_expert"


def test_blackboard_to_context_text_includes_broadcasts():
    """to_context_text 包含工具广播区块。"""
    from agent.blackboard import Blackboard
    bb = Blackboard()
    bb.write_tool_broadcast(ToolBroadcastEntry(
        tool_name="query_valuation", query="沪深300",
        caller_agent="valuation_expert", caller_agent_name="估值专家",
        key_fields={"PE": 12.5}, summary="PE=12.5", timestamp=time.time(),
    ))
    text = bb.to_context_text()
    assert "已有工具结果" in text
    assert "估值专家" in text
    assert "query_valuation" in text


def test_blackboard_to_context_text_exclude_self_broadcast():
    """专家自己的工具调用不回显给自己。"""
    from agent.blackboard import Blackboard
    bb = Blackboard()
    bb.write_tool_broadcast(ToolBroadcastEntry(
        tool_name="query_valuation", query="沪深300",
        caller_agent="valuation_expert", caller_agent_name="估值专家",
        summary="PE=12.5", timestamp=time.time(),
    ))
    # 估值专家自己看上下文：不应看到自己的广播
    text = bb.to_context_text(exclude_agent="valuation_expert")
    assert "已有工具结果" not in text
