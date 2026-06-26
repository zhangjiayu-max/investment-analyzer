"""真实分散度分析 — 基金相关性矩阵"""
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter

from db import list_holdings, get_config, get_config_int, create_async_task, update_async_task
from db.portfolio import save_analysis_cache, get_analysis_cache
from llm_service import _call_llm, call_llm_async, MODEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio/analysis", tags=["analysis-correlation"])

_background_tasks: set = set()


def _get_fund_nav_series(fund_code: str, lookback_days: int = 252) -> list[tuple]:
    """获取基金净值序列，返回 [(date, nav), ...]"""
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df is None or df.empty:
            return []
        df = df.tail(lookback_days)
        result = []
        for _, row in df.iterrows():
            date_str = str(row.get("净值日期", ""))
            nav = float(row.get("单位净值", 0))
            if nav > 0:
                result.append((date_str, nav))
        return result
    except Exception as e:
        logger.warning(f"[corr] 获取{fund_code}净值失败: {e}")
        return []


def _calc_daily_returns(nav_series: list[tuple]) -> list[float]:
    """计算日收益率序列"""
    if len(nav_series) < 2:
        return []
    returns = []
    for i in range(1, len(nav_series)):
        prev = nav_series[i - 1][1]
        curr = nav_series[i][1]
        if prev > 0:
            returns.append((curr - prev) / prev)
    return returns


def _pearson_correlation(x: list[float], y: list[float]) -> float:
    """计算皮尔逊相关系数"""
    n = min(len(x), len(y))
    if n < 10:
        return 0.0
    x, y = x[:n], y[:n]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / n
    std_x = (sum((xi - mean_x) ** 2 for xi in x) / n) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y) / n) ** 0.5
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)


def _build_correlation_matrix(holdings: list, lookback_days: int = 252) -> dict:
    """构建相关性矩阵（日期对齐版本）"""
    nav_data = {}
    fund_info = {}
    for h in holdings:
        code = h.get("fund_code", "")
        name = h.get("fund_name", "")
        value = h.get("current_value", 0) or 0
        if code and value > 0:
            series = _get_fund_nav_series(code, lookback_days)
            if len(series) >= 30:
                nav_data[code] = series
                fund_info[code] = {"name": name, "value": value}

    if len(nav_data) < 2:
        return {"error": "有效基金不足2只，无法计算相关性"}

    # 日期对齐：构建 date->nav 映射
    date_nav_maps = {}
    for code, series in nav_data.items():
        date_nav_maps[code] = {d[0]: d[1] for d in series}

    # 取所有基金日期的交集
    all_date_sets = [set(m.keys()) for m in date_nav_maps.values()]
    common_dates = sorted(set.intersection(*all_date_sets))

    if len(common_dates) < 30:
        # 交集太少，放宽到 80% 覆盖
        all_dates = sorted(set.union(*all_date_sets))
        min_coverage = max(1, len(nav_data) * 0.8)
        common_dates = []
        for d in all_dates:
            covered = sum(1 for m in date_nav_maps.values() if d in m)
            if covered >= min_coverage:
                common_dates.append(d)

    if len(common_dates) < 30:
        return {"error": "日期重叠不足，无法计算相关性"}

    # 按对齐日期计算收益率
    returns = {}
    for code, m in date_nav_maps.items():
        aligned_navs = [m[d] for d in common_dates if d in m]
        returns[code] = _calc_daily_returns([(common_dates[i], aligned_navs[i]) for i in range(len(aligned_navs))])

    codes = sorted(nav_data.keys())
    matrix = {}
    high_corr_pairs = []
    for i in range(len(codes)):
        for j in range(i, len(codes)):
            ci, cj = codes[i], codes[j]
            if ci == cj:
                corr = 1.0
            else:
                corr = _pearson_correlation(returns[ci], returns[cj])
            matrix[(ci, cj)] = corr
            matrix[(cj, ci)] = corr
            if ci != cj and corr > 0.85:
                high_corr_pairs.append({
                    "fund_a": {"code": ci, "name": fund_info[ci]["name"]},
                    "fund_b": {"code": cj, "name": fund_info[cj]["name"]},
                    "correlation": round(corr, 3),
                })

    high_corr_pairs.sort(key=lambda x: x["correlation"], reverse=True)

    total_value = sum(fund_info[c]["value"] for c in codes)
    weights = {c: fund_info[c]["value"] / total_value for c in codes}
    effective_n = 1.0 / sum(w ** 2 for w in weights.values())

    category_groups = {}
    for h in holdings:
        code = h.get("fund_code", "")
        cat = h.get("fund_category", "未知") or "未知"
        if code in fund_info:
            if cat not in category_groups:
                category_groups[cat] = {"count": 0, "value": 0}
            category_groups[cat]["count"] += 1
            category_groups[cat]["value"] += fund_info[code]["value"]

    return {
        "fund_count": len(codes),
        "effective_n": round(effective_n, 2),
        "total_value": total_value,
        "high_corr_pairs": high_corr_pairs,
        "matrix": {f"{ci}_{cj}": round(matrix[(ci, cj)], 3) for ci in codes for cj in codes},
        "fund_info": fund_info,
        "category_groups": category_groups,
        "lookback_days": lookback_days,
        "data_start": min(min(d[0] for d in v) for v in nav_data.values()),
        "data_end": max(max(d[0] for d in v) for v in nav_data.values()),
    }


