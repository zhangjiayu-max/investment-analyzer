"""交易复盘 — POST /api/portfolio/analysis/trade-review"""
import asyncio
import json
import logging
import math
import time

from fastapi import APIRouter, HTTPException

from db import (
    list_transactions, get_transaction_tags, get_analysis_agent_by_name,
    create_portfolio_analysis_record,
    create_async_task, update_async_task, get_running_async_task,
)
from db.portfolio import update_analysis_record
from db.agent_analysis_log import create_analysis_log, complete_analysis_log
from ._shared import build_portfolio_facts_with_check, build_data_gap_section
from db.config import get_config_int, get_config_float
from models.portfolio import TradeReviewRequest

from ._agent_contract import require_analysis_agent

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-trade-review"])

_background_tasks: set = set()


def _snapshot_percentile(snapshot: dict, key: str) -> float | None:
    """历史快照允许缺字段；不将缺失分位伪装成中位数。"""
    value = snapshot.get(key)
    if isinstance(value, bool):
        return None
    try:
        value = float(str(value).strip().removesuffix("%"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 <= value <= 100 else None


def _extract_candidates_safely(record_id: int, analysis_type: str, result_text: str):
    try:
        from db.decisions import extract_recommendation_candidates_from_analysis
        extract_recommendation_candidates_from_analysis(record_id, analysis_type, result_text)
    except Exception as e:
        logger.warning(f"建议候选抽取失败 record_id={record_id}: {e}")


@router.post("/api/portfolio/analysis/trade-review")
async def trade_review_api(req: TradeReviewRequest):
    """模式 3：交易复盘 — 分析交易行为模式和操作质量。"""
    agent = require_analysis_agent("交易复盘分析师", get_analysis_agent_by_name)
    txs = list_transactions(limit=500)
    if not txs:
        raise HTTPException(400, "暂无交易记录")

    # 过滤日期范围
    if req.start_date:
        txs = [t for t in txs if t.get("transaction_date", "") >= req.start_date]
    if req.end_date:
        txs = [t for t in txs if t.get("transaction_date", "") <= req.end_date]
    if not txs:
        raise HTTPException(400, "所选日期范围内无交易记录")

    # 交易记录 + 标签 + 估值快照
    tx_lines = []
    total_fee = 0
    pe_values = {"buy": [], "sell": []}
    for t in sorted(txs, key=lambda x: x.get("transaction_date", "")):
        tags = get_transaction_tags(t["id"])
        tag_str = f" [{','.join(tags)}]" if tags else ""

        # 解析估值快照
        snapshot_str = t.get("valuation_snapshot")
        valuation_str = ""
        snap = {}
        if snapshot_str:
            try:
                parsed = json.loads(snapshot_str)
                if isinstance(parsed, dict):
                    snap = parsed
            except (ValueError, TypeError):
                pass
        pe_pct = _snapshot_percentile(snap, "pe_percentile")
        pb_pct = _snapshot_percentile(snap, "pb_percentile")
        if pe_pct is not None and t["transaction_type"] in pe_values:
            pe_values[t["transaction_type"]].append(pe_pct)
        valuation_parts = []
        if pe_pct is not None:
            valuation_parts.append(f"PE分位:{pe_pct:.1f}%")
        if pb_pct is not None:
            valuation_parts.append(f"PB分位:{pb_pct:.1f}%")
        if valuation_parts:
            valuation_str = " [" + ", ".join(valuation_parts) + "]"

        # 手续费
        fee = t.get("fee") or 0
        total_fee += fee
        fee_str = f", 手续费 {fee:.2f}" if fee > 0 else ""

        tx_lines.append(
            f"- {t['transaction_date']} {t.get('transaction_time','')} "
            f"{'买入' if t['transaction_type']=='buy' else '卖出'}"
            f"{tag_str}{valuation_str}: "
            f"{t.get('fund_name','')}({t.get('fund_code','')}), "
            f"金额 {(t.get('amount') or 0):.2f}, 价格 {(t.get('price') or 0):.4f}{fee_str}"
        )

    # 汇总统计
    buy_count = len([t for t in txs if t["transaction_type"] == "buy"])
    sell_count = len([t for t in txs if t["transaction_type"] == "sell"])
    buy_total = sum(t.get("amount", 0) or 0 for t in txs if t["transaction_type"] == "buy")
    sell_total = sum(t.get("amount", 0) or 0 for t in txs if t["transaction_type"] == "sell")

    # 仅用有效 PE 样本计算统计，并向模型说明覆盖率，避免缺失值污染判断。
    valuation_summary = ""
    for kind, count, label in (("buy", buy_count, "买入"), ("sell", sell_count, "卖出")):
        if not count:
            continue
        values = pe_values[kind]
        valuation_summary += f"\n{label}估值数据覆盖: 有效PE快照 {len(values)}/{count} 笔，PE分位缺失或无效 {count - len(values)} 笔"
        if values:
            condition = "低估买入(PE<30%)" if kind == "buy" else "高估卖出(PE>70%)"
            matched = sum(v < 30 if kind == "buy" else v > 70 for v in values)
            valuation_summary += f"\n{label}时估值分析: 平均PE分位 {sum(values) / len(values):.1f}%, {condition} {matched}/{len(values)} 笔"

    # 组合约束注入
    facts_block = build_portfolio_facts_with_check()

    user_content = (
        f"## 组合约束（系统注入，优先级最高）\n```json\n{facts_block}\n```\n\n---\n\n"
        f"## 操作总览\n"
        f"- 买入 {buy_count} 笔, 共 {buy_total:.2f} 元\n"
        f"- 卖出 {sell_count} 笔, 共 {sell_total:.2f} 元\n"
        f"- 净投入: {buy_total - sell_total:.2f} 元\n"
        f"- 手续费总计: {total_fee:.2f} 元\n"
        f"{valuation_summary}\n"
        f"\n## 交易明细（含交易时点估值）\n" + "\n".join(tx_lines)
    )

    from db.async_tasks import start_analysis_task
    started = start_analysis_task(
        "trade_review_analysis", "trade_review", {"start_date": req.start_date, "end_date": req.end_date, "agent_id": agent["id"]},
        "portfolio_analysis_records", dict(analysis_type="trade_review", summary=f"交易复盘 · {buy_count}买{sell_count}卖", input_data=json.dumps({"start_date": req.start_date, "end_date": req.end_date, "tx_count": len(txs)}, ensure_ascii=False), agent_id=agent["id"]))
    if started["reused"]:
        return started
    record_id, async_task_id = started["id"], started["task_id"]

    task = asyncio.create_task(_run_trade_review_async(async_task_id, record_id, agent["system_prompt"], user_content, agent["id"], agent["name"]))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return started


async def _run_trade_review_async(async_task_id: int, record_id: int, system_prompt: str, user_content: str, agent_id: int = None, agent_name: str = ""):
    """后台执行交易复盘分析。"""
    import uuid
    trace_id = f"log_{uuid.uuid4().hex[:12]}"
    _start_ts = time.time()
    try:
        create_analysis_log(
            trace_id=trace_id, agent_id=agent_id, agent_name=agent_name,
            analysis_type="trade_review", source_table="portfolio_analysis_records",
            source_id=record_id, query=user_content[:300],
            input_summary="交易复盘",
        )
        from services.llm_service import _call_llm, MODEL_AUX
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_trade_review",
            trace_id=trace_id,
            model=MODEL_AUX,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=get_config_float('llm.temperature_analysis', 0.3),
            max_tokens=get_config_int('llm.max_tokens_analysis', 8192),
        ))
        result_text = response.choices[0].message.content or ""
        if not result_text.strip():
            raise ValueError("模型未返回有效分析，请重试")
        tokens = response.usage.total_tokens if response.usage else 0
        update_analysis_record(record_id, result_data=result_text, token_usage=tokens, status="done")
        update_async_task(async_task_id, status="done")
        _elapsed_ms = int((time.time() - _start_ts) * 1000)
        complete_analysis_log(trace_id=trace_id, status="done", duration_ms=_elapsed_ms, token_usage=tokens)
        _extract_candidates_safely(record_id, "trade_review", result_text)
        logger.info(f"交易复盘完成 record_id={record_id}")
    except Exception as e:
        logger.error(f"交易复盘失败 record_id={record_id}: {e}")
        update_analysis_record(record_id, status="error", error_msg=str(e))
        update_async_task(async_task_id, status="error", error_msg=str(e))
        _elapsed_ms = int((time.time() - _start_ts) * 1000)
        complete_analysis_log(trace_id=trace_id, status="error", duration_ms=_elapsed_ms, error_msg=str(e))


@router.get("/api/portfolio/analysis/trade-review/records")
async def list_trade_review_records_api(limit: int = 10):
    """列出交易复盘历史记录。"""
    from db import list_portfolio_analysis_records
    records = list_portfolio_analysis_records(analysis_type="trade_review", limit=limit)
    return {"records": records}
