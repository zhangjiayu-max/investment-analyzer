"""
AI 建议准确率分析模块

对比 AI 历史建议和实际走势，算出建议到底准不准。
纯数据查询，零 LLM 调用。

数据来源：
  - recommendation_candidates: AI 给出的买入/卖出/定投/再平衡建议
  - daily_position_signals: 每日持仓信号 (dca/hold/watch)
  - decision_records: 决策记录 (add/rebalance/watch)
  - recommendations: 指数方向预测 (up/down)
  - portfolio_transactions: 用户实际操作
  - fund_nav_history: 基金净值走势
"""

import json
import sqlite3
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# ── 建议方向 → 期望走势映射 ──────────────────────────────────────
# 买入/定投 → 期望涨;  卖出/减少 → 期望跌;  持有/观察 → 中性
ACTION_DIRECTION = {
    "buy": "up",
    "dca": "up",
    "reduce": "down",
    "sell": "down",
    "rebalance": "neutral",
    "hold": "neutral",
    "watch": "neutral",
}

# 方向正确判断：建议后 N 天内的涨跌幅
VERIFY_WINDOW_DAYS = 7


def _safe_json(text: Optional[str], default=None):
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _get_nav_change(fund_code: str, ref_date: str, days: int = VERIFY_WINDOW_DAYS) -> Optional[dict]:
    """获取基金在 ref_date 之后 days 天内的涨跌幅。

    Returns: {"start_nav": float, "end_nav": float, "change_pct": float, "end_date": str}
    如果数据不足返回 None。
    """
    conn = _get_conn()
    try:
        # ref_date 之前的最近一个净值作为基准
        row = conn.execute(
            """SELECT nav, nav_date FROM fund_nav_history
               WHERE fund_code = ? AND nav_date <= ?
               ORDER BY nav_date DESC LIMIT 1""",
            (fund_code, ref_date),
        ).fetchone()
        if not row or not row["nav"]:
            return None

        start_nav = row["nav"]
        start_date = row["nav_date"]

        # ref_date 之后 days 天的净值
        end_row = conn.execute(
            """SELECT nav, nav_date FROM fund_nav_history
               WHERE fund_code = ? AND nav_date > ?
               ORDER BY nav_date ASC
               LIMIT 1""",
            (fund_code, ref_date),
        ).fetchone()

        if not end_row or not end_row["nav"]:
            return None

        end_nav = end_row["nav"]
        change_pct = round((end_nav - start_nav) / start_nav * 100, 2) if start_nav else 0.0

        return {
            "start_nav": round(start_nav, 4),
            "end_nav": round(end_nav, 4),
            "change_pct": change_pct,
            "start_date": start_date,
            "end_date": end_row["nav_date"],
        }
    except Exception as e:
        logger.warning(f"获取净值变化失败 fund={fund_code} date={ref_date}: {e}")
        return None
    finally:
        conn.close()


def _check_user_adopted(fund_code: str, suggestion_date: str, action_type: str) -> bool:
    """检查用户是否在建议后 14 天内有对应操作。"""
    conn = _get_conn()
    try:
        end_date = (datetime.strptime(suggestion_date[:10], "%Y-%m-%d") + timedelta(days=14)).strftime("%Y-%m-%d")

        if action_type in ("buy", "dca"):
            row = conn.execute(
                """SELECT 1 FROM portfolio_transactions
                   WHERE fund_code = ? AND transaction_type = 'buy'
                   AND transaction_date >= ? AND transaction_date <= ?
                   LIMIT 1""",
                (fund_code, suggestion_date[:10], end_date),
            ).fetchone()
            return row is not None
        elif action_type in ("reduce", "sell"):
            row = conn.execute(
                """SELECT 1 FROM portfolio_transactions
                   WHERE fund_code = ? AND transaction_type = 'sell'
                   AND transaction_date >= ? AND transaction_date <= ?
                   LIMIT 1""",
                (fund_code, suggestion_date[:10], end_date),
            ).fetchone()
            return row is not None
        return False
    except Exception:
        return False
    finally:
        conn.close()


