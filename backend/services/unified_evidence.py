"""统一证据层。

把知识库、市场信号、决策回归、事件验证、关注基金和近期复盘
收拢成同一份可复用快照，供对话、看板、市场雷达和决策页共享。
"""

from __future__ import annotations

from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace("%", "").strip())
    except Exception:
        return default


def _safe_join(items: list[str], sep: str = "、") -> str:
    return sep.join([item for item in items if item])


def _safe_top(items: list[dict], limit: int = 3, key: str = "title") -> list[str]:
    result = []
    for item in items[:limit]:
        value = item.get(key) if isinstance(item, dict) else ""
        if value:
            result.append(str(value))
    return result


def _build_market_evidence(user_id: str = "default", limit: int = 4) -> dict:
    try:
        from db.market_events import list_active_events, list_verified_events
        from db.watchlist import list_watchlist
        from services.event_radar import get_sector_accuracy_stats

        watch_items = [
            item for item in list_watchlist(user_id=user_id, status="watching")
            if item.get("status") != "bought"
        ]
        watch_counts = {"green": 0, "yellow": 0, "red": 0, "gray": 0}
        for item in watch_items:
            status = item.get("signal_status") or "gray"
            watch_counts[status] = watch_counts.get(status, 0) + 1

        top_watch_items = sorted(
            watch_items,
            key=lambda item: (
                {"green": 0, "yellow": 1, "red": 2, "gray": 3}.get(item.get("signal_status") or "gray", 3),
                _to_float(item.get("current_percentile"), 999.0),
                -_to_float(item.get("priority"), 0.0),
            ),
        )[:limit]

        accuracy = get_sector_accuracy_stats().get(
            "overall",
            {"total": 0, "correct": 0, "wrong": 0, "flat": 0, "accuracy": 0.0},
        )
        active_events = list_active_events()[:limit]
        verified_events = list_verified_events(limit=limit)

        active_titles = _safe_top(active_events, limit=3, key="title")
        verified_titles = _safe_top(verified_events, limit=2, key="title")

        summary_parts = []
        if watch_counts["green"] > 0:
            summary_parts.append(f"关注列表有 {watch_counts['green']} 只可上车")
        if watch_counts["yellow"] > 0:
            summary_parts.append(f"{watch_counts['yellow']} 只接近上车")
        if accuracy.get("total"):
            summary_parts.append(f"事件验证准确率 {accuracy.get('accuracy', 0.0) * 100:.0f}%")
        if not summary_parts:
            summary_parts.append("当前市场信号偏少，适合继续观察")

        chips = [
            {"label": "可上车", "value": watch_counts["green"], "tone": "good"},
            {"label": "接近上车", "value": watch_counts["yellow"], "tone": "warn"},
            {"label": "等待中", "value": watch_counts["red"], "tone": "danger"},
            {"label": "数据不足", "value": watch_counts["gray"], "tone": "muted"},
        ]
        if accuracy.get("total"):
            chips.append({
                "label": "验证准确率",
                "value": f"{accuracy.get('accuracy', 0.0) * 100:.0f}%",
                "tone": "info",
            })

        highlights = []
        if top_watch_items:
            highlights.append(
                "关注基金：" + _safe_join(
                    [item.get("fund_name") or item.get("fund_code") for item in top_watch_items[:3]]
                )
            )
        if active_titles:
            highlights.append("前瞻事件：" + _safe_join([title[:14] for title in active_titles]))
        if verified_titles:
            highlights.append("最近验证：" + _safe_join([title[:14] for title in verified_titles]))

        return {
            "summary": "；".join(summary_parts),
            "chips": chips,
            "highlights": highlights,
            "watchlist_items": top_watch_items,
            "active_events": [
                {
                    "title": ev.get("title", ""),
                    "status": ev.get("status", ""),
                    "direction": ev.get("direction", ""),
                    "confidence": float(ev.get("confidence") or 0),
                }
                for ev in active_events
            ],
            "verified_events": [
                {
                    "title": ev.get("title", ""),
                    "result": ev.get("verification_result", ""),
                }
                for ev in verified_events
            ],
            "event_accuracy": accuracy,
        }
    except Exception as e:
        logger.debug(f"构建市场证据失败: {e}")
        return {
            "summary": "暂无市场共享信号。",
            "chips": [],
            "highlights": [],
            "watchlist_items": [],
            "active_events": [],
            "verified_events": [],
            "event_accuracy": {"total": 0, "correct": 0, "wrong": 0, "flat": 0, "accuracy": 0.0},
        }


