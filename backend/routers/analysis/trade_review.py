"""交易复盘 — POST /api/portfolio/analysis/trade-review"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException

from db import (
    list_transactions, get_transaction_tags, get_analysis_agent,
    create_portfolio_analysis_record,
)
from db.portfolio import update_analysis_record
from db.config import get_config_int, get_config_float
from models.portfolio import TradeReviewRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-trade-review"])

_background_tasks: set = set()


def _extract_candidates_safely(record_id: int, analysis_type: str, result_text: str):
    try:
        from db.decisions import extract_recommendation_candidates_from_analysis
        extract_recommendation_candidates_from_analysis(record_id, analysis_type, result_text)
    except Exception as e:
        logger.warning(f"建议候选抽取失败 record_id={record_id}: {e}")


@router.post("/api/portfolio/analysis/trade-review")
async def trade_review_api(req: TradeReviewRequest):
    """模式 3：交易复盘 — 分析交易行为模式和操作质量。"""
    txs = list_transactions(limit=500)
    if not txs:
        raise HTTPException(400, "暂无交易记录")

    agent = get_analysis_agent(5)
    if not agent:
        raise HTTPException(404, "交易复盘分析师未配置")

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
    for t in sorted(txs, key=lambda x: x.get("transaction_date", "")):
        tags = get_transaction_tags(t["id"])
        tag_str = f" [{','.join(tags)}]" if tags else ""

        # 解析估值快照
        snapshot_str = t.get("valuation_snapshot")
        valuation_str = ""
        if snapshot_str:
            try:
                snap = json.loads(snapshot_str)
                pe_pct = snap.get("pe_percentile")
                pb_pct = snap.get("pb_percentile")
                if pe_pct is not None:
                    valuation_str = f" [PE分位:{pe_pct:.1f}%"
                    if pb_pct is not None:
                        valuation_str += f", PB分位:{pb_pct:.1f}%"
                    valuation_str += "]"
            except Exception:
                pass

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

    # 有估值快照的交易统计
    txs_with_valuation = [t for t in txs if t.get("valuation_snapshot")]
    valuation_summary = ""
    if txs_with_valuation:
        buy_with_val = [t for t in txs_with_valuation if t["transaction_type"] == "buy"]
        sell_with_val = [t for t in txs_with_valuation if t["transaction_type"] == "sell"]
        if buy_with_val:
            avg_buy_pe = sum(json.loads(t["valuation_snapshot"]).get("pe_percentile", 50)
                           for t in buy_with_val) / len(buy_with_val)
            low_buy = len([t for t in buy_with_val
                          if json.loads(t["valuation_snapshot"]).get("pe_percentile", 50) < 30])
            valuation_summary += f"\n买入时估值分析: 平均PE分位 {avg_buy_pe:.1f}%, 低估买入(PE<30%) {low_buy}/{len(buy_with_val)} 笔"
        if sell_with_val:
            avg_sell_pe = sum(json.loads(t["valuation_snapshot"]).get("pe_percentile", 50)
                            for t in sell_with_val) / len(sell_with_val)
            high_sell = len([t for t in sell_with_val
                           if json.loads(t["valuation_snapshot"]).get("pe_percentile", 50) > 70])
            valuation_summary += f"\n卖出时估值分析: 平均PE分位 {avg_sell_pe:.1f}%, 高估卖出(PE>70%) {high_sell}/{len(sell_with_val)} 笔"

    user_content = (
        f"## 操作总览\n"
        f"- 买入 {buy_count} 笔, 共 {buy_total:.2f} 元\n"
        f"- 卖出 {sell_count} 笔, 共 {sell_total:.2f} 元\n"
        f"- 净投入: {buy_total - sell_total:.2f} 元\n"
        f"- 手续费总计: {total_fee:.2f} 元\n"
        f"{valuation_summary}\n"
        f"\n## 交易明细（含交易时点估值）\n" + "\n".join(tx_lines)
    )

    # 创建记录（status='running'）
    record_id = create_portfolio_analysis_record(
        analysis_type="trade_review",
        summary=f"交易复盘 · {buy_count}买{sell_count}卖",
        input_data=json.dumps({"start_date": req.start_date, "end_date": req.end_date, "tx_count": len(txs)},
                              ensure_ascii=False),
        status="running",
        agent_id=5,
    )

    # 后台执行分析
    task = asyncio.create_task(_run_trade_review_async(record_id, agent["system_prompt"], user_content))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"ok": True, "id": record_id, "status": "running"}


async def _run_trade_review_async(record_id: int, system_prompt: str, user_content: str):
    """后台执行交易复盘分析。"""
    import uuid
    trace_id = f"trd_{uuid.uuid4().hex[:12]}"
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_trade_review",
            trace_id=trace_id,
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=get_config_float('llm.temperature_analysis', 0.3),
            max_tokens=get_config_int('llm.max_tokens_analysis', 8192),
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
        update_analysis_record(record_id, result_data=result_text, token_usage=tokens, status="done")
        _extract_candidates_safely(record_id, "trade_review", result_text)
        logger.info(f"交易复盘完成 record_id={record_id}")
    except Exception as e:
        logger.error(f"交易复盘失败 record_id={record_id}: {e}")
        update_analysis_record(record_id, status="error", error_msg=str(e))


@router.get("/api/portfolio/analysis/trade-review/records")
async def list_trade_review_records_api(limit: int = 10):
    """列出交易复盘历史记录。"""
    from db import list_portfolio_analysis_records
    records = list_portfolio_analysis_records(analysis_type="trade_review", limit=limit)
    return {"records": records}