def _gather_suggestions(days_back: int = 30) -> list:
    """从多个表汇总 AI 建议，统一格式。"""
    conn = _get_conn()
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d 00:00:00")
    suggestions = []

    try:
        # 1) recommendation_candidates — AI 聊天/每日建议产生的结构化建议
        #    排除 source_type='analysis' 的事后复盘类建议（交易复盘不是预测）
        try:
            rows = conn.execute(
                """SELECT id, source_type, source_id, scenario_type, action_type,
                          target_type, target_code, target_name, summary, confidence,
                          status, created_at, suggested_amount
                   FROM recommendation_candidates
                   WHERE created_at >= ? AND action_type IN ('buy','dca','reduce','sell')
                   AND source_type != 'analysis'
                   ORDER BY created_at DESC""",
                (cutoff,),
            ).fetchall()
            for r in rows:
                suggestions.append({
                    "id": f"rc_{r['id']}",
                    "source_table": "recommendation_candidates",
                    "source_type": r["source_type"] or "unknown",
                    "action_type": r["action_type"],
                    "target_code": r["target_code"] or "",
                    "target_name": r["target_name"] or "",
                    "summary": (r["summary"] or "")[:200],
                    "confidence": r["confidence"] or "unknown",
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "agent_key": r["source_type"] or "unknown",
                })
        except Exception as e:
            logger.warning(f"读取 recommendation_candidates 失败: {e}")

        # 2) daily_position_signals — 每日持仓信号
        try:
            rows = conn.execute(
                """SELECT id, signal_date, signal_type, action_type,
                          target_code, target_name, summary, confidence,
                          status, created_at
                   FROM daily_position_signals
                   WHERE created_at >= ? AND action_type IN ('dca','hold','watch')
                   ORDER BY created_at DESC""",
                (cutoff,),
            ).fetchall()
            for r in rows:
                suggestions.append({
                    "id": f"dps_{r['id']}",
                    "source_table": "daily_position_signals",
                    "source_type": "daily_advice",
                    "action_type": r["action_type"],
                    "target_code": r["target_code"] or "",
                    "target_name": r["target_name"] or "",
                    "summary": (r["summary"] or "")[:200],
                    "confidence": r["confidence"] or "unknown",
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "agent_key": "daily_advisor",
                })
        except Exception as e:
            logger.warning(f"读取 daily_position_signals 失败: {e}")

        # 3) recommendations — 指数方向预测
        try:
            rows = conn.execute(
                """SELECT id, index_name, index_code, direction, reason, confidence,
                          status, created_at, change_pct, verified_at
                   FROM recommendations
                   WHERE created_at >= ?
                   ORDER BY created_at DESC""",
                (cutoff,),
            ).fetchall()
            for r in rows:
                # direction: up/down → action_type: buy(看多)/sell(看空)
                action = "buy" if r["direction"] == "up" else "sell" if r["direction"] == "down" else "hold"
                suggestions.append({
                    "id": f"rec_{r['id']}",
                    "source_table": "recommendations",
                    "source_type": "market_analysis",
                    "action_type": action,
                    "target_code": r["index_code"] or "",
                    "target_name": r["index_name"] or "",
                    "summary": (r["reason"] or "")[:200],
                    "confidence": r["confidence"] or "unknown",
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "agent_key": "market_analyzer",
                    "pre_verified_change_pct": r["change_pct"],
                })
        except Exception as e:
            logger.warning(f"读取 recommendations 失败: {e}")

        # 4) decision_records — 决策记录
        try:
            rows = conn.execute(
                """SELECT id, source_type, decision_type, target_type, target_code,
                          target_name, summary, confidence, status, created_at
                   FROM decision_records
                   WHERE created_at >= ?
                   ORDER BY created_at DESC""",
                (cutoff,),
            ).fetchall()
            for r in rows:
                # decision_type: add → buy, rebalance → rebalance, watch → watch
                dt = r["decision_type"]
                if dt == "add":
                    action = "buy"
                elif dt == "rebalance":
                    action = "rebalance"
                elif dt == "watch":
                    action = "watch"
                else:
                    action = dt
                suggestions.append({
                    "id": f"dr_{r['id']}",
                    "source_table": "decision_records",
                    "source_type": r["source_type"] or "unknown",
                    "action_type": action,
                    "target_code": r["target_code"] or "",
                    "target_name": r["target_name"] or "",
                    "summary": (r["summary"] or "")[:200],
                    "confidence": r["confidence"] or "unknown",
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "agent_key": r["source_type"] or "unknown",
                })
        except Exception as e:
            logger.warning(f"读取 decision_records 失败: {e}")

    finally:
        conn.close()

    return suggestions


