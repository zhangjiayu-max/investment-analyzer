"""每日持仓提示引擎 — 基于真实持仓生成加减仓信号。

核心规则：
- 累计跌幅触发（不是连续下跌天数），中间涨跌不影响
- 4%定投法：跌破最近买入价4%触发，分3档
- 估值过滤：低估才加仓，高估才减仓
- 仓位/现金约束：超限降级为 blocked
- 主题基金更严格
"""

import json
import logging
from datetime import datetime, timedelta
from db import (
    list_holdings, get_cash_balance, get_portfolio_summary,
    get_latest_valuation, list_portfolio_snapshots,
)
from db._conn import _get_conn, _row_to_dict
from db.daily_advice import (
    create_run, update_run, get_today_run,
    create_signal, list_today_signals, update_signal_status,
    expire_old_signals,
)
from db.config import get_config, get_config_float, get_config_int
from db.decisions import create_candidate_from_structured_recommendation

logger = logging.getLogger(__name__)


def run_daily_position_advice(user_id: str = "default", trigger_type: str = "manual", force: bool = False) -> dict:
    """运行每日持仓提示引擎。"""
    today = datetime.now().strftime("%Y-%m-%d")

    # 检查是否已运行
    if not force:
        existing = get_today_run(user_id, today)
        if existing and existing.get("status") == "completed":
            signals = list_today_signals(user_id, today)
            return {
                "run_id": existing["id"],
                "run_date": today,
                "summary": existing.get("summary", ""),
                "stats": json.loads(existing.get("stats_json", "{}")),
                "signals": signals,
                "skipped": True,
            }

    run_id = create_run(user_id, today, trigger_type)

    # force 模式下先删除今天的旧信号（包括 expired），以便重新生成
    if force:
        conn = _get_conn()
        try:
            conn.execute(
                "DELETE FROM daily_position_signals WHERE user_id=? AND signal_date=?",
                (user_id, today)
            )
            conn.commit()
        finally:
            conn.close()

    try:
        holdings = list_holdings(user_id)
        active = [h for h in holdings if (h.get("shares") or 0) > 0]

        if not active:
            update_run(run_id, "completed", "无持仓，跳过", {"total": 0})
            return {"run_id": run_id, "run_date": today, "summary": "无持仓", "stats": {"total": 0}, "signals": []}

        # 获取组合级数据
        cash_info = get_cash_balance(user_id)
        cash_balance = cash_info.get("balance", 0) if cash_info else 0
        summary = get_portfolio_summary(user_id)
        total_assets = summary.get("total_assets", 0) or sum(h.get("current_value", 0) or 0 for h in active) + cash_balance

        # 获取配置
        cfg = _load_config()

        # 获取近90天交易
        recent_txs = _get_recent_transactions(user_id, days=90)

        # 获取未完成决策
        pending_decisions = _get_pending_decisions(user_id)

        signals = []
        stats = {"total": 0, "actionable": 0, "watch": 0, "info": 0, "blocked": 0}

        for h in active:
            fund_code = h.get("fund_code", "")
            fund_name = h.get("fund_name", fund_code)
            current_price = h.get("current_price") or 0
            cost_price = h.get("cost_price") or 0
            current_value = h.get("current_value") or 0
            position_pct = (current_value / total_assets * 100) if total_assets > 0 else 0
            cash_pct = (cash_balance / total_assets * 100) if total_assets > 0 else 0
            profit_rate = h.get("profit_rate", 0) or 0

            # 数据新鲜度检查
            price_updated = h.get("price_updated_at", "")
            is_stale = _is_data_stale(price_updated, cfg["stale_days"])

            # 最近买入价
            last_buy_price = _get_last_buy_price(user_id, fund_code, recent_txs)
            ref_price = last_buy_price or cost_price

            # 估值数据
            val_info = _get_fund_valuation(h)

            # 基金类型判定
            is_theme = _is_theme_fund(fund_name, h.get("fund_category", ""))

            # 累计跌幅（从最近买入价/成本价到现在）
            drop_pct = 0
            if ref_price and ref_price > 0 and current_price and current_price > 0:
                drop_pct = (ref_price - current_price) / ref_price

            # 近期买入次数
            recent_buy_count = _count_recent_buys(fund_code, recent_txs, cfg["recent_buy_cooldown_days"])

            # 生成信号
            fund_signals = _generate_signals(
                fund_code=fund_code,
                fund_name=fund_name,
                current_price=current_price,
                ref_price=ref_price,
                cost_price=cost_price,
                drop_pct=drop_pct,
                profit_rate=profit_rate,
                position_pct=position_pct,
                cash_pct=cash_pct,
                cash_balance=cash_balance,
                current_value=current_value,
                val_info=val_info,
                is_theme=is_theme,
                is_stale=is_stale,
                recent_buy_count=recent_buy_count,
                pending_decisions=pending_decisions,
                cfg=cfg,
                user_id=user_id,
                run_id=run_id,
                today=today,
            )

            signals.extend(fund_signals)

        # 统计
        for s in signals:
            stats["total"] += 1
            sev = s.get("severity", "info")
            if sev in stats:
                stats[sev] += 1

        # 写入数据库
        saved_signals = []
        for s in signals:
            signal_id = create_signal(s)
            if signal_id:
                s["id"] = signal_id
                saved_signals.append(s)
            else:
                # dedupe 命中，仍加入返回列表
                saved_signals.append(s)

        # watch 信号写入 portfolio_alerts 轻量提醒
        for s in saved_signals:
            if s.get("severity") == "watch" and s.get("id"):
                _create_portfolio_alert_from_signal(s, user_id)

        # 强建议写入 recommendation_candidates
        for s in saved_signals:
            if s.get("severity") == "actionable" and s.get("next_step") == "create_candidate":
                _create_candidate_from_signal(s, user_id)

        summary_text = _build_summary(saved_signals, stats)
        update_run(run_id, "completed", summary_text, stats)

        return {
            "run_id": run_id,
            "run_date": today,
            "summary": summary_text,
            "stats": stats,
            "signals": saved_signals,
        }

    except Exception as e:
        logger.error(f"每日提示引擎失败: {e}", exc_info=True)
        update_run(run_id, "failed", "", error=str(e))
        return {"run_id": run_id, "run_date": today, "error": str(e), "signals": []}


