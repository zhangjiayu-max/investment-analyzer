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
        dict with keys: snapshot, constraints, market, recent_analyses,
                        market_state, recent_decisions
    """
    facts = {
        "snapshot": _build_snapshot(conn),
        "constraints": _build_constraints(),
        "market": _build_market_summary(),
        "recent_analyses": _build_recent_analyses(),
        "market_state": _build_market_state(),
        "recent_decisions": _build_recent_decisions(),
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


def _build_market_state() -> dict:
    """
    推断当前市场状态（regime + sentiment）。

    regime 规则：
        沪深300 PE 百分位 < 30  → bear
        沪深300 PE 百分位 > 70  → bull
        30 ≤ PE 百分位 ≤ 70     → sideways

    sentiment 规则：
        优先从 system_config 读恐贪指数，
        若无则用债市温度推断：温度>70→fear, 温度<30→greed, 否则neutral

    Returns:
        {"regime": "bull"|"bear"|"sideways", "sentiment": "greed"|"fear"|"neutral"}
        失败返回默认值
    """
    result = {
        "regime": "unknown",
        "sentiment": "neutral",
    }

    # ── regime: 从 index_valuations 取沪深300 PE 百分位 ──
    try:
        c, own = _get_conn_once(None)
        if c is not None:
            row = c.execute(
                """SELECT percentile
                   FROM index_valuations
                   WHERE (index_code = '399300.SZ' OR index_code = '000300.SH')
                     AND metric_type = '市盈率'
                   ORDER BY snapshot_date DESC
                   LIMIT 1"""
            ).fetchone()
            if own and c:
                c.close()

            if row and row["percentile"] is not None:
                pct = float(row["percentile"])
                if pct < 30:
                    result["regime"] = "bear"
                elif pct > 70:
                    result["regime"] = "bull"
                else:
                    result["regime"] = "sideways"
            elif own:
                c.close()
    except Exception as e:
        logger.warning(f"推断 market_state.regime 失败: {e}")

    # ── sentiment: 尝试从 system_config 读恐贪指数，否则用债市温度 ──
    try:
        # 尝试读取恐贪指数
        c, own = _get_conn_once(None)
        if c is not None:
            row = c.execute(
                "SELECT value FROM system_config WHERE key = ?",
                ("market.fear_greed_index",),
            ).fetchone()
            if own and c:
                c.close()

            if row:
                try:
                    fgi = float(row["value"])
                    if fgi > 60:
                        result["sentiment"] = "greed"
                    elif fgi < 40:
                        result["sentiment"] = "fear"
                    else:
                        result["sentiment"] = "neutral"
                    return result
                except (ValueError, TypeError):
                    pass
            elif own:
                c.close()
    except Exception:
        pass

    # 回退：用债市温度推断
    try:
        from tools import _get_bond_temperature

        bond_raw = json.loads(_get_bond_temperature())
        temperature = bond_raw.get("temperature")
        if temperature is not None:
            if temperature > 70:
                result["sentiment"] = "fear"  # 债市偏热 → 恐
            elif temperature < 30:
                result["sentiment"] = "greed"  # 债市偏冷 → 贪
            else:
                result["sentiment"] = "neutral"
    except Exception as e:
        logger.warning(f"推断 market_state.sentiment 失败: {e}")

    return result


def _build_recent_decisions() -> dict:
    """
    获取近期决策记录（最近3天）和待执行行动。

    Returns:
        {
            "last_3_days": [{source_type, target_subject, action, summary, created_at}, ...],
            "pending_actions": [{transaction_type, fund_code, fund_name, amount, notes, ...}, ...],
        }
        失败返回空结构
    """
    result = {
        "last_3_days": [],
        "pending_actions": [],
    }

    # ── 从 analysis_conclusions 获取最近3天结论 ──
    try:
        c, own = _get_conn_once(None)
        if c is not None:
            # 确保表存在
            cur = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_conclusions'"
            )
            if cur.fetchone():
                rows = c.execute(
                    """SELECT source_type, target_subject, action, summary, created_at
                       FROM analysis_conclusions
                       WHERE created_at >= datetime('now', 'localtime', '-3 days')
                       ORDER BY created_at DESC
                       LIMIT 20"""
                ).fetchall()
                for r in rows:
                    result["last_3_days"].append({
                        "source_type": r["source_type"],
                        "target_subject": r["target_subject"],
                        "action": r["action"],
                        "summary": r["summary"],
                        "created_at": r["created_at"],
                    })
            if own and c:
                c.close()
    except Exception as e:
        logger.warning(f"获取最近决策失败: {e}")

    # ── 从 portfolio_transactions 获取已记录但未执行的 pending 交易 ──
    try:
        c, own = _get_conn_once(None)
        if c is not None:
            # 交易状态：pending / submitted 表示未执行或待确认
            rows = c.execute(
                """SELECT transaction_type, fund_code, fund_name,
                          amount, notes, status, created_at
                   FROM portfolio_transactions
                   WHERE status IN ('pending', 'submitted')
                   ORDER BY created_at DESC
                   LIMIT 10"""
            ).fetchall()
            for r in rows:
                result["pending_actions"].append({
                    "transaction_type": r["transaction_type"],
                    "fund_code": r["fund_code"],
                    "fund_name": r["fund_name"],
                    "amount": r["amount"],
                    "notes": r["notes"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                })
            if own and c:
                c.close()
    except Exception as e:
        logger.warning(f"获取待执行交易失败: {e}")

    return result