def _build_decision_evidence(user_id: str = "default", limit: int = 4) -> dict:
    try:
        from db.decisions import (
            list_decisions,
            list_due_decision_reviews,
            list_today_decisions,
            list_recommendation_candidates,
            get_decision_stats,
        )

        decisions = list_decisions(user_id=user_id, limit=200)
        pending = [d for d in decisions if d.get("status") in ("proposed", "accepted", "deferred")]
        due_reviews = list_due_decision_reviews(user_id=user_id, limit=limit)
        today = list_today_decisions(user_id=user_id, limit=limit)
        candidates = list_recommendation_candidates(user_id=user_id, status="new", limit=limit)
        stats = get_decision_stats(user_id=user_id)

        candidate_items = [
            {
                "id": c.get("id"),
                "summary": c.get("summary", ""),
                "action_type": c.get("action_type", ""),
                "target_name": c.get("target_name", ""),
                "target_code": c.get("target_code", ""),
                "confidence": c.get("confidence", ""),
            }
            for c in candidates[:limit]
        ]

        chips = [
            {"label": "待执行", "value": len(pending), "tone": "warn"},
            {"label": "待复盘", "value": len(due_reviews), "tone": "info"},
            {"label": "今日行动", "value": len(today), "tone": "good"},
            {"label": "新候选", "value": len(candidate_items), "tone": "muted"},
        ]

        highlights = []
        if due_reviews:
            highlights.append("优先处理到期复盘决策")
        if pending:
            highlights.append("优先复核待执行决策的预检查")
        if candidate_items:
            highlights.append("把 AI 候选转成可执行决策前先看共享市场信号")

        if pending or due_reviews or candidate_items:
            summary = f"待执行 {len(pending)}，待复盘 {len(due_reviews)}，今日行动 {len(today)}，新候选 {len(candidate_items)}"
        else:
            summary = "当前没有明显的决策积压。"

        return {
            "summary": summary,
            "chips": chips,
            "highlights": highlights,
            "candidate_items": candidate_items,
            "stats": {
                "total": stats.get("total", 0),
                "reviewed": stats.get("reviewed", 0),
                "review_helpful_rate": stats.get("review_helpful_rate", 0),
                "total_profit_change": stats.get("total_profit_change", 0),
            },
        }
    except Exception as e:
        logger.debug(f"构建决策证据失败: {e}")
        return {
            "summary": "暂无决策共享信号。",
            "chips": [],
            "highlights": [],
            "candidate_items": [],
            "stats": {"total": 0, "reviewed": 0, "review_helpful_rate": 0, "total_profit_change": 0},
        }


