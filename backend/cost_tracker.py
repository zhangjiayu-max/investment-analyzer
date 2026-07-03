"""成本治理 — Token 成本追踪 + 月度预算控制。

借鉴企业级多 Agent 成本控制体系：
1. 成本感知路由（不同 Agent 用不同模型）
2. 月度预算控制（超额自动降级）
3. 成本仪表盘（按 Agent/功能 统计）
"""

import json
import logging
from datetime import datetime
from typing import Optional

from db._conn import _get_conn
from db._utils import _add_column_if_not_exists
from db.config import get_config, get_config_float

logger = logging.getLogger(__name__)


# ── 模型价格表（每百万 tokens，单位：元）────────────────────

MODEL_PRICES = {
    # DeepSeek 系列
    "deepseek-chat": {"input": 0.5, "output": 2.0},
    "deepseek-reasoner": {"input": 1.0, "output": 4.0},
    "deepseek-v4-flash": {"input": 0.5, "output": 2.0},
    "deepseek-v4-pro": {"input": 1.0, "output": 4.0},
    # MIMO（如还在用）
    "mimo": {"input": 0.3, "output": 1.2},
    # Ollama 本地模型（零成本）
    "ollama": {"input": 0.0, "output": 0.0},
    "qwen3-vl": {"input": 0.0, "output": 0.0},
}


def _get_price(model: str) -> dict:
    """获取模型价格，未知模型用默认值。"""
    if not model:
        return {"input": 0.5, "output": 2.0}
    for key, price in MODEL_PRICES.items():
        if key in model.lower():
            return price
    return {"input": 0.5, "output": 2.0}


# ── 数据库表初始化 ──────────────────────────────────────────

def init_cost_tables(conn):
    """初始化成本追踪相关表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cost_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_logs_date ON cost_logs(date(created_at))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_logs_source ON cost_logs(source_type)")


# ── 成本追踪器 ──────────────────────────────────────────────

class CostTracker:
    """Token 成本追踪。每个 LLM 调用都记录到 cost_logs 表。"""

    def record(self, source_type: str, model: str,
               prompt_tokens: int, completion_tokens: int) -> float:
        """记录一次调用，返回估算成本（元）。"""
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return 0.0

        price = _get_price(model)
        cost = (prompt_tokens * price["input"] + completion_tokens * price["output"]) / 1_000_000

        try:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO cost_logs (source_type, model, prompt_tokens, completion_tokens, cost) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_type, model or "unknown", prompt_tokens, completion_tokens, round(cost, 6))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"记录成本失败: {e}")

        return cost

    def daily_summary(self, date_str: str = None) -> dict:
        """每日成本汇总。"""
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT source_type, SUM(cost), SUM(prompt_tokens), SUM(completion_tokens) "
                "FROM cost_logs WHERE date(created_at) = ? "
                "GROUP BY source_type ORDER BY SUM(cost) DESC",
                (date_str,)
            ).fetchall()
            conn.close()
        except Exception:
            return {"date": date_str, "total_cost": 0, "by_type": {}, "estimate_monthly": 0}

        total = sum((r[1] or 0) for r in rows)
        by_type = {r[0]: {
            "cost": round(r[1] or 0, 4),
            "tokens": (r[2] or 0) + (r[3] or 0),
        } for r in rows}

        return {
            "date": date_str,
            "total_cost": round(total, 4),
            "by_type": by_type,
            "estimate_monthly": round(total * 30, 2),
        }

    def monthly_summary(self) -> dict:
        """当月成本汇总。"""
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT SUM(cost), SUM(prompt_tokens), SUM(completion_tokens), COUNT(*) "
                "FROM cost_logs WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
            ).fetchone()
            conn.close()
        except Exception:
            return {"total_cost": 0, "total_tokens": 0, "call_count": 0}

        return {
            "total_cost": round(row[0] or 0, 4),
            "total_tokens": (row[1] or 0) + (row[2] or 0),
            "call_count": row[3] or 0,
        }


# 全局单例
cost_tracker = CostTracker()


# ── 预算控制器 ──────────────────────────────────────────────

class BudgetController:
    """月度预算控制器。超额时自动降级模型。"""

    def __init__(self, monthly_budget: float = 0):
        self.budget = monthly_budget or get_config_float("cost.monthly_budget", 30.0)

    def get_status(self) -> dict:
        """查询当月预算使用情况。"""
        monthly = cost_tracker.monthly_summary()
        used = monthly["total_cost"]
        remaining = self.budget - used
        pct = (used / self.budget * 100) if self.budget > 0 else 0

        return {
            "budget": round(self.budget, 2),
            "used": round(used, 2),
            "remaining": round(remaining, 2),
            "usage_pct": round(pct, 1),
            "call_count": monthly["call_count"],
            "total_tokens": monthly["total_tokens"],
        }

    def should_use_expensive_model(self, analysis_type: str = "") -> bool:
        """是否还能用高成本模型。

        预算 < 50% → 正常
        预算 50-80% → 核心分析用贵模型，非核心降级
        预算 > 80% → 全部降级为便宜模型
        """
        info = self.get_status()
        pct = info["usage_pct"]

        if pct < 50:
            return True
        elif pct < 80:
            # 核心分析类型允许用贵模型
            return analysis_type in {"deep_dive", "portfolio_trade", "buy_decision", "sell_decision"}
        else:
            return False

    def get_recommended_model(self, agent_key: str, analysis_type: str = "") -> Optional[str]:
        """根据预算状态推荐模型。"""
        if not self.should_use_expensive_model(analysis_type):
            return get_config("cost_routing.conservative_model", "deepseek-v4-flash")
        return None  # None 表示用默认模型


budget_controller = BudgetController()
