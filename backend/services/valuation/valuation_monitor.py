"""估值数据利用监测 — 记录上下文注入与 LLM 实际引用情况。

本模块不阻塞主流程，所有写入都用 try/except 包裹，确保监测逻辑不影响业务。
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from db._conn import _get_conn

logger = logging.getLogger(__name__)


# ── 上下文注入监测 ──────────────────────────────────────────

def log_valuation_context_usage(
    conv_id: int = None,
    message_id: int = None,
    trace_id: str = None,
    query_source: str = "unknown",
    valuation_summary: str = "",
    index_count: int = 0,
    data_sources: list[str] = None,
    metric_types: list[str] = None,
    max_days_old: int = 0,
    has_expired: bool = False,
    online_fallback_count: int = 0,
):
    """记录一次估值上下文注入事件。"""
    try:
        from db.config import get_config_bool
        if not get_config_bool("valuation.context_usage_enabled", True):
            return

        conn = _get_conn()
        conn.execute("""
            INSERT INTO valuation_context_usage
                (conv_id, message_id, trace_id, query_source,
                 valuation_summary_length, index_count, data_sources,
                 metric_types, max_days_old, has_expired, online_fallback_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            conv_id, message_id, trace_id, query_source,
            len(valuation_summary or ""),
            index_count,
            ",".join(sorted(set(data_sources or []))),
            ",".join(sorted(set(metric_types or []))),
            max_days_old,
            1 if has_expired else 0,
            online_fallback_count,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"[valuation_monitor] 写入上下文使用日志失败: {e}")


# ── LLM 引用检测 ────────────────────────────────────────────

_VALUATION_KEYWORDS = [
    "PE", "PB", "PS", "市盈率", "市净率", "市销率", "股息率",
    "分位", "百分位", "低估", "高估", "合理", "极度低估", "极度高估",
    "zscore", "z-score", "估值",
]

_INDEX_NAME_PATTERNS = [
    r"沪深300", r"中证500", r"中证1000", r"中证红利", r"中证白酒",
    r"中证银行", r"中证医疗", r"中证医药", r"中证新能", r"中证芯片",
    r"中证军工", r"中证证券", r"中证畜牧", r"中证房地产", r"中证科技",
    r"恒生科技", r"恒生指数", r"标普500", r"纳斯达克", r"道琼斯",
    r"上证指数", r"创业板指", r"科创50", r"国证2000",
]


def _extract_valuation_refs(text: str) -> dict[str, Any]:
    """从文本中提取估值引用信息。

    返回：
        {
            "refs_found": int,
            "ref_sources": list[str],
            "ref_metrics": list[str],
            "sample_text": str,
        }
    """
    if not text:
        return {"refs_found": 0, "ref_sources": [], "ref_metrics": [], "sample_text": ""}

    refs_found = 0
    ref_metrics = set()
    ref_sources = set()
    samples = []

    # 1. 按关键词匹配，并检查附近是否有数值
    for kw in _VALUATION_KEYWORDS:
        for m in re.finditer(re.escape(kw), text, re.IGNORECASE):
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            snippet = text[start:end]
            # 附近出现数字或百分号，才认为是一次有效引用
            if re.search(r"\d+\.?\d*\s*%?|\d+%", snippet):
                refs_found += 1
                ref_metrics.add(kw)
                if len(samples) < 3:
                    samples.append(snippet)

    # 2. 识别提到的指数/基金名
    for pat in _INDEX_NAME_PATTERNS:
        for m in re.finditer(pat, text):
            ref_sources.add(m.group(0))

    return {
        "refs_found": refs_found,
        "ref_sources": sorted(ref_sources),
        "ref_metrics": sorted(ref_metrics),
        "sample_text": " ... ".join(samples)[:500],
    }


