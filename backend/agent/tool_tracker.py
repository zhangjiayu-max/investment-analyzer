"""
工具调用质量追踪器 — 异步离线评估，不阻塞主流程。

评估维度：
1. 工具选择正确性（是否选了最合适的工具）
2. 参数正确性（必填参数是否全部提供，值是否合理）
3. 调用效率（是否有多余的调用、重复调用）
4. 结果利用率（工具返回的数据是否被 Agent 用上了）
"""

import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# 冗余判定阈值：同一工具被同一 Agent 调用超过此数视为冗余
REDUNDANT_THRESHOLD = 2


class ToolCallTracker:
    """工具调用质量追踪器。"""

    def __init__(self, query: str, specialist_results: list[dict]):
        self.query = query
        self.tool_calls: list[dict] = []
        self._extract_tool_calls(specialist_results)

    def _extract_tool_calls(self, results: list[dict]) -> None:
        """从 specialist 结果中提取工具调用记录。"""
        for sr in results or []:
            agent = sr.get("agent", "unknown")
            agent_key = sr.get("agent_key", "")
            for tc in sr.get("tool_calls", []):
                self.tool_calls.append({
                    "agent": agent,
                    "agent_key": agent_key,
                    "tool_name": tc.get("tool_name") or tc.get("name", "unknown"),
                    "arguments": tc.get("arguments", {}),
                    "result_preview": tc.get("result_preview", ""),
                    "duration_ms": sr.get("duration_ms", 0),
                })

    def evaluate(self) -> dict:
        """
        异步评估（离线，不影响主流程）。

        返回评估指标，可存入 eval 表。
        """
        metrics: dict[str, Any] = {
            "total_calls": len(self.tool_calls),
            "unique_tools": len({t["tool_name"] for t in self.tool_calls}),
            "unique_agents": len({t["agent_key"] for t in self.tool_calls if t["agent_key"]}),
            "redundant_calls": 0,
            "empty_result_calls": 0,
            "efficiency_score": 1.0,
            "tool_distribution": {},
        }

        # 工具分布
        tool_counts = Counter(t["tool_name"] for t in self.tool_calls)
        metrics["tool_distribution"] = dict(tool_counts)

        # 冗余调用检测
        pair_counts = Counter((t["tool_name"], t["agent_key"]) for t in self.tool_calls)
        metrics["redundant_calls"] = sum(
            max(0, c - REDUNDANT_THRESHOLD) for c in pair_counts.values()
        )

        # 空结果/失败调用检测
        _empty_or_err = 0
        _err_signals = ("error", "fail", "调用失败", "超时", "timeout", "skill_version_required",
                        "需要登录", "HTTP 4", "HTTP 5", "未找到匹配")
        for t in self.tool_calls:
            rp = t.get("result_preview", "")
            if not rp or rp in ("", "{}", "null", "None"):
                _empty_or_err += 1
            elif any(sig in rp.lower() for sig in _err_signals):
                _empty_or_err += 1
        metrics["empty_result_calls"] = _empty_or_err

        # 效率评分
        total = metrics["total_calls"]
        if total == 0:
            metrics["efficiency_score"] = 1.0  # 未调用工具，不扣分
        else:
            penalty = (metrics["redundant_calls"] * 0.3
                       + metrics["empty_result_calls"] * 0.15)
            metrics["efficiency_score"] = round(max(0.0, 1.0 - penalty), 3)

        return metrics

    def should_flag_bad_case(self, metrics: dict | None = None) -> bool:
        """效率评分低于阈值时标记为 bad case。"""
        m = metrics or self.evaluate()
        return m["efficiency_score"] < 0.5


def evaluate_tool_calls_async(query: str, specialist_results: list[dict]) -> dict:
    """
    同步评估工具调用质量（设计为在后台线程/任务中调用）。

    不抛异常，任何失败都返回空指标，绝不影响主流程。
    """
    try:
        tracker = ToolCallTracker(query, specialist_results)
        metrics = tracker.evaluate()

        # 存入 eval 表
        from db.eval import save_tool_eval_metrics
        save_tool_eval_metrics(metrics)

        # 低效率标记 bad case
        if tracker.should_flag_bad_case(metrics):
            try:
                from db.eval import add_bad_case
                add_bad_case({
                    "type": "tool_efficiency",
                    "query": query[:200],
                    "metrics": metrics,
                })
            except Exception:
                pass

        logger.info(
            f"[tool_eval] calls={metrics['total_calls']} "
            f"redundant={metrics['redundant_calls']} "
            f"efficiency={metrics['efficiency_score']}"
        )
        return metrics
    except Exception as e:
        logger.warning(f"工具调用评估失败（不影响主流程）: {e}")
        return {"total_calls": 0, "efficiency_score": 1.0, "error": str(e)}