# ── 信号生成 ────────────────────────────────────────────────

def _generate_signals(**kw) -> list[dict]:
    """为单只持仓生成信号。"""
    fund_code = kw["fund_code"]
    fund_name = kw["fund_name"]
    drop_pct = kw["drop_pct"]
    cfg = kw["cfg"]
    is_stale = kw["is_stale"]
    val_info = kw["val_info"]
    position_pct = kw["position_pct"]
    cash_pct = kw["cash_pct"]
    is_theme = kw["is_theme"]
    recent_buy_count = kw["recent_buy_count"]
    pending_decisions = kw["pending_decisions"]
    user_id = kw["user_id"]
    run_id = kw["run_id"]
    today = kw["today"]

    signals = []

    # 主题基金更严格的阈值
    # 设计稿：单标的上限 15%→8%，加仓金额为宽基 50%，加仓估值 35%→25%，减仓估值 80%→70%
    if is_theme:
        add_val_max = min(cfg["add_valuation_max_percentile"] * 0.714, 25)  # 35*0.714≈25
        single_pos_max = min(cfg["default_single_position_pct"] * 0.533, 8)  # 15*0.533≈8
        max_dca_amount = cfg["base_dca_amount"] * 0.5
        reduce_val_min = min(cfg["reduce_valuation_min_percentile"] * 0.875, 70)  # 80*0.875=70
    else:
        add_val_max = cfg["add_valuation_max_percentile"]
        single_pos_max = cfg["default_single_position_pct"]
        max_dca_amount = cfg["base_dca_amount"]
        reduce_val_min = cfg["reduce_valuation_min_percentile"]

    val_percentile = val_info.get("percentile") if val_info else None
    val_name = val_info.get("index_name", "") if val_info else ""

    # 数据过期 → 只提示刷新
    if is_stale:
        signals.append(_make_signal(
            run_id=run_id, user_id=user_id, signal_date=today,
            signal_type="stale_data", action_type="refresh",
            target_code=fund_code, target_name=fund_name,
            severity="info", score=0,
            summary=f"{fund_name} 净值数据已过期，建议先刷新",
            rationale="数据过期时无法准确判断跌幅和估值，不生成金额建议。",
            evidence={"last_update": kw.get("current_price", 0), "stale_days": cfg["stale_days"]},
            risks={"notes": ["数据过期时不生成买卖金额"]},
            dedupe_key=f"daily:{today}:stale_data:fund:{fund_code}",
        ))
        return signals

    # 有未确认交易 → blocked
    has_pending_tx = fund_code in pending_decisions.get("pending_tx_funds", set())
    if has_pending_tx:
        signals.append(_make_signal(
            run_id=run_id, user_id=user_id, signal_date=today,
            signal_type="pending_tx", action_type="hold",
            target_code=fund_code, target_name=fund_name,
            severity="blocked", score=0,
            summary=f"{fund_name} 有未确认交易，暂不生成新建议",
            rationale="已有待确认交易时避免重复操作。",
            evidence={"fund_code": fund_code},
            risks={"notes": ["等待交易确认后再评估"]},
            dedupe_key=f"daily:{today}:pending_tx:fund:{fund_code}",
        ))
        return signals

    # ── 4% 定投法（累计跌幅触发）──
    if drop_pct >= cfg["dca_drop_step_pct"] / 100:
        # 计算档位
        step = int(drop_pct / (cfg["dca_drop_step_pct"] / 100))
        step = min(step, cfg["max_dca_steps"])

        # 金额
        base = min(cfg["base_dca_amount"], max_dca_amount)
        amount_multiplier = [1.0, 1.5, 2.0][min(step - 1, 2)]
        suggested_amount = base * amount_multiplier

        # 现金约束：单条建议最多用可用现金的 max_cash_use_pct_per_signal%
        _cash = kw.get("cash_balance", 0)
        max_cash_use = _cash * cfg["max_cash_use_pct_per_signal"] / 100
        if suggested_amount > max_cash_use:
            suggested_amount = max_cash_use

        # 评分
        score = _calc_signal_score(drop_pct * 100, val_percentile, position_pct, cash_pct)

        # 风险降级
        severity = "actionable" if score >= 60 else "watch"
        risk_notes = []

        # 估值过高 → 降级
        if val_percentile is not None and val_percentile > (reduce_val_min * 0.875):
            severity = "watch"
            risk_notes.append("估值偏高，价格跌但估值不便宜")

        # 仓位超限 → blocked
        if position_pct > single_pos_max:
            severity = "blocked"
            risk_notes.append(f"仓位 {position_pct:.1f}% 已超过上限 {single_pos_max:.0f}%")

        # 现金不足 → blocked
        if cash_pct < cfg["min_cash_pct"]:
            severity = "blocked"
            risk_notes.append(f"现金占比 {cash_pct:.1f}% 低于最低保留 {cfg['min_cash_pct']}%")

        # 补仓过密 → 降级
        if recent_buy_count >= cfg["recent_buy_max_count"]:
            severity = "watch"
            risk_notes.append(f"近 {cfg['recent_buy_cooldown_days']} 天已买入 {recent_buy_count} 次，避免过密补仓")

        # 估值缺失 → 最多 watch
        if val_percentile is None:
            if severity == "actionable":
                severity = "watch"
            risk_notes.append("估值数据缺失，无法确认低估")

        next_step = "create_candidate" if severity == "actionable" else "none"

        signals.append(_make_signal(
            run_id=run_id, user_id=user_id, signal_date=today,
            signal_type="dca_add", action_type="dca",
            target_code=fund_code, target_name=fund_name,
            severity=severity, score=score,
            suggested_amount=round(suggested_amount, 2) if suggested_amount else None,
            suggested_ratio=None,
            summary=_dca_summary(fund_name, drop_pct * 100, step, suggested_amount, val_percentile),
            rationale=f"累计跌幅 {drop_pct*100:.1f}%，触发{step}档定投。参考价 {kw['ref_price']:.4f}，当前 {kw['current_price']:.4f}。",
            evidence={
                "drop_pct": round(drop_pct * 100, 2),
                "ref_price": kw["ref_price"],
                "current_price": kw["current_price"],
                "step": step,
                "valuation_percentile": val_percentile,
                "position_pct": round(position_pct, 1),
                "cash_pct": round(cash_pct, 1),
                "recent_buy_count": recent_buy_count,
                "is_theme": is_theme,
            },
            risks={"notes": risk_notes} if risk_notes else {},
            source_snapshot={
                "cost_price": kw["cost_price"],
                "current_value": kw["current_value"],
                "profit_rate": kw["profit_rate"],
            },
            dedupe_key=f"daily:{today}:dca_add:fund:{fund_code}",
            next_step=next_step,
        ))
        return signals

    # ── 减仓信号：估值高 + 盈利 ──
    if val_percentile is not None and val_percentile >= reduce_val_min and kw["profit_rate"] > 0:
        score = 50 + min((val_percentile - reduce_val_min) / (100 - reduce_val_min) * 30, 30)
        severity = "actionable" if score >= 60 else "watch"

        # 仓位超限加大减仓力度
        if position_pct > single_pos_max:
            score += 15
            severity = "actionable"

        reduce_pct = "10%-15%" if position_pct > single_pos_max else "5%-10%"

        signals.append(_make_signal(
            run_id=run_id, user_id=user_id, signal_date=today,
            signal_type="reduce_watch", action_type="reduce",
            target_code=fund_code, target_name=fund_name,
            severity=severity, score=score,
            suggested_amount=None,
            suggested_ratio=0.1,
            summary=f"{fund_name} 估值偏高（{val_percentile:.0f}%分位），盈利 {kw['profit_rate']*100:.1f}%，建议复核减仓 {reduce_pct}",
            rationale=f"估值 {val_percentile:.0f}% 分位属于高估区，仓位 {position_pct:.1f}%。",
            evidence={
                "valuation_percentile": val_percentile,
                "profit_rate": round(kw["profit_rate"] * 100, 1),
                "position_pct": round(position_pct, 1),
            },
            risks={"notes": ["减仓不等于清仓，分批操作", "如果长期目标明确可继续持有"]},
            dedupe_key=f"daily:{today}:reduce_watch:fund:{fund_code}",
            next_step="create_candidate" if severity == "actionable" else "none",
        ))
        return signals

    # ── 估值低估但未触发4%（观察）──
    if val_percentile is not None and val_percentile <= add_val_max and drop_pct < cfg["dca_drop_step_pct"] / 100:
        signals.append(_make_signal(
            run_id=run_id, user_id=user_id, signal_date=today,
            signal_type="valuation_watch", action_type="watch",
            target_code=fund_code, target_name=fund_name,
            severity="watch", score=30,
            summary=f"{fund_name} 估值偏低（{val_percentile:.0f}%分位），但累计跌幅未达 {cfg['dca_drop_step_pct']}%，继续观察",
            rationale=f"估值 {val_percentile:.0f}% 分位处于低估区，但跌幅 {drop_pct*100:.1f}% 未触发定投。",
            evidence={
                "valuation_percentile": val_percentile,
                "drop_pct": round(drop_pct * 100, 2),
            },
            risks={"notes": ["低估不等于立即买入，等待跌幅触发"]},
            dedupe_key=f"daily:{today}:valuation_watch:fund:{fund_code}",
        ))
        return signals

    # ── 默认：持有 ──
    signals.append(_make_signal(
        run_id=run_id, user_id=user_id, signal_date=today,
        signal_type="hold", action_type="hold",
        target_code=fund_code, target_name=fund_name,
        severity="info", score=0,
        summary=f"{fund_name} 继续持有",
        rationale=f"跌幅 {drop_pct*100:.1f}%，估值分位 {val_percentile or 'N/A'}，仓位 {position_pct:.1f}%，无触发条件。",
        evidence={
            "drop_pct": round(drop_pct * 100, 2),
            "valuation_percentile": val_percentile,
            "position_pct": round(position_pct, 1),
        },
        risks={},
        dedupe_key=f"daily:{today}:hold:fund:{fund_code}",
    ))

    return signals


