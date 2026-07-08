"""对话流水线状态机 — 替代隐式 ReAct 循环变量，提供显式阶段管理和 checkpoint 能力。

设计要点：
- PipelinePhase 枚举定义 6 个执行阶段 + 3 个终态
- PipelineState 数据类持有所有阶段产出，支持序列化用于 checkpoint
- 阶段转换有合法性校验，防止非法跳转
- token 预算按阶段分配，超预算自动告警

与现有代码的关系：
- 现有 orchestrate() 的 ReAct 循环变量（specialist_results, already_called 等）
  全部归入 PipelineState.phase_results["execution"]
- Checkpoint 表结构不变，PipelineState.to_dict() 直接写入 checkpoint_state
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PipelinePhase(str, Enum):
    """对话流水线阶段。"""
    PREPROCESS = "preprocess"       # Phase 0: 预处理（意图识别 + Query 改写 + 复杂度评估）
    INFO_GATHER = "info_gather"     # Phase 1: 信息收集（RAG + 预取 + 记忆加载）
    PLANNING = "planning"           # Phase 2: 计划生成（选专家 + 排序 + 预算分配）
    EXECUTION = "execution"         # Phase 3: 专家执行（并行 + 黑板 + 收敛检测）
    DEBATE = "debate"               # Phase 3.7: 对抗式辩论（冲突时触发）
    REFLECTION = "reflection"       # Phase 3.5: 反思（自评 + 冲突识别 + 质量问题）
    SYNTHESIS = "synthesis"         # Phase 4: 综合（交叉审阅 + 仲裁 + 最终回答）
    MEMORY = "memory"               # Phase 5: 记忆持久化（结论 + 摘要 + 偏好）
    # 终态
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── 阶段转换合法性 ──────────────────────────────

_VALID_TRANSITIONS: dict[PipelinePhase, set[PipelinePhase]] = {
    PipelinePhase.PREPROCESS: {PipelinePhase.INFO_GATHER, PipelinePhase.FAILED, PipelinePhase.CANCELLED},
    PipelinePhase.INFO_GATHER: {PipelinePhase.PLANNING, PipelinePhase.FAILED, PipelinePhase.CANCELLED},
    PipelinePhase.PLANNING: {PipelinePhase.EXECUTION, PipelinePhase.FAILED, PipelinePhase.CANCELLED},
    PipelinePhase.EXECUTION: {PipelinePhase.DEBATE, PipelinePhase.REFLECTION, PipelinePhase.SYNTHESIS, PipelinePhase.FAILED, PipelinePhase.CANCELLED},
    PipelinePhase.DEBATE: {PipelinePhase.REFLECTION, PipelinePhase.SYNTHESIS, PipelinePhase.FAILED, PipelinePhase.CANCELLED},
    PipelinePhase.REFLECTION: {PipelinePhase.SYNTHESIS, PipelinePhase.FAILED, PipelinePhase.CANCELLED},
    PipelinePhase.SYNTHESIS: {PipelinePhase.MEMORY, PipelinePhase.FAILED, PipelinePhase.CANCELLED},
    PipelinePhase.MEMORY: {PipelinePhase.COMPLETED, PipelinePhase.FAILED},
    # 终态不可转换
    PipelinePhase.COMPLETED: set(),
    PipelinePhase.FAILED: set(),
    PipelinePhase.CANCELLED: set(),
}


@dataclass
class PipelineState:
    """对话流水线状态 — 替代隐式 ReAct 循环变量。

    所有阶段产出集中存放，便于：
    1. Checkpoint 持久化（to_dict → JSON）
    2. 阶段间数据传递（Phase 1 结果给 Phase 3 用）
    3. 审计与调试（trace 查询可看完整状态）
    """

    # ── 标识 ──────────────────────────────
    trace_id: str = ""
    conversation_id: int = 0
    message_id: int = 0
    original_query: str = ""
    refined_query: str = ""

    # ── 阶段状态 ──────────────────────────
    phase: PipelinePhase = PipelinePhase.PREPROCESS
    phase_results: dict[str, Any] = field(default_factory=dict)
    # 每个阶段的产出：{"preprocess": {...}, "info_gather": {...}, ...}
    phase_started_at: dict[str, float] = field(default_factory=dict)
    phase_finished_at: dict[str, float] = field(default_factory=dict)

    # ── 预算控制 ──────────────────────────
    token_budget: dict = field(default_factory=dict)
    # 各阶段预算 + max_total + max_specialists
    tokens_used_by_phase: dict[str, int] = field(default_factory=dict)
    # {"preprocess": 200, "info_gather": 1500, ...}
    tokens_used: int = 0
    start_time: float = 0.0

    # ── Phase 0: 预处理产出 ────────────────
    # 放入 phase_results["preprocess"]，包括 intent/targets/needed_info/complexity 等

    # ── Phase 1: 信息收集产出 ──────────────
    # 放入 phase_results["info_gather"]，包括 rag/portfolio/valuations/memory/info_gaps

    # ── Phase 2: 计划产出 ─────────────────
    # 放入 phase_results["planning"]，即 AnalysisPlan.to_dict()

    # ── Phase 3: 执行产出 ─────────────────
    specialist_results: list = field(default_factory=list)
    called_agents: set = field(default_factory=set)
    # called_queries 由 ConvergenceDetector 管理，这里只存引用

    # ── Phase 4: 综合产出 ─────────────────
    # 放入 phase_results["synthesis"]，包括 cross_review/arbitration/validator/answer

    # ── Phase 5: 记忆产出 ─────────────────
    # 放入 phase_results["memory"]，包括 saved_conclusion_id/updated_memory_ids

    # ── 最终输出 ──────────────────────────
    answer: str = ""
    error: str = ""

    # ── Checkpoint ────────────────────────
    checkpoint_saved: bool = False

    # ── 阶段转换方法 ──────────────────────

    def transition_to(self, new_phase: PipelinePhase) -> tuple[bool, str]:
        """转换到新阶段，返回 (是否成功, 原因)。

        非法转换会记录日志但允许（降级场景下可能跳过某些阶段）。
        """
        if new_phase == self.phase:
            return True, "已在当前阶段"

        allowed = _VALID_TRANSITIONS.get(self.phase, set())
        if new_phase not in allowed:
            # 非法转换：记录但允许（降级场景）
            logger.warning(
                f"[pipeline] 非法阶段转换: {self.phase.value} → {new_phase.value}（允许但不推荐）"
            )

        # 记录前一阶段完成时间
        if self.phase.value not in self.phase_finished_at:
            self.phase_finished_at[self.phase.value] = time.time()

        # 记录新阶段开始时间
        self.phase = new_phase
        self.phase_started_at[new_phase.value] = time.time()
        return True, ""

    def record_phase_tokens(self, phase: str, tokens: int) -> None:
        """记录某阶段的 token 消耗。"""
        current = self.tokens_used_by_phase.get(phase, 0)
        self.tokens_used_by_phase[phase] = current + tokens
        self.tokens_used += tokens

        # 预算检查
        budget = self.token_budget.get(phase)
        if budget and self.tokens_used_by_phase[phase] > budget:
            logger.warning(
                f"[pipeline] Phase {phase} 超预算: "
                f"{self.tokens_used_by_phase[phase]}/{budget}"
            )

    def is_phase_over_budget(self, phase: str) -> bool:
        """检查某阶段是否超预算。"""
        budget = self.token_budget.get(phase)
        if not budget:
            return False
        return self.tokens_used_by_phase.get(phase, 0) > budget

    def is_total_over_budget(self) -> bool:
        """检查总 token 是否超预算。"""
        max_total = self.token_budget.get("max_total")
        if not max_total:
            return False
        return self.tokens_used >= max_total

    # ── 阶段产出读写 ──────────────────────

    def set_phase_result(self, phase: str, result: Any) -> None:
        """设置某阶段的产出。"""
        self.phase_results[phase] = result

    def get_phase_result(self, phase: str) -> Optional[Any]:
        """获取某阶段的产出。"""
        return self.phase_results.get(phase)

    # ── 序列化（用于 checkpoint） ───────────

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的字典（用于 checkpoint 持久化）。"""
        return {
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "original_query": self.original_query,
            "refined_query": self.refined_query,
            "phase": self.phase.value,
            "phase_results": _safe_serialize(self.phase_results),
            "phase_started_at": self.phase_started_at,
            "phase_finished_at": self.phase_finished_at,
            "token_budget": self.token_budget,
            "tokens_used_by_phase": self.tokens_used_by_phase,
            "tokens_used": self.tokens_used,
            "start_time": self.start_time,
            "specialist_results": _safe_serialize(self.specialist_results),
            "called_agents": list(self.called_agents),
            "answer": self.answer,
            "error": self.error,
            "checkpoint_saved": self.checkpoint_saved,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineState":
        """从字典反序列化（用于恢复 checkpoint）。"""
        state = cls(
            trace_id=data.get("trace_id", ""),
            conversation_id=data.get("conversation_id", 0),
            message_id=data.get("message_id", 0),
            original_query=data.get("original_query", ""),
            refined_query=data.get("refined_query", ""),
            phase=_parse_phase(data.get("phase", "preprocess")),
            phase_results=data.get("phase_results", {}),
            phase_started_at=data.get("phase_started_at", {}),
            phase_finished_at=data.get("phase_finished_at", {}),
            token_budget=data.get("token_budget", {}),
            tokens_used_by_phase=data.get("tokens_used_by_phase", {}),
            tokens_used=data.get("tokens_used", 0),
            start_time=data.get("start_time", 0.0),
            specialist_results=data.get("specialist_results", []),
            called_agents=set(data.get("called_agents", [])),
            answer=data.get("answer", ""),
            error=data.get("error", ""),
            checkpoint_saved=data.get("checkpoint_saved", False),
        )
        return state

    # ── 统计 ──────────────────────────────

    @property
    def elapsed_seconds(self) -> float:
        """已执行时长（秒）。"""
        if not self.start_time:
            return 0.0
        return time.time() - self.start_time

    def stats(self) -> dict:
        """返回统计信息。"""
        return {
            "phase": self.phase.value,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "tokens_used": self.tokens_used,
            "tokens_by_phase": dict(self.tokens_used_by_phase),
            "token_budget": self.token_budget,
            "called_agents": list(self.called_agents),
            "phase_durations": {
                p: round(self.phase_finished_at.get(p, 0) - self.phase_started_at.get(p, 0), 2)
                for p in self.phase_started_at
            },
        }


# ── 辅助函数 ──────────────────────────────────

def _parse_phase(value: str) -> PipelinePhase:
    """解析阶段字符串，无效值返回 PREPROCESS。"""
    try:
        return PipelinePhase(value)
    except ValueError:
        return PipelinePhase.PREPROCESS


def _safe_serialize(obj: Any) -> Any:
    """安全序列化：处理 set 等不可 JSON 化的类型。"""
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_serialize(item) for item in obj]
    if isinstance(obj, Enum):
        return obj.value
    return obj


# ── 便捷工厂函数 ──────────────────────────────

def create_initial_state(
    trace_id: str,
    conversation_id: int,
    message_id: int,
    original_query: str,
    refined_query: str = "",
    complexity: str = "medium",
) -> PipelineState:
    """创建初始 Pipeline 状态。

    Args:
        complexity: 用于初始化 token 预算（simple/medium/complex）
    """
    from agent.termination import get_token_budget_for_complexity
    budget = get_token_budget_for_complexity(complexity)

    state = PipelineState(
        trace_id=trace_id,
        conversation_id=conversation_id,
        message_id=message_id,
        original_query=original_query,
        refined_query=refined_query or original_query,
        phase=PipelinePhase.PREPROCESS,
        token_budget=budget,
        start_time=time.time(),
    )
    state.phase_started_at[PipelinePhase.PREPROCESS.value] = state.start_time
    return state
