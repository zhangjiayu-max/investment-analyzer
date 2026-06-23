"""费率拖累分析 — 计算持仓基金的真实持有成本"""
import logging
import time
from datetime import datetime

from fastapi import APIRouter

from db import list_holdings, get_config_int, create_async_task, update_async_task
from db.portfolio import save_analysis_cache, get_analysis_cache
from llm_service import _call_llm, call_llm_async, MODEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio/analysis", tags=["analysis-fee"])

_background_tasks: set = set()


def _get_fund_fees(fund_code: str) -> dict:
    """获取基金费率信息"""
    result = {
        "management_fee": 0.0,
        "custody_fee": 0.0,
        "sales_service_fee": 0.0,
        "subscription_fee": "",
        "redemption_rules": [],
        "total_annual": 0.0,
        "source": "",
    }
    try:
        import akshare as ak
        df = ak.fund_fee_em(symbol=fund_code, indicator="运作费用")
        if not df.empty:
            row = df.iloc[0]
            for i, val in enumerate(row):
                if str(val) == "管理费率" and i + 1 < len(row):
                    rate_str = str(row.iloc[i + 1]).replace("%（每年）", "").replace("%", "").strip()
                    try:
                        result["management_fee"] = float(rate_str)
                    except ValueError:
                        pass
                elif str(val) == "托管费率" and i + 1 < len(row):
                    rate_str = str(row.iloc[i + 1]).replace("%（每年）", "").replace("%", "").strip()
                    try:
                        result["custody_fee"] = float(rate_str)
                    except ValueError:
                        pass
                elif str(val) == "销售服务费率" and i + 1 < len(row):
                    rate_str = str(row.iloc[i + 1]).replace("%（每年）", "").replace("%", "").strip()
                    try:
                        result["sales_service_fee"] = float(rate_str)
                    except ValueError:
                        pass
            result["total_annual"] = result["management_fee"] + result["custody_fee"] + result["sales_service_fee"]
            result["source"] = "天天基金"
    except Exception as e:
        logger.warning(f"[fee] 获取{fund_code}费率失败: {e}")

    try:
        import akshare as ak
        df2 = ak.fund_fee_em(symbol=fund_code, indicator="赎回费率")
        if not df2.empty:
            for _, row in df2.iterrows():
                period = str(row.get("适用期限", ""))
                rate = str(row.get("赎回费率", ""))
                result["redemption_rules"].append({"period": period, "rate": rate})
    except Exception:
        pass

    return result


def _find_cheaper_alternatives(fund_code: str, fund_name: str, index_code: str = "") -> list[dict]:
    """寻找同指数的低费率替代基金"""
    alternatives = []
    try:
        import akshare as ak
        if index_code:
            df = ak.fund_info_index_em()
            if not df.empty and "跟踪标的" in df.columns:
                same_index = df[df["跟踪标的"].str.contains(index_code, na=False)]
                for _, row in same_index.head(10).iterrows():
                    code = str(row.get("基金代码", ""))
                    name = str(row.get("基金名称", ""))
                    fee_str = str(row.get("手续费", "0"))
                    try:
                        fee = float(fee_str.replace("%", ""))
                    except ValueError:
                        fee = 0
                    if code != fund_code:
                        alternatives.append({
                            "fund_code": code,
                            "fund_name": name,
                            "fee_pct": fee,
                        })
                alternatives.sort(key=lambda x: x["fee_pct"])
    except Exception as e:
        logger.warning(f"[fee] 寻找替代基金失败: {e}")
    return alternatives[:5]


