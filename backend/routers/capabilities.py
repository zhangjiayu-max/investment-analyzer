"""能力中心路由 — /api/capabilities/*
聚合展示系统所有工具能力（内置 TOOLS + 3 个 MCP 客户端），含已暴露/未暴露标记、参数 schema、接入成本。
"""
import json
import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(tags=["capabilities"])


# ── 能力分类映射（按工具名前缀/关键词）──────────────

def _classify_tool(name: str) -> str:
    """根据工具名推断分类。"""
    if name.startswith("ttfund_") or name.startswith("fund_"):
        return "天天基金"
    if name.startswith("eastmoney_"):
        return "东方财富"
    if name.startswith("yingmi_"):
        return "盈米且慢"
    if name in ("query_valuation", "get_valuation_list"):
        return "估值分析"
    if name in ("search_knowledge", "get_author_opinions", "fetch_article"):
        return "知识检索"
    if name.startswith("query_portfolio") or name.startswith("query_fund") or name.startswith("analyze_") or name == "generate_portfolio_alert":
        return "持仓管理"
    if name == "calculate_metrics":
        return "计算工具"
    if name == "web_search":
        return "新闻搜索"
    if name.startswith("get_bond_") or name.startswith("get_macro_"):
        return "债市宏观"
    if name in ("analyze_attribution", "diagnose_behavior", "query_decision_accuracy", "query_valuation_forecast"):
        return "理财决策"
    return "其他"


def _source_of(name: str) -> str:
    """工具来源。"""
    if name.startswith("ttfund_"):
        return "ttfund"
    if name.startswith("eastmoney_"):
        return "eastmoney"
    if name.startswith("yingmi_"):
        return "yingmi"
    return "builtin"


def _extract_tool_fields(t: dict) -> dict:
    """从 TOOLS 数组条目中提取 name/description/parameters。

    兼容两种格式：
    - 扁平格式: {'name': '...', 'description': '...', 'parameters': {...}}
    - OpenAI function-calling: {'type': 'function', 'function': {'name': ..., 'description': ..., 'parameters': {...}}}
    """
    if "function" in t and isinstance(t["function"], dict):
        f = t["function"]
        return {
            "name": f.get("name", ""),
            "description": f.get("description", ""),
            "parameters": f.get("parameters", {}),
        }
    return {
        "name": t.get("name", ""),
        "description": t.get("description", ""),
        "parameters": t.get("parameters", {}),
    }


# ── MCP 未暴露能力清单（静态注册，与 mcp/ 客户端同步）──────────

_TTFUND_UNEXPOSED = [
    {"name": "fund_manager", "skill_id": "TTFUND_MANAGER_INFO", "description": "基金经理信息查询",
     "has_method": True, "cost": "low"},
    {"name": "fund_condition", "skill_id": "TTFUND_CONDITION_SELECT", "description": "条件选基（按类型/规模/收益等筛选）",
     "has_method": True, "cost": "low"},
    {"name": "fund_holding", "skill_id": "TTFUND_HOLDING_INFO", "description": "基金重仓股/持仓查询",
     "has_method": True, "cost": "low"},
    {"name": "fund_gold", "skill_id": "TTFUND_GOLD_INFO", "description": "黄金行情查询",
     "has_method": True, "cost": "low"},
    {"name": "fund_nav", "skill_id": "TTFUND_NAV_INFO", "description": "基金净值历史查询",
     "has_method": True, "cost": "low"},
    {"name": "fund_tg_strategy", "skill_id": "TTFUND_STRATEGY_INFO", "description": "投顾策略查询",
     "has_method": False, "cost": "high"},
    {"name": "fund_portfolio", "skill_id": "TTFUND_PORTFOLIO_ANALYSIS", "description": "基金组合分析",
     "has_method": False, "cost": "high"},
    {"name": "fund_favor", "skill_id": "TTFUND_FAVOR_ZX", "description": "自选基金管理",
     "has_method": False, "cost": "high"},
    {"name": "fund_backtest", "skill_id": "TTFUND_GROUP_BACKTEST", "description": "基金组合回测",
     "has_method": False, "cost": "high"},
]