def _verify_suggestion(suggestion: dict) -> dict:
    """验证单条建议的方向是否正确。"""
    action = suggestion["action_type"]
    expected = ACTION_DIRECTION.get(action, "neutral")
    target_code = suggestion["target_code"]
    created_at = suggestion["created_at"]

    # 如果已经有预验证结果 (recommendations 表)
    if suggestion.get("pre_verified_change_pct") is not None:
        change_pct = suggestion["pre_verified_change_pct"]
        is_correct = _is_direction_correct(expected, change_pct)
        suggestion["verified"] = True
        suggestion["actual_change_pct"] = change_pct
        suggestion["direction_correct"] = is_correct
        suggestion["adopted"] = False  # 指数预测不直接对应操作
        return suggestion

    if not target_code or expected == "neutral":
        suggestion["verified"] = False
        suggestion["actual_change_pct"] = None
        suggestion["direction_correct"] = None
        suggestion["adopted"] = False
        return suggestion

    # 获取建议后的实际走势
    nav_change = _get_nav_change(target_code, created_at[:10])
    if nav_change:
        suggestion["verified"] = True
        suggestion["actual_change_pct"] = nav_change["change_pct"]
        suggestion["nav_detail"] = nav_change
        suggestion["direction_correct"] = _is_direction_correct(expected, nav_change["change_pct"])
    else:
        suggestion["verified"] = False
        suggestion["actual_change_pct"] = None
        suggestion["direction_correct"] = None

    # 检查用户是否采纳
    suggestion["adopted"] = _check_user_adopted(target_code, created_at, action)

    return suggestion


def _is_direction_correct(expected: str, change_pct: float) -> bool:
    """判断方向是否正确。"""
    if expected == "up":
        return change_pct > 0
    elif expected == "down":
        return change_pct < 0
    else:
        return True  # neutral 总是 correct


