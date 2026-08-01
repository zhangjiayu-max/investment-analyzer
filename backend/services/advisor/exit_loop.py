"""补仓-止盈闭环状态机 — exit_loop。

三模块联动优化 P0-S2（2026-08-01）：见 doc/plans/2026-08-01-三模块联动优化.md

定位：让补仓"有始有终"。补仓决策一旦确认，即生成分批止盈计划（回本路径 +
分批减仓档位），写入统一决策账本（investment_decisions.exit_plan）；后续扫描
按状态机跟踪价格，触发回本/止盈提醒并更新状态，避免"补完就忘、坐过山车"。

设计原则：
- 核心为纯函数状态机（可单测）：build_exit_plan / evaluate_exit
- 止盈阈值/比例/复盘周期全部走 system_config（smartadd.exitloop.*，金融严谨性）
- 状态机防重：exit_state 记录已提醒批次，叠加 create_alert 24h 去重，避免刷屏
- 优雅降级：无持仓/无价格/无计划则跳过，不阻断
- 所有提醒附带数据来源与风险提示（合规）
"""
import logging

logger = logging.getLogger(__name__)

# 状态机事件 → 预警级别
_EVENT_SEVERITY = {"breakeven": "info", "tp1": "warning", "tp2": "warning"}


# ════════════════════════════════════════════════════════════════
# 纯函数层（可单测，无外部依赖）
# ════════════════════════════════════════════════════════════════

def build_exit_plan(cost_price: float, fund_category: str | None = None,
                    shares: float | None = None, buy_date: str | None = None) -> dict:
    """根据成本价生成分批止盈计划（阈值/比例走配置）。

    P1-S5（2026-08-01）：止盈档位扣赎回费。
    - 开关 `opportunity.constraint.exit_fee_aware_enabled`（默认 true）
    - 当 shares + buy_date 提供时，计算 tp1/tp2 价位的扣赎回费后净收益率
    - 净收益 = (tp_price - cost) × shares × (1 - sell_rate) - cost × shares
             = shares × (tp_price - cost) - sell_fee

    Returns:
        {cost_price, breakeven_price, tp_first_pct, tp_first_ratio, tp1_price,
         tp_second_pct, tp_second_ratio, tp2_price, batch_take_profit,
         breakeven_alert, review_days, fund_category,
         tp1_net_profit_pct, tp2_net_profit_pct, sell_fee_basis}
    """
    try:
        from db.config import get_config_float, get_config_int, get_config_bool
        tp1_pct = get_config_float("smartadd.exitloop.tp_first_pct", 15.0)
        tp1_ratio = get_config_float("smartadd.exitloop.tp_first_ratio", 0.33)
        tp2_pct = get_config_float("smartadd.exitloop.tp_second_pct", 30.0)
        tp2_ratio = get_config_float("smartadd.exitloop.tp_second_ratio", 0.33)
        batch = get_config_bool("smartadd.exitloop.batch_take_profit", True)
        be_alert = get_config_bool("smartadd.exitloop.breakeven_alert", True)
        review_days = get_config_int("smartadd.exitloop.review_days", 15)
    except Exception:
        tp1_pct, tp1_ratio, tp2_pct, tp2_ratio = 15.0, 0.33, 30.0, 0.33
        batch, be_alert, review_days = True, True, 15

    cost = float(cost_price or 0)
    plan = {
        "cost_price": round(cost, 4),
        "breakeven_price": round(cost, 4),
        "tp_first_pct": tp1_pct,
        "tp_first_ratio": tp1_ratio,
        "tp1_price": round(cost * (1 + tp1_pct / 100), 4) if cost else None,
        "tp_second_pct": tp2_pct,
        "tp_second_ratio": tp2_ratio,
        "tp2_price": round(cost * (1 + tp2_pct / 100), 4) if cost else None,
        "batch_take_profit": batch,
        "breakeven_alert": be_alert,
        "review_days": review_days,
        "fund_category": fund_category,
    }

    # P1-S5：止盈档位扣赎回费后的净收益率
    tp1_net_pct = None
    tp2_net_pct = None
    sell_fee_basis = ""
    try:
        from db.config import get_config_bool as _gcb
        if (_gcb("opportunity.constraint.exit_fee_aware_enabled", True)
                and shares and shares > 0 and buy_date and cost > 0):
            from services.fee_calculator import calc_sell_fee
            tp1_price = plan["tp1_price"]
            tp2_price = plan["tp2_price"]
            # tp1 档：按 tp1_ratio 比例卖出
            if tp1_price:
                sell_shares_1 = shares * tp1_ratio
                fee1, rate1, basis1 = calc_sell_fee(sell_shares_1, tp1_price, {"buy_date": buy_date})
                gross_profit_1 = (tp1_price - cost) * sell_shares_1
                net_profit_1 = gross_profit_1 - fee1
                # 净收益率相对成本
                tp1_net_pct = round(net_profit_1 / (cost * shares) * 100, 2)
                sell_fee_basis = f"tp1: {basis1}，赎回费¥{fee1:.2f}"
            # tp2 档：累计卖出 tp1+tp2 比例
            if tp2_price:
                sell_shares_2 = shares * (tp1_ratio + tp2_ratio)
                fee2, rate2, basis2 = calc_sell_fee(sell_shares_2, tp2_price, {"buy_date": buy_date})
                gross_profit_2 = (tp2_price - cost) * sell_shares_2
                net_profit_2 = gross_profit_2 - fee2
                tp2_net_pct = round(net_profit_2 / (cost * shares) * 100, 2)
                sell_fee_basis = (sell_fee_basis + f" | tp2: {basis2}，赎回费¥{fee2:.2f}").strip(" |")
    except Exception as e:
        logger.debug(f"[exit_loop] 止盈扣赎回费计算失败: {e}")

    plan["tp1_net_profit_pct"] = tp1_net_pct
    plan["tp2_net_profit_pct"] = tp2_net_pct
    plan["sell_fee_basis"] = sell_fee_basis
    return plan