# ── 辅助函数 ────────────────────────────────────────────────

def _load_config() -> dict:
    """从数据库加载配置。"""
    return {
        "enabled": get_config("daily_advice.enabled", "true") == "true",
        "base_dca_amount": get_config_float("daily_advice.base_dca_amount", 500),
        "dca_drop_step_pct": get_config_float("daily_advice.dca_drop_step_pct", 4),
        "max_dca_steps": get_config_int("daily_advice.max_dca_steps", 3),
        "min_cash_pct": get_config_float("daily_advice.min_cash_pct", 5),
        "max_cash_use_pct_per_signal": get_config_float("daily_advice.max_cash_use_pct_per_signal", 10),
        "default_single_position_pct": get_config_float("daily_advice.default_single_position_pct", 15),
        "add_valuation_max_percentile": get_config_float("daily_advice.add_valuation_max_percentile", 35),
        "reduce_valuation_min_percentile": get_config_float("daily_advice.reduce_valuation_min_percentile", 80),
        "recent_buy_cooldown_days": get_config_int("daily_advice.recent_buy_cooldown_days", 10),
        "recent_buy_max_count": get_config_int("daily_advice.recent_buy_max_count", 2),
        "down_days_watch": get_config_int("daily_advice.down_days_watch", 3),
        "down_days_action": get_config_int("daily_advice.down_days_action", 5),
        "stale_days": get_config_int("daily_advice.stale_days", 3),
    }


