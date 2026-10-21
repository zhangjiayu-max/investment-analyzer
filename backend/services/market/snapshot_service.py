"""交易日行情快照服务 — 全系统统一行情口径。

问题（2026-10-21 复核）：
- 日报走 `get_market_overview()`（akshare/东财，有涨跌家数与板块）
- 市场情报走盈米 `get_latest_quotations()`（基金净值口径）
两条路径互不相干，同一天可以得出"上证 +0.12%、3778 涨"和"上证 -0.60%、
3364 跌"两套相反结论，且都没标注数据截止日。

做法：
1. 按交易日只保留一份快照（`market_snapshots` 表），跨进程复用；
2. 快照内自带 `as_of`（数据截止日）与 `is_intraday`（盘中/收盘）；
3. 输出给模型的文本强制带"数据截止日"和"唯一口径"声明；
4. 任何 agent 引用大盘涨跌，只能引用本快照，不得另取数据源。
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 同一天内两次抓取的复用窗口：行情在盘中会变，收盘后基本不变
_INTRADAY_REUSE_MINUTES = 15
_CLOSED_REUSE_MINUTES = 24 * 60


def _is_trading_session(now: datetime.datetime) -> bool:
    """A 股连续竞价时段（不含集合竞价细节），用于判定盘中/收盘。"""
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 30) <= minutes <= (11 * 60 + 30) or (13 * 60) <= minutes <= (15 * 60)


def _latest_history_date() -> str:
    """库内最新收盘交易日；取不到返回空串。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            row = conn.execute("SELECT MAX(trade_date) FROM index_price_history").fetchone()
            return (row[0] or "") if row else ""
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(f"[market_snapshot] 读取最新交易日失败: {exc}")
        return ""


def resolve_as_of(now: Optional[datetime.datetime] = None) -> tuple[str, bool]:
    """返回 (数据截止日, 是否盘中)。

    盘中 → 当天（实时涨跌幅就是当天的）
    非盘中 → 库内最新收盘交易日（而不是简单用今天，避免把隔夜数据当今日）
    """
    now = now or datetime.datetime.now()
    today = now.date().isoformat()
    if _is_trading_session(now):
        return today, True
    latest = _latest_history_date()
    if latest:
        return latest, False
    return today, False


def _fresh_enough(snapshot: dict, now: datetime.datetime) -> bool:
    fetched = snapshot.get("fetched_at") or ""
    try:
        fetched_at = datetime.datetime.strptime(fetched, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False
    ttl = _INTRADAY_REUSE_MINUTES if snapshot.get("is_intraday") else _CLOSED_REUSE_MINUTES
    return (now - fetched_at) < datetime.timedelta(minutes=ttl)


def _fetch_overview() -> tuple[dict, str]:
    """抓一次行情；返回 (payload, source)。失败返回空 payload。"""
    try:
        from services.market_data import get_market_overview
        overview = get_market_overview()
        if overview and (overview.get("indices") or overview.get("breadth")):
            return overview, "akshare/eastmoney"
    except Exception as exc:
        logger.warning(f"[market_snapshot] get_market_overview 失败: {exc}")
    return {}, ""


def get_trading_day_snapshot(force: bool = False,
                             now: Optional[datetime.datetime] = None,
                             peek: bool = False) -> dict:
    """取当日统一行情快照（必要时刷新并落库）。

    peek=True 时只读库、不触发网络抓取 —— 供工具调用等对延迟敏感的路径使用。

    返回 {'trade_date', 'as_of', 'is_intraday', 'payload', 'source', 'fetched_at'}
    取不到任何行情时返回 payload={} 的占位快照，调用方需自行降级。
    """
    from db.market_snapshot import get_snapshot, save_snapshot

    now = now or datetime.datetime.now()
    as_of, intraday = resolve_as_of(now)

    if not force:
        cached = get_snapshot(as_of)
        if cached and _fresh_enough(cached, now):
            return cached
        if peek:
            return cached or {
                "trade_date": as_of, "as_of": as_of, "is_intraday": intraday,
                "payload": {}, "source": "", "fetched_at": "",
            }

    payload, source = _fetch_overview()
    if not payload:
        # 抓不到就沿用旧快照，并标记来源为 stale，避免整份报告缺行情
        stale = get_snapshot(as_of)
        if stale:
            stale["source"] = (stale.get("source") or "") + "/stale"
            return stale
        return {
            "trade_date": as_of, "as_of": as_of, "is_intraday": intraday,
            "payload": {}, "source": "", "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

    save_snapshot(
        trade_date=as_of, as_of=as_of, payload=payload, source=source,
        is_intraday=intraday, fetched_at=now.strftime("%Y-%m-%d %H:%M:%S"),
    )
    return {
        "trade_date": as_of, "as_of": as_of, "is_intraday": intraday,
        "payload": payload, "source": source,
        "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def format_snapshot_text(snapshot: dict, with_sectors: bool = True) -> str:
    """把快照渲染成可直接塞进 prompt 的文本，强制带数据截止日。"""
    if not snapshot or not snapshot.get("payload"):
        return "【行情快照】本次未获取到行情数据，禁止凭空描述今日涨跌；如需行情结论请说明数据缺失。"

    payload = snapshot["payload"]
    as_of = snapshot.get("as_of") or snapshot.get("trade_date") or "未知"
    mode = "盘中实时" if snapshot.get("is_intraday") else "收盘"
    source = snapshot.get("source") or "未知"
    lines = [
        f"【行情快照｜数据截止 {as_of}（{mode}）｜来源 {source}】",
        "口径声明：全系统同一交易日只使用本条快照。引用大盘涨跌/涨跌家数/成交额时必须标注"
        f"「数据截止 {as_of}」；不得与其他来源（含盈米基金净值口径）混用，也不得改称"
        "「今日」而不注明日期。",
    ]

    indices = payload.get("indices") or []
    if indices:
        lines.append("")
        lines.append("主要指数：")
        for idx in indices:
            pct = idx.get("change_pct") or 0
            sign = "+" if pct >= 0 else ""
            lines.append(
                f"- {idx.get('name','')}: {idx.get('price',0)}（{sign}{pct}%）"
                f"成交{(idx.get('volume_yi') or 0):.0f}亿"
            )

    breadth = payload.get("breadth") or {}
    if breadth.get("up") or breadth.get("down"):
        lines.append("")
        lines.append(
            f"涨跌统计：上涨{breadth.get('up',0)} / 下跌{breadth.get('down',0)}"
            f" / 成交{(breadth.get('total_volume_yi') or 0):.0f}亿"
        )

    if with_sectors:
        for key, label in (("sectors_top", "领涨板块"), ("sectors_bottom", "领跌板块")):
            items = payload.get(key) or []
            if not items:
                continue
            lines.append("")
            lines.append(f"{label}：")
            for item in items:
                pct = item.get("change_pct") or 0
                sign = "+" if pct >= 0 else ""
                lines.append(f"- {item.get('name','')}: {sign}{pct}%")

    return "\n".join(lines)
