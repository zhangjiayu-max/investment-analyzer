"""Orchestrator 性能优化模块

优化点：
1. 减少 LLM 调用次数
2. 优化交叉审阅逻辑
3. 添加超时控制
4. 使用缓存减少重复计算
"""

import time
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


class OrchestratorOptimizer:
    """Orchestrator 优化器"""

    # 缓存配置结果
    _complexity_cache = {}
    _CACHE_TTL = 300  # 5分钟缓存

    @staticmethod
    def should_skip_cross_review(specialist_results: list, complexity: str) -> bool:
        """判断是否应该跳过交叉审阅（优化版）

        跳过条件：
        1. 专家数量 < 2
        2. 所有专家方向一致
        3. 简单任务
        """
        # 条件1：专家数量不足
        if len(specialist_results) < 2:
            return True

        # 条件2：简单任务
        if complexity in ("simple", "chat"):
            return True

        # 条件3：快速检查专家方向是否一致
        directions = set()
        for sr in specialist_results:
            analysis = sr.get("analysis", "").lower()
            # 快速关键词检测
            bullish_count = sum(1 for kw in ["低估", "机会", "建议买", "加仓", "看好"] if kw in analysis)
            bearish_count = sum(1 for kw in ["高估", "风险高", "减仓", "回避", "谨慎"] if kw in analysis)

            if bullish_count > bearish_count + 1:
                directions.add("bullish")
            elif bearish_count > bullish_count + 1:
                directions.add("bearish")
            else:
                directions.add("neutral")

        # 如果只有一种方向，跳过交叉审阅
        return len(directions) == 1

    @staticmethod
    def should_skip_arbitration(specialist_results: list, complexity: str) -> bool:
        """判断是否应该跳过仲裁（优化版）

        跳过条件：
        1. 简单任务
        2. 专家意见高度一致
        3. 只有1个专家
        """
        # 条件1：简单任务
        if complexity in ("simple", "chat"):
            return True

        # 条件2：专家数量不足
        if len(specialist_results) < 2:
            return True

        # 条件3：快速检查专家是否高度一致
        # 如果所有专家的结论关键词相似，跳过仲裁
        conclusions = []
        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            # 提取结论部分（前200字）
            conclusions.append(analysis[:200].lower())

        # 简单的一致性检查
        if len(conclusions) >= 2:
            # 检查是否有明显的分歧关键词
            has_disagreement = False
            for c1 in conclusions:
                for c2 in conclusions:
                    if c1 == c2:
                        continue
                    # 检查是否有相反的观点
                    if ("建议买" in c1 and "不建议买" in c2) or \
                       ("高估" in c1 and "低估" in c2):
                        has_disagreement = True
                        break
                if has_disagreement:
                    break

            if not has_disagreement:
                return True

        return False

    @staticmethod
    def optimize_specialist_queries(original_query: str, specialists: list) -> dict:
        """优化专家查询，减少重复处理

        返回：{agent_key: optimized_query}
        """
        optimized = {}
        for agent_key in specialists:
            # 为每个专家生成更简洁的查询
            if agent_key == "valuation_expert":
                optimized[agent_key] = f"分析估值水平：{original_query}"
            elif agent_key == "risk_assessor":
                optimized[agent_key] = f"评估风险：{original_query}"
            elif agent_key == "allocation_advisor":
                optimized[agent_key] = f"资产配置建议：{original_query}"
            elif agent_key == "market_analyst":
                optimized[agent_key] = f"市场分析：{original_query}"
            else:
                optimized[agent_key] = original_query
        return optimized

    @staticmethod
    def get_fast_model() -> str:
        """获取快速模型（用于中间步骤）"""
        from config import FAST_MODEL, MODEL
        return FAST_MODEL if FAST_MODEL else MODEL

    @staticmethod
    def calculate_optimal_timeout(complexity: str, specialist_count: int) -> int:
        """计算最优超时时间（秒）"""
        base_timeout = {
            "simple": 60,
            "medium": 180,
            "complex": 300,
        }
        # 每个专家额外增加30秒
        timeout = base_timeout.get(complexity, 180) + (specialist_count * 30)
        return min(timeout, 600)  # 最大10分钟


class ParallelExecutor:
    """并行执行器优化"""

    @staticmethod
    def estimate_execution_time(specialists: list, complexity: str) -> dict:
        """估算执行时间

        返回：{
            "parallel_time": 并行执行时间（取最长）,
            "sequential_time": 串行执行时间（总和）,
            "speedup": 加速比
        }
        """
        # 估算每个专家的执行时间（秒）
        estimated_times = {
            "valuation_expert": 60,
            "risk_assessor": 45,
            "allocation_advisor": 50,
            "market_analyst": 40,
            "fund_analyst": 55,
        }

        times = [estimated_times.get(s, 60) for s in specialists]

        if not times:
            return {"parallel_time": 0, "sequential_time": 0, "speedup": 1}

        parallel_time = max(times)
        sequential_time = sum(times)
        speedup = sequential_time / parallel_time if parallel_time > 0 else 1

        return {
            "parallel_time": parallel_time,
            "sequential_time": sequential_time,
            "speedup": speedup,
        }


def optimize_orchestration_config():
    """优化编排配置（基于历史数据）"""
    from db._conn import _get_conn

    conn = _get_conn()

    # 分析最近的执行数据
    recent_runs = conn.execute("""
        SELECT
            agent_key,
            AVG(duration_ms) as avg_duration,
            COUNT(*) as run_count
        FROM agent_runs
        WHERE created_at >= datetime('now', '-7 days')
            AND status = 'completed'
            AND agent_key != 'chat_turn'
        GROUP BY agent_key
    """).fetchall()

    conn.close()

    if not recent_runs:
        return {}

    # 计算优化建议
    suggestions = {}
    for run in recent_runs:
        agent_key = run["agent_key"]
        avg_duration = run["avg_duration"] or 0

        if avg_duration > 120000:  # 超过2分钟
            suggestions[agent_key] = {
                "avg_duration_ms": avg_duration,
                "suggestion": "考虑优化 prompt 或使用更快的模型",
            }

    return suggestions


def log_performance_metrics(conversation_id: int, message_id: int, metrics: dict):
    """记录性能指标（用于后续优化）"""
    import json
    from db._conn import _get_conn

    conn = _get_conn()
    conn.execute("""
        INSERT INTO performance_metrics
        (conversation_id, message_id, metrics_json, created_at)
        VALUES (?, ?, ?, datetime('now','localtime'))
    """, (conversation_id, message_id, json.dumps(metrics, ensure_ascii=False)))
    conn.commit()
    conn.close()


# 创建性能指标表（如果不存在）
def init_performance_metrics_table():
    """初始化性能指标表"""
    from db._conn import _get_conn

    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS performance_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            metrics_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_perf_metrics_conv
        ON performance_metrics(conversation_id)
    """)
    conn.commit()
    conn.close()
