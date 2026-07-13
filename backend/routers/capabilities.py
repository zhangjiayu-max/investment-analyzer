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
            from db._conn import _get_conn
            conn = _get_conn()
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


# ── 工具调用统计 ──────────────────────────────

@router.get("/api/capabilities/stats")
def api_capabilities_stats(days: int = 7, tool_name: str = ""):
    """工具调用统计 — 聚合 tool_audit_logs。

    Args:
        days: 时间窗口（1/7/30/90）
        tool_name: 指定工具名（空=全部）
    """
    try:
        from db._conn import _get_conn
        conn = _get_conn()

        days = max(1, min(days, 365))
        where_clause = f"WHERE created_at >= datetime('now','-{days} days')"
        params: list = []
        if tool_name:
            where_clause += " AND tool_name = ?"
            params.append(tool_name)

        # 总览
        row = conn.execute(
            f"SELECT COUNT(*) as cnt, "
            f"SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as ok, "
            f"COALESCE(ROUND(AVG(duration_ms),1),0) as avg_ms "
            f"FROM tool_audit_logs {where_clause}", params
        ).fetchone()
        total_calls = row["cnt"] or 0
        total_success = row["ok"] or 0
        total_failed = total_calls - total_success
        overall_rate = round(total_success / total_calls, 4) if total_calls > 0 else 0.0

        # 按工具分组
        by_tool_rows = conn.execute(
            f"SELECT tool_name, COUNT(*) as cnt, "
            f"SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as ok, "
            f"COALESCE(ROUND(AVG(duration_ms),1),0) as avg_ms, "
            f"COALESCE(MAX(duration_ms),0) as max_ms "
            f"FROM tool_audit_logs {where_clause} "
            f"GROUP BY tool_name ORDER BY cnt DESC", params
        ).fetchall()

        # P95 计算 + 错误分类分布（按工具）
        by_tool = []
        for r in by_tool_rows:
            # P95：取该工具所有耗时的 95 分位
            p95_row = conn.execute(
                f"SELECT duration_ms FROM tool_audit_logs "
                f"{where_clause} AND tool_name = ? "
                f"ORDER BY duration_ms ASC", params + [r["tool_name"]]
            ).fetchall()
            durations = [x["duration_ms"] or 0 for x in p95_row]
            p95 = _percentile(durations, 95)

            # 错误分类
            err_rows = conn.execute(
                f"SELECT error_category, COUNT(*) as c FROM tool_audit_logs "
                f"{where_clause} AND tool_name = ? "
                f"GROUP BY error_category", params + [r["tool_name"]]
            ).fetchall()
            err_map = {x["error_category"]: x["c"] for x in err_rows}

            calls = r["cnt"] or 0
            ok = r["ok"] or 0
            by_tool.append({
                "tool_name": r["tool_name"],
                "calls": calls,
                "success": ok,
                "failed": calls - ok,
                "success_rate": round(ok / calls, 4) if calls > 0 else 0.0,
                "avg_duration_ms": r["avg_ms"],
                "max_duration_ms": r["max_ms"],
                "p95_duration_ms": p95,
                "error_categories": err_map,
            })

        # 按天趋势
        by_day_rows = conn.execute(
            f"SELECT DATE(created_at) as d, COUNT(*) as cnt, "
            f"SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as ok "
            f"FROM tool_audit_logs {where_clause} "
            f"GROUP BY DATE(created_at) ORDER BY d ASC", params
        ).fetchall()
        by_day = [{"date": r["d"], "calls": r["cnt"], "success": r["ok"]} for r in by_day_rows]

        # 错误分类总分布
        err_total_rows = conn.execute(
            f"SELECT error_category, COUNT(*) as c FROM tool_audit_logs "
            f"{where_clause} GROUP BY error_category", params
        ).fetchall()
        by_error = {r["error_category"]: r["c"] for r in err_total_rows}

        conn.close()

        return {
            "days": days,
            "total_calls": total_calls,
            "total_success": total_success,
            "total_failed": total_failed,
            "overall_success_rate": overall_rate,
            "avg_duration_ms": row["avg_ms"],
            "by_tool": by_tool,
            "by_day": by_day,
            "by_error_category": by_error,
        }
    except Exception as e:
        logger.error(f"工具调用统计失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))


def _percentile(values: list, pct: float) -> float:
    """计算分位数（线性插值法）。"""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


@router.get("/api/capabilities/stats/recent")
def api_capabilities_recent(tool_name: str = "", limit: int = 20, success: str = ""):
    """最近工具调用记录。

    Args:
        tool_name: 指定工具（空=全部）
        limit: 返回条数（最大 100）
        success: 'true'=仅成功, 'false'=仅失败, ''=全部
    """
    try:
        from db._conn import _get_conn
        conn = _get_conn()

        limit = max(1, min(limit, 100))
        where_parts = []
        params: list = []
        if tool_name:
            where_parts.append("tool_name = ?")
            params.append(tool_name)
        if success == "true":
            where_parts.append("success = 1")
        elif success == "false":
            where_parts.append("success = 0")
        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        rows = conn.execute(
            f"SELECT id, trace_id, tool_name, arguments, result_preview, "
            f"success, error_category, duration_ms, created_at "
            f"FROM tool_audit_logs {where_clause} "
            f"ORDER BY id DESC LIMIT ?", params + [limit]
        ).fetchall()
        conn.close()

        return {
            "items": [{
                "id": r["id"],
                "trace_id": r["trace_id"] or "",
                "tool_name": r["tool_name"],
                "arguments": r["arguments"] or "",
                "result_preview": r["result_preview"] or "",
                "success": bool(r["success"]),
                "error_category": r["error_category"] or "none",
                "duration_ms": r["duration_ms"] or 0,
                "created_at": r["created_at"] or "",
            } for r in rows],
            "total": len(rows),
        }
    except Exception as e:
        logger.error(f"最近调用记录查询失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ── 工具调试 ──────────────────────────────────

@router.post("/api/capabilities/debug")
def api_capabilities_debug(body: dict):
    """工具调试执行 — 输入参数 → 执行 → 返回真实结果。

    自动写入 tool_audit_logs（trace_id 前缀 debug_）。

    请求体: {"tool_name": "...", "arguments": {...}, "trace_id": "..."(可选)}
    """
    try:
        import time as _time
        import uuid as _uuid
        from tools import TOOLS, execute_tool

        tool_name = body.get("tool_name", "").strip()
        arguments = body.get("arguments", {}) or {}
        trace_id = body.get("trace_id", "").strip() or f"debug_{_uuid.uuid4().hex[:8]}"

        if not tool_name:
            raise HTTPException(400, "tool_name 不能为空")

        # 校验：仅允许已暴露工具
        exposed_names = {_extract_tool_fields(t)["name"] for t in TOOLS}
        if tool_name not in exposed_names:
            raise HTTPException(403, f"工具 {tool_name} 未暴露，禁止调试")

        # 执行（复用 execute_tool，自动写审计日志 + 超时保护）
        t0 = _time.time()
        result_str = execute_tool(tool_name, arguments, trace_id=trace_id, timeout=30)
        duration_ms = int((_time.time() - t0) * 1000)

        # 解析结果
        parsed = None
        try:
            parsed = json.loads(result_str)
        except Exception:
            parsed = None

        # 判断成功/失败
        success = True
        error_category = "none"
        if isinstance(parsed, dict) and "error" in parsed:
            success = False
            error_category = parsed.get("error_category", "tool_error")
        elif parsed is None and not result_str:
            success = False
            error_category = "tool_error"

        # 结果截断（5000 字符）
        result_display = result_str if len(result_str) <= 5000 else result_str[:5000] + "\n...(结果已截断)"

        return {
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result_display,
            "parsed_result": parsed,
            "duration_ms": duration_ms,
            "success": success,
            "error_category": error_category,
            "trace_id": trace_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"工具调试失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))
