"""债券 AI 推荐 — 从 bond.py 提取"""
import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter

from db.config import get_config_int
from db import (
    list_holdings, get_total_cash_balance, get_analysis_agent,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
    DEFAULT_BOND_PROMPT,
    create_async_task, update_async_task,
    save_analysis_conclusion,
)
from state import track_agent, untrack_agent

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-bond-recommend"])

_background_tasks = set()


def _fetch_bond_data():
    """抓取有知有行债市温度数据，返回原始数据列表。"""
    try:
        import html as html_mod
        import requests as req
        resp = req.get(
            "https://youzhiyouxing.cn/data/macro",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=15,
        )
        resp.raise_for_status()

        match = re.search(r'data-cbond-history="([^"]+)"', resp.text)
        if not match:
            return []

        raw = html_mod.unescape(match.group(1))
        bracket_count = 0
        end_idx = 0
        for i, c in enumerate(raw):
            if c == "[":
                bracket_count += 1
            elif c == "]":
                bracket_count -= 1
                if bracket_count == 0:
                    end_idx = i + 1
                    break

        if end_idx == 0:
            logging.warning("[_fetch_bond_data] 未找到完整 JSON 数组")
            return []

        return json.loads(raw[:end_idx])
    except Exception as e:
        logging.warning(f"[_fetch_bond_data] 错误: {e}")
        return []