def _build_opportunity_evidence(user_id: str = "default", limit: int = 5) -> dict:
    try:
        from db.opportunities import get_opportunity_track_stats, list_opportunities

        items = list_opportunities(user_id=user_id, limit=limit * 2)
        active_items = [
            item for item in items
            if item.get("status") in ("active", "watching", "bought")
        ][:limit]
        can_buy_count = sum(1 for item in active_items if item.get("verdict") == "can_buy")
        watch_count = sum(1 for item in active_items if item.get("verdict") == "watch")
        avoid_count = sum(1 for item in active_items if item.get("verdict") == "avoid")
        track_stats = get_opportunity_track_stats(user_id=user_id, limit=limit)

        opportunity_items = []
        for item in active_items:
            matched_funds = []
            for fund in (item.get("matched_funds") or [])[:3]:
                matched_funds.append({
                    "fund_code": fund.get("fund_code", ""),
                    "fund_name": fund.get("fund_name", ""),
                    "index_name": fund.get("index_name", ""),
                    "short_term_suitable": bool(fund.get("short_term_suitable")),
                    "tradeability": fund.get("tradeability", ""),
                })
            opportunity_items.append({
                "id": item.get("id"),
                "theme": item.get("theme", ""),
                "verdict": item.get("verdict", ""),
                "opportunity_score": item.get("opportunity_score", 0),
                "summary": item.get("summary", ""),
                "status": item.get("status", ""),
                "review_date": (item.get("exit_plan") or {}).get("review_date", ""),
                "entry_action": (item.get("entry_plan") or {}).get("action", ""),
                "matched_funds": matched_funds,
                "risk_note": item.get("risk_note", ""),
            })

        summary_parts = []
        if can_buy_count:
            summary_parts.append(f"{can_buy_count} 个主题可小仓试投")
        if watch_count:
            summary_parts.append(f"{watch_count} 个主题继续观察")
        if avoid_count:
            summary_parts.append(f"{avoid_count} 个主题先回避")
        if track_stats.get("due_reviews"):
            summary_parts.append(f"{track_stats['due_reviews']} 个机会到期复盘")
        if track_stats.get("hit_rate") is not None:
            summary_parts.append(f"已验证命中率 {track_stats['hit_rate']:.0f}%")
        if not summary_parts:
            summary_parts.append("当前主题机会较少，继续等待更明确催化")

        chips = [
            {"label": "可买主题", "value": can_buy_count, "tone": "good"},
            {"label": "观察主题", "value": watch_count, "tone": "warn"},
            {"label": "回避主题", "value": avoid_count, "tone": "danger"},
            {"label": "跟踪中", "value": track_stats.get("open_tracks", 0), "tone": "info"},
        ]
        if track_stats.get("due_reviews"):
            chips.append({"label": "待复盘", "value": track_stats.get("due_reviews", 0), "tone": "muted"})
        if track_stats.get("average_return_pct") is not None:
            chips.append({
                "label": "平均回报",
                "value": f"{_to_float(track_stats.get('average_return_pct')):.2f}%",
                "tone": "info",
            })
        if track_stats.get("hit_rate") is not None:
            chips.append({
                "label": "命中率",
                "value": f"{_to_float(track_stats.get('hit_rate')):.0f}%",
                "tone": "good" if _to_float(track_stats.get('hit_rate')) >= 50 else "warn",
            })

        highlights = []
        if opportunity_items:
            highlights.append(
                "主题机会：" + _safe_join([
                    f"{item['theme']}·{item['verdict']}·{_to_float(item.get('opportunity_score', 0)):.0f}分"
                    for item in opportunity_items[:3]
                ])
            )
        if track_stats.get("recent_items"):
            highlights.append(
                "跟踪复盘：" + _safe_join([
                    f"{item.get('theme', '')}·{item.get('opportunity_status', '')}·{('到期' if item.get('review_due_date') else '未定')}"
                    for item in track_stats.get("recent_items", [])[:3]
                ])
            )

        return {
            "summary": "；".join(summary_parts),
            "chips": chips,
            "highlights": highlights,
            "opportunity_items": opportunity_items,
            "track_stats": track_stats,
        }
    except Exception as e:
        logger.debug(f"构建机会证据失败: {e}")
        return {
            "summary": "暂无主题机会证据。",
            "chips": [],
            "highlights": [],
            "opportunity_items": [],
            "track_stats": {
                "total": 0,
                "open_tracks": 0,
                "bought_tracks": 0,
                "exited_tracks": 0,
                "due_reviews": 0,
                "average_return_pct": None,
                "recent_items": [],
            },
        }


