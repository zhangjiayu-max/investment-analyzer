"""4%定投法策略引擎 — 雷牛牛方法论的程序化实现。

来源：https://mp.weixin.qq.com/s/8Dhofw5taUPL_teHoYJ8-w
核心逻辑：
1. 估值锁底：百分位 < 门槛（默认20%）才允许启动
2. 跌幅触发：相对上一买入点跌幅 ≥ 设定值（默认4%）
3. 固定份数：总预算÷10份，每次触发买1份
4. 买入基准：递归用上一买入点（不是当前价）

与现有智能补仓信号A/B/C并列，互不替代。
受独立开关 dip_4pct.enabled 控制（默认 false）。
"""
import json
import logging
import math
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class DipStrategy4Pct:
    """4%定投法策略引擎。

    使用方式：
        strategy = DipStrategy4Pct()
        result = strategy.check_trigger(fund_code="161725")
        if result["triggered"]:
            print(f"建议买入 ¥{result['suggested_amount']} (第{result['trigger_num']}次)")
    """

    def __init__(self):
        # 注意：配置在 is_enabled()/check_trigger() 时实时读取，避免单例缓存导致开关失效
        pass

    def is_enabled(self) -> bool:
        """开关是否开启（实时读取配置，避免单例缓存）。"""
        from db.config import get_config_bool
        return get_config_bool("dip_4pct.enabled", False)

    def check_trigger(self, fund_code: str, current_price: float = None,
                      valuation_percentile: float = None,
                      user_id: str = "default", account: str = None) -> dict:
        """检查是否触发4%定投。

        Args:
            fund_code: 基金代码
            current_price: 当前净值（None则自动查询）
            valuation_percentile: 估值百分位（None则自动查询）

        Returns:
            {
                triggered: bool,
                reason: str,
                plan: dict,              # 配置信息
                trigger_num: int,        # 第几次（1-10）
                prev_buy_price: float,   # 上一买入价
                current_price: float,
                actual_dip_pct: float,
                valuation_percentile: float,
                suggested_amount: float,
                cumulative_invested: float,
                remaining_budget: float,
                next_trigger_price: float,  # 下次触发价
            }
        """
        if not self.is_enabled():
            return {"triggered": False, "reason": "4%定投法开关未开启"}

        # 1. 查配置
        from db.dip_investment_plans import get_dip_plan, count_executed_triggers
        plan = get_dip_plan(fund_code)
        if not plan or not plan.get("enabled") or plan.get("status") != "active":
            return {"triggered": False, "reason": "未配置4%定投法或已停用"}

        max_amount = plan["max_amount"]
        total_shares = plan["total_shares"]
        dip_pct_threshold = plan["dip_pct"]
        valuation_threshold = plan["valuation_threshold"]
        single_amount = max_amount / total_shares

        # 2. 检查是否已完成所有份数
        executed_count = count_executed_triggers(plan["id"])
        if executed_count >= total_shares:
            return {"triggered": False, "reason": f"已完成全部{total_shares}份定投",
                    "plan": plan, "status": "completed"}

        # 3. 获取当前净值（若未传）
        if current_price is None:
            current_price = self._fetch_current_price(fund_code)
            if current_price is None:
                return {"triggered": False, "reason": "无法获取当前净值",
                        "plan": plan}
        if not math.isfinite(current_price) or current_price <= 0:
            return {"triggered": False, "reason": "当前净值无效", "plan": plan}

        # 4. 获取估值百分位（若未传）
        if valuation_percentile is None:
            valuation_percentile = self._fetch_valuation_percentile(
                fund_code, plan.get("metric_type"), user_id, account
            )

        # 5. 估值前置检查
        if (valuation_percentile is None or not math.isfinite(valuation_percentile)
                or not 0 <= valuation_percentile <= 100):
            return {"triggered": False, "reason": "缺少估值数据，无法判断低估前提",
                    "plan": plan, "current_price": current_price,
                    "valuation_percentile": None}
        if valuation_percentile is not None and valuation_percentile >= valuation_threshold:
            return {
                "triggered": False,
                "reason": f"估值{valuation_percentile:.1f}%≥{valuation_threshold}%门槛，不满足低估前提",
                "plan": plan,
                "current_price": current_price,
                "valuation_percentile": valuation_percentile,
            }

        # 6. 查上一买入价（从transactions表）
        prev_buy_price = self._get_last_buy_price(fund_code, user_id=user_id, account=account)
        if executed_count and prev_buy_price is None:
            return {"triggered": False, "reason": "已有执行记录但缺少确认买入价，无法计算跌幅",
                    "plan": plan}

        # 同一份未执行建议复用原始快照，避免每次轮询重复触发/改价。
        from db.dip_investment_plans import list_dip_triggers

        def stored_trigger_result(trigger):
            """成功创建与唯一键冲突均返回同一份已保存快照。"""
            return {
                "triggered": not trigger["executed"],
                "reason": "定投建议已执行" if trigger["executed"] else "已有待执行定投建议",
                "plan": plan,
                "trigger_id": trigger["id"], "trigger_num": trigger["trigger_num"],
                "prev_buy_price": trigger["prev_buy_price"],
                "current_price": trigger["current_price"],
                "actual_dip_pct": trigger["actual_dip_pct"],
                "valuation_percentile": trigger["valuation_percentile"],
                "suggested_amount": trigger["suggested_amount"],
                "cumulative_invested": executed_count * single_amount,
                "remaining_budget": max_amount - (executed_count + 1) * single_amount,
                "next_trigger_price": trigger["current_price"] * (1 - dip_pct_threshold / 100),
            }

        pending = next((t for t in list_dip_triggers(plan_id=plan["id"])
                        if t["trigger_num"] == executed_count + 1 and not t["executed"]), None)
        if pending:
            return stored_trigger_result(pending)

        # 7. 计算跌幅并判断是否触发
        if prev_buy_price is None:
            # 首次建仓：无历史买入点，直接触发（估值已满足低估）
            result = {
                "triggered": True,
                "reason": "首次建仓（估值满足低估门槛）",
                "plan": plan,
                "trigger_num": 1,
                "prev_buy_price": None,
                "current_price": current_price,
                "actual_dip_pct": 0,
                "valuation_percentile": valuation_percentile,
                "suggested_amount": single_amount,
                "cumulative_invested": 0,
                "remaining_budget": max_amount,
                "next_trigger_price": current_price * (1 - dip_pct_threshold / 100),
            }
        else:
            # 计算跌幅
            dip_pct = (prev_buy_price - current_price) / prev_buy_price * 100

            if dip_pct < dip_pct_threshold:
                # 未触发
                next_trigger_price = prev_buy_price * (1 - dip_pct_threshold / 100)
                result = {
                    "triggered": False,
                    "reason": f"跌幅{dip_pct:.2f}%<{dip_pct_threshold}%未触发",
                    "plan": plan,
                    "prev_buy_price": prev_buy_price,
                    "current_price": current_price,
                    "actual_dip_pct": dip_pct,
                    "valuation_percentile": valuation_percentile,
                    "next_trigger_price": next_trigger_price,
                }
            else:
                # 触发！
                cumulative_invested = executed_count * single_amount
                result = {
                    "triggered": True,
                    "reason": f"跌幅{dip_pct:.2f}%≥{dip_pct_threshold}%触发定投",
                    "plan": plan,
                    "trigger_num": executed_count + 1,
                    "prev_buy_price": prev_buy_price,
                    "current_price": current_price,
                    "actual_dip_pct": dip_pct,
                    "valuation_percentile": valuation_percentile,
                    "suggested_amount": single_amount,
                    "cumulative_invested": cumulative_invested,
                    "remaining_budget": max_amount - cumulative_invested - single_amount,
                    "next_trigger_price": current_price * (1 - dip_pct_threshold / 100),
                }

        # 8. 记录触发（无论是否触发，都记录便于追踪）
        if result["triggered"]:
            try:
                from db.dip_investment_plans import create_dip_trigger
                try:
                    create_dip_trigger(
                        plan_id=plan["id"],
                        fund_code=fund_code,
                        trigger_num=result["trigger_num"],
                        trigger_date=datetime.now().strftime("%Y-%m-%d"),
                        prev_buy_price=prev_buy_price or current_price,
                        current_price=current_price,
                        actual_dip_pct=result["actual_dip_pct"],
                        valuation_percentile=valuation_percentile,
                        suggested_amount=result["suggested_amount"],
                    )
                except sqlite3.IntegrityError:
                    # 并发请求可能已写入同一(plan_id, trigger_num)，以唯一键获胜者为准。
                    pass
                saved = next((t for t in list_dip_triggers(plan_id=plan["id"])
                              if t["trigger_num"] == result["trigger_num"]), None)
                if saved is None:
                    raise RuntimeError("触发记录未保存")
                result = stored_trigger_result(saved)
                logger.info(
                    f"[dip_4pct] 触发 {fund_code} 第{result['trigger_num']}次 "
                    f"prev={prev_buy_price} curr={current_price} dip={result['actual_dip_pct']:.2f}%"
                )
            except Exception as e:
                logger.warning(f"[dip_4pct] 记录触发失败: {e}")
                return {"triggered": False, "reason": "无法保存定投建议，请稍后重试", "plan": plan}

        return result

    def get_status(self, fund_code: str) -> dict:
        """查询4%定投进度（用于前端展示）。"""
        from db.dip_investment_plans import get_dip_plan, list_dip_triggers, count_executed_triggers

        plan = get_dip_plan(fund_code)
        if not plan:
            return {"configured": False, "reason": "未配置4%定投法"}

        triggers = list_dip_triggers(plan_id=plan["id"])
        executed_count = count_executed_triggers(plan["id"])
        single_amount = plan["max_amount"] / plan["total_shares"]
        cumulative_invested = executed_count * single_amount

        # 查当前价和估值
        current_price = self._fetch_current_price(fund_code)
        valuation_percentile = self._fetch_valuation_percentile(
            fund_code, plan.get("metric_type")
        )
        prev_buy_price = self._get_last_buy_price(fund_code)

        # 下次触发价
        next_trigger_price = None
        if prev_buy_price and plan:
            next_trigger_price = prev_buy_price * (1 - plan["dip_pct"] / 100)

        return {
            "configured": True,
            "plan": plan,
            "current_price": current_price,
            "valuation_percentile": valuation_percentile,
            "prev_buy_price": prev_buy_price,
            "next_trigger_price": next_trigger_price,
            "executed_count": executed_count,
            "total_shares": plan["total_shares"],
            "single_amount": single_amount,
            "cumulative_invested": cumulative_invested,
            "remaining_budget": plan["max_amount"] - cumulative_invested,
            "progress_pct": executed_count / plan["total_shares"] * 100,
            "triggers": triggers,
        }

    def _get_last_buy_price(self, fund_code: str, user_id: str = "default",
                            account: str = None) -> Optional[float]:
        """按基金、用户与账户查询最近一次真实确认买入价。"""
        try:
            from db._conn import _get_conn
            conn = _get_conn()
            try:
                sql = """SELECT t.price FROM portfolio_transactions t
                    LEFT JOIN portfolio_holdings h ON h.id = t.holding_id
                    WHERE t.fund_code = ? AND t.user_id = ?
                      AND t.transaction_type IN ('buy', 'subscribe') AND t.status = 'confirmed'
                      AND COALESCE(t.is_system, 0) = 0 AND COALESCE(t.is_hypothetical, 0) = 0
                      AND t.price > 0 AND t.transaction_date <= ?"""
                params = [fund_code, user_id, datetime.now().strftime("%Y-%m-%d")]
                if account is not None:
                    sql += " AND COALESCE(NULLIF(t.account, ''), h.account) = ?"
                    params.append(account)
                row = conn.execute(sql + " ORDER BY t.transaction_date DESC, t.id DESC LIMIT 1", params).fetchone()
                return float(row["price"]) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[dip_4pct] 查询买入记录失败 {fund_code}: {e}")
            return None

    def _fetch_current_price(self, fund_code: str) -> Optional[float]:
        """获取当前最新净值。"""
        try:
            from db.portfolio import fetch_fund_nav
            nav_data = fetch_fund_nav(fund_code)
            if nav_data and nav_data.get("nav"):
                return float(nav_data["nav"])
            return None
        except Exception as e:
            logger.warning(f"[dip_4pct] 获取净值失败 {fund_code}: {e}")
            return None

    def _fetch_valuation_percentile(self, fund_code: str,
                                     metric_type: str = None, user_id: str = "default",
                                     account: str = None) -> Optional[float]:
        """获取基金跟踪指数的估值百分位。

        复用已修复的 query_valuation 工具（支持后缀兼容+别名兜底）。
        """
        try:
            # 1. 从持仓表查基金跟踪的指数名称
            from db._conn import _get_conn
            conn = _get_conn()
            try:
                sql = ("SELECT index_name FROM portfolio_holdings WHERE fund_code = ? AND user_id = ? "
                       "AND index_name IS NOT NULL AND index_name != ''")
                params = [fund_code, user_id]
                if account is not None:
                    sql += " AND account = ?"
                    params.append(account)
                row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
            finally:
                conn.close()
            if not row or not row["index_name"]:
                return None

            index_name = row["index_name"]

            # 2. 调用 query_valuation 工具
            import sys
            if 'tools' not in sys.modules:
                sys.path.insert(0, '.')
            from tools import _query_valuation
            result_str = _query_valuation({"index_name": index_name})
            data = json.loads(result_str)

            if isinstance(data, list) and data:
                metrics = data[0].get("metrics", [])
                if metrics:
                    # 优先匹配指定metric_type，否则取第一个有percentile的
                    for m in metrics:
                        if metric_type and m.get("metric_type") == metric_type:
                            return m.get("percentile")
                    for m in metrics:
                        if m.get("percentile") is not None:
                            return m["percentile"]
            elif isinstance(data, dict) and data.get("ok"):
                # ttfund 兜底返回
                indexes = data.get("indexes", [])
                if indexes:
                    idx = indexes[0]
                    if metric_type == "市净率" and idx.get("pb_percentile_10y"):
                        return float(idx["pb_percentile_10y"])
                    if idx.get("pe_percentile_10y"):
                        return float(idx["pe_percentile_10y"])
            return None
        except Exception as e:
            logger.warning(f"[dip_4pct] 获取估值失败 {fund_code}: {e}")
            return None


# 模块级单例
_strategy_instance = None


def get_dip_4pct_strategy() -> DipStrategy4Pct:
    """获取策略实例（单例）。"""
    global _strategy_instance
    if _strategy_instance is None:
        _strategy_instance = DipStrategy4Pct()
    return _strategy_instance