def evaluate_exit(exit_plan: dict, exit_state: dict | None, current_price: float) -> dict:
    """止盈状态机单次转移：依据当前价格判断触发哪些事件，并推进状态。

    状态字段（exit_state）：breakeven_alerted / tp1_alerted / tp2_alerted（已提醒标志）。
    每个事件只提醒一次（标志置位后不再触发），叠加 create_alert 的 24h 去重双重保险。

    Returns:
        {events: [{type, severity, ratio, price, reason}], new_state: {...}}
    """
    state = dict(exit_state or {})
    events = []
    if not exit_plan or not current_price or current_price <= 0:
        return {"events": events, "new_state": state}

    cost = exit_plan.get("cost_price") or 0
    tp1_price = exit_plan.get("tp1_price")
    tp2_price = exit_plan.get("tp2_price")

    # 回本提醒（亏损标的回到成本价；仅在尚未进入第一批止盈区间时触发，避免与 tp1 冗余）
    if (exit_plan.get("breakeven_alert") and cost and current_price >= cost
            and (tp1_price is None or current_price < tp1_price)
            and not state.get("breakeven_alerted")):
        events.append({
            "type": "breakeven", "severity": _EVENT_SEVERITY["breakeven"],
            "ratio": 0, "price": round(current_price, 4),
            "reason": f"价格回到成本价 {cost}，已回本",
        })
        state["breakeven_alerted"] = True

    # 第一批止盈
    if tp1_price and current_price >= tp1_price and not state.get("tp1_alerted"):
        events.append({
            "type": "tp1", "severity": _EVENT_SEVERITY["tp1"],
            "ratio": exit_plan.get("tp_first_ratio", 0.33), "price": round(current_price, 4),
            "reason": f"涨幅达 {exit_plan.get('tp_first_pct')}%（≥{tp1_price}），建议减仓 {int(exit_plan.get('tp_first_ratio', 0.33)*100)}%",
        })
        state["tp1_alerted"] = True

    # 第二批止盈
    if tp2_price and current_price >= tp2_price and not state.get("tp2_alerted"):
        events.append({
            "type": "tp2", "severity": _EVENT_SEVERITY["tp2"],
            "ratio": exit_plan.get("tp_second_ratio", 0.33), "price": round(current_price, 4),
            "reason": f"涨幅达 {exit_plan.get('tp_second_pct')}%（≥{tp2_price}），建议再减仓 {int(exit_plan.get('tp_second_ratio', 0.33)*100)}%",
        })
        state["tp2_alerted"] = True

    return {"events": events, "new_state": state}


