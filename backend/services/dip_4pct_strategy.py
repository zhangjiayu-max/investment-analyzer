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
                      valuation_percentile: float = None) -> dict:
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

        # 4. 获取估值百分位（若未传）
        if valuation_percentile is None:
            valuation_percentile = self._fetch_valuation_percentile(
                fund_code, plan.get("metric_type")
            )

        # 5. 估值前置检查
        if valuation_percentile is not None and valuation_percentile >= valuation_threshold:
            return {
                "triggered": False,
                "reason": f"估值{valuation_percentile:.1f}%≥{valuation_threshold}%门槛，不满足低估前提",
                "plan": plan,
                "current_price": current_price,
                "valuation_percentile": valuation_percentile,
            }

        # 6. 查上一买入价（从transactions表）
        prev_buy_price = self._get_last_buy_price(fund_code)

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
                create_dip_trigger(
                    plan_id=plan["id"],
                    fund_code=fund_code,
                    trigger_num=result["trigger_num"],
                    trigger_date=datetime.now().strftime("%Y-%m-%d"),
                    prev_buy_price=prev_buy_price or current_price,
                    current_price=current_price,
                    actual_dip_pct=result["actual_dip_pct"],
                    valuation_percentile=valuation_percentile or 0,
                    suggested_amount=result["suggested_amount"],
                )
                logger.info(
                    f"[dip_4pct] 触发 {fund_code} 第{result['trigger_num']}次 "
                    f"prev={prev_buy_price} curr={current_price} dip={result['actual_dip_pct']:.2f}%"
                )
            except Exception as e:
                logger.warning(f"[dip_4pct] 记录触发失败: {e}")

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

    def _get_last_buy_price(self, fund_code: str) -> Optional[float]:
        """从transactions表查最近一次买入价。"""
        try:
            from db.portfolio import list_transactions
            txns = list_transactions(fund_code=fund_code, limit=50)
            for t in txns:
                if t.get("type") == "buy" and t.get("price"):
                    return float(t["price"])
            return None
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
                                     metric_type: str = None) -> Optional[float]:
        """获取基金跟踪指数的估值百分位。

        复用已修复的 query_valuation 工具（支持后缀兼容+别名兜底）。
        """
        try:
            # 1. 从持仓表查基金跟踪的指数名称
            from db._conn import _get_conn
            conn = _get_conn()
            row = conn.execute(
                "SELECT index_name FROM holdings WHERE fund_code = ?", (fund_code,)
            ).fetchone()
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