def _count_recent_buys(fund_code: str, recent_txs: list, cooldown_days: int) -> int:
    """统计冷静期内某基金的买入次数。"""
    cutoff = (datetime.now() - timedelta(days=cooldown_days)).strftime("%Y-%m-%d")
    count = 0
    for tx in recent_txs:
        if tx.get("fund_code") == fund_code and tx.get("transaction_type") == "buy":
            tx_date = tx.get("transaction_date", "")
            if tx_date >= cutoff:
                count += 1
    return count


def _is_data_stale(price_updated: str, stale_days: int) -> bool:
    """检查净值数据是否过期。"""
    if not price_updated:
        return True
    try:
        updated = datetime.strptime(price_updated[:10], "%Y-%m-%d")
        return (datetime.now() - updated).days > stale_days
    except Exception:
        return True


def _get_last_buy_price(user_id: str, fund_code: str, recent_txs: list) -> float | None:
    """从交易记录获取最近买入价。"""
    for tx in recent_txs:
        if tx.get("fund_code") == fund_code and tx.get("transaction_type") == "buy" and tx.get("status") == "confirmed":
            price = tx.get("price")
            if price and price > 0:
                return price
    return None


def _get_recent_transactions(user_id: str, days: int = 90) -> list[dict]:
    """获取近期交易记录。"""
    conn = _get_conn()
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT * FROM portfolio_transactions WHERE user_id=? AND transaction_date >= ? ORDER BY transaction_date DESC",
            (user_id, cutoff)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _get_pending_decisions(user_id: str) -> dict:
    """获取未完成决策和待确认交易。"""
    conn = _get_conn()
    try:
        # 待确认交易
        pending_tx = conn.execute(
            "SELECT DISTINCT fund_code FROM portfolio_transactions WHERE user_id=? AND status='pending'",
            (user_id,)
        ).fetchall()
        pending_funds = {r["fund_code"] for r in pending_tx if r["fund_code"]}

        # 未复盘决策
        pending_decisions = conn.execute(
            "SELECT target_code FROM decision_records WHERE user_id=? AND status IN ('pending','executing')",
            (user_id,)
        ).fetchall()
        pending_funds.update(r["target_code"] for r in pending_decisions if r["target_code"])

        return {"pending_tx_funds": pending_funds}
    except Exception:
        return {"pending_tx_funds": set()}
    finally:
        conn.close()


