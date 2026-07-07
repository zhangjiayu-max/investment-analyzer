"""收敛检测器 — 检测执行是否已收敛，防止无效循环和重复专家调用。

设计要点：
- has_converged(): 比较最近两轮的结论/数据引用，新信息比例 <20% 即收敛
- should_skip_agent(): 同 agent_key + 相似 query 已调用过则跳过
- record_call(): 记录调用历史，供后续判断

依赖：无外部依赖，纯算法模块。
"""

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)


def _normalize_query(query: str) -> str:
    """归一化 query：去标点、去多余空白、转小写。"""
    if not query:
        return ""
    # 去除中英文标点（用字符串 translate 更安全，避免正则字符类转义问题）
    import unicodedata
    # 保留字母数字和空格，其他标点（中英文）都替换为空格
    cleaned_chars = []
    for ch in query:
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):
            cleaned_chars.append(" ")
        else:
            cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars)
    # 折叠空白
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0-1），基于 SequenceMatcher。"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class _RoundRecord:
    """单轮执行记录。"""
    conclusions: set = field(default_factory=set)  # 结论摘要集合
    data_refs: set = field(default_factory=set)    # 数据引用集合（如 "沪深300:PE=12.3"）


class ConvergenceDetector:
    """检测执行是否已收敛（无新信息），防止无效循环。

    用法：
        detector = ConvergenceDetector(min_new_info_ratio=0.2)
        # 每轮记录新结论
        detector.record_round(new_conclusions=["沪深300低估"], new_data_refs=["沪深300:PE=12.3"])
        # 判断收敛
        converged, reason = detector.has_converged()
        if converged:
            # 跳过后续专家，直接进入综合
            pass
        # 判断是否跳过某个专家
        if detector.should_skip_agent("valuation_analyst", "分析沪深300估值"):
            # 已调用过相似 query，跳过
            pass
        # 记录调用
        detector.record_call("valuation_analyst", "分析沪深300估值")
    """

    def __init__(self, min_new_info_ratio: float = 0.2, similarity_threshold: float = 0.85):
        self.min_new_info_ratio = min_new_info_ratio
        self.similarity_threshold = similarity_threshold
        self.history: list[_RoundRecord] = []
        # called_queries: {(agent_key, normalized_query): True}
        self.called_queries: dict[tuple[str, str], bool] = {}

    # ── 轮次记录 ──────────────────────────────

    def record_round(self, new_conclusions: list[str], new_data_refs: list[str]) -> None:
        """记录一轮执行结果。

        Args:
            new_conclusions: 本轮新产出的结论摘要列表（每条 ≤100 字）
            new_data_refs: 本轮引用的数据点列表（如 "沪深300:PE=12.3"）
        """
        self.history.append(_RoundRecord(
            conclusions=set(new_conclusions),
            data_refs=set(new_data_refs),
        ))
        if len(self.history) > 10:  # 最多保留 10 轮记录
            self.history = self.history[-10:]

    def has_converged(self) -> tuple[bool, str]:
        """判断是否收敛，返回 (是否收敛, 原因)。

        收敛条件（满足任一）：
        1. 本轮无任何新结论和数据引用
        2. 新信息比例 < min_new_info_ratio
        """
        if len(self.history) < 2:
            return False, ""

        latest = self.history[-1]
        prev = self.history[-2]

        # 条件1：本轮无产出
        total_latest = len(latest.conclusions) + len(latest.data_refs)
        if total_latest == 0:
            return True, "本轮无新结论和数据引用"

        # 条件2：新信息比例 < 阈值
        new_conclusions = latest.conclusions - prev.conclusions
        new_data = latest.data_refs - prev.data_refs
        new_count = len(new_conclusions) + len(new_data)

        new_ratio = new_count / max(total_latest, 1)
        if new_ratio < self.min_new_info_ratio:
            return True, f"新信息比例 {new_ratio:.0%} < 阈值 {self.min_new_info_ratio:.0%}"

        return False, ""

    # ── 专家调用去重 ──────────────────────────

    def should_skip_agent(self, agent_key: str, query: str) -> bool:
        """判断是否应该跳过该专家（已调用过相同或相似 query）。

        Args:
            agent_key: 专家标识
            query: 准备发给专家的问题

        Returns:
            True 表示应跳过（已调用过），False 表示可调用
        """
        if not query:
            return False
        normalized = _normalize_query(query)
        if not normalized:
            return False

        # 精确匹配
        key = (agent_key, normalized)
        if key in self.called_queries:
            logger.info(f"[convergence] 跳过 {agent_key}：query 完全匹配历史调用")
            return True

        # 相似度匹配
        for (ak, nq) in self.called_queries.keys():
            if ak == agent_key and _similarity(normalized, nq) > self.similarity_threshold:
                logger.info(f"[convergence] 跳过 {agent_key}：query 相似度 "
                            f"{_similarity(normalized, nq):.2f} > {self.similarity_threshold}")
                return True

        return False

    def record_call(self, agent_key: str, query: str) -> None:
        """记录一次专家调用。"""
        if not query:
            return
        normalized = _normalize_query(query)
        if normalized:
            self.called_queries[(agent_key, normalized)] = True

    # ── 工具方法 ──────────────────────────────

    @property
    def called_agent_count(self) -> int:
        """已调用的不同 agent 数量。"""
        return len({ak for (ak, _) in self.called_queries.keys()})

    @property
    def total_calls(self) -> int:
        """总调用次数。"""
        return len(self.called_queries)

    def reset(self) -> None:
        """重置检测器状态（用于新对话）。"""
        self.history.clear()
        self.called_queries.clear()


# ── 全局单例（单次对话内复用） ────────────────────

_global_detector: Optional[ConvergenceDetector] = None


def get_convergence_detector() -> ConvergenceDetector:
    """获取当前对话的收敛检测器实例。

    注意：每次新对话开始时应调用 reset_convergence_detector() 重置。
    """
    global _global_detector
    if _global_detector is None:
        _global_detector = ConvergenceDetector()
    return _global_detector


def reset_convergence_detector() -> None:
    """重置全局收敛检测器（新对话开始时调用）。"""
    global _global_detector
    _global_detector = None