def _build_knowledge_evidence(user_id: str = "default", query: str = "", limit: int = 5) -> dict:
    try:
        from db.knowledge import get_knowledge_feedback_stats, get_knowledge_stats, list_knowledge
        from services.rag import build_rag_context_with_details

        knowledge_stats = get_knowledge_stats()
        feedback_stats = get_knowledge_feedback_stats()
        recent_lessons = list_knowledge(category="user_lesson", limit=limit)
        useful_lessons = sum(1 for item in recent_lessons if (item.get("usefulness_score") or 0) > 0)
        lessons = [
            {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "usefulness_score": item.get("usefulness_score", 0),
            }
            for item in recent_lessons[:limit]
        ]

        rag_context = ""
        rag_items = []
        if query:
            rag_result = build_rag_context_with_details(query=query, limit=limit)
            rag_context = rag_result.get("context", "")
            rag_items = [
                {
                    "title": item.get("title", ""),
                    "content_type": item.get("content_type", ""),
                    "score": item.get("score", 0),
                }
                for item in rag_result.get("results", [])[:limit]
            ]

        summary_parts = []
        if knowledge_stats.get("total"):
            summary_parts.append(f"知识库 {knowledge_stats.get('total', 0)} 条")
        if feedback_stats.get("total_lessons"):
            summary_parts.append(f"复盘教训 {feedback_stats.get('total_lessons', 0)} 条")
        if useful_lessons:
            summary_parts.append(f"有用教训 {useful_lessons} 条")
        if rag_items:
            summary_parts.append(f"当前问题命中 {len(rag_items)} 条相关知识")
        if not summary_parts:
            summary_parts.append("暂无可复用的知识库证据")

        chips = [
            {"label": "知识总量", "value": knowledge_stats.get("total", 0), "tone": "info"},
            {"label": "教训条数", "value": feedback_stats.get("total_lessons", 0), "tone": "warn"},
            {"label": "有用教训", "value": useful_lessons, "tone": "good"},
            {"label": "相关命中", "value": len(rag_items), "tone": "muted"},
        ]
        highlights = []
        if lessons:
            highlights.append("最近教训：" + _safe_join([item["title"][:14] for item in lessons[:3]]))
        if rag_items:
            highlights.append("相关知识：" + _safe_join([item["title"][:14] for item in rag_items[:3]]))

        return {
            "summary": "；".join(summary_parts),
            "chips": chips,
            "highlights": highlights,
            "recent_lessons": lessons,
            "rag_context": rag_context,
            "rag_items": rag_items,
            "knowledge_stats": knowledge_stats,
            "feedback_stats": {**feedback_stats, "useful_lessons": useful_lessons},
        }
    except Exception as e:
        logger.debug(f"构建知识证据失败: {e}")
        return {
            "summary": "暂无知识库证据。",
            "chips": [],
            "highlights": [],
            "recent_lessons": [],
            "rag_context": "",
            "rag_items": [],
            "knowledge_stats": {"total": 0, "by_category": {}},
            "feedback_stats": {"total_lessons": 0, "useful_lessons": 0, "recent_lessons": []},
        }


def _build_regression_evidence(user_id: str = "default", limit: int = 5) -> dict:
    try:
        from services.quality.decision_accuracy import (
            get_accuracy_stats,
            get_adoption_stats,
            get_verified_recent,
        )
        from services.event_radar import get_sector_accuracy_stats

        accuracy_stats = get_accuracy_stats(period_days=90, group_by="agent")
        adoption_stats = get_adoption_stats(period_days=180)
        recent_verified = get_verified_recent(limit=limit)
        event_stats = get_sector_accuracy_stats().get(
            "overall",
            {"total": 0, "correct": 0, "wrong": 0, "flat": 0, "accuracy": 0.0},
        )

        overall = accuracy_stats.get("overall", {})
        summary_parts = []
        if overall.get("verified"):
            summary_parts.append(f"决策验证 {overall.get('verified', 0)} 条，准确率 {overall.get('accuracy', 0.0) * 100:.0f}%")
        if adoption_stats.get("total_marked"):
            summary_parts.append(f"采纳率 {adoption_stats.get('adoption_rate', 0.0) * 100:.0f}%")
        if event_stats.get("total"):
            summary_parts.append(f"事件雷达准确率 {event_stats.get('accuracy', 0.0) * 100:.0f}%")
        if not summary_parts:
            summary_parts.append("暂无回归验证结果")

        chips = [
            {"label": "建议准确率", "value": f"{overall.get('accuracy', 0.0) * 100:.0f}%", "tone": "good"},
            {"label": "采纳率", "value": f"{adoption_stats.get('adoption_rate', 0.0) * 100:.0f}%", "tone": "info"},
            {"label": "事件准确率", "value": f"{event_stats.get('accuracy', 0.0) * 100:.0f}%", "tone": "warn"},
            {"label": "回归样本", "value": overall.get("verified", 0), "tone": "muted"},
        ]
        highlights = []
        if recent_verified:
            highlights.append("最近验证：" + _safe_join([item.get("index_name", "")[:14] for item in recent_verified[:3]]))
        if adoption_stats.get("verified_count"):
            highlights.append(
                "采纳 vs 未采纳："
                f"{adoption_stats.get('adopted_correct_rate', 0.0) * 100:.0f}% / "
                f"{adoption_stats.get('rejected_correct_rate', 0.0) * 100:.0f}%"
            )

        return {
            "summary": "；".join(summary_parts),
            "chips": chips,
            "highlights": highlights,
            "recent_verified": recent_verified,
            "accuracy_stats": accuracy_stats,
            "adoption_stats": adoption_stats,
            "event_stats": event_stats,
        }
    except Exception as e:
        logger.debug(f"构建回归证据失败: {e}")
        return {
            "summary": "暂无回归证据。",
            "chips": [],
            "highlights": [],
            "recent_verified": [],
            "accuracy_stats": {"overall": {"total": 0, "verified": 0, "correct": 0, "wrong": 0, "flat": 0, "accuracy": 0.0}},
            "adoption_stats": {"total_marked": 0, "adopted": 0, "rejected": 0, "adoption_rate": 0.0},
            "event_stats": {"total": 0, "correct": 0, "wrong": 0, "flat": 0, "accuracy": 0.0},
        }