def _get_fund_valuation(holding: dict) -> dict | None:
    """获取持仓对应的估值数据。"""
    from db.valuations import get_latest_valuation as _glv

    index_code = holding.get("index_code", "")
    index_name = holding.get("index_name", "")

    # 尝试多种 index_code 格式
    candidates = []
    if index_code:
        candidates.append(index_code)
        # 短码补全
        if "." not in index_code:
            candidates.append(f"{index_code}.SZ")
            candidates.append(f"{index_code}.SH")
            candidates.append(f"{index_code}.CSI")
            candidates.append(f"{index_code}.CNI")

    for code in candidates:
        val = _glv(code)
        if val:
            return val

    # 尝试用基金名称关联指数
    fund_name = holding.get("fund_name", "")
    index_map = {
        "白酒": ["399997", "399997.SZ"],
        "医药": ["399386", "399386.SZ", "931140.CSI"],
        "医疗": ["399386", "399386.SZ"],
        "银行": ["399986", "399986.SZ"],
        "证券": ["399975", "399975.SZ"],
        "军工": ["399967", "399967.SZ"],
        "新能源": ["399808", "399808.SZ"],
        "半导体": ["980017", "980017.SZ"],
        "消费": ["000009", "000009.SH"],
        "沪深300": ["000300", "000300.SH"],
        "中证500": ["000905", "000905.SH"],
        "创业板": ["399006", "399006.SZ"],
        "恒生科技": ["HSTECH"],
        "港股": ["HSI"],
        "房地产": ["399393", "399393.SZ"],
        "畜牧": ["930746", "930746.CSI"],
        "红利": ["000022", "000022.SH"],
        "互联网": ["931605", "931605.CSI"],
    }
    for keyword, codes in index_map.items():
        if keyword in fund_name or keyword in (index_name or ""):
            for code in codes:
                val = _glv(code)
                if val:
                    return val

    return None