def check_valuation_reference(
    trace_id: str,
    conv_id: int = None,
    message_id: int = None,
    agent_name: str = None,
    analysis_type: str = "specialist",
    analysis_text: str = "",
):
    """检测一段分析文本是否实际引用了估值数据，并记录到库。

    analysis_type: 'specialist' / 'synthesis' / 'arbitration' / 'cross_review'
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("valuation.reference_check_enabled", True):
            return

        # 1. 是否注入了估值上下文
        context_injected = 0
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT id FROM valuation_context_usage WHERE trace_id = ? LIMIT 1",
                (trace_id,)
            ).fetchone()
            context_injected = 1 if row else 0
            conn.close()
        except Exception:
            pass

        # 2. 是否调用过估值工具
        tool_called = 0
        tool_call_count = 0
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT COUNT(*) as cnt FROM tool_audit_logs
                WHERE trace_id = ? AND tool_name IN ('query_valuation', 'query_online_valuation')
            """, (trace_id,)).fetchall()
            conn.close()
            if rows:
                tool_call_count = rows[0]["cnt"]
                tool_called = 1 if tool_call_count > 0 else 0
        except Exception:
            pass

        # 3. 文本中引用情况
        refs = _extract_valuation_refs(analysis_text)

        # 4. 幻觉风险判定
        has_hallucination_risk = 0
        confidence = "low"
        if context_injected == 0 and tool_called == 0 and refs["refs_found"] > 0:
            has_hallucination_risk = 1
            confidence = "high"
        elif (context_injected == 1 or tool_called == 1) and refs["refs_found"] == 0:
            has_hallucination_risk = 1
            confidence = "medium"
        elif refs["refs_found"] > 0:
            confidence = "high"

        conn = _get_conn()
        conn.execute("""
            INSERT INTO valuation_reference_check
                (trace_id, conv_id, message_id, agent_name, analysis_type,
                 context_injected, tool_called, tool_call_count, refs_found,
                 ref_sources, ref_metrics, has_hallucination_risk,
                 confidence, sample_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trace_id, conv_id, message_id, agent_name, analysis_type,
            context_injected, tool_called, tool_call_count, refs["refs_found"],
            json.dumps(refs["ref_sources"], ensure_ascii=False),
            json.dumps(refs["ref_metrics"], ensure_ascii=False),
            has_hallucination_risk,
            confidence,
            refs["sample_text"],
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"[valuation_monitor] 写入引用检测日志失败: {e}")


# ── 统计与报告 ──────────────────────────────────────────────

def get_valuation_usage_report(days: int = 7) -> dict[str, Any]:
    """生成估值数据利用监测报告。"""
    try:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        conn = _get_conn()

        # 1. 工具查询统计
        total_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM valuation_query_logs WHERE created_at >= ?", (since,)
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        source_rows = conn.execute("""
            SELECT final_source, COUNT(*) as cnt
            FROM valuation_query_logs
            WHERE created_at >= ?
            GROUP BY final_source
            ORDER BY cnt DESC
        """, (since,)).fetchall()

        online_row = conn.execute("""
            SELECT COUNT(*) as cnt FROM valuation_query_logs
            WHERE created_at >= ? AND final_source IN ('akshare', 'ttfund', 'online_cached')
        """, (since,)).fetchall()

        failed_row = conn.execute("""
            SELECT COUNT(*) as cnt FROM valuation_query_logs
            WHERE created_at >= ? AND final_source = 'failed'
        """, (since,)).fetchall()

        cache_row = conn.execute("""
            SELECT COUNT(*) as cnt FROM valuation_query_logs
            WHERE created_at >= ? AND cache_hit = 1
        """, (since,)).fetchall()

        # 2. 上下文注入统计
        ctx_row = conn.execute("""
            SELECT COUNT(*) as cnt,
                   AVG(index_count) as avg_index_count,
                   SUM(has_expired) as expired_count
            FROM valuation_context_usage
            WHERE created_at >= ?
        """, (since,)).fetchone()

        # 3. 引用检测统计
        ref_row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN refs_found > 0 THEN 1 ELSE 0 END) as refs,
                   SUM(CASE WHEN has_hallucination_risk = 1 AND confidence = 'medium' THEN 1 ELSE 0 END) as medium_risk,
                   SUM(CASE WHEN has_hallucination_risk = 1 AND confidence = 'high' THEN 1 ELSE 0 END) as high_risk
            FROM valuation_reference_check
            WHERE created_at >= ?
        """, (since,)).fetchone()

        # 4. 风险列表 Top 20
        risk_rows = conn.execute("""
            SELECT trace_id, conv_id, agent_name, analysis_type, refs_found,
                   has_hallucination_risk, confidence, sample_text
            FROM valuation_reference_check
            WHERE created_at >= ? AND has_hallucination_risk = 1
            ORDER BY created_at DESC
            LIMIT 20
        """, (since,)).fetchall()

        conn.close()

        source_counts = {r["final_source"]: r["cnt"] for r in source_rows}

        ref_total = ref_row["total"] if ref_row else 0
        ref_count = ref_row["refs"] if ref_row else 0

        return {
            "days": days,
            "tool_queries": {
                "total": total,
                "source_counts": source_counts,
                "source_percent": {
                    k: round(v / total * 100, 1) if total > 0 else 0
                    for k, v in source_counts.items()
                },
                "online_fallback_count": online_row[0]["cnt"] if online_row else 0,
                "failed_count": failed_row[0]["cnt"] if failed_row else 0,
                "cache_hit_count": cache_row[0]["cnt"] if cache_row else 0,
            },
            "context_injections": {
                "total": ctx_row["cnt"] if ctx_row else 0,
                "avg_index_count": round(ctx_row["avg_index_count"], 1) if ctx_row and ctx_row["avg_index_count"] else 0,
                "expired_count": ctx_row["expired_count"] if ctx_row else 0,
            },
            "reference_checks": {
                "total": ref_total,
                "ref_count": ref_count,
                "ref_rate": round(ref_count / ref_total * 100, 1) if ref_total > 0 else 0,
                "medium_risk": ref_row["medium_risk"] if ref_row else 0,
                "high_risk": ref_row["high_risk"] if ref_row else 0,
            },
            "risks": [dict(r) for r in risk_rows],
        }
    except Exception as e:
        logger.warning(f"[valuation_monitor] 生成报告失败: {e}")
        return {
            "days": days,
            "tool_queries": {"total": 0, "source_counts": {}, "source_percent": {},
                             "online_fallback_count": 0, "failed_count": 0, "cache_hit_count": 0},
            "context_injections": {"total": 0, "avg_index_count": 0, "expired_count": 0},
            "reference_checks": {"total": 0, "ref_count": 0, "ref_rate": 0,
                                 "medium_risk": 0, "high_risk": 0},
            "risks": [],
            "error": str(e),
        }


def get_conversation_valuation_usage(conv_id: int, limit: int = 100) -> dict[str, Any]:
    """获取单个对话的估值利用详情。"""
    try:
        conn = _get_conn()
        tool_rows = conn.execute("""
            SELECT id, index_name, final_source, created_at
            FROM valuation_query_logs
            WHERE conv_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (conv_id, limit)).fetchall()

        ref_rows = conn.execute("""
            SELECT id, agent_name, analysis_type, refs_found,
                   has_hallucination_risk, confidence, created_at
            FROM valuation_reference_check
            WHERE conv_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (conv_id, limit)).fetchall()
        conn.close()

        return {
            "conv_id": conv_id,
            "tool_queries": [dict(r) for r in tool_rows],
            "reference_checks": [dict(r) for r in ref_rows],
        }
    except Exception as e:
        logger.warning(f"[valuation_monitor] 获取对话估值使用详情失败: {e}")
        return {"conv_id": conv_id, "tool_queries": [], "reference_checks": [], "error": str(e)}