def _render_block(title: str, summary: str, chips: list[dict], highlights: list[str]) -> str:
    lines = [f"### {title}"]
    if summary:
        lines.append(f"- {summary}")
    for chip in chips[:4]:
        label = chip.get("label", "")
        value = chip.get("value", "")
        if label:
            lines.append(f"- {label}: {value}")
    for item in highlights[:3]:
        if item:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _build_recommendation(market: dict, opportunity: dict, decision: dict, regression: dict, knowledge: dict) -> str:
    green = next((c["value"] for c in market.get("chips", []) if c.get("label") == "可上车"), 0)
    yellow = next((c["value"] for c in market.get("chips", []) if c.get("label") == "接近上车"), 0)
    can_buy_theme = next((c["value"] for c in opportunity.get("chips", []) if c.get("label") == "可买主题"), 0)
    due_reviews = next((c["value"] for c in opportunity.get("chips", []) if c.get("label") == "待复盘"), 0)
    helpful_rate = _to_float(regression.get("adoption_stats", {}).get("adopted_correct_rate", 0.0))
    lessons = knowledge.get("recent_lessons", [])

    if can_buy_theme:
        base = "先看可买主题机会，确认资金、仓位和退出条件，再决定是否上车。"
    elif green:
        base = "优先看可上车的关注基金，并检查是否能转成决策。"
    elif yellow:
        base = "继续观察接近上车的关注基金，等信号更明确再下手。"
    else:
        base = "市场可执行信号不足，先处理到期决策和复盘。"

    if due_reviews:
        base += " 记得先处理到期的机会复盘。"
    if helpful_rate >= 0.6 and lessons:
        base += " 复盘教训可作为当前判断的校验锚点。"
    if decision.get("candidate_items"):
        base += " AI 候选需要先过预检查和证据核对。"
    return base


def build_unified_evidence(
    user_id: str = "default",
    query: str = "",
    scenario_type: str = "general_analysis",
    limit: int = 5,
) -> dict:
    """构建统一证据快照。"""
    market = _build_market_evidence(user_id=user_id, limit=limit)
    opportunity = _build_opportunity_evidence(user_id=user_id, limit=limit)
    decision = _build_decision_evidence(user_id=user_id, limit=limit)
    knowledge = _build_knowledge_evidence(user_id=user_id, query=query, limit=limit)
    regression = _build_regression_evidence(user_id=user_id, limit=limit)

    summary_parts = [
        market.get("summary", ""),
        opportunity.get("summary", ""),
        decision.get("summary", ""),
        knowledge.get("summary", ""),
        regression.get("summary", ""),
    ]
    summary = "；".join([part for part in summary_parts if part]) or "暂无共享证据。"
    recommendation = _build_recommendation(market, opportunity, decision, regression, knowledge)

    prompt_parts = [
        _render_block("市场信号", market.get("summary", ""), market.get("chips", []), market.get("highlights", [])),
        _render_block("主题机会", opportunity.get("summary", ""), opportunity.get("chips", []), opportunity.get("highlights", [])),
        _render_block("决策状态", decision.get("summary", ""), decision.get("chips", []), decision.get("highlights", [])),
        _render_block("知识库证据", knowledge.get("summary", ""), knowledge.get("chips", []), knowledge.get("highlights", [])),
        _render_block("回归验证", regression.get("summary", ""), regression.get("chips", []), regression.get("highlights", [])),
    ]
    prompt_context = "## 共享证据\n" + "\n\n".join(prompt_parts)

    return {
        "summary": summary,
        "recommendation": recommendation,
        "market": market,
        "opportunity": opportunity,
        "decision": decision,
        "knowledge": knowledge,
        "regression": regression,
        "scenario_type": scenario_type,
        "query": query,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "prompt_context": prompt_context,
    }