def _is_theme_fund(fund_name: str, category: str) -> bool:
    """判断是否为主题/行业基金。
    
    判断条件：基金名称包含"主题"/"行业"/"赛道"/"医药"/"白酒"/"新能源"/"半导体"/"军工"/"消费"等关键词，
    或 QDII/商品类。
    """
    theme_keywords = [
        # 设计稿关键词
        "主题", "行业", "赛道", "医药", "白酒", "新能源", "半导体", "军工", "消费",
        # 扩展关键词
        "医疗", "芯片", "畜牧", "养殖", "生物", "科技", "互联网", "银行", "证券",
        "煤炭", "有色", "钢铁", "新能源车", "光伏", "风电", "机器人", "人工智能",
        "房地产", "红利", "游戏", "传媒", "旅游", "食品",
    ]
    for kw in theme_keywords:
        if kw in fund_name:
            return True
    # QDII / 商品类
    if category:
        cat_lower = category.lower() if isinstance(category, str) else ""
        if cat_lower in ("qdii", "commodity", "qdii_commodity"):
            return True
    # 名称中直接包含 QDII 或商品
    if "QDII" in fund_name or "商品" in fund_name:
        return True
    return False


def _calc_signal_score(drop_pct: float, val_percentile: float | None, position_pct: float, cash_pct: float) -> int:
    """信号评分 0-100。"""
    score = 0
    # 跌幅（0-40分）
    score += min(drop_pct / 12 * 40, 40) if drop_pct else 0
    # 估值优势（0-30分）
    if val_percentile is not None and val_percentile <= 35:
        score += max(0, (35 - val_percentile) / 35 * 30)
    # 仓位空间（0-20分）
    score += max(0, (15 - position_pct) / 15 * 20) if position_pct else 10
    # 流动性（0-10分）
    score += min(cash_pct / 15 * 10, 10)
    return min(100, round(score))


def total_assets_ratio(kw: dict) -> float:
    """从信号上下文获取总资产比例因子。"""
    # 简化：直接用 position_pct + cash_pct 作为近似
    return 1.0


