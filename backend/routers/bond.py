"""债市数据路由 — /api/bond/*"""

import json
import logging
import re
import time
import html as html_mod

import requests as req
from fastapi import APIRouter, HTTPException

from db import (
    list_holdings, get_cash_balance, get_total_cash_balance, get_analysis_agent,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
    DEFAULT_BOND_PROMPT,
)
from state import track_agent, untrack_agent

router = APIRouter(prefix="/api/bond", tags=["bond"])


def _fetch_bond_data():
    """抓取有知有行债市温度数据，返回原始数据列表。"""
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

    return json.loads(raw[:end_idx])


@router.get("/market-temperature")
async def get_bond_market_temperature():
    """抓取有知有行债市温度数据。"""
    try:
        data = _fetch_bond_data()
        last = data[-1] if data else {}
        return {
            "history": data,
            "current": {
                "date": last.get("date"),
                "temperature": last.get("degree"),
                "rate": float(last["yield"]) if last.get("yield") else None,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"数据源请求失败: {e}")


@router.get("/yield-curve")
async def bond_yield_curve_api(country: str = "china"):
    """获取国债收益率曲线数据。"""
    from tools import _get_bond_yield_curve
    result = json.loads(_get_bond_yield_curve({"country": country}))
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.get("/market-overview")
async def bond_market_overview_api():
    """获取债市综合概况。"""
    from tools import _get_bond_market_overview
    result = json.loads(_get_bond_market_overview())
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.post("/ai-recommend")
async def bond_ai_recommend():
    """AI 债券配置推荐。"""
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

    combined_input = "请基于以下数据给出债券配置建议：\n\n" + "\n\n".join(context_lines)

    # 9. 调用 LLM
    uid = f"bond_{int(time.time())}"
    track_agent(uid, "债券配置顾问", "债券配置推荐")
    try:
        from llm_service import chat_with_agent
        result = chat_with_agent(system_prompt, [{"role": "user", "content": combined_input}], max_tokens=8000)
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
    try:
        summary_text = parsed.get("summary", "债券配置推荐") if isinstance(parsed, dict) else "债券配置推荐"
        create_portfolio_analysis_record(
            analysis_type="bond_recommend",
            summary=summary_text[:100],
            input_data=combined_input[:2000],
            result_data=json_mod.dumps(parsed, ensure_ascii=False),
        )
    except Exception as e:
        logging.error(f"[bond_ai_recommend] 保存记录失败: {e}")

    return {"ok": True, "result": parsed}


@router.get("/ai-recommend/records")
async def list_bond_recommend_records_api(limit: int = 5):
    """列出债券推荐历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="bond_recommend", limit=limit)
    return {"records": records}