def _format_fee_analysis_text(holdings: list, fee_data: dict) -> str:
    """格式化费率分析文本"""
    lines = []
    lines.append("# 📊 持仓费率拖累分析报告\n")
    lines.append(f"分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    total_annual_cost = 0.0
    total_value = 0.0
    fund_details = []

    for h in holdings:
        code = h.get("fund_code", "")
        name = h.get("fund_name", "")
        value = h.get("current_value", 0) or 0
        if value <= 0:
            continue
        fees = fee_data.get(code, {})
        annual_rate = fees.get("total_annual", 0)
        annual_cost = value * annual_rate / 100
        total_annual_cost += annual_cost
        total_value += value

        fund_details.append({
            "code": code,
            "name": name,
            "value": value,
            "rate": annual_rate,
            "cost": annual_cost,
            "mgmt": fees.get("management_fee", 0),
            "custody": fees.get("custody_fee", 0),
            "sales": fees.get("sales_service_fee", 0),
        })

    if not fund_details:
        return "无有效持仓数据"

    fund_details.sort(key=lambda x: x["rate"], reverse=True)

    avg_rate = total_annual_cost / total_value * 100 if total_value > 0 else 0
    lines.append("## 总览\n")
    lines.append(f"- 持仓总市值：¥{total_value:,.0f}")
    lines.append(f"- 年化总费率成本：¥{total_annual_cost:,.0f}")
    lines.append(f"- 加权平均费率：{avg_rate:.2f}%")
    lines.append(f"- 10年复利侵蚀：约 ¥{total_annual_cost * 10 * 1.5:,.0f}（假设年化收益8%复利）\n")

    lines.append("## 各基金费率明细\n")
    lines.append("| 基金 | 市值 | 管理费 | 托管费 | 销售服务费 | 年化总费率 | 年成本 |")
    lines.append("|------|------|--------|--------|------------|-----------|--------|")
    for d in fund_details:
        lines.append(f"| {d['name'][:8]} | ¥{d['value']:,.0f} | {d['mgmt']:.2f}% | {d['custody']:.2f}% | {d['sales']:.2f}% | {d['rate']:.2f}% | ¥{d['cost']:,.0f} |")

    high_fee_funds = [d for d in fund_details if d["rate"] >= 1.0]
    if high_fee_funds:
        lines.append("\n## ⚠️ 高费率警告\n")
        for d in high_fee_funds:
            lines.append(f"- **{d['name']}** 费率 {d['rate']:.2f}%，年成本 ¥{d['cost']:,.0f}")
            lines.append(f"  10年复利侵蚀约 ¥{d['cost'] * 15:,.0f}")

    low_fee_funds = [d for d in fund_details if d["rate"] <= 0.3]
    if low_fee_funds:
        lines.append("\n## ✅ 低费率标杆\n")
        for d in low_fee_funds:
            lines.append(f"- {d['name']} 费率仅 {d['rate']:.2f}%")

    if high_fee_funds:
        savings = sum(d["cost"] for d in high_fee_funds) * 0.7
        lines.append("\n## 💰 节省潜力\n")
        lines.append("若将高费率基金（≥1%）替换为同类低费率指数基金：")
        lines.append(f"- 年省约 ¥{savings:,.0f}")
        lines.append(f"- 10年累计省 ¥{savings * 15:,.0f}（复利效应）")

    return "\n".join(lines)


async def _run_fee_analysis_async(task_id: int, holdings: list):
    """后台费率分析"""
    try:
        update_async_task(task_id, status="running", progress={"pct": 10, "stage": "正在获取费率数据..."})

        fee_data = {}
        total = len(holdings)
        for i, h in enumerate(holdings):
            code = h.get("fund_code", "")
            if code:
                fee_data[code] = _get_fund_fees(code)
                progress = 10 + int(80 * (i + 1) / total)
                update_async_task(task_id, status="running", progress={"pct": progress, "stage": f"已获取 {i+1}/{total} 只基金费率"})

        update_async_task(task_id, status="running", progress={"pct": 90, "stage": "正在生成分析报告..."})

        text = _format_fee_analysis_text(holdings, fee_data)

        try:
            llm_prompt = f"""你是基金费率专家。基于以下费率分析报告，给出：
1. 3条最优先的降费建议（具体到基金名称和建议操作）
2. 费率优化后的预期年节省金额
3. 注意事项（赎回费、持有期等）

报告：
{text[:6000]}"""

            llm_result = await asyncio.to_thread(lambda: _call_llm(
                model=MODEL,
                messages=[{"role": "user", "content": llm_prompt}],
                temperature=0.3,
                max_tokens=get_config_int("llm.max_tokens_analysis", 8000),
            ))
            llm_text = llm_result.get("content", "") if isinstance(llm_result, dict) else str(llm_result)
        except Exception as e:
            logger.warning(f"[fee] LLM总结失败: {e}")
            llm_text = ""

        final_text = text
        if llm_text:
            final_text += f"\n\n---\n## 🤖 AI 费率优化建议\n\n{llm_text}"

        save_analysis_cache("fee_analysis_default", {
            "text": final_text,
            "fee_data": fee_data,
            "generated_at": datetime.now().isoformat(),
        })

        update_async_task(task_id, status="done", result={"text": final_text})

    except Exception as e:
        logger.error(f"[fee] 费率分析失败: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))
        raise


@router.post("/fee")
async def trigger_fee_analysis(user_id: str = "default"):
    """触发费率拖累分析"""
    holdings = list_holdings(user_id)
    active = [h for h in holdings if (h.get("shares") or 0) > 0 and (h.get("current_value") or 0) > 0]
    if not active:
        return {"status": "error", "message": "无有效持仓"}

    task_id = create_async_task("fee_analysis", user_id)

    import asyncio
    task = asyncio.create_task(_run_fee_analysis_async(task_id, active))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "ok", "task_id": task_id, "message": "费率分析已启动"}


@router.get("/fee/records")
async def list_fee_records(user_id: str = "default", limit: int = 10):
    """获取费率分析历史"""
    cache = get_analysis_cache("fee_analysis_default")
    return {"status": "ok", "records": [cache] if cache else []}