def _format_correlation_text(result: dict) -> str:
    """格式化相关性分析报告"""
    if "error" in result:
        return f"分析失败：{result['error']}"

    lines = []
    lines.append("# 📊 持仓真实分散度报告\n")
    lines.append(f"分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"数据区间：{result['data_start']} ~ {result['data_end']}（{result['lookback_days']}个交易日）\n")

    fund_count = result["fund_count"]
    effective_n = result["effective_n"]
    real_ratio = effective_n / fund_count * 100 if fund_count > 0 else 0

    lines.append("## 核心指标\n")
    lines.append(f"- 持仓数量：{fund_count} 只基金")
    lines.append(f"- **有效持仓数（Effective N）：{effective_n}**")
    lines.append(f"- 真实分散效率：{real_ratio:.0f}%（每只基金的独立贡献）")

    if effective_n < fund_count * 0.5:
        lines.append("- ⚠️ **警告：有效持仓数不足名义持仓的一半，存在大量重叠！**\n")
    elif effective_n < fund_count * 0.7:
        lines.append("- ⚠️ 注意：分散效率偏低，部分基金高度相关\n")
    else:
        lines.append("- ✅ 分散度良好\n")

    high_pairs = result["high_corr_pairs"]
    if high_pairs:
        lines.append("## ⚠️ 高相关基金对（相关系数 > 0.85）\n")
        lines.append("这些基金走势几乎一致，持有多只等于重复暴露：\n")
        for p in high_pairs:
            lines.append(f"- **{p['fund_a']['name']}** ↔ **{p['fund_b']['name']}**：相关系数 {p['correlation']}")
        lines.append("")
        lines.append("💡 **建议**：高相关对中保留一只即可，其余可合并或替换为不同风格/行业的基金\n")
    else:
        lines.append("## ✅ 无高相关基金对\n")
        lines.append("所有基金两两相关系数均低于 0.85，分散度良好\n")

    cats = result.get("category_groups", {})
    if cats:
        lines.append("## 资产类别分布\n")
        total = result["total_value"]
        lines.append("| 类别 | 只数 | 市值 | 占比 |")
        lines.append("|------|------|------|------|")
        for cat, info in sorted(cats.items(), key=lambda x: -x[1]["value"]):
            pct = info["value"] / total * 100 if total > 0 else 0
            lines.append(f"| {cat} | {info['count']} | ¥{info['value']:,.0f} | {pct:.1f}% |")
        lines.append("")

    lines.append("## 💡 分散度优化建议\n")
    if high_pairs:
        lines.append(f"1. **合并高相关基金**：{len(high_pairs)} 对高度相关的基金可合并为1只")
        lines.append("2. **增加不同风格**：考虑加入与当前持仓低相关（<0.5）的基金")
        lines.append("3. **跨市场分散**：A股基金之间天然高相关，可加入港股/美股/债券基金")
    else:
        lines.append("1. 当前分散度良好，保持现有结构")
        lines.append("2. 定期复查相关性，市场环境变化可能导致相关性上升")

    return "\n".join(lines)


async def _run_correlation_async(task_id: int, holdings: list, lookback_days: int):
    """后台相关性分析"""
    try:
        update_async_task(task_id, status="running", progress={"pct": 10, "stage": "正在获取基金净值数据..."})

        result = _build_correlation_matrix(holdings, lookback_days)

        update_async_task(task_id, status="running", progress={"pct": 70, "stage": "正在计算相关性矩阵..."})

        text = _format_correlation_text(result)

        try:
            if get_config("llm_cost.page_llm_summary", "false") != "true":
                raise RuntimeError("页面 LLM 总结已关闭")
            llm_prompt = f"""你是基金投资顾问。基于以下持仓相关性分析报告，给出：
1. 最需要合并的2-3对基金（具体名称和原因）
2. 建议新增哪类低相关基金来提升分散度
3. 有效持仓数的优化目标

报告：
{text[:6000]}"""

            llm_result = await asyncio.to_thread(lambda: _call_llm(
                caller="page_summary_correlation",
                model=MODEL,
                messages=[{"role": "user", "content": llm_prompt}],
                temperature=0.3,
                max_tokens=get_config_int("llm.max_tokens_analysis", 8000),
            ))
            llm_text = llm_result.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"[corr] LLM总结失败: {e}")
            llm_text = ""

        final_text = text
        if llm_text:
            final_text += f"\n\n---\n## 🤖 AI 分散度优化建议\n\n{llm_text}"

        # 提取可执行行动
        actions = []
        try:
            from analysis.action_extractor import extract_actions, format_actions_for_response
            actions = format_actions_for_response(extract_actions("correlation", {"high_correlation_pairs": [
                {"fund_a": p.get("fund_a", ""), "fund_b": p.get("fund_b", ""), "correlation": p.get("correlation", 0)}
                for p in result.get("high_corr_pairs", [])
            ]}))
        except Exception as e:
            logger.warning(f"[corr] 行动提取失败: {e}")

        save_analysis_cache("correlation_analysis_default", {
            "text": final_text,
            "effective_n": result.get("effective_n"),
            "high_corr_count": len(result.get("high_corr_pairs", [])),
            "actions": actions,
            "generated_at": datetime.now().isoformat(),
        })

        update_async_task(task_id, status="done", result={"text": final_text})

    except Exception as e:
        logger.error(f"[corr] 相关性分析失败: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))
        raise


@router.post("/correlation")
async def trigger_correlation_analysis(
    user_id: str = "default",
    lookback_days: int = 252,
):
    """触发持仓相关性分析"""
    holdings = list_holdings(user_id)
    active = [h for h in holdings if (h.get("shares") or 0) > 0 and (h.get("current_value") or 0) > 0]
    if len(active) < 2:
        return {"status": "error", "message": "有效持仓不足2只，无法计算相关性"}

    task_id = create_async_task("correlation_analysis", user_id)

    import asyncio
    task = asyncio.create_task(_run_correlation_async(task_id, active, lookback_days))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "ok", "task_id": task_id, "message": "相关性分析已启动"}


@router.get("/correlation/records")
async def list_correlation_records(user_id: str = "default", limit: int = 10):
    """获取相关性分析历史"""
    cache = get_analysis_cache("correlation_analysis_default")
    return {"status": "ok", "records": [cache] if cache else []}