def _dca_summary(fund_name: str, drop_pct: float, step: int, amount: float, val_pct: float | None) -> str:
    """生成定投摘要。"""
    val_text = f"，估值 {val_pct:.0f}% 分位" if val_pct is not None else ""
    return f"{fund_name} 累计跌幅 {drop_pct:.1f}%，触发{step}档定投，建议加仓 ¥{amount:.0f}{val_text}"


def _make_signal(**kw) -> dict:
    """构建信号字典。"""
    return {
        "run_id": kw["run_id"],
        "user_id": kw["user_id"],
        "signal_date": kw["signal_date"],
        "signal_type": kw["signal_type"],
        "action_type": kw["action_type"],
        "target_code": kw["target_code"],
        "target_name": kw["target_name"],
        "severity": kw["severity"],
        "score": kw["score"],
        "suggested_amount": kw.get("suggested_amount"),
        "suggested_ratio": kw.get("suggested_ratio"),
        "summary": kw["summary"],
        "rationale": kw["rationale"],
        "evidence": kw.get("evidence", {}),
        "risks": kw.get("risks", {}),
        "source_snapshot": kw.get("source_snapshot", {}),
        "dedupe_key": kw["dedupe_key"],
        "next_step": kw.get("next_step", "none"),
    }


def _create_candidate_from_signal(signal: dict, user_id: str):
    """将 actionable 信号写入建议候选。"""
    try:
        candidate_id = create_candidate_from_structured_recommendation({
            "source_type": "daily_advice",
            "source_id": str(signal.get("run_id")),
            "scenario_type": signal["signal_type"],
            "action_type": signal["action_type"],
            "target_type": "fund",
            "target_code": signal["target_code"],
            "target_name": signal["target_name"],
            "summary": signal["summary"],
            "rationale": signal["rationale"],
            "suggested_amount": signal.get("suggested_amount"),
            "confidence": "medium" if signal["score"] >= 70 else "low",
            "evidence": signal.get("evidence", {}),
            "risk": signal.get("risks", {}),
            "source_snapshot": signal.get("source_snapshot", {}),
            "dedupe_key": signal["dedupe_key"],
            "priority": 2,
            "status": "new",
        }, user_id=user_id)
        return candidate_id
    except Exception as e:
        logger.warning(f"创建建议候选失败: {e}")
        return None


def _create_portfolio_alert_from_signal(signal: dict, user_id: str):
    """将 watch 信号写入 portfolio_alerts 轻量提醒。"""
    try:
        from db.portfolio import create_alert
        alert_id = create_alert(
            alert_type="daily_advice_signal",
            title=signal.get("summary", "每日持仓提示"),
            content=signal.get("rationale", ""),
            severity="info",
            related_fund_code=signal.get("target_code", ""),
            related_fund_name=signal.get("target_name", ""),
            source="daily_advice",
            user_id=user_id,
        )
        # 回写 alert_id 到信号记录
        if alert_id and signal.get("id"):
            conn = _get_conn()
            try:
                conn.execute(
                    "UPDATE daily_position_signals SET alert_id=? WHERE id=?",
                    (alert_id, signal["id"])
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(f"已为信号 {signal.get('id')} 创建 portfolio_alert {alert_id}")
        return alert_id
    except Exception as e:
        logger.warning(f"创建 portfolio_alert 失败: {e}")
        return None


def _build_summary(signals: list[dict], stats: dict) -> str:
    """构建运行摘要。"""
    actionable = stats.get("actionable", 0)
    watch = stats.get("watch", 0)
    blocked = stats.get("blocked", 0)
    info = stats.get("info", 0)
    total = stats.get("total", 0)

    if total == 0:
        return "今日无持仓提示"

    parts = [f"今日生成 {total} 条提示"]
    if actionable:
        parts.append(f"可行动 {actionable} 条")
    if watch:
        parts.append(f"观察 {watch} 条")
    if blocked:
        parts.append(f"风险拦截 {blocked} 条")
    if info:
        parts.append(f"持有 {info} 条")

    return "，".join(parts) + "。"
