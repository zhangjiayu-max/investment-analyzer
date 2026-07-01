"""
组合事实层（Portfolio Fact Layer）
所有分析入口调用 build_portfolio_facts() 获取统一的组合事实快照。
"""

import json
import logging

logger = logging.getLogger(__name__)


def build_portfolio_facts(conn=None) -> dict:
    """
    所有分析入口调用此函数获取统一的组合事实快照。

    Returns:
        dict with keys: snapshot, constraints, market, recent_analyses
    """
    facts = {
        "snapshot": _build_snapshot(conn),
        "constraints": _build_constraints(),
        "market": _build_market_summary(),
        "recent_analyses": _build_recent_analyses(),
    }
    return facts


# ── 子模块 ──────────────────────────────────────────────


def _get_conn_once(conn=None):
    """获取数据库连接（复用传入的，或新建）。"""
    if conn is not None:
        return conn, False  # 不负责关闭
    try:
        from db._conn import _get_conn as _db_get_conn
        return _db_get_conn(), True
    except Exception as e:
        logger.warning(f"无法获取数据库连接: {e}")
        return None, False


def _build_snapshot(conn=None) -> dict:
    """构建持仓快照：总市值、债基占比、过度集中基金列表。"""
    try:
        c, own = _get_conn_once(conn)
        if c is None:
            return {
                "total_value": None,
                "bond_pct": None,
                "overconcentrated": [],
            }

        from db import list_holdings
        holdings = list_holdings()
        total_value = sum((h.get("current_value", 0) or 0) for h in holdings)
        bond_value = sum(
            (h.get("current_value", 0) or 0)
            for h in holdings
            if (h.get("fund_category") or "").lower() == "bond"
        )
        bond_pct = round(bond_value / total_value * 100, 1) if total_value > 0 else 0

        overconcentrated = []
        for h in holdings:
            val = h.get("current_value", 0) or 0
            pct = round(val / total_value * 100, 1) if total_value > 0 else 0
            if pct > 25:
                overconcentrated.append({
                    "fund_code": h.get("fund_code", ""),
                    "fund_name": h.get("fund_name", ""),
                    "pct": pct,
                })

        if own and c:
            c.close()

        return {
            "total_value": round(total_value, 2),
            "bond_pct": bond_pct,
            "overconcentrated": overconcentrated,
        }
    except Exception as e:
        logger.warning(f"构建持仓快照失败: {e}")
        return {
            "total_value": None,
            "bond_pct": None,
            "overconcentrated": [],
        }


def _build_constraints() -> dict:
    """构建约束规则：债市温度、是否偏贵、禁加目标列表。"""
    try:
        from tools import _get_bond_temperature

        bond_raw = json.loads(_get_bond_temperature())
        temperature = bond_raw.get("temperature")
        bond_expensive = temperature is not None and temperature > 70

        # 构建禁加目标
        no_add_targets = []
        if bond_expensive:
            no_add_targets.append("债券型基金（债市温度>70°，偏贵）")

        # 检查集中度
        try:
            c, own = _get_conn_once(None)
            if c is not None:
                from db import list_holdings
                holdings = list_holdings()
                total_value = sum((h.get("current_value", 0) or 0) for h in holdings)
                for h in holdings:
                    val = h.get("current_value", 0) or 0
                    pct = round(val / total_value * 100, 1) if total_value > 0 else 0
                    if pct > 25:
                        no_add_targets.append(
                            f"{h.get('fund_code','')} {h.get('fund_name','')}（占比{pct}%>25%）"
                        )
                if own and c:
                    c.close()
        except Exception:
            pass

        return {
            "bond_temperature": temperature,
            "bond_expensive": bond_expensive,
            "no_add_targets": no_add_targets,
        }
    except Exception as e:
        logger.warning(f"构建约束规则失败: {e}")
        return {
            "bond_temperature": None,
            "bond_expensive": False,
            "no_add_targets": [],
        }


def _build_market_summary() -> dict:
    """构建主要指数估值摘要。"""
    try:
        from db import list_valuation_indexes
        indexes = list_valuation_indexes()

        # 去重，按 index_code 聚合
        seen = {}
        for idx in indexes:
            code = idx.get("index_code", "")
            if code and code not in seen:
                seen[code] = {
                    "index_code": code,
                    "index_name": idx.get("index_name", code),
                    "metric_type": idx.get("metric_type", ""),
                    "current_value": idx.get("current_value"),
                    "percentile": idx.get("percentile"),
                    "change_pct": idx.get("change_pct"),
                }

        return {"indices": list(seen.values())}
    except Exception as e:
        logger.warning(f"构建市场摘要失败: {e}")
        return {"indices": []}


def _build_recent_analyses() -> list:
    """获取最近 24 小时内其他分析的结论摘要。"""
    try:
        c, own = _get_conn_once(None)
        if c is None:
            return []

        rows = c.execute("""
            SELECT id, analysis_type, summary, result_data, created_at
            FROM portfolio_analysis_records
            WHERE created_at >= datetime('now', 'localtime', '-24 hours')
              AND status = 'done'
            ORDER BY created_at DESC
            LIMIT 10
        """).fetchall()

        if own and c:
            c.close()

        results = []
        for r in rows:
            r = dict(r)
            # 尝试从 result_data 提取结论
            conclusion = ""
            try:
                result_text = r.get("result_data", "") or ""
                # 取前 200 字符作为结论摘要
                if result_text:
                    conclusion = result_text[:200].strip()
            except Exception:
                pass

            results.append({
                "type": r.get("analysis_type", ""),
                "conclusion": conclusion,
                "time": r.get("created_at", ""),
            })

        return results
    except Exception as e:
        logger.warning(f"获取最近分析记录失败: {e}")
        return []