def analyze_suggestion_accuracy(days_back: int = 30) -> dict:
    """
    主分析函数：统计 AI 建议的准确率。

    Returns:
        dict with keys:
        - total_suggestions: 总建议数
        - verified: 能验证的建议数
        - adopted: 用户采纳数
        - correct: 方向正确数
        - accuracy: 准确率 (correct/verified)
        - adoption_rate: 采纳率 (adopted/total)
        - accuracy_by_type: 按建议类型分别统计
        - accuracy_by_agent: 按 agent 分别统计
        - accuracy_by_confidence: 按置信度分别统计
        - bad_suggestions: 建议了但结果亏了的 case
        - missed_opportunities: 建议了但用户没采纳、结果涨了的 case
        - detail: 明细列表
    """
    raw_suggestions = _gather_suggestions(days_back)

    if not raw_suggestions:
        return {
            "total_suggestions": 0,
            "verified": 0,
            "adopted": 0,
            "correct": 0,
            "accuracy": 0,
            "adoption_rate": 0,
            "accuracy_by_type": {},
            "accuracy_by_agent": {},
            "accuracy_by_confidence": {},
            "bad_suggestions": [],
            "missed_opportunities": [],
            "detail": [],
            "message": f"最近 {days_back} 天没有找到 AI 建议",
        }

    # 逐条验证
    verified_suggestions = []
    for s in raw_suggestions:
        try:
            verified_suggestions.append(_verify_suggestion(s))
        except Exception as e:
            logger.warning(f"验证建议 {s.get('id')} 失败: {e}")
            s["verified"] = False
            s["direction_correct"] = None
            s["actual_change_pct"] = None
            s["adopted"] = False
            verified_suggestions.append(s)

    # ── 总体统计 ──
    total = len(verified_suggestions)
    verified = [s for s in verified_suggestions if s.get("verified")]
    adopted = [s for s in verified_suggestions if s.get("adopted")]
    correct = [s for s in verified if s.get("direction_correct") is True]

    accuracy = round(len(correct) / len(verified) * 100, 1) if verified else 0.0
    adoption_rate = round(len(adopted) / total * 100, 1) if total else 0.0

    # ── 按建议类型统计 ──
    accuracy_by_type = {}
    for action_type in set(s["action_type"] for s in verified_suggestions):
        subset = [s for s in verified if s["action_type"] == action_type]
        if subset:
            accuracy_by_type[action_type] = {
                "total": len([s for s in verified_suggestions if s["action_type"] == action_type]),
                "verified": len(subset),
                "correct": len([s for s in subset if s.get("direction_correct") is True]),
                "accuracy": round(len([s for s in subset if s.get("direction_correct") is True]) / len(subset) * 100, 1),
                "adopted": len([s for s in verified_suggestions if s["action_type"] == action_type and s.get("adopted")]),
            }

    # ── 按 agent/source 统计 ──
    accuracy_by_agent = {}
    for agent_key in set(s.get("agent_key", "unknown") for s in verified_suggestions):
        subset = [s for s in verified if s.get("agent_key") == agent_key]
        if subset:
            accuracy_by_agent[agent_key] = {
                "total": len([s for s in verified_suggestions if s.get("agent_key") == agent_key]),
                "verified": len(subset),
                "correct": len([s for s in subset if s.get("direction_correct") is True]),
                "accuracy": round(len([s for s in subset if s.get("direction_correct") is True]) / len(subset) * 100, 1),
                "adopted": len([s for s in verified_suggestions if s.get("agent_key") == agent_key and s.get("adopted")]),
            }

    # ── 按置信度统计 ──
    accuracy_by_confidence = {}
    for conf in set(s.get("confidence", "unknown") for s in verified_suggestions):
        subset = [s for s in verified if s.get("confidence") == conf]
        if subset:
            accuracy_by_confidence[conf] = {
                "total": len([s for s in verified_suggestions if s.get("confidence") == conf]),
                "verified": len(subset),
                "correct": len([s for s in subset if s.get("direction_correct") is True]),
                "accuracy": round(len([s for s in subset if s.get("direction_correct") is True]) / len(subset) * 100, 1),
            }

    # ── 建议了但结果亏了 (bad_suggestions) ──
    bad_suggestions = []
    for s in verified:
        if s.get("direction_correct") is False:
            bad_suggestions.append({
                "id": s["id"],
                "action_type": s["action_type"],
                "target_code": s["target_code"],
                "target_name": s["target_name"],
                "summary": s["summary"],
                "created_at": s["created_at"],
                "actual_change_pct": s.get("actual_change_pct"),
                "confidence": s.get("confidence"),
                "agent_key": s.get("agent_key"),
            })

    # ── 建议了但用户没采纳、结果涨了 (missed_opportunities) ──
    missed_opportunities = []
    for s in verified:
        if (
            not s.get("adopted")
            and s["action_type"] in ("buy", "dca")
            and s.get("direction_correct") is True
            and s.get("actual_change_pct", 0) > 0
        ):
            missed_opportunities.append({
                "id": s["id"],
                "action_type": s["action_type"],
                "target_code": s["target_code"],
                "target_name": s["target_name"],
                "summary": s["summary"],
                "created_at": s["created_at"],
                "actual_change_pct": s.get("actual_change_pct"),
                "confidence": s.get("confidence"),
                "agent_key": s.get("agent_key"),
            })

    # 明细 (精简版)
    detail = []
    for s in verified_suggestions:
        detail.append({
            "id": s["id"],
            "source": s.get("source_table"),
            "action": s["action_type"],
            "target": f"{s['target_code']} {s['target_name']}".strip(),
            "created_at": s["created_at"],
            "verified": s.get("verified", False),
            "actual_pct": s.get("actual_change_pct"),
            "correct": s.get("direction_correct"),
            "adopted": s.get("adopted", False),
            "confidence": s.get("confidence"),
        })

    return {
        "total_suggestions": total,
        "verified": len(verified),
        "adopted": len(adopted),
        "correct": len(correct),
        "accuracy": accuracy,
        "adoption_rate": adoption_rate,
        "accuracy_by_type": accuracy_by_type,
        "accuracy_by_agent": accuracy_by_agent,
        "accuracy_by_confidence": accuracy_by_confidence,
        "bad_suggestions": bad_suggestions,
        "missed_opportunities": missed_opportunities,
        "bad_count": len(bad_suggestions),
        "missed_count": len(missed_opportunities),
        "detail": detail,
        "period_days": days_back,
    }