_EASTMONEY_UNEXPOSED = [
    {"name": "screener", "endpoint": "selectSecurity", "description": "智能选股（按条件筛选股票）",
     "has_method": True, "cost": "low"},
    {"name": "finance_data", "endpoint": "searchFinanceData", "description": "金融数据查询（财务指标）",
     "has_method": True, "cost": "low"},
    {"name": "macro_data", "endpoint": "searchMacroData", "description": "宏观数据查询（GDP/CPI/PMI 等）",
     "has_method": True, "cost": "low"},
    {"name": "comparable", "endpoint": "comparable-company-analysis", "description": "可比公司分析",
     "has_method": True, "cost": "low"},
    {"name": "industry", "endpoint": "write/industry/research", "description": "行业研究报告（耗时约 20 分钟，需异步）",
     "has_method": True, "cost": "low", "async_required": True},
    {"name": "earnings", "endpoint": "write/performance/comment", "description": "业绩点评写作",
     "has_method": False, "cost": "high"},
    {"name": "topic", "endpoint": "write/thematic/research", "description": "主题研究报告",
     "has_method": False, "cost": "high"},
]


# ── API 端点 ──────────────────────────────────

@router.get("/api/capabilities/overview")
def api_capabilities_overview():
    """能力总览统计。"""
    try:
        from tools import TOOLS
        builtin_count = len(TOOLS)
        ttfund_exposed = sum(1 for t in TOOLS if _extract_tool_fields(t)["name"].startswith("ttfund_"))
        eastmoney_exposed = sum(1 for t in TOOLS if _extract_tool_fields(t)["name"].startswith("eastmoney_"))
        yingmi_exposed = sum(1 for t in TOOLS if _extract_tool_fields(t)["name"].startswith("yingmi_"))
        builtin_exposed = builtin_count - ttfund_exposed - eastmoney_exposed - yingmi_exposed

        ttfund_total = ttfund_exposed + len(_TTFUND_UNEXPOSED)
        eastmoney_total = eastmoney_exposed + len(_EASTMONEY_UNEXPOSED)
        yingmi_total = yingmi_exposed
        total = builtin_exposed + ttfund_total + eastmoney_total + yingmi_total
        exposed = builtin_exposed + ttfund_exposed + eastmoney_exposed + yingmi_exposed

        return {
            "total": total,
            "exposed": exposed,
            "unexposed": total - exposed,
            "by_source": {
                "builtin": {"total": builtin_exposed, "exposed": builtin_exposed, "unexposed": 0},
                "ttfund": {"total": ttfund_total, "exposed": ttfund_exposed, "unexposed": len(_TTFUND_UNEXPOSED)},
                "eastmoney": {"total": eastmoney_total, "exposed": eastmoney_exposed, "unexposed": len(_EASTMONEY_UNEXPOSED)},
                "yingmi": {"total": yingmi_total, "exposed": yingmi_exposed, "unexposed": 0},
            },
        }
    except Exception as e:
        logger.error(f"能力总览失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/api/capabilities/tools")
def api_capabilities_tools(source: str = "", category: str = "", exposed_only: bool = False):
    """完整能力清单（含分类、参数、来源、暴露状态）。

    Args:
        source: 来源筛选（builtin/ttfund/eastmoney/yingmi），空=全部
        category: 分类筛选，空=全部
        exposed_only: 仅返回已暴露能力
    """
    try:
        from tools import TOOLS

        # 查 tool_registry 启用状态
        enabled_map = {}
        try:
            from db._conn import DB_PATH
            import sqlite3
            conn = sqlite3.connect(str(DB_PATH))
            rows = conn.execute("SELECT name, enabled FROM tool_registry").fetchall()
            conn.close()
            enabled_map = {r[0]: bool(r[1]) for r in rows}
        except Exception:
            pass

        result = []

        # 1. 已暴露工具（来自 TOOLS 数组）
        exposed_names = set()
        for t in TOOLS:
            fields = _extract_tool_fields(t)
            name = fields["name"]
            exposed_names.add(name)
            src = _source_of(name)
            cat = _classify_tool(name)

            # 筛选
            if source and src != source:
                continue
            if category and cat != category:
                continue

            result.append({
                "name": name,
                "description": fields["description"],
                "category": cat,
                "source": src,
                "exposed": True,
                "enabled": enabled_map.get(name, True),
                "parameters": fields["parameters"],
                "has_method": True,
                "cost": "none",
            })

        # 2. 未暴露的 MCP 能力（ttfund + eastmoney）
        if not exposed_only:
            for item in _TTFUND_UNEXPOSED:
                name = item["name"]
                src = "ttfund"
                cat = "天天基金"
                if source and src != source:
                    continue
                if category and cat != category:
                    continue
                result.append({
                    "name": name,
                    "description": item["description"],
                    "category": cat,
                    "source": src,
                    "exposed": False,
                    "enabled": False,
                    "parameters": {},
                    "has_method": item.get("has_method", False),
                    "cost": item.get("cost", "high"),
                    "skill_id": item.get("skill_id", ""),
                    "async_required": item.get("async_required", False),
                })

            for item in _EASTMONEY_UNEXPOSED:
                name = item["name"]
                src = "eastmoney"
                cat = "东方财富"
                if source and src != source:
                    continue
                if category and cat != category:
                    continue
                result.append({
                    "name": name,
                    "description": item["description"],
                    "category": cat,
                    "source": src,
                    "exposed": False,
                    "enabled": False,
                    "parameters": {},
                    "has_method": item.get("has_method", False),
                    "cost": item.get("cost", "high"),
                    "endpoint": item.get("endpoint", ""),
                    "async_required": item.get("async_required", False),
                })

        return {"tools": result, "total": len(result)}
    except Exception as e:
        logger.error(f"能力清单查询失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/api/capabilities/mcp/unexposed")