# ════════════════════════════════════════════════════════════════
# 账本集成层
# ════════════════════════════════════════════════════════════════

def attach_exit_plan_to_decision(decision_id: int, cost_price: float,
                                 fund_category: str | None = None,
                                 shares: float | None = None,
                                 buy_date: str | None = None) -> dict | None:
    """为决策账本中的某条补仓决策生成分批止盈计划并写入。

    P1-S5：可选传入 shares + buy_date 以启用止盈扣赎回费计算。
    """
    try:
        from db import update_exit_plan, get_decision
    except Exception:
        return None
    plan = build_exit_plan(cost_price, fund_category, shares=shares, buy_date=buy_date)
    update_exit_plan(decision_id, plan)
    dec = get_decision(decision_id)
    return dec.get("exit_plan") if dec else plan


def scan_exit_signals(user_id: str = "default") -> dict:
    """扫描所有有止盈计划的在途决策，按状态机触发回本/止盈提醒。

    流程：取在途决策（confirmed/executed 且有计划、未回测关闭）→ 读持仓现价 →
    状态机评估 → 生成预警 → 更新 exit_state。

    Returns:
        {scanned, alerts_created, details: [{decision_id, fund_code, events}]}
    """
    try:
        from db import list_decisions, update_exit_state
        from db.portfolio import get_holding_by_fund, create_alert
    except Exception as e:
        logger.warning(f"[exit_loop] 依赖加载失败: {e}")
        return {"scanned": 0, "alerts_created": 0, "details": []}

    try:
        from db.config import get_config_bool
        if not get_config_bool("smartadd.exitloop.enabled", True):
            return {"scanned": 0, "alerts_created": 0, "details": [], "disabled": True}
    except Exception:
        pass

    decisions = list_decisions(user_id=user_id, limit=500)
    scanned = 0
    alerts_created = 0
    details = []

    for dec in decisions:
        if dec.get("status") not in ("confirmed", "executed"):
            continue
        plan = dec.get("exit_plan") or {}
        if not plan or not plan.get("cost_price"):
            continue
        fund_code = dec.get("fund_code")
        holding = get_holding_by_fund(fund_code, user_id) if fund_code else None
        if not holding:
            continue
        current_price = holding.get("current_price") or 0
        if current_price <= 0:
            continue

        scanned += 1
        result = evaluate_exit(plan, dec.get("exit_state") or {}, current_price)
        events = result["events"]
        if not events:
            continue

        # 更新状态机
        update_exit_state(dec["id"], result["new_state"])

        # 生成预警（create_alert 自带 24h 去重）
        fund_name = holding.get("fund_name") or dec.get("fund_name") or fund_code
        for ev in events:
            title = f"【止盈闭环】{fund_name} {_event_cn(ev['type'])}"
            content = (
                f"{ev['reason']}。当前价 {current_price}，成本 {plan.get('cost_price')}。"
                f"数据来源：持仓行情（实时）；本提醒为投研辅助，不构成投资建议，请结合自身风险偏好决策。"
            )
            try:
                create_alert(
                    alert_type=f"exit_{ev['type']}",
                    title=title,
                    content=content,
                    severity=ev["severity"],
                    related_fund_code=fund_code,
                    related_fund_name=fund_name,
                    source="exit_loop",
                    user_id=user_id,
                    holding_id=holding.get("id"),
                )
                alerts_created += 1
            except Exception as e:
                logger.debug(f"[exit_loop] 预警创建失败 {fund_code}: {e}")

        details.append({"decision_id": dec["id"], "fund_code": fund_code, "events": events})

    return {"scanned": scanned, "alerts_created": alerts_created, "details": details}


def _event_cn(event_type: str) -> str:
    return {"breakeven": "已回本", "tp1": "触发第一批止盈", "tp2": "触发第二批止盈"}.get(event_type, event_type)