def format_report(result: dict) -> str:
    """将分析结果格式化为可读文本。"""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  📊 AI 建议准确率报告 (最近 {result.get('period_days', 30)} 天)")
    lines.append("=" * 70)
    lines.append("")

    # 总体
    lines.append("【总体概况】")
    lines.append(f"  总建议数:     {result['total_suggestions']}")
    lines.append(f"  可验证数:     {result['verified']}")
    lines.append(f"  方向正确:     {result['correct']}")
    lines.append(f"  准确率:       {result['accuracy']}%")
    lines.append(f"  用户采纳:     {result['adopted']}")
    lines.append(f"  采纳率:       {result['adoption_rate']}%")
    lines.append("")

    # 按类型
    if result.get("accuracy_by_type"):
        lines.append("【按建议类型】")
        lines.append(f"  {'类型':<12} {'总数':>6} {'可验证':>6} {'正确':>6} {'准确率':>8} {'采纳':>6}")
        lines.append(f"  {'─'*12} {'─'*6} {'─'*6} {'─'*6} {'─'*8} {'─'*6}")
        for k, v in sorted(result["accuracy_by_type"].items()):
            lines.append(f"  {k:<12} {v['total']:>6} {v['verified']:>6} {v['correct']:>6} {v['accuracy']:>7.1f}% {v['adopted']:>6}")
        lines.append("")

    # 按 agent
    if result.get("accuracy_by_agent"):
        lines.append("【按来源 Agent】")
        lines.append(f"  {'Agent':<20} {'总数':>6} {'可验证':>6} {'正确':>6} {'准确率':>8} {'采纳':>6}")
        lines.append(f"  {'─'*20} {'─'*6} {'─'*6} {'─'*6} {'─'*8} {'─'*6}")
        for k, v in sorted(result["accuracy_by_agent"].items()):
            lines.append(f"  {k:<20} {v['total']:>6} {v['verified']:>6} {v['correct']:>6} {v['accuracy']:>7.1f}% {v['adopted']:>6}")
        lines.append("")

    # 按置信度
    if result.get("accuracy_by_confidence"):
        lines.append("【按置信度】")
        lines.append(f"  {'置信度':<12} {'总数':>6} {'可验证':>6} {'正确':>6} {'准确率':>8}")
        lines.append(f"  {'─'*12} {'─'*6} {'─'*6} {'─'*6} {'─'*8}")
        for k, v in sorted(result["accuracy_by_confidence"].items()):
            lines.append(f"  {k:<12} {v['total']:>6} {v['verified']:>6} {v['correct']:>6} {v['accuracy']:>7.1f}%")
        lines.append("")

    # 错误建议
    if result.get("bad_suggestions"):
        lines.append(f"【❌ 方向判断错误 ({result['bad_count']} 条)】")
        for i, b in enumerate(result["bad_suggestions"][:10], 1):
            lines.append(f"  {i}. [{b['action_type']}] {b['target_code']} {b['target_name']}")
            lines.append(f"     建议: {b['summary'][:80]}...")
            lines.append(f"     实际涨跌: {b['actual_change_pct']}% | 置信度: {b['confidence']} | 时间: {b['created_at']}")
        if result["bad_count"] > 10:
            lines.append(f"  ... 还有 {result['bad_count'] - 10} 条")
        lines.append("")

    # 错失机会
    if result.get("missed_opportunities"):
        lines.append(f"【💡 错失机会 ({result['missed_count']} 条)】")
        for i, m in enumerate(result["missed_opportunities"][:10], 1):
            lines.append(f"  {i}. [{m['action_type']}] {m['target_code']} {m['target_name']}")
            lines.append(f"     建议: {m['summary'][:80]}...")
            lines.append(f"     实际涨幅: +{m['actual_change_pct']}% | 时间: {m['created_at']}")
        if result["missed_count"] > 10:
            lines.append(f"  ... 还有 {result['missed_count'] - 10} 条")
        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


if __name__ == "__main__":
    result = analyze_suggestion_accuracy(days_back=30)
    print(format_report(result))
