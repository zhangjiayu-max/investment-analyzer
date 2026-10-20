"""用户决策档案与量化账本之间的兼容适配层。

两套表的职责不同：decision_records 保存用户工作流，investment_decisions
保存量化信号和回测。此模块只负责身份关联、字段转换和状态同步，不改变
任一套表原有的回测口径。
"""

from __future__ import annotations

import logging
import sqlite3

from db._conn import _get_conn

logger = logging.getLogger(__name__)

_QUANT_TO_ARCHIVE_STATUS = {
    "suggested": "proposed",
    "confirmed": "accepted",
    "executed": "executed",
    "expired": "expired",
}
_DECISION_TYPE = {
    "opportunity_buy": "buy",
    "smart_add": "add",
    "loss_recovery": "add",
    "valuation_channel": "buy",
    "watch": "watch",
}
_VERDICT_LABEL = {
    "can_buy": "可小仓",
    "watch": "观察",
    "avoid": "回避",
    "reduce": "减仓",
}


def _confidence_label(value: float | str | None) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return str(value or "medium")
    if score >= 0.7:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _append_note(decision_id: int, note: str) -> None:
    from db.investment_decisions import append_decision_note

    append_decision_note(decision_id, note)


def _link_pair(archive_id: int, investment_id: int) -> None:
    """在一个事务中写入双向关联，并拒绝覆盖已有不同关联。"""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        archive = conn.execute(
            "SELECT user_id, investment_decision_id FROM decision_records WHERE id = ?",
            (archive_id,),
        ).fetchone()
        investment = conn.execute(
            "SELECT user_id, decision_record_id FROM investment_decisions WHERE id = ?",
            (investment_id,),
        ).fetchone()
        if not archive or not investment:
            raise ValueError("决策关联的一端不存在")
        if archive["user_id"] != investment["user_id"]:
            raise ValueError("决策档案与量化账本归属不一致")
        if archive["investment_decision_id"] not in (None, investment_id):
            raise ValueError("决策档案已关联其他量化账本")
        if investment["decision_record_id"] not in (None, archive_id):
            raise ValueError("量化账本已关联其他决策档案")
        conn.execute(
            "UPDATE decision_records SET investment_decision_id = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (investment_id, archive_id),
        )
        conn.execute(
            "UPDATE investment_decisions SET decision_record_id = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (archive_id, investment_id),
        )
        conn.commit()
    finally:
        conn.close()


def _build_archive_payload(quant: dict) -> dict:
    verdict = quant.get("verdict") or "watch"
    fund_name = quant.get("fund_name") or quant.get("fund_code") or "目标基金"
    label = _VERDICT_LABEL.get(verdict, verdict)
    signal = quant.get("signal_snapshot") or {}
    evidence = signal.get("evidence") or []
    if not evidence:
        evidence = [{"type": "quant_pipeline", "summary": "来自投资决策流水线的量化信号"}]
    decision_type = _DECISION_TYPE.get(quant.get("decision_type"), "watch")
    if verdict in ("avoid", "watch"):
        decision_type = "watch"
    elif verdict == "reduce":
        decision_type = "reduce"
    shares = signal.get("suggested_shares") if decision_type in ("reduce", "sell") else None
    actions = []
    if decision_type in ("buy", "add", "reduce", "sell") and (quant.get("suggested_amount") or shares):
        actions.append({
            "action_type": decision_type,
            "title": (
                f"按建议金额 ¥{quant.get('suggested_amount') or 0:,.0f} 生成交易草稿"
                if decision_type in ("buy", "add") else
                f"确认卖出 {shares:g} 份后生成草稿" if shares else "需补充卖出份额后生成减仓草稿"
            ),
            "params": {
                "transaction_type": "buy" if decision_type in ("buy", "add") else "sell",
                "amount": quant.get("suggested_amount") if decision_type in ("buy", "add") else None,
                "shares": shares if decision_type in ("reduce", "sell") else None,
                "fund_code": quant.get("fund_code") or "",
                "fund_name": fund_name,
            },
        })
    return {
        "source_type": "decision_pipeline",
        "source_id": quant["id"],
        "user_id": quant.get("user_id") or "default",
        "decision_type": decision_type,
        "target_type": "fund",
        "target_code": quant.get("fund_code") or "",
        "target_name": fund_name,
        "summary": f"{fund_name}：{label}",
        "rationale": f"决策流水线结论：{label}；建议金额 ¥{quant.get('suggested_amount') or 0:g}",
        "evidence": {"signal_snapshot": signal, "items": evidence},
        "risk": quant.get("risk_check") or {},
        "suitability": {"notes": ["执行前请结合个人资金用途、期限和风险承受能力确认"]},
        "confidence": _confidence_label(quant.get("confidence")),
        "status": _QUANT_TO_ARCHIVE_STATUS.get(quant.get("status"), "proposed"),
        "review_at": quant.get("review_at"),
        "actions": actions,
    }


