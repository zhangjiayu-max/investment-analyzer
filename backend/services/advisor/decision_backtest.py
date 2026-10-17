"""投资决策回测 + 信号权重反哺 — decision_backtest。

三模块联动优化 P0-A3（2026-08-01）：见 doc/plans/2026-08-01-三模块联动优化.md

定位：系统"越来越准"的核心引擎。统一决策账本（investment_decisions）中到期的
决策，自动计算其相对基准（沪深300）的超额收益，判定命中/未命中并归因入库；
命中率反哺机会雷达的主题信号权重——连续失误的主题降权、表现好的主题恢复权重，
形成「决策 → 执行 → 回测 → 归因 → 权重调整」的自我进化闭环。

设计原则：
- 判定为纯函数（可单测）：judge_hit
- 命中阈值走 system_config（decision_ledger.hit_excess_pct / hit_abs_pct，金融严谨性）
- 历史标的或同区间基准缺失时保留待复盘，避免错误学习
- 权重反哺有上下限（降权 min 0.5、升权 max 1.5），避免极端值
- 优雅降级：无入场价/无现价 → 跳过该决策，标记 skipped
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 纯函数层（可单测）
# ════════════════════════════════════════════════════════════════

def judge_hit(return_pct: float, benchmark_pct: float | None,
              hit_excess_pct: float, hit_abs_pct: float) -> tuple[bool, float | None]:
    """判定决策是否命中。

    命中规则（金融严谨性：阈值可配置）：
    - 有基准：超额收益（return - benchmark）≥ hit_excess_pct 算命中
    - 无基准：绝对收益 return ≥ hit_abs_pct 算命中（兜底）

    Returns:
        (is_hit, excess_pct)  excess_pct 为 None 表示无基准
    """
    if benchmark_pct is None:
        return (return_pct >= hit_abs_pct, None)
    excess = return_pct - benchmark_pct
    return (excess >= hit_excess_pct, round(excess, 2))


def adjust_theme_weight(theme: str, is_hit: bool) -> float | None:
    """根据回测命中结果反哺机会雷达主题信号权重。

    未命中 → 权重 ×0.8（下限 0.5）；命中 → 权重 ×1.1（上限 1.5）。
    权重写入 system_config 的 opportunity.weight_adjust_theme_{theme}，
    机会雷达评分时会读取该权重（_score_theme 的主题权重维度）。

    Returns:
        调整后的权重值（异常/无主题返回 None）
    """
    if not theme:
        return None
    key = f"opportunity.weight_adjust_theme_{theme}"
    try:
        from db.config import get_config_float, update_config
        current = get_config_float(key, 1.0)
        if is_hit:
            new_weight = min(1.5, round(current * 1.1, 4))
        else:
            new_weight = max(0.5, round(current * 0.8, 4))
        update_config(key, str(new_weight))
        logger.info(f"[decision_backtest] 主题「{theme}」权重反哺: {current} → {new_weight}（{'命中' if is_hit else '未命中'}）")
        return new_weight
    except Exception as e:
        logger.warning(f"[decision_backtest] 权重反哺失败 {theme}: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# 基准与数据获取
# ════════════════════════════════════════════════════════════════

def _resolve_review_sample(decision: dict, as_of_date: str) -> dict | None:
    """同一起止日期的历史样本；无数据或成交日期不明确时保持待复盘。"""
    import math
    from db._conn import _get_conn
    snapshot = decision.get('signal_snapshot') or {}
    actual_price = decision.get('actual_price')
    if actual_price:
        entry_date = snapshot.get('actual_price_date') or snapshot.get('actual_trade_date')
        entry = actual_price
    else:
        entry_date = snapshot.get('entry_date') or snapshot.get('price_date') or decision.get('created_at')
        entry = snapshot.get('entry_price')
    if not entry_date or not decision.get('fund_code'):
        return None
    entry_date = str(entry_date)[:10]
    as_of_date = str(as_of_date)[:10]
    if entry_date >= as_of_date:
        return None
    conn = _get_conn()
    try:
        start = conn.execute("""SELECT nav,nav_date FROM fund_nav_history
            WHERE fund_code=? AND nav_date<=? ORDER BY nav_date DESC LIMIT 1""",
            (decision['fund_code'], entry_date)).fetchone()
        end = conn.execute("""SELECT nav,nav_date FROM fund_nav_history
            WHERE fund_code=? AND nav_date<=? AND nav_date>? ORDER BY nav_date DESC LIMIT 1""",
            (decision['fund_code'], as_of_date, entry_date)).fetchone()
        if not end:
            return None
        if not entry:
            if not start:
                return None
            entry, entry_date = start['nav'], start['nav_date']
        from db.config import get_config
        benchmark = get_config('index.hs300_code', '000300.SH')
        prices = conn.execute("""SELECT trade_date,close FROM index_price_history
            WHERE index_code=? AND trade_date IN (?,?)""",
            (benchmark, entry_date, end['nav_date'])).fetchall()
        benchmark_prices = {r['trade_date']: r['close'] for r in prices}
        baseline = benchmark_prices.get(entry_date)
        terminal = benchmark_prices.get(end['nav_date'])
        if not all(v is not None and math.isfinite(float(v)) and float(v)>0
                   for v in (entry, end['nav'], baseline, terminal)):
            return None
        return {'entry_price':float(entry), 'entry_date':entry_date,
                'current_price':float(end['nav']), 'price_date':end['nav_date'],
                'benchmark_pct':round((terminal/baseline-1)*100,2),
                'benchmark_code':benchmark,
                'sample_kind':'executed' if actual_price else 'suggestion'}
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# 回测主流程（配置驱动）
# ════════════════════════════════════════════════════════════════

def run_decision_backtest(user_id: str = "default", as_of_date: str | None = None) -> dict:
    """对到期决策执行回测：计算超额收益 → 判定命中 → 归因入库 → 反哺权重。

    Returns:
        {processed, hits, misses, skipped, weight_changes:[{theme, weight, is_hit}],
         details:[{decision_id, fund_code, return_pct, benchmark_pct, excess_pct, is_hit}]}
    """
    summary = {"processed": 0, "hits": 0, "misses": 0, "skipped": 0,
               "weight_changes": [], "details": []}
    try:
        from db.config import get_config_bool, get_config_float, get_config_int
        if not get_config_bool("decision_ledger.auto_backtest_enabled", True):
            summary["disabled"] = True
            return summary
        hit_excess = get_config_float("decision_ledger.hit_excess_pct", 2.0)
        hit_abs = get_config_float("decision_ledger.hit_abs_pct", 3.0)
    except Exception as e:
        logger.warning(f"[decision_backtest] 配置读取失败: {e}")
        return summary

    try:
        from db.investment_decisions import get_decisions_due_for_review, record_review
    except Exception as e:
        logger.warning(f"[decision_backtest] 账本依赖加载失败: {e}")
        return summary

    if as_of_date is None:
        as_of_date = datetime.now().strftime("%Y-%m-%d")

    due = get_decisions_due_for_review(user_id, as_of_date)

    for dec in due:
        sample = _resolve_review_sample(dec, as_of_date)
        if sample is None:
            summary["skipped"] += 1
            summary["details"].append({
                "decision_id": dec["id"], "fund_code": dec.get("fund_code"),
                "status": "skipped", "reason": "缺少同区间历史价格、基准或实际成交日期",
            })
            continue

        entry, current = sample['entry_price'], sample['current_price']
        benchmark_pct = sample['benchmark_pct']
        return_pct = round((current - entry) / entry * 100, 2)
        is_hit, excess_pct = judge_hit(return_pct, benchmark_pct, hit_excess, hit_abs)

        record_review(
            dec["id"], is_hit=is_hit, excess_return_pct=excess_pct,
            review_result={
                **sample,
                "return_pct": return_pct,
                "benchmark_pct": benchmark_pct,

                "entry_price": entry,
                "current_price": current,
                "as_of_date": as_of_date,
                "hit_rule": "excess" if benchmark_pct is not None else "absolute",
            },
        )
        summary["processed"] += 1
        if is_hit:
            summary["hits"] += 1
        else:
            summary["misses"] += 1

        detail = {
            "decision_id": dec["id"], "fund_code": dec.get("fund_code"),
            "return_pct": return_pct, "benchmark_pct": benchmark_pct,
            "excess_pct": excess_pct, "is_hit": is_hit,
        }

        # 机会雷达来源的决策 → 反哺主题信号权重（"越来越准"的关键）
        theme = dec.get("theme")
        if theme and dec.get("source_module") == "opportunity_radar":
            new_weight = adjust_theme_weight(theme, is_hit)
            if new_weight is not None:
                summary["weight_changes"].append({"theme": theme, "weight": new_weight, "is_hit": is_hit})
                detail["theme_weight"] = new_weight

        summary["details"].append(detail)

    logger.info(
        f"[decision_backtest] 回测完成: 处理{summary['processed']} 命中{summary['hits']} "
        f"未命中{summary['misses']} 跳过{summary['skipped']} 权重调整{len(summary['weight_changes'])}"
    )
    return summary