@router.post("/api/bond/ai-recommend")
async def bond_ai_recommend():
    """AI 债券配置推荐（异步）。立即返回 task_id，后台执行。"""
    task_id = create_async_task("bond_recommend", caller="bond_ai_recommend")
    task = asyncio.create_task(_run_bond_recommend_async(task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


async def _run_bond_recommend_async(task_id: int):
    """后台执行 AI 债券配置推荐。"""
    try:
        result = await _do_bond_recommend()
        update_async_task(task_id, status="done", result=result)
    except Exception as e:
        logging.error(f"债券推荐异步任务失败: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


async def _do_bond_recommend():
    """AI 债券配置推荐业务逻辑。"""
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    import akshare as ak
    import json as json_mod
    from tools import _get_bond_yield_curve, _get_macro_policy_data

    # 1. 债市温度（完整历史，用于趋势分析）
    bond_history = []
    try:
        raw = _fetch_bond_data()
        if raw:
            bond_history = raw[-90:]
    except Exception:
        pass

    # 2. 收益率曲线
    yield_curve = {}
    try:
        yc = json.loads(_get_bond_yield_curve({"country": "china"}))
        if "error" not in yc:
            yield_curve = yc
    except Exception:
        pass

    # 3. 现有持仓穿透分析
    holdings_with_penetration = []
    total_bond_value = 0
    total_portfolio_value = 0
    try:
        all_h = list_holdings()
        for h in all_h:
            if (h.get("shares") or 0) > 0:
                v = h.get("current_value") or 0
                total_portfolio_value += v
                if h.get("fund_category") == "bond":
                    total_bond_value += v
                    code = h.get("fund_code", "")
                    has_stock = False
                    stock_ratio = 0
                    try:
                        stock_df = ak.fund_portfolio_hold_em(symbol=code, date="2025")
                        if stock_df is not None and not stock_df.empty:
                            has_stock = True
                            stock_ratio = float(stock_df["占净值比例"].sum())
                    except Exception:
                        pass
                    bond_types = []
                    try:
                        bond_df = ak.fund_portfolio_bond_hold_em(symbol=code, date="2025")
                        if bond_df is not None and not bond_df.empty:
                            for _, row in bond_df.iterrows():
                                bname = str(row.get("债券名称", ""))
                                if any(k in bname for k in ("国债", "国开", "政金", "农发", "进出")):
                                    bond_types.append("利率债")
                                elif "可转债" in bname or "转债" in bname:
                                    bond_types.append("可转债")
                                else:
                                    bond_types.append("信用债")
                    except Exception:
                        pass
                    holdings_with_penetration.append({
                        "code": code,
                        "name": h.get("fund_name", ""),
                        "value": v,
                        "pct_of_portfolio": round(v / total_portfolio_value * 100, 1) if total_portfolio_value > 0 else 0,
                        "profit": h.get("profit_loss", 0),
                        "has_stock": has_stock,
                        "stock_ratio_pct": round(stock_ratio, 2),
                        "bond_type_tags": list(set(bond_types)) if bond_types else ["待确认"],
                    })
    except Exception:
        pass

    # 4. 零钱余额
    cash_balance = 0
    try:
        cash_balance = get_total_cash_balance()
    except Exception:
        pass

    # 5. 全市场纯债基金排行榜
    all_bond_funds = []
    try:
        df = ak.fund_open_fund_rank_em(symbol="债券型")
        pure_mask = ~df["基金简称"].str.contains("可转债", na=False)
        for _, row in df[pure_mask].head(30).iterrows():
            all_bond_funds.append({
                "code": row["基金代码"],
                "name": row["基金简称"],
                "year_return": row.get("近1年"),
                "fee": row.get("手续费"),
            })
    except Exception:
        pass

    # 6. 货币基金排行榜
    money_funds = []
    try:
        mf = ak.fund_money_rank_em()
        for _, row in mf.head(5).iterrows():
            money_funds.append({
                "code": row.get("基金代码", ""),
                "name": row.get("基金简称", ""),
                "year_return": row.get("近1年"),
            })
    except Exception:
        pass

    # 7. 宏观货币政策数据
    macro_data = {}
    try:
        macro_raw = _get_macro_policy_data()
        macro_data = json_mod.loads(macro_raw) if isinstance(macro_raw, str) else macro_raw
    except Exception:
        pass

    # 8. 构建 LLM 上下文
    # 组合约束注入
    facts_block = ""
    try:
        from portfolio_fact_layer import build_portfolio_facts
        facts = build_portfolio_facts()
        facts_block = json_mod.dumps(facts, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass

    agent = get_analysis_agent(8)
    system_prompt = agent["system_prompt"] if agent else DEFAULT_BOND_PROMPT

    context_lines = [
        f"## 债市温度历史（近90天）\n{json_mod.dumps(bond_history, ensure_ascii=False, indent=2)}",
        f"## 收益率曲线\n{json_mod.dumps(yield_curve, ensure_ascii=False, indent=2)}",
        f"## 宏观货币政策环境\n{json_mod.dumps(macro_data, ensure_ascii=False, indent=2)}",
        f"## 现有债券持仓（含穿透数据）\n持有 {len(holdings_with_penetration)} 只，总值 {total_bond_value:.2f}，占总资产 {round(total_bond_value/total_portfolio_value*100,1) if total_portfolio_value > 0 else 0}%\n" + json_mod.dumps(holdings_with_penetration, ensure_ascii=False, indent=2),
        f"## 零钱余额\n{cash_balance} 元（占总资产 {round(cash_balance/total_portfolio_value*100,1) if total_portfolio_value > 0 else 0}%）",
        f"## 全市场纯债基金排行榜（Top 30）\n" + json_mod.dumps(all_bond_funds, ensure_ascii=False, indent=2),
        f"## 货币基金排行榜（备选）\n" + json_mod.dumps(money_funds, ensure_ascii=False, indent=2),
    ]

    combined_input = "请基于以下数据给出债券配置建议：\n\n"

    # 注入组合约束
    if facts_block:
        combined_input += f"## 组合约束（系统注入，优先级最高）\n```json\n{facts_block}\n```\n\n---\n\n"

    combined_input += "\n\n".join(context_lines)

    # 9. 调用 LLM
    uid = f"bond_{int(time.time())}"
    track_agent(uid, "债券配置顾问", "债券配置推荐")
    try:
        from llm_service import chat_with_agent
        result = chat_with_agent(system_prompt, [{"role": "user", "content": combined_input}], max_tokens=get_config_int('llm.max_tokens_analysis', 8000))
        logging.info(f"[bond_ai_recommend] LLM result length: {len(result) if result else 0}, type: {type(result)}")
        if not result:
            logging.warning("[bond_ai_recommend] LLM returned empty/None result")
            result = ""
    except Exception as e:
        logging.error(f"[bond_ai_recommend] LLM call failed: {e}")
        result = ""
    finally:
        untrack_agent(uid)

    # 尝试从结果中提取 JSON
    try:
        parsed = json_mod.loads(result)
    except Exception:
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', result, re.DOTALL)
        if match:
            try:
                parsed = json_mod.loads(match.group(1))
            except Exception:
                parsed = {"summary": "AI分析完成", "raw": result}
        else:
            parsed = {"summary": "AI分析完成", "raw": result}

    # 持久化到数据库
    record_id = None
    try:
        summary_text = parsed.get("summary", "债券配置推荐") if isinstance(parsed, dict) else "债券配置推荐"
        record_id = create_portfolio_analysis_record(
            analysis_type="bond_recommend",
            summary=summary_text[:100],
            input_data=combined_input[:2000],
            result_data=json_mod.dumps(parsed, ensure_ascii=False),
        )
    except Exception as e:
        logging.error(f"[bond_ai_recommend] 保存记录失败: {e}")

    # ── 桥接 B：保存分析结论 ──
    try:
        raw_text = result if result else ""
        summary = raw_text[:100].replace("\n", " ").strip()
        action = "hold"
        for candidate, act in [("减仓", "decrease"), ("加仓", "increase"),
                                ("买入", "buy"), ("卖出", "sell"),
                                ("配置", "increase"), ("定投", "increase"),
                                ("持有", "hold"), ("观望", "hold")]:
            if candidate in raw_text:
                action = act
                break

        key_vars = []
        for var in ["债市温度", "利率", "收益率", "曲线", "利差",
                    "信用", "久期", "仓位", "货币", "宏观"]:
            if var in raw_text:
                key_vars.append(var)

        save_analysis_conclusion(
            source_system="independent_analysis",
            source_type="bond_recommend",
            source_id=record_id,
            target_subject="债券型基金",
            action=action,
            summary=summary,
            reasoning=raw_text[100:250].replace("\n", " ").strip() if len(raw_text) > 100 else "",
            key_variables=key_vars[:5] if key_vars else None,
        )
    except Exception as e:
        logging.warning(f"[bond_ai_recommend] 结论保存失败: {e}")

    return {"ok": True, "result": parsed}


@router.get("/api/bond/ai-recommend/records")
async def list_bond_recommend_records_api(limit: int = 5):
    """列出债券推荐历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="bond_recommend", limit=limit)
    return {"records": records}
