"""多维度终止条件检查器 — 替代单一 MAX_TURNS，从多个维度判断是否应终止执行。

设计要点：
- 硬限制：token 预算、执行时长、专家数量
- 收敛检测：与 ConvergenceDetector 集成
- 灵活输入：既可接受 PipelineState（Phase B 后），也可接受简单参数（Phase A 独立使用）
- 配置驱动：所有阈值可从 config 读取，默认值与现有代码兼容

用法：
    checker = TerminationChecker(start_time=start_time)
    should_stop, reason = checker.check(
        tokens_used=8500,
        token_budget={"max_total": 12000, "max_specialists": 6},
        called_agents={"valuation_analyst", "fund_analyst"},
        convergence_detector=detector,
    )
    if should_stop:
        # 进入综合阶段
        pass
"""

import logging
import time
from typing import Optional, Any

from db.config import get_config_int, get_config_float, get_config_bool

logger = logging.getLogger(__name__)


class TerminationChecker:
    """多维度终止条件检查器。

    检查维度：
    1. Token 预算用尽
    2. 执行超时（默认 8 分钟硬限制）
    3. 收敛检测（信息已收敛）
    4. 专家数量上限
    5. 异常状态（如连续失败）
    """

    def __init__(self, start_time: float = 0.0):
        self.start_time = start_time or time.time()
        # 连续失败计数
        self._consecutive_failures = 0
        # 检查历史
        self._check_history: list[tuple[float, str]] = []

    def check(
        self,
        tokens_used: int = 0,
        token_budget: Optional[dict] = None,
        called_agents: Optional[set] = None,
        called_agent_count: Optional[int] = None,
        convergence_detector: Optional[Any] = None,
        current_phase: str = "",
        extra: Optional[dict] = None,
    ) -> tuple[bool, str]:
        """检查是否应终止执行。

        Args:
            tokens_used: 已用 token 数
            token_budget: 预算配置，可包含 max_total / max_specialists
            called_agents: 已调用的 agent_key 集合（与 called_agent_count 二选一）
            called_agent_count: 已调用 agent 数量
            convergence_detector: ConvergenceDetector 实例（可选）
            current_phase: 当前阶段名（如 "execution"）
            extra: 扩展参数（保留字段）

        Returns:
            (是否终止, 原因)
        """
        token_budget = token_budget or {}

        # 1. Token 预算检查
        max_total = token_budget.get("max_total") or self._get_config_int_safe(
            "agent.pipeline_max_total_tokens", 12000
        )
        if tokens_used >= max_total:
            reason = f"token 预算用尽 ({tokens_used}/{max_total})"
            self._record_check(reason)
            return True, reason

        # 2. 超时检查
        timeout_sec = self._get_config_int_safe("conversation.abort_at_minutes", 8) * 60
        elapsed = time.time() - self.start_time
        if elapsed > timeout_sec:
            reason = f"执行超时 ({elapsed:.0f}s > {timeout_sec}s)"
            self._record_check(reason)
            return True, reason

        # 3. 专家数量上限
        agent_count = called_agent_count if called_agent_count is not None else (
            len(called_agents) if called_agents else 0
        )
        max_specialists = token_budget.get("max_specialists") or self._get_config_int_safe(
            "agent.pipeline_max_specialists", 6
        )
        if agent_count >= max_specialists:
            reason = f"专家数量已达上限 ({agent_count}/{max_specialists})"
            self._record_check(reason)
            return True, reason

        # 4. 收敛检测（仅在 execution 阶段触发）
        if convergence_detector is not None and current_phase == "execution":
            try:
                converged, conv_reason = convergence_detector.has_converged()
                if converged:
                    reason = f"信息已收敛：{conv_reason}"
                    self._record_check(reason)
                    return True, reason
            except Exception as e:
                logger.debug(f"[termination] 收敛检测异常: {e}")

        # 5. 连续失败检查
        max_consecutive_failures = self._get_config_int_safe(
            "agent.pipeline_max_consecutive_failures", 3
        )
        if self._consecutive_failures >= max_consecutive_failures:
            reason = f"连续失败 {self._consecutive_failures} 次，触发保护终止"
            self._record_check(reason)
            return True, reason

        # 6. 警告阈值（不终止，但记录日志）
        warn_minutes = self._get_config_int_safe("conversation.warn_at_minutes", 5)
        if elapsed > warn_minutes * 60:
            logger.warning(
                f"[termination] 执行已 {elapsed:.0f}s，超过 {warn_minutes} 分钟警告阈值"
            )

        return False, ""

    def record_failure(self, reason: str = "") -> None:
        """记录一次失败，用于连续失败计数。"""
        self._consecutive_failures += 1
        logger.warning(
            f"[termination] 记录失败 ({self._consecutive_failures}): {reason}"
        )

    def record_success(self) -> None:
        """记录一次成功，重置连续失败计数。"""
        if self._consecutive_failures > 0:
            self._consecutive_failures = 0

    def reset(self, start_time: float = 0.0) -> None:
        """重置检查器状态（新对话开始时调用）。"""
        self.start_time = start_time or time.time()
        self._consecutive_failures = 0
        self._check_history.clear()

    @property
    def elapsed_seconds(self) -> float:
        """已执行时长（秒）。"""
        return time.time() - self.start_time

    @property
    def stats(self) -> dict:
        """返回统计信息。"""
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "consecutive_failures": self._consecutive_failures,
            "termination_checks": len(self._check_history),
            "last_check_reason": self._check_history[-1][1] if self._check_history else "",
        }

    # ── 内部方法 ──────────────────────────────

    def _record_check(self, reason: str) -> None:
        """记录一次检查结果。"""
        self._check_history.append((time.time(), reason))
        # 最多保留 50 条
        if len(self._check_history) > 50:
            self._check_history = self._check_history[-50:]

    @staticmethod
    def _get_config_int_safe(key: str, default: int) -> int:
        """安全读取 int 配置。"""
        try:
            return get_config_int(key, default)
        except Exception:
            return default

    @staticmethod
    def _get_config_float_safe(key: str, default: float) -> float:
        """安全读取 float 配置。"""
        try:
            return get_config_float(key, default)
        except Exception:
            return default