def ensure_decision_record_for_investment(investment_id: int) -> dict:
    """为量化账本幂等创建用户可见的决策档案。"""
    from db.decisions import create_decision, get_decision, get_decision_by_investment_id
    from db.investment_decisions import get_decision as get_quant_decision

    quant = get_quant_decision(investment_id)
    if not quant:
        raise ValueError(f"量化决策不存在: {investment_id}")
    existing = get_decision_by_investment_id(investment_id)
    if existing:
        return existing

    linked_id = quant.get("decision_record_id")
    if linked_id:
        linked = get_decision(linked_id)
        if linked:
            _link_pair(linked["id"], investment_id)
            return get_decision(linked["id"]) or linked

    payload = _build_archive_payload(quant)
    archive_id = create_decision(**payload)
    try:
        _link_pair(archive_id, investment_id)
    except (sqlite3.IntegrityError, ValueError):
        logger.exception("关联量化决策失败 investment_id=%s archive_id=%s", investment_id, archive_id)
        existing = get_decision_by_investment_id(investment_id)
        if existing:
            conn = _get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM decision_actions WHERE decision_id = ? AND EXISTS "
                    "(SELECT 1 FROM decision_records WHERE id = ? AND investment_decision_id IS NULL)",
                    (archive_id, archive_id),
                )
                conn.execute(
                    "DELETE FROM decision_records WHERE id = ? AND investment_decision_id IS NULL",
                    (archive_id,),
                )
                conn.commit()
            finally:
                conn.close()
            return existing
        raise
    if quant.get("status") == "closed":
        current = get_decision(archive_id)
        if current and "量化回测已完成" not in (current.get("user_note") or ""):
            from db.decisions import append_decision_user_note

            append_decision_user_note(archive_id, "量化回测已完成；用户复盘仍需单独提交")
    return get_decision(archive_id) or {"id": archive_id, **payload, "investment_decision_id": investment_id}


def sync_archive_status(archive_id: int, archive_status: str, user_note: str = "") -> dict:
    """通过共用数据入口原子更新两套状态，交易草稿和确认也使用同一入口。"""
    from db.decisions import get_decision, update_decision_status
    from db.investment_decisions import get_decision as get_quant_decision

    archive = get_decision(archive_id)
    if not archive:
        raise ValueError(f"决策档案不存在: {archive_id}")
    quant_id = archive.get("investment_decision_id")
    if quant_id:
        quant = get_quant_decision(quant_id)
        if not quant:
            raise ValueError("关联量化账本不存在，档案状态未更新")
        if quant.get("user_id", "default") != archive.get("user_id", "default"):
            raise ValueError("决策档案与量化账本归属不一致")
    if not update_decision_status(archive_id, archive_status, user_note):
        raise ValueError("无效状态或决策不存在")
    archive = get_decision(archive_id)
    quant_id = archive.get("investment_decision_id")
    return {
        "status": archive_status,
        "sync_status": "synced" if quant_id else "not_linked",
        "decision_record": archive,
        "investment_decision": get_quant_decision(quant_id) if quant_id else None,
    }


def sync_archive_review(archive_id: int, review: dict) -> dict:
    """保留用户复盘在档案中，并将摘要追加到量化账本备注。"""
    from db.decisions import get_decision
    from db.investment_decisions import get_decision as get_quant_decision

    archive = get_decision(archive_id)
    if not archive:
        raise ValueError(f"决策档案不存在: {archive_id}")
    quant_id = archive.get("investment_decision_id")
    if not quant_id or not get_quant_decision(quant_id):
        return {"sync_status": "not_linked", "decision_record": archive}
    outcome = review.get("outcome") or "unknown"
    note = review.get("lesson") or review.get("result_note") or "用户已完成复盘"
    _append_note(quant_id, f"用户复盘({outcome})：{note}")
    return {
        "sync_status": "synced",
        "decision_record": get_decision(archive_id),
        "investment_decision": get_quant_decision(quant_id),
    }


def sync_investment_status(investment_id: int) -> dict:
    """把量化账本的状态和回测结果写入档案，但不伪造用户复盘。"""
    from db.decisions import append_decision_user_note, get_decision
    from db.investment_decisions import get_decision as get_quant_decision

    quant = get_quant_decision(investment_id)
    if not quant:
        raise ValueError(f"量化决策不存在: {investment_id}")
    archive = ensure_decision_record_for_investment(investment_id)
    quant_status = quant.get("status")
    if quant_status == "closed":
        result = "已命中" if quant.get("is_hit") is True else "未命中" if quant.get("is_hit") is False else "已完成"
        append_decision_user_note(archive["id"], f"量化回测已完成：{result}；用户复盘仍需单独提交")
    elif quant_status == "expired":
        from db.decisions import update_decision_status

        update_decision_status(archive["id"], "expired", "量化账本已过期")
    return {
        "sync_status": "synced",
        "decision_record": get_decision(archive["id"]),
        "investment_decision": get_quant_decision(investment_id),
    }


def unified_decision(*, investment_id: int | None = None, archive_id: int | None = None,
                     user_id: str | None = None) -> dict | None:
    """返回两套记录组成的统一只读视图。"""
    from db.decisions import get_decision, get_decision_by_investment_id
    from db.investment_decisions import get_decision as get_quant_decision, get_decision_by_archive_id

    archive = get_decision(archive_id) if archive_id else None
    quant = get_quant_decision(investment_id) if investment_id else None
    if archive is None and investment_id:
        archive = get_decision_by_investment_id(investment_id)
    if quant is None and archive_id:
        quant = get_decision_by_archive_id(archive_id)
    if archive is None and quant is None:
        return None
    if user_id is not None and any(item and item.get("user_id", "default") != user_id for item in (archive, quant)):
        return None
    if archive and quant:
        if archive.get("investment_decision_id") != quant.get("id") or quant.get("decision_record_id") != archive.get("id"):
            raise ValueError("决策档案与量化账本未关联")
    return {
        "decision_record": archive,
        "investment_decision": quant,
        "decision_record_id": archive.get("id") if archive else None,
        "investment_decision_id": quant.get("id") if quant else None,
    }