def api_capabilities_unexposed():
    """仅未暴露的 MCP 能力（用于扩展面板）。"""
    try:
        return {
            "ttfund": _TTFUND_UNEXPOSED,
            "eastmoney": _EASTMONEY_UNEXPOSED,
            "total": len(_TTFUND_UNEXPOSED) + len(_EASTMONEY_UNEXPOSED),
            "low_cost": sum(1 for x in _TTFUND_UNEXPOSED + _EASTMONEY_UNEXPOSED if x.get("cost") == "low"),
            "high_cost": sum(1 for x in _TTFUND_UNEXPOSED + _EASTMONEY_UNEXPOSED if x.get("cost") == "high"),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/capabilities/integration-guide")
def api_integration_guide(name: str):
    """获取指定未暴露能力的接入指引代码片段。

    Args:
        name: 能力名（如 fund_manager / screener）
    """
    try:
        # 查找能力定义
        item = None
        src = ""
        for x in _TTFUND_UNEXPOSED:
            if x["name"] == name:
                item = x
                src = "ttfund"
                break
        if not item:
            for x in _EASTMONEY_UNEXPOSED:
                if x["name"] == name:
                    item = x
                    src = "eastmoney"
                    break
        if not item:
            raise HTTPException(404, f"未找到能力: {name}")

        # 生成接入指引
        tool_name = f"{src}_{name}" if src == "ttfund" else f"eastmoney_{name}"

        if src == "ttfund":
            client_call = f"client.{name}()" if item.get("has_method") else f"client._invoke('{item['skill_id']}', **arguments)"
            guide = f"""# 接入 {name} 能力

## 1. 在 tools/__init__.py 的 TOOLS 数组追加定义

```python
{{
    "name": "{tool_name}",
    "description": "{item['description']}",
    "parameters": {{
        "type": "object",
        "properties": {{
            # TODO: 根据实际参数补充
        }},
        "required": []
    }}
}},
```

## 2. 在 tools/__init__.py 的 execute_tool() 追加分发

```python
elif name == "{tool_name}":
    from mcp.ttfund_client import TtfundClient
    client = TtfundClient()
    result = {client_call}
    return json.dumps(result, ensure_ascii=False)
```

## 3. 验证
- python3 -m py_compile tools/__init__.py
- 重启后端
- 在能力中心确认 {tool_name} 已变为"已暴露"
"""
        else:
            client_call = f"client.{name}(arguments)" if item.get("has_method") else f"# TODO: 需在 eastmoney_client.py 新增 {name} 方法"
            guide = f"""# 接入 {name} 能力

## 1. 在 tools/__init__.py 的 TOOLS 数组追加定义

```python
{{
    "name": "{tool_name}",
    "description": "{item['description']}",
    "parameters": {{
        "type": "object",
        "properties": {{
            "query": {{"type": "string", "description": "查询内容"}}
        }},
        "required": ["query"]
    }}
}},
```

## 2. 在 tools/__init__.py 的 execute_tool() 追加分发

```python
elif name == "{tool_name}":
    from mcp.eastmoney_client import EastMoneyClient
    client = EastMoneyClient()
    result = {client_call}
    return json.dumps(result, ensure_ascii=False)
```

{'## ⚠️ 注意：此能力耗时约 20 分钟，需用 asyncio 异步处理' if item.get('async_required') else ''}

## 3. 验证
- python3 -m py_compile tools/__init__.py
- 重启后端
"""

        return {"name": name, "tool_name": tool_name, "source": src, "guide": guide, "cost": item.get("cost")}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"接入指引生成失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))