# ── 便捷函数 ──────────────────────────────────

def check_termination(
    start_time: float,
    tokens_used: int = 0,
    token_budget: Optional[dict] = None,
    called_agents: Optional[set] = None,
    convergence_detector: Optional[Any] = None,
    current_phase: str = "",
) -> tuple[bool, str]:
    """一次性终止检查（不维护状态，适用于简单场景）。

    对于需要连续失败计数的场景，请使用 TerminationChecker 实例。
    """
    checker = TerminationChecker(start_time=start_time)
    return checker.check(
        tokens_used=tokens_used,
        token_budget=token_budget,
        called_agents=called_agents,
        convergence_detector=convergence_detector,
        current_phase=current_phase,
    )


def get_max_turns_for_complexity(complexity: str) -> int:
    """根据复杂度获取最大轮次（兼容现有 MAX_TURNS 逻辑）。

    Args:
        complexity: "simple" / "medium" / "complex"

    Returns:
        最大轮次
    """
    defaults = {"simple": 2, "medium": 3, "complex": 4}
    return TerminationChecker._get_config_int_safe(
        "agent.pipeline_max_turns",
        defaults.get(complexity, 3),
    )


def get_token_budget_for_complexity(complexity: str) -> dict:
    """根据复杂度获取 token 预算配置。

    与设计稿中的 TOKEN_BUDGETS 表对应。
    """
    defaults = {
        "simple": {
            "preprocess": 300,
            "info_gather": 1500,
            "planning": 500,
            "execution": 2000,
            "synthesis": 1500,
            "memory": 300,
            "max_total": 6000,
            "max_specialists": 2,
        },
        "medium": {
            "preprocess": 400,
            "info_gather": 2500,
            "planning": 800,
            "execution": 4000,
            "synthesis": 2500,
            "memory": 500,
            "max_total": 10000,
            "max_specialists": 4,
        },
        "complex": {
            "preprocess": 500,
            "info_gather": 3500,
            "planning": 1000,
            "execution": 6000,
            "synthesis": 3500,
            "memory": 800,
            "max_total": 15000,
            "max_specialists": 6,
        },
    }
    return defaults.get(complexity, defaults["medium"])
