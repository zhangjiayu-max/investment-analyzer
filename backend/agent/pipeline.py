"""Pipeline 主控 — 6 阶段确定性流水线，替代隐式 ReAct 循环。

设计要点：
- run_pipeline() 是 generator，yield 流式事件
- 每个阶段有明确输入/输出、token 预算、终止条件
- 任何阶段失败自动降级到现有 ReAct 模式（config 控制）
- 简单闲聊走快速路径，不调专家
- 与 orchestrator.py 集成：作为新的主路径，ReAct 作为降级路径

阶段：
  Phase 0: 预处理（意图识别 + Query 改写 + 复杂度评估）
  Phase 1: 信息收集（RAG + 预取 + 记忆加载）
  Phase 2: 计划生成（选专家 + 排序 + 预算分配）
  Phase 3: 专家执行（并行 + 黑板 + 收敛检测）
  Phase 4: 综合（交叉审阅 + 仲裁 + 最终回答）
  Phase 5: 记忆持久化（结论 + 摘要 + 偏好）
"""

import json
import logging
import time
from typing import Optional, Any, Generator

from db.config import get_config_bool, get_config_int
from db.agents import create_agent_run

from agent.pipeline_state import PipelineState, PipelinePhase, create_initial_state
from agent.query_understander import (
    understand_query, is_simple_chat, get_complexity,
    needs_clarification, INTENT_FINANCIAL_QUERY,
)
from agent.blackboard import Blackboard, extract_entry_from_result, reset_global_blackboard
from agent.convergence import ConvergenceDetector, reset_convergence_detector
from agent.termination import TerminationChecker, get_token_budget_for_complexity
from agent.tool_dedup import reset_tool_call_cache
from agent.context_builder import build_specialist_context, build_simple_chat_context

logger = logging.getLogger(__name__)


# ── Pipeline 事件类型 ──────────────────────────

EVENT_PHASE_START = "phase_start"
EVENT_PHASE_END = "phase_end"
EVENT_CLARIFICATION = "clarification"
EVENT_SIMPLE_CHAT = "simple_chat"
EVENT_PLAN_GENERATED = "plan_generated"
EVENT_SPECIALIST_START = "specialist_start"
EVENT_SPECIALIST_DONE = "specialist_done"
EVENT_REFLECTION_DONE = "reflection_done"
EVENT_DEBATE_DONE = "debate_done"
EVENT_ANSWER = "answer"
EVENT_ERROR = "error"
EVENT_DEGRADE = "degrade"
EVENT_TERMINATED = "terminated"
EVENT_RECOMMENDATIONS = "recommendations"  # P0-A 决策闭环：建议落库后通知前端


# ── 合规过滤 + 基金代码风险标注 ──────────────────

# 违规表述正则（金融合规：禁止保本/稳赚承诺）
_COMPLIANCE_BLOCKLIST = [
    "保本", "稳赚", "稳赢", "必涨", "必跌", "零风险", "无风险",
    "guaranteed", "risk.?free", "sure.?win", "can.?not.?lose",
    "包赚", "绝对盈利", "稳赚不赔",
]
_COMPLIANCE_RE = __import__("re").compile(
    "|".join(_COMPLIANCE_BLOCKLIST), __import__("re").IGNORECASE
)


def _apply_compliance_and_warning(answer: str, trace_id: str) -> str:
    """Phase 4 综合答案输出前：合规过滤 + 基金代码风险标注。

    - 合规过滤：扫描违规表述（保本/稳赚等），命中则追加合规提示
    - 基金代码标注：含基金代码时追加核实提示
    - 均为追加文本，不修改原文
    """
    if not answer:
        return answer

    suffix = []

    # 合规过滤
    try:
        compliance_enabled = get_config_bool("pipeline.compliance_filter_enabled", True)
    except Exception:
        compliance_enabled = True
    if compliance_enabled:
        hits = _COMPLIANCE_RE.findall(answer)
        if hits:
            logger.warning(f"[pipeline:{trace_id}] 合规过滤命中违规表述: {hits}")
            suffix.append(
                "\n\n---\n⚠️ **合规提示**：本回复可能含有不合规表述（如保本/稳赚承诺），"
                "投资有风险，不存在保本承诺，请审慎判断。"
            )

    # 基金代码风险标注
    try:
        code_warning_enabled = get_config_bool("pipeline.fund_code_warning_enabled", True)
    except Exception:
        code_warning_enabled = True
    if code_warning_enabled:
        try:
            from agent.hallucination_guard import quick_check_fund_codes
            codes = quick_check_fund_codes(answer)
            if codes:
                logger.info(f"[pipeline:{trace_id}] 答案含基金代码 {len(codes)} 个，追加核实提示")
                suffix.append(
                    "\n\n---\n⚠️ **代码核实提示**：本回复含基金代码，"
                    "请以官方平台查询结果为准，注意代码与名称匹配核实。"
                )
        except Exception as e:
            logger.debug(f"[pipeline:{trace_id}] 基金代码标注跳过: {e}")

    if suffix:
        return answer + "".join(suffix)
    return answer


# ── 主入口 ──────────────────────────────────

def run_pipeline(
    query: str,
    history: list,
    conversation_id: int,
    message_id: int,
    trace_id: str,
    user_id: str = "default",
    cancel_event=None,
    resumed_state: Optional[dict] = None,
) -> Generator[dict, None, None]:
    """Pipeline 主入口 — 6 阶段确定性流水线。

    Args:
        query: 用户原始问题
        history: 对话历史
        conversation_id: 对话 ID
        message_id: 消息 ID
        trace_id: 追踪 ID
        user_id: 用户 ID
        cancel_event: 取消事件
        resumed_state: 恢复的 checkpoint 状态

    Yields:
        事件 dict，type 字段标识事件类型
    """
    start_time = time.time()

    # 重置全局状态（新对话开始）
    reset_convergence_detector()
    reset_tool_call_cache()
    reset_global_blackboard()

    # 初始化状态
    if resumed_state:
        state = PipelineState.from_dict(resumed_state)
        logger.info(f"[pipeline] 从 checkpoint 恢复，当前阶段: {state.phase.value}")
    else:
        state = create_initial_state(
            trace_id=trace_id,
            conversation_id=conversation_id,
            message_id=message_id,
            original_query=query,
            refined_query=query,  # Phase 0 会更新
            complexity="medium",  # Phase 0 会更新
        )

    # 初始化检查器
    checker = TerminationChecker(start_time=start_time)
    detector = ConvergenceDetector()
    blackboard = Blackboard()

    try:
        # ── Phase 0: 预处理 ──────────────────
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.PREPROCESS.value}
        phase0_result = _phase_preprocess(state, query, history, user_id, trace_id)
        state.set_phase_result(PipelinePhase.PREPROCESS.value, phase0_result)
        state.refined_query = phase0_result.get("refined_query", query)
        complexity = phase0_result.get("complexity", "medium")
        # 根据复杂度更新预算
        state.token_budget = get_token_budget_for_complexity(complexity)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.PREPROCESS.value,
               "result": phase0_result}

        # P0-2: 持久化 refined_query 供可观测性
        if state.refined_query != state.original_query:
            yield {
                "type": "query_refined",
                "original_query": state.original_query,
                "refined_query": state.refined_query,
                "rewrite_reason": phase0_result.get("rewrite_reason", ""),
            }

        # 简单闲聊快速路径
        query_info = phase0_result.get("query_info", {})
        if is_simple_chat(query_info):
            yield from _handle_simple_chat(query, state, trace_id)
            return

        # 需要澄清（交互式：yield checkpoint 供续答恢复）
        need_clarify, clarify_q = needs_clarification(query_info)
        if need_clarify:
            # 序列化当前状态作为 checkpoint（含原始 query + phase0 结果）
            checkpoint = state.to_dict()
            checkpoint["query_info"] = query_info  # 供续答时跳过 Phase 0
            yield {
                "type": EVENT_CLARIFICATION,
                "question": clarify_q,
                "reason": query_info.get("clarification_reason", ""),
                "options": query_info.get("clarification_options", []),
                "checkpoint": checkpoint,
            }
            state.answer = clarify_q
            state.transition_to(PipelinePhase.CANCELLED)
            return

        # ── Phase 1: 信息收集 ──────────────────
        state.transition_to(PipelinePhase.INFO_GATHER)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.INFO_GATHER.value}
        phase1_result = _phase_info_gather(state, query_info, history, trace_id)
        state.set_phase_result(PipelinePhase.INFO_GATHER.value, phase1_result)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.INFO_GATHER.value,
               "result": {"rag_count": len(phase1_result.get("rag", [])),
                          "has_portfolio": bool(phase1_result.get("portfolio"))}}

        # ── Phase 2: 计划生成 ──────────────────
        state.transition_to(PipelinePhase.PLANNING)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.PLANNING.value}
        phase2_result = _phase_planning(state, query_info, phase1_result, trace_id)
        state.set_phase_result(PipelinePhase.PLANNING.value, phase2_result)
        yield {"type": EVENT_PLAN_GENERATED, "plan": phase2_result}
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.PLANNING.value}

        # ── Phase 3: 专家执行 ──────────────────
        state.transition_to(PipelinePhase.EXECUTION)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.EXECUTION.value}
        # _phase_execution 是 generator，yield specialist_start/done 事件
        exec_gen = _phase_execution(
            state, phase2_result, phase1_result, query_info,
            detector, blackboard, checker, cancel_event, trace_id,
        )
        phase3_result = None
        while True:
            try:
                evt = next(exec_gen)
                yield evt  # 转发 specialist_start/done 事件
            except StopIteration as si:
                phase3_result = si.value
                break
        if phase3_result is None:
            phase3_result = {"specialists": [], "tool_calls": []}
        state.set_phase_result(PipelinePhase.EXECUTION.value, phase3_result)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.EXECUTION.value,
               "result": {"specialist_count": len(phase3_result.get("specialists", []))}}

        # ── Phase 3.5: 反思（默认开启）──────────────
        # Reflection 节点 — 自评质量问题 + 冲突识别 + 自纠错重跑
        reflection_result = None
        try:
            reflection_enabled = get_config_bool("pipeline.reflection_enabled", True)
        except Exception:
            reflection_enabled = True

        if reflection_enabled and phase3_result.get("specialists"):
            state.transition_to(PipelinePhase.REFLECTION)
            yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.REFLECTION.value}
            try:
                reflection_result = _phase_reflection(
                    state, query, phase3_result, blackboard, trace_id
                )
                state.set_phase_result(PipelinePhase.REFLECTION.value, reflection_result)
                yield {"type": EVENT_REFLECTION_DONE, "result": reflection_result}

                # 自纠错循环：低置信度时重跑质量最差的专家
                rerun_result = _maybe_rerun_specialist(
                    state, query, phase3_result, reflection_result,
                    blackboard, trace_id,
                )
                if rerun_result:
                    # 重跑成功：更新 phase3_result 的对应专家分析
                    phase3_result["specialists"] = _replace_specialist_analysis(
                        phase3_result["specialists"], rerun_result
                    )
                    state.set_phase_result(PipelinePhase.EXECUTION.value, phase3_result)
                    yield {"type": EVENT_SPECIALIST_DONE,
                           "agent": rerun_result.get("agent", ""),
                           "result": rerun_result,
                           "rerun": True}
            except Exception as refl_err:
                logger.warning(f"[pipeline] Reflection 失败，跳过: {refl_err}")
                reflection_result = None
            yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.REFLECTION.value}

        # ── Phase 3.7: 对抗式辩论（P1，冲突时触发）──────────────
        debate_result = None
        try:
            debate_enabled = get_config_bool("pipeline.debate_enabled", True)
        except Exception:
            debate_enabled = True

        if debate_enabled and phase3_result.get("specialists"):
            conflicts = blackboard.find_conflicts() if blackboard else []
            if conflicts:
                logger.info(f"[pipeline] 检测到 {len(conflicts)} 个冲突，触发对抗式辩论")
                state.transition_to(PipelinePhase.DEBATE)
                yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.DEBATE.value}
                try:
                    debate_result = _phase_debate(
                        state, query, phase3_result, blackboard, trace_id
                    )
                    state.set_phase_result(PipelinePhase.DEBATE.value, debate_result)
                    yield {"type": EVENT_DEBATE_DONE, "result": debate_result}
                except Exception as debate_err:
                    logger.warning(f"[pipeline] 辩论失败，跳过: {debate_err}")
                    debate_result = None
                yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.DEBATE.value}
            else:
                logger.debug("[pipeline] 无冲突，跳过辩论节点")

        # ── Phase 4: 综合 ──────────────────
        state.transition_to(PipelinePhase.SYNTHESIS)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.SYNTHESIS.value}

        # 关键防护：专家执行结果为空 → 主动降级到 ReAct，不返回占位文本
        # 这样 orchestrator 会走 ReAct 路径，前端能看到"重新生成"按钮
        phase3_specialists = phase3_result.get("specialists", [])
        if not phase3_specialists:
            logger.warning(f"[pipeline] Phase 3 无专家结果，降级到 ReAct")
            yield {"type": EVENT_DEGRADE,
                   "reason": "no_specialist_results",
                   "message": "Pipeline 未产出专家分析，降级到标准模式"}
            state.transition_to(PipelinePhase.FAILED)
            state.error = "no_specialist_results"
            return

        phase4_result = _phase_synthesis(
            state, query, phase3_result, blackboard, trace_id,
            reflection_result=reflection_result,
            debate_result=debate_result,
        )
        state.set_phase_result(PipelinePhase.SYNTHESIS.value, phase4_result)
        state.answer = phase4_result.get("answer", "")

        # 二次防护：综合结果为空或为占位文本 → 降级
        if not state.answer or state.answer == "无法生成分析":
            logger.warning(f"[pipeline] Phase 4 综合结果为空/占位，降级到 ReAct")
            yield {"type": EVENT_DEGRADE,
                   "reason": "empty_synthesis",
                   "message": "Pipeline 综合失败，降级到标准模式"}
            state.transition_to(PipelinePhase.FAILED)
            state.error = "empty_synthesis"
            return

        # 合规过滤 + 基金代码风险标注（EVENT_ANSWER 前）
        state.answer = _apply_compliance_and_warning(state.answer, trace_id)

        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.SYNTHESIS.value}
        yield {"type": EVENT_ANSWER, "content": state.answer,
               "specialist_results": phase3_specialists,
               "tool_calls": phase3_result.get("tool_calls", []),
               "confidence": phase4_result.get("confidence", 0.0)}

        # ── Phase 5: 记忆持久化 ──────────────────
        state.transition_to(PipelinePhase.MEMORY)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.MEMORY.value}
        phase5_result = _phase_memory(state, phase4_result, query_info, trace_id, phase1_result)
        state.set_phase_result(PipelinePhase.MEMORY.value, phase5_result)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.MEMORY.value}

        # P0-A 决策闭环：建议落库后通知前端渲染建议卡片
        rec_data = phase5_result.get("recommendations") or {}
        if rec_data.get("enabled") and rec_data.get("recommendations"):
            yield {
                "type": EVENT_RECOMMENDATIONS,
                "recommendations": rec_data["recommendations"],
                "recommendation_ids": rec_data.get("recommendation_ids", []),
                "conversation_id": state.conversation_id,
            }

        state.transition_to(PipelinePhase.COMPLETED)

    except _PipelineCancelled:
        state.transition_to(PipelinePhase.CANCELLED)
        yield {"type": EVENT_TERMINATED, "reason": "用户取消"}
    except Exception as e:
        logger.exception(f"[pipeline] 流水线异常: {e}")
        state.transition_to(PipelinePhase.FAILED)
        state.error = str(e)
        yield {"type": EVENT_ERROR, "error": str(e),
               "message": "Pipeline 执行失败，请重试或联系管理员"}


def run_pipeline_from_checkpoint(
    checkpoint: dict,
    user_answer: str,
    history: list,
    trace_id: str,
    cancel_event=None,
) -> Generator[dict, None, None]:
    """从澄清 checkpoint 恢复 Pipeline，用用户回答改写 query 后继续执行。

    跳过 Phase 0 的澄清检查，直接从 Phase 1（信息收集）开始。
    query 改写：original_query + user_answer 拼接。
    """
    start_time = time.time()

    # 恢复状态
    state = PipelineState.from_dict(checkpoint)
    query_info = checkpoint.get("query_info", {})
    original_query = state.original_query

    # 用回答融合 query（LLM 语义融合，失败降级为拼接）
    from agent.query_rewriter import fuse_clarified_query
    state.refined_query = fuse_clarified_query(original_query, user_answer, trace_id)
    logger.info(
        f"[pipeline:{trace_id}] 澄清续答恢复: query='{original_query}' → '{state.refined_query}', "
        f"answer='{user_answer[:50]}'"
    )

    # 重置全局状态
    reset_convergence_detector()
    reset_tool_call_cache()
    reset_global_blackboard()

    checker = TerminationChecker(start_time=start_time)
    detector = ConvergenceDetector()
    blackboard = Blackboard()

    try:
        # 直接进入 Phase 1（跳过 Phase 0 澄清检查）
        state.transition_to(PipelinePhase.INFO_GATHER)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.INFO_GATHER.value}
        phase1_result = _phase_info_gather(state, query_info, history, trace_id)
        state.set_phase_result(PipelinePhase.INFO_GATHER.value, phase1_result)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.INFO_GATHER.value,
               "result": {"rag_count": len(phase1_result.get("rag", [])),
                          "has_portfolio": bool(phase1_result.get("portfolio"))}}

        # Phase 2: 计划生成
        state.transition_to(PipelinePhase.PLANNING)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.PLANNING.value}
        phase2_result = _phase_planning(state, query_info, phase1_result, trace_id)
        state.set_phase_result(PipelinePhase.PLANNING.value, phase2_result)
        yield {"type": EVENT_PLAN_GENERATED, "plan": phase2_result}
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.PLANNING.value}

        # Phase 3: 专家执行
        state.transition_to(PipelinePhase.EXECUTION)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.EXECUTION.value}
        exec_gen = _phase_execution(
            state, phase2_result, phase1_result, query_info,
            detector, blackboard, checker, cancel_event, trace_id,
        )
        phase3_result = None
        while True:
            try:
                evt = next(exec_gen)
                yield evt
            except StopIteration as si:
                phase3_result = si.value
                break
        if phase3_result is None:
            phase3_result = {"specialists": [], "tool_calls": []}
        state.set_phase_result(PipelinePhase.EXECUTION.value, phase3_result)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.EXECUTION.value,
               "result": {"specialist_count": len(phase3_result.get("specialists", []))}}

        # ── Phase 3.5: 反思（与主路径保持一致）──────────────
        reflection_result = None
        try:
            reflection_enabled = get_config_bool("pipeline.reflection_enabled", True)
        except Exception:
            reflection_enabled = True

        if reflection_enabled and phase3_result.get("specialists"):
            state.transition_to(PipelinePhase.REFLECTION)
            yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.REFLECTION.value}
            try:
                reflection_result = _phase_reflection(
                    state, state.refined_query, phase3_result, blackboard, trace_id
                )
                state.set_phase_result(PipelinePhase.REFLECTION.value, reflection_result)
                yield {"type": EVENT_REFLECTION_DONE, "result": reflection_result}

                rerun_result = _maybe_rerun_specialist(
                    state, state.refined_query, phase3_result, reflection_result,
                    blackboard, trace_id,
                )
                if rerun_result:
                    phase3_result["specialists"] = _replace_specialist_analysis(
                        phase3_result["specialists"], rerun_result
                    )
                    state.set_phase_result(PipelinePhase.EXECUTION.value, phase3_result)
                    yield {"type": EVENT_SPECIALIST_DONE,
                           "agent": rerun_result.get("agent", ""),
                           "result": rerun_result,
                           "rerun": True}
            except Exception as refl_err:
                logger.warning(f"[pipeline:{trace_id}] 续答 Reflection 失败，跳过: {refl_err}")
                reflection_result = None
            yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.REFLECTION.value}

        # ── Phase 3.7: 对抗式辩论（冲突时触发）──────────────
        debate_result = None
        try:
            debate_enabled = get_config_bool("pipeline.debate_enabled", True)
        except Exception:
            debate_enabled = True

        if debate_enabled and phase3_result.get("specialists"):
            conflicts = blackboard.find_conflicts() if blackboard else []
            if conflicts:
                logger.info(f"[pipeline:{trace_id}] 续答检测到 {len(conflicts)} 个冲突，触发辩论")
                state.transition_to(PipelinePhase.DEBATE)
                yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.DEBATE.value}
                try:
                    debate_result = _phase_debate(
                        state, state.refined_query, phase3_result, blackboard, trace_id
                    )
                    state.set_phase_result(PipelinePhase.DEBATE.value, debate_result)
                    yield {"type": EVENT_DEBATE_DONE, "result": debate_result}
                except Exception as debate_err:
                    logger.warning(f"[pipeline:{trace_id}] 续答辩论失败，跳过: {debate_err}")
                    debate_result = None
                yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.DEBATE.value}

        # Phase 4: 综合
        state.transition_to(PipelinePhase.SYNTHESIS)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.SYNTHESIS.value}
        phase3_specialists = phase3_result.get("specialists", [])
        if not phase3_specialists:
            yield {"type": EVENT_DEGRADE, "reason": "no_specialist_results",
                   "message": "Pipeline 未产出专家分析，降级到标准模式"}
            state.transition_to(PipelinePhase.FAILED)
            state.error = "no_specialist_results"
            return

        phase4_result = _phase_synthesis(
            state, state.refined_query, phase3_result, blackboard, trace_id,
            reflection_result=reflection_result,
            debate_result=debate_result,
        )
        state.set_phase_result(PipelinePhase.SYNTHESIS.value, phase4_result)
        state.answer = phase4_result.get("answer", "")

        if not state.answer or state.answer == "无法生成分析":
            yield {"type": EVENT_DEGRADE, "reason": "empty_synthesis",
                   "message": "Pipeline 综合失败，降级到标准模式"}
            state.transition_to(PipelinePhase.FAILED)
            state.error = "empty_synthesis"
            return

        state.answer = _apply_compliance_and_warning(state.answer, trace_id)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.SYNTHESIS.value}
        yield {"type": EVENT_ANSWER, "content": state.answer,
               "specialist_results": phase3_specialists,
               "tool_calls": phase3_result.get("tool_calls", []),
               "confidence": phase4_result.get("confidence", 0.0)}

        # Phase 5: 记忆持久化
        state.transition_to(PipelinePhase.MEMORY)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.MEMORY.value}
        phase5_result = _phase_memory(state, phase4_result, query_info, trace_id, phase1_result)
        state.set_phase_result(PipelinePhase.MEMORY.value, phase5_result)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.MEMORY.value}

        # P0-A 决策闭环：建议落库后通知前端渲染建议卡片
        rec_data = phase5_result.get("recommendations") or {}
        if rec_data.get("enabled") and rec_data.get("recommendations"):
            yield {
                "type": EVENT_RECOMMENDATIONS,
                "recommendations": rec_data["recommendations"],
                "recommendation_ids": rec_data.get("recommendation_ids", []),
                "conversation_id": state.conversation_id,
            }

        state.transition_to(PipelinePhase.COMPLETED)

    except _PipelineCancelled:
        state.transition_to(PipelinePhase.CANCELLED)
        yield {"type": EVENT_TERMINATED, "reason": "用户取消"}
    except Exception as e:
        logger.exception(f"[pipeline] 续答恢复异常: {e}")
        state.transition_to(PipelinePhase.FAILED)
        state.error = str(e)
        yield {"type": EVENT_ERROR, "error": str(e),
               "message": "续答恢复失败，请重试"}


# ── Phase 0: 预处理 ──────────────────────────

def _phase_preprocess(
    state: PipelineState,
    query: str,
    history: list,
    user_id: str,
    trace_id: str,
) -> dict:
    """Phase 0: 意图识别 + Query 改写 + 复杂度评估。"""
    from agent.memory import build_user_memory_context

    # 1. Query 理解
    history_summary = _summarize_history_for_understanding(history)
    portfolio_summary = build_user_memory_context(user_id)[:500] if user_id else ""
    query_info = understand_query(
        query, history_summary, portfolio_summary, trace_id
    )

    # 2. Query 改写（复用现有 query_rewriter）
    refined_query = query
    rewrite_reason = ""
    try:
        from agent.query_rewriter import rewrite_query, needs_rewrite
        need_rewrite, reason = needs_rewrite(query)
        if need_rewrite:
            # rewrite_query 返回 (query, meta_dict)，只取 query 部分
            rewrite_result = rewrite_query(query, history)
            if isinstance(rewrite_result, tuple):
                refined_query = rewrite_result[0]
            else:
                refined_query = rewrite_result
            rewrite_reason = reason
            logger.info(f"[pipeline] Query 改写: '{query}' → '{refined_query}'")
    except Exception as e:
        logger.debug(f"[pipeline] Query 改写跳过: {e}")

    # 3. 复杂度（优先用 query_understander 的结果）
    complexity = query_info.get("complexity", "medium")

    return {
        "query_info": query_info,
        "refined_query": refined_query,
        "rewrite_reason": rewrite_reason,
        "complexity": complexity,
        "intent": query_info.get("intent", ""),
        "targets": query_info.get("targets", []),
        "needed_info": query_info.get("needed_info", []),
    }


def _summarize_history_for_understanding(history: list) -> str:
    """为 Query 理解生成简短的历史摘要。"""
    if not history:
        return ""
    # 取最近 3 轮
    recent = history[-6:]
    parts = []
    for msg in recent:
        role = msg.get("role", "")
        content = (msg.get("content", "") or "")[:100]
        if not content:
            continue
        if role == "user":
            parts.append(f"用户: {content}")
        elif role == "assistant":
            parts.append(f"助手: {content[:80]}")
    return " | ".join(parts[-4:])  # 最多 4 条


# ── Phase 1: 信息收集 ──────────────────────────

def _phase_info_gather(
    state: PipelineState,
    query_info: dict,
    history: list,
    trace_id: str,
) -> dict:
    """Phase 1: RAG 检索 + 持仓预取 + 估值预取 + 记忆加载。"""
    results = {
        "rag": [],
        "rag_context": "",
        "portfolio": "",
        "valuations": {},
        "memory": "",
        "info_gaps": [],
    }

    # 并行执行 RAG 检索 + 持仓 + 估值 + 记忆（省 3-5 秒串行等待）
    from concurrent.futures import ThreadPoolExecutor

    def _rag_task():
        """RAG 检索（子查询展开 + intent 驱动 content_types + rag_logs 日志）"""
        try:
            from services.rag import build_rag_context_with_details, log_rag_search
            enhanced_query = _enhance_rag_query(state.refined_query, query_info)
            content_types = _map_content_types(query_info.get("needed_info", []))

            # 子查询展开（开关控制）
            sub_queries = [enhanced_query]
            try:
                sub_expansion_enabled = get_config_bool("rag.subquery_expansion_enabled", True)
            except Exception:
                sub_expansion_enabled = True
            if sub_expansion_enabled:
                try:
                    from agent.query_rewriter import expand_query
                    expanded = expand_query(enhanced_query)
                    if expanded and len(expanded) > 1:
                        sub_queries = expanded[:4]  # 最多 4 个子查询
                        logger.info(f"[pipeline] 子查询展开: {len(sub_queries)} 个 → {sub_queries}")
                except Exception as e:
                    logger.debug(f"[pipeline] 子查询展开跳过: {e}")

            # 多路检索
            all_rag_results = []
            rag_keywords = []
            total_fts = 0
            total_chroma = 0
            for sq in sub_queries:
                rag_data = build_rag_context_with_details(sq, limit=5, content_types=content_types)
                if isinstance(rag_data, dict):
                    all_rag_results.extend(rag_data.get("results", []))
                    rag_keywords = rag_keywords or rag_data.get("keywords", [])
                    total_fts += rag_data.get("fts_count", 0)
                    total_chroma += rag_data.get("chroma_count", 0)

            # 去重（按 content_type + reference_id 二元组，与 rag.py 内部去重一致）
            seen = set()
            deduped = []
            for r in all_rag_results:
                key = f"{r.get('content_type','')}:{r.get('reference_id','')}"
                if key not in seen:
                    seen.add(key)
                    deduped.append(r)

            # 按分数排序
            deduped.sort(key=lambda x: float(x.get("_score", 0)), reverse=True)

            # 构建上下文
            if sub_queries and len(sub_queries) == 1:
                rag_context = rag_data.get("context", "") if isinstance(rag_data, dict) else ""
            else:
                rag_context = _build_rag_context_from_results(deduped[:8])

            logger.info(
                f"[pipeline] RAG 检索完成: {len(deduped)} 条结果 "
                f"(子查询 {len(sub_queries)}, FTS {total_fts}, Chroma {total_chroma})"
            )

            # RAG 命中质量检测
            max_score = max((float(r.get("_score", 0)) for r in deduped), default=0.0)
            rag_low_quality = False
            if deduped and max_score < 0.05:
                rag_low_quality = True
                logger.warning(
                    f"[pipeline] RAG 命中质量低: max_score={max_score:.4f} < 0.05, "
                    f"将提示专家基于工具数据分析"
                )
            elif not deduped:
                rag_low_quality = True
                logger.warning("[pipeline] RAG 无命中，将提示专家基于工具数据分析")

            if rag_low_quality:
                low_quality_hint = (
                    "\n\n⚠️ 注意：知识库未检索到高相关内容（命中分数偏低），"
                    "请优先通过工具调用获取最新新闻/行情/持仓数据进行分析，"
                    "不要过度依赖上述知识库片段。"
                )
                rag_context = (rag_context or "") + low_quality_hint

            # 写 rag_logs 日志
            try:
                log_rag_search(
                    conversation_id=state.conversation_id,
                    message_id=state.message_id,
                    query=enhanced_query,
                    keywords=rag_keywords,
                    results=deduped[:10],
                    content_types=content_types,
                    fts_count=total_fts,
                    chroma_count=total_chroma,
                    trace_id=trace_id,
                    rag_low_quality=rag_low_quality,
                )
            except Exception as log_err:
                logger.debug(f"[pipeline] rag_logs 写入失败: {log_err}")

            return {"rag": deduped, "rag_context": rag_context, "rag_low_quality": rag_low_quality}
        except Exception as e:
            logger.warning(f"[pipeline] RAG 检索失败: {e}")
            return {"rag": [], "rag_context": "", "rag_low_quality": False}

    def _portfolio_task():
        """持仓摘要（含完整盈亏+交易记录+集中度）"""
        try:
            from services.portfolio_context import build_portfolio_context
            return build_portfolio_context("default")
        except Exception as e:
            logger.debug(f"[pipeline] 持仓加载失败: {e}")
            return ""

    def _valuation_task():
        """估值预取（基于 targets）"""
        valuations = {}
        targets = query_info.get("targets", [])
        if targets:
            for target in targets[:3]:
                val = _lookup_valuation(target)
                if val:
                    valuations[target] = val
        return valuations

    def _memory_task():
        """用户记忆"""
        try:
            from agent.memory import build_user_memory_context
            return build_user_memory_context("default")[:500]
        except Exception as e:
            logger.debug(f"[pipeline] 记忆加载失败: {e}")
            return ""

    with ThreadPoolExecutor(max_workers=4) as pool:
        rag_future = pool.submit(_rag_task)
        portfolio_future = pool.submit(_portfolio_task)
        valuation_future = pool.submit(_valuation_task)
        memory_future = pool.submit(_memory_task)

        rag_result = rag_future.result()
        results["rag"] = rag_result["rag"]
        results["rag_context"] = rag_result["rag_context"]
        if rag_result.get("rag_low_quality"):
            results["rag_low_quality"] = True
        results["portfolio"] = portfolio_future.result()
        results["valuations"] = valuation_future.result()
        results["memory"] = memory_future.result()

    # 5. 信息缺口检测
    needed_info = set(query_info.get("needed_info", []))
    if "valuation" in needed_info and not results["valuations"]:
        results["info_gaps"].append("valuation_data_missing")
    if "portfolio" in needed_info and not results["portfolio"]:
        results["info_gaps"].append("portfolio_data_missing")

    return results


def _enhance_rag_query(refined_query: str, query_info: dict) -> str:
    """增强 RAG 查询：加入 targets 和 needed_info 关键词。"""
    targets = query_info.get("targets", [])
    if not targets:
        return refined_query
    # 简单拼接 targets
    return f"{refined_query} {' '.join(targets)}"


# intent → content_types 映射（用于 intent 驱动检索）
_INTENT_TO_CONTENT_TYPES = {
    "valuation": ["valuation", "analysis"],
    "portfolio": ["analysis", "article"],  # P4: 移除 book（投资书籍摘录与持仓分析相关性差）
    "risk": ["analysis", "article"],
    "strategy": ["analysis", "article"],   # P4: 移除 book（策略类问题优先分析文章）
    "article": ["article", "author_article"],
    "text": ["article", "author_article"],  # 补全 text 意图映射，避免纯文章解读类问题全类型检索
}


def _map_content_types(needed_info: list) -> Optional[list]:
    """根据 needed_info 映射到检索 content_types。

    Returns:
        content_types 列表，或 None（表示全类型检索）
    """
    try:
        enabled = get_config_bool("rag.intent_driven_types_enabled", True)
    except Exception:
        enabled = True
    if not enabled or not needed_info:
        return None
    types = set()
    for info in needed_info:
        types.update(_INTENT_TO_CONTENT_TYPES.get(info, []))
    return list(types) if types else None


def _build_rag_context_from_results(results: list, max_chars: int = 3000) -> str:
    """从检索结果列表构建上下文文本（多子查询场景）。

    Phase C: 补相关度标签，让专家区分高分精确命中与边缘命中。
    阈值基于实测分数分布（0.014-0.125）：高>=0.08, 中>=0.03, 低<0.03
    """
    if not results:
        return ""
    parts = []
    total = 0
    for r in results:
        label = r.get("label", r.get("content_type", ""))
        title = r.get("title", "")
        body = (r.get("body", "") or "")[:600]
        source = r.get("source", "")
        # 相关度标签（基于 _score 字段）
        score = float(r.get("_score", 0))
        if score >= 0.08:
            relevance_tag = "相关度:高"
        elif score >= 0.03:
            relevance_tag = "相关度:中"
        else:
            relevance_tag = "相关度:低"
        part = f"[{label}] [{relevance_tag}] {title}"
        if source:
            part += f" [来源: {source}]"
        part += f"\n{body}"
        parts.append(part)
        total += len(part)
        if total >= max_chars:
            break
    return "\n\n---\n\n".join(parts)


def _lookup_valuation(target: str) -> Optional[dict]:
    """查询标的估值数据（从 index_valuations 表）。"""
    try:
        from db.valuation import get_latest_valuation
        # 尝试按名称查
        return get_latest_valuation(target)
    except Exception:
        pass
    try:
        from db.valuation import get_latest_valuation_by_code
        # 尝试按代码查
        if target.isdigit():
            return get_latest_valuation_by_code(target)
    except Exception:
        pass
    return None


# ── Phase 2: 计划生成 ──────────────────────────

def _phase_planning(
    state: PipelineState,
    query_info: dict,
    info_gather_result: dict,
    trace_id: str,
) -> dict:
    """Phase 2: 调用 plan_executor 生成分析计划。

    P4: 优先使用路由命中的专家列表作为 hint，避免 plan_generator LLM 覆盖路由结果。
    """
    try:
        from agent.plan_executor import generate_plan
        from db.agents import load_specialist_agents
        from agent.router import SmartRouter

        # 加载可用专家（load_specialist_agents 返回 dict，需转为 list）
        specialists_dict = load_specialist_agents()
        if not specialists_dict:
            return {"plan": None, "specialists": [], "fallback": True}

        # 转为 generate_plan 期望的 list 格式
        specialists = [
            {"agent_key": k, **v}
            for k, v in specialists_dict.items()
        ]

        # P4: 先调用 SmartRouter 获取路由建议
        routed_specialists = None
        try:
            router = SmartRouter()
            history_summary = info_gather_result.get("history_summary", "")
            portfolio_summary = info_gather_result.get("portfolio", "")
            route_result = router.route(state.refined_query, history_summary, portfolio_summary)
            routed_specialists = route_result.get("specialists", [])
            route_by = route_result.get("route_by", "unknown")
            logger.info(f"[pipeline] P4 路由命中: {routed_specialists} (by={route_by})")
        except Exception as route_err:
            logger.warning(f"[pipeline] 路由失败，降级到 plan_generator: {route_err}")

        # 调用 Plan 生成（传入 routed_specialists 作为 hint）
        plan = generate_plan(
            user_query=state.original_query,
            refined_query=state.refined_query,
            complexity=query_info.get("complexity", "medium"),
            available_specialists=specialists,
            trace_id=trace_id,
            routed_specialists=routed_specialists,  # P4: 路由 hint
            portfolio_summary=info_gather_result.get("portfolio", ""),  # P3: 持仓感知
        )
        return {
            "plan": plan.to_dict(),
            "specialists": specialists,
            "fallback": False,
        }
    except Exception as e:
        logger.warning(f"[pipeline] Plan 生成失败，降级全量执行: {e}")
        # 降级：仍尝试加载专家，供 Phase 3 fallback 使用
        try:
            specialists_dict = load_specialist_agents()
            specialists = [
                {"agent_key": k, **v}
                for k, v in (specialists_dict or {}).items()
            ]
        except Exception:
            specialists = []
        return {"plan": None, "specialists": specialists, "fallback": True, "error": str(e)}


# ── Phase 3: 专家执行 ──────────────────────────

def _phase_execution(
    state: PipelineState,
    plan_result: dict,
    info_gather_result: dict,
    query_info: dict,
    detector: ConvergenceDetector,
    blackboard: Blackboard,
    checker: TerminationChecker,
    cancel_event,
    trace_id: str,
):
    """Phase 3: 按计划执行专家，并行 + 黑板 + 收敛检测。

    Generator：yield specialist_start/done 事件，return 结果 dict。
    """
    from agent.multi_agent import run_specialist

    specialists_result = []
    all_tool_calls = []

    # 获取计划步骤
    plan_dict = plan_result.get("plan")
    if not plan_dict:
        # 降级：直接用第一个可用专家
        result = yield from _fallback_execution(
            state, plan_result, info_gather_result,
            detector, blackboard, trace_id, cancel_event,
        )
        return result

    steps = plan_dict.get("steps", [])
    if not steps:
        return {"specialists": [], "tool_calls": []}

    # 准备共享上下文
    rag_context = info_gather_result.get("rag_context", "")
    portfolio = info_gather_result.get("portfolio", "")
    valuations = info_gather_result.get("valuations", {})
    rag_low_quality = bool(info_gather_result.get("rag_low_quality", False))

    # P2: 并行执行路径 — 无 depends_on 且步骤 >= 2 时启用
    # 配置开关 pipeline.parallel_execution 默认 false，验证通过后再启用
    try:
        parallel_enabled = get_config_bool("pipeline.parallel_execution", False)
    except Exception:
        parallel_enabled = False

    if (parallel_enabled
            and len(steps) >= 2
            and not any(s.get("depends_on") for s in steps)):
        result = yield from _execute_steps_parallel(
            state, steps, plan_result, info_gather_result, query_info,
            detector, blackboard, checker, cancel_event, trace_id,
        )
        return result

    # 按 depends_on 分组执行（原顺序路径）
    completed_step_ids = set()
    for step in steps:
        # 检查取消
        if cancel_event and cancel_event.is_set():
            raise _PipelineCancelled()

        # 检查终止条件
        should_stop, reason = checker.check(
            tokens_used=state.tokens_used,
            token_budget=state.token_budget,
            called_agent_count=len(state.called_agents),
            convergence_detector=detector,
            current_phase=PipelinePhase.EXECUTION.value,
        )
        if should_stop:
            logger.info(f"[pipeline] 执行终止: {reason}")
            break

        agent_key = step.get("agent_key")
        agent_name = step.get("agent_name", agent_key)
        agent_icon = "🤖"
        step_query = step.get("query", state.refined_query)

        # 收敛检测：跳过已调用的相似 query
        if detector.should_skip_agent(agent_key, step_query):
            logger.info(f"[pipeline] 跳过 {agent_name}：已调用过相似 query")
            continue

        # 构建专家配置
        agent_config = _find_agent_config(agent_key, plan_result.get("specialists", []))
        if not agent_config:
            logger.warning(f"[pipeline] 找不到专家配置: {agent_key}")
            continue
        agent_icon = agent_config.get("icon", "🤖")

        # 构建上下文（含黑板）
        ctx = build_specialist_context(
            agent=agent_config,
            history=[],  # 专家内部自己管理历史
            conversation_id=state.conversation_id,
            rag_context=rag_context,
            portfolio_summary=portfolio,
            valuation_data=valuations,
            blackboard=blackboard,
            complexity=query_info.get("complexity", "medium"),
            rag_low_quality=rag_low_quality,
        )

        # 记录调用
        detector.record_call(agent_key, step_query)
        state.called_agents.add(agent_key)

        # 发送 specialist_start 事件
        yield {
            "type": EVENT_SPECIALIST_START,
            "agent_key": agent_key,
            "agent": agent_name,
            "icon": agent_icon,
        }

        # 执行专家
        step_start = time.time()
        try:
            result = run_specialist(
                agent_key=agent_key,
                query=step_query,
                prebuilt_context=ctx,
                trace_id=trace_id,
                from_pipeline=True,
                conversation_id=state.conversation_id,
                message_id=state.message_id,
            )
            specialists_result.append(result)
            all_tool_calls.extend(result.get("tool_calls", []))

            # 写入黑板
            entry = extract_entry_from_result(
                agent_key=agent_key,
                agent_name=agent_name,
                result=result,
                tokens_used=result.get("tokens_used", 0),
            )
            blackboard.write(entry)

            # 记录成功
            checker.record_success()

            # 记录 token
            tokens = result.get("tokens_used", 0)
            state.record_phase_tokens(PipelinePhase.EXECUTION.value, tokens)

            # P0: 写入 agent_runs（可观测性修复）— 失败不影响主流程
            # P5: 优先使用 result 自带的 duration_ms（run_specialist 内部精确计时）
            duration_ms_for_log = result.get("duration_ms") or int((time.time() - step_start) * 1000)
            try:
                create_agent_run(
                    conversation_id=state.conversation_id,
                    message_id=state.message_id,
                    agent_key=agent_key,
                    agent_name=agent_name,
                    query=step_query,
                    result=result.get("analysis", "")[:4000],
                    tool_calls=str(result.get("tool_calls", []))[:2000],
                    duration_ms=duration_ms_for_log,
                    trace_id=trace_id,
                    status="success",
                )
            except Exception as log_err:
                logger.warning(f"[pipeline] agent_runs 写入失败 ({agent_key}): {log_err}")

            # 发送 specialist_done 事件
            duration_ms = int((time.time() - step_start) * 1000)
            yield {
                "type": EVENT_SPECIALIST_DONE,
                "agent_key": agent_key,
                "agent": agent_name,
                "icon": agent_icon,
                "analysis": result.get("analysis", ""),
                "status": result.get("status", "success"),
                "error": result.get("status") == "failed",
                "duration_ms": duration_ms,
            }

        except Exception as e:
            logger.error(f"[pipeline] 专家 {agent_name} 执行失败: {e}")
            checker.record_failure(str(e))
            # P0: 失败也写入 agent_runs
            duration_ms_for_log = int((time.time() - step_start) * 1000)
            try:
                create_agent_run(
                    conversation_id=state.conversation_id,
                    message_id=state.message_id,
                    agent_key=agent_key,
                    agent_name=agent_name,
                    query=step_query,
                    result=f"（执行失败：{e}）",
                    duration_ms=duration_ms_for_log,
                    trace_id=trace_id,
                    status="error",
                )
            except Exception as log_err:
                logger.warning(f"[pipeline] agent_runs 写入失败 ({agent_key}): {log_err}")
            # 发送失败的 specialist_done 事件
            duration_ms = int((time.time() - step_start) * 1000)
            yield {
                "type": EVENT_SPECIALIST_DONE,
                "agent_key": agent_key,
                "agent": agent_name,
                "icon": agent_icon,
                "analysis": f"（执行失败：{e}）",
                "status": "failed",
                "error": True,
                "duration_ms": duration_ms,
            }
            continue

        completed_step_ids.add(step.get("step_id"))

    return {
        "specialists": specialists_result,
        "tool_calls": all_tool_calls,
    }


def _execute_steps_parallel(
    state: PipelineState,
    steps: list,
    plan_result: dict,
    info_gather_result: dict,
    query_info: dict,
    detector,
    blackboard: Blackboard,
    checker,
    cancel_event,
    trace_id: str,
):
    """P2: 并行执行所有步骤（无 depends_on 时）。

    Generator：yield specialist_start/done 事件，return 结果 dict。
    策略：先批量 yield specialist_start，再用 ThreadPoolExecutor 并行执行，
    最后按完成顺序 yield specialist_done + 写黑板 + agent_runs。
    """
    import concurrent.futures
    from agent.multi_agent import run_specialist
    from agent.context_builder import build_specialist_context

    rag_context = info_gather_result.get("rag_context", "")
    portfolio = info_gather_result.get("portfolio", "")
    valuations = info_gather_result.get("valuations", {})
    rag_low_quality = bool(info_gather_result.get("rag_low_quality", False))

    # 过滤可执行的步骤（收敛检测 + 终止检查）
    executable_steps = []
    for step in steps:
        if cancel_event and cancel_event.is_set():
            raise _PipelineCancelled()
        agent_key = step.get("agent_key")
        agent_name = step.get("agent_name", agent_key)
        step_query = step.get("query", state.refined_query)
        if detector.should_skip_agent(agent_key, step_query):
            logger.info(f"[pipeline-parallel] 跳过 {agent_name}：已调用过相似 query")
            continue
        executable_steps.append(step)

    if not executable_steps:
        return {"specialists": [], "tool_calls": []}

    # 批量构建上下文 + yield specialist_start
    step_ctxs = []
    for step in executable_steps:
        agent_key = step.get("agent_key")
        agent_name = step.get("agent_name", agent_key)
        step_query = step.get("query", state.refined_query)
        agent_config = _find_agent_config(agent_key, plan_result.get("specialists", []))
        if not agent_config:
            logger.warning(f"[pipeline-parallel] 找不到专家配置: {agent_key}")
            continue
        agent_icon = agent_config.get("icon", "🤖")

        ctx = build_specialist_context(
            agent=agent_config,
            history=[],
            conversation_id=state.conversation_id,
            rag_context=rag_context,
            portfolio_summary=portfolio,
            valuation_data=valuations,
            blackboard=blackboard,
            complexity=query_info.get("complexity", "medium"),
            rag_low_quality=rag_low_quality,
        )

        detector.record_call(agent_key, step_query)
        state.called_agents.add(agent_key)

        # 先 yield specialist_start（顺序发出，表示"开始排队"）
        yield {
            "type": EVENT_SPECIALIST_START,
            "agent_key": agent_key,
            "agent": agent_name,
            "icon": agent_icon,
        }
        step_ctxs.append((step, agent_config, agent_icon, ctx))

    if not step_ctxs:
        return {"specialists": [], "tool_calls": []}

    # 并行执行（最多 3 个并发，避免 token 突刺）
    max_workers = min(len(step_ctxs), 3)
    specialists_result = []
    all_tool_calls = []

    def _run_one(step, ctx, agent_key):
        step_query = step.get("query", state.refined_query)
        return run_specialist(
            agent_key=agent_key,
            query=step_query,
            prebuilt_context=ctx,
            trace_id=trace_id,
            from_pipeline=True,
            conversation_id=state.conversation_id,
            message_id=state.message_id,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_meta = {
            executor.submit(_run_one, step, ctx, step.get("agent_key")): (step, agent_config, agent_icon, ctx)
            for step, agent_config, agent_icon, ctx in step_ctxs
        }
        # 按完成顺序处理结果
        for future in concurrent.futures.as_completed(future_to_meta):
            if cancel_event and cancel_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                raise _PipelineCancelled()

            step, agent_config, agent_icon, _ = future_to_meta[future]
            agent_key = step.get("agent_key")
            agent_name = step.get("agent_name", agent_key)
            step_query = step.get("query", state.refined_query)
            step_start = time.time()

            try:
                result = future.result(timeout=300)
                specialists_result.append(result)
                all_tool_calls.extend(result.get("tool_calls", []))

                # 写黑板（Blackboard.write 已加锁，线程安全）
                entry = extract_entry_from_result(
                    agent_key=agent_key,
                    agent_name=agent_name,
                    result=result,
                    tokens_used=result.get("tokens_used", 0),
                )
                blackboard.write(entry)

                checker.record_success()
                tokens = result.get("tokens_used", 0)
                state.record_phase_tokens(PipelinePhase.EXECUTION.value, tokens)

                # P5: 优先使用 result 内部精确计时（run_specialist 记录），
                # 避免并行场景下 step_start 在 submit 之后才记录导致 duration 偏小
                duration_ms = result.get("duration_ms") or int((time.time() - step_start) * 1000)
                try:
                    create_agent_run(
                        conversation_id=state.conversation_id,
                        message_id=state.message_id,
                        agent_key=agent_key,
                        agent_name=agent_name,
                        query=step_query,
                        result=result.get("analysis", "")[:4000],
                        tool_calls=str(result.get("tool_calls", []))[:2000],
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        status="success",
                    )
                except Exception as log_err:
                    logger.warning(f"[pipeline-parallel] agent_runs 写入失败 ({agent_key}): {log_err}")

                yield {
                    "type": EVENT_SPECIALIST_DONE,
                    "agent_key": agent_key,
                    "agent": agent_name,
                    "icon": agent_icon,
                    "analysis": result.get("analysis", ""),
                    "status": result.get("status", "success"),
                    "error": result.get("status") == "failed",
                    "duration_ms": duration_ms,
                }
            except Exception as e:
                logger.error(f"[pipeline-parallel] 专家 {agent_name} 执行失败: {e}")
                checker.record_failure(str(e))
                duration_ms = int((time.time() - step_start) * 1000)
                try:
                    create_agent_run(
                        conversation_id=state.conversation_id,
                        message_id=state.message_id,
                        agent_key=agent_key,
                        agent_name=agent_name,
                        query=step_query,
                        result=f"（并行执行失败：{e}）",
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        status="error",
                    )
                except Exception as log_err:
                    logger.warning(f"[pipeline-parallel] agent_runs 写入失败 ({agent_key}): {log_err}")
                yield {
                    "type": EVENT_SPECIALIST_DONE,
                    "agent_key": agent_key,
                    "agent": agent_name,
                    "icon": agent_icon,
                    "analysis": f"（执行失败：{e}）",
                    "status": "failed",
                    "error": True,
                    "duration_ms": duration_ms,
                }

    return {
        "specialists": specialists_result,
        "tool_calls": all_tool_calls,
    }


def _fallback_execution(
    state: PipelineState,
    plan_result: dict,
    info_gather_result: dict,
    detector: ConvergenceDetector,
    blackboard: Blackboard,
    trace_id: str,
    cancel_event,
):
    """降级执行：Plan 生成失败时，直接调用所有可用专家。

    Generator：yield specialist_start/done 事件，return 结果 dict。
    """
    from agent.multi_agent import run_specialist

    specialists = plan_result.get("specialists", [])
    if not specialists:
        return {"specialists": [], "tool_calls": []}

    rag_context = info_gather_result.get("rag_context", "")
    portfolio = info_gather_result.get("portfolio", "")
    valuations = info_gather_result.get("valuations", {})
    rag_low_quality = bool(info_gather_result.get("rag_low_quality", False))

    results = []
    tool_calls = []

    # 最多取前 3 个专家
    for spec in specialists[:3]:
        if cancel_event and cancel_event.is_set():
            raise _PipelineCancelled()

        agent_key = spec.get("agent_key")
        agent_name = spec.get("name", agent_key)
        agent_icon = spec.get("icon", "🤖")
        query = state.refined_query

        if detector.should_skip_agent(agent_key, query):
            continue

        ctx = build_specialist_context(
            agent=spec,
            history=[],
            conversation_id=state.conversation_id,
            rag_context=rag_context,
            portfolio_summary=portfolio,
            valuation_data=valuations,
            blackboard=blackboard,
            rag_low_quality=rag_low_quality,
        )

        detector.record_call(agent_key, query)
        state.called_agents.add(agent_key)

        yield {
            "type": EVENT_SPECIALIST_START,
            "agent_key": agent_key,
            "agent": agent_name,
            "icon": agent_icon,
        }

        step_start = time.time()
        try:
            result = run_specialist(
                agent_key=agent_key, query=query,
                prebuilt_context=ctx, trace_id=trace_id,
                from_pipeline=True,
                conversation_id=state.conversation_id,
                message_id=state.message_id,
            )
            results.append(result)
            tool_calls.extend(result.get("tool_calls", []))

            entry = extract_entry_from_result(
                agent_key, agent_name, result,
                tokens_used=result.get("tokens_used", 0),
            )
            blackboard.write(entry)

            # P0: 写入 agent_runs（降级路径同样补齐）
            # P5: 优先使用 result 自带的 duration_ms
            duration_ms_for_log = result.get("duration_ms") or int((time.time() - step_start) * 1000)
            try:
                create_agent_run(
                    conversation_id=state.conversation_id,
                    message_id=state.message_id,
                    agent_key=agent_key,
                    agent_name=agent_name,
                    query=query,
                    result=result.get("analysis", "")[:4000],
                    tool_calls=str(result.get("tool_calls", []))[:2000],
                    duration_ms=duration_ms_for_log,
                    trace_id=trace_id,
                    status="success",
                )
            except Exception as log_err:
                logger.warning(f"[pipeline] agent_runs 写入失败 ({agent_key}): {log_err}")

            duration_ms = int((time.time() - step_start) * 1000)
            yield {
                "type": EVENT_SPECIALIST_DONE,
                "agent_key": agent_key,
                "agent": agent_name,
                "icon": agent_icon,
                "analysis": result.get("analysis", ""),
                "status": result.get("status", "success"),
                "error": result.get("status") == "failed",
                "duration_ms": duration_ms,
            }
        except Exception as e:
            logger.error(f"[pipeline] 降级执行 {agent_name} 失败: {e}")
            duration_ms_for_log = int((time.time() - step_start) * 1000)
            try:
                create_agent_run(
                    conversation_id=state.conversation_id,
                    message_id=state.message_id,
                    agent_key=agent_key,
                    agent_name=agent_name,
                    query=query,
                    result=f"（降级执行失败：{e}）",
                    duration_ms=duration_ms_for_log,
                    trace_id=trace_id,
                    status="error",
                )
            except Exception as log_err:
                logger.warning(f"[pipeline] agent_runs 写入失败 ({agent_key}): {log_err}")
            duration_ms = int((time.time() - step_start) * 1000)
            yield {
                "type": EVENT_SPECIALIST_DONE,
                "agent_key": agent_key,
                "agent": agent_name,
                "icon": agent_icon,
                "analysis": f"（执行失败：{e}）",
                "status": "failed",
                "error": True,
                "duration_ms": duration_ms,
            }

    return {"specialists": results, "tool_calls": tool_calls}


def _find_agent_config(agent_key: str, specialists: list) -> Optional[dict]:
    """从专家列表中查找指定 agent_key 的配置。"""
    for s in specialists:
        if s.get("agent_key") == agent_key:
            return s
    return None


# ── Phase 4: 综合 ──────────────────────────

def _phase_synthesis(
    state: PipelineState,
    query: str,
    execution_result: dict,
    blackboard: Blackboard,
    trace_id: str,
    reflection_result: dict = None,
    debate_result: dict = None,
) -> dict:
    """Phase 4: 交叉审阅 + 仲裁 + 生成最终回答。

    Args:
        reflection_result: P3 Reflection 阶段的产出（可选），包含 quality_issues 等
        debate_result: P1 辩论节点的产出（可选），包含 bull/bear/arbitration 等
    """
    specialists = execution_result.get("specialists", [])
    tool_calls = execution_result.get("tool_calls", [])

    # P2 置信度校准：检测数据缺失信号
    data_gaps = _detect_data_gaps(state, tool_calls, specialists, trace_id)

    # 单个专家：直接用其结果
    if len(specialists) <= 1:
        answer = specialists[0].get("analysis", "") if specialists else "无法生成分析"
        confidence = _compute_confidence(
            reflection_result, 1, conflict_count=0, data_gaps=data_gaps,
        )
        return {
            "answer": answer,
            "cross_review": None,
            "arbitration": None,
            "specialist_count": len(specialists),
            "confidence": confidence,
            "data_gaps": data_gaps,
        }

    # 多专家：调用 LLM 综合
    try:
        answer = _synthesize_multiple_specialists(
            query, specialists, blackboard, trace_id,
            reflection_result=reflection_result,
            debate_result=debate_result,
            data_gaps=data_gaps,
        )
    except Exception as e:
        logger.error(f"[pipeline] 综合失败，降级拼接: {e}")
        answer = _fallback_synthesize(query, specialists)

    # 冲突检测
    conflicts = blackboard.find_conflicts() if blackboard else []
    # P0-A: 收集风险否决供前端展示
    vetoes = blackboard.get_vetoes() if blackboard else []
    # P0-B: 收集持仓影响供前端展示
    impacts = blackboard.get_portfolio_impacts() if blackboard else []

    # 综合置信度（基于 Reflection 调整 + 专家数 + 冲突数 + 数据缺失）
    confidence = _compute_confidence(
        reflection_result, len(specialists), len(conflicts), data_gaps=data_gaps,
    )

    return {
        "answer": answer,
        "cross_review": {"conflicts": conflicts},
        "arbitration": None,
        "specialist_count": len(specialists),
        "reflection": reflection_result,
        "risk_vetoes": vetoes,
        "portfolio_impacts": impacts,
        "confidence": confidence,
        "data_gaps": data_gaps,
    }


def _detect_data_gaps(
    state: PipelineState, tool_calls: list, specialists: list, trace_id: str,
) -> list:
    """P2 置信度校准：检测数据缺失信号，返回数据缺口列表。

    检测来源：
    1. RAG 低质量标记（rag_low_quality）
    2. query_valuation 工具返回 data_status=unavailable/partial 或 error
    3. 专家分析文本中包含"数据缺失""无法获取"等关键词
    """
    gaps = []

    # 1. RAG 低质量
    try:
        info_gather = state.get_phase_result(PipelinePhase.INFO_GATHER.value)
        if info_gather and info_gather.get("rag_low_quality"):
            gaps.append({"type": "rag_low_quality", "detail": "RAG 检索最高分<0.05，知识库参考价值低"})
    except Exception:
        pass

    # 2. 扫描工具调用结果中的数据缺失信号
    for tc in tool_calls or []:
        name = tc.get("name", "")
        preview = tc.get("result_preview", "") or ""

        if name == "query_valuation":
            # 检测 data_status 或 error
            if "data_status" in preview and ("unavailable" in preview or "partial" in preview):
                gaps.append({
                    "type": "valuation_missing",
                    "tool": name,
                    "detail": f"估值工具返回数据缺失: {preview[:100]}",
                })
            elif '"error"' in preview or "未找到" in preview or "无法获取" in preview:
                gaps.append({
                    "type": "valuation_missing",
                    "tool": name,
                    "detail": f"估值工具返回错误: {preview[:100]}",
                })

    # 3. 扫描专家分析文本中的数据缺失自述（采样前200字符）
    _missing_keywords = ("数据缺失", "无法获取", "估值数据缺失", "数据不可用", "暂无数据")
    for s in specialists:
        analysis = (s.get("analysis") or "")[:500]
        for kw in _missing_keywords:
            if kw in analysis:
                gaps.append({
                    "type": "expert_self_reported",
                    "agent": s.get("agent", ""),
                    "detail": f"专家自述数据缺失（命中关键词: {kw}）",
                })
                break  # 每个专家只记一次

    if gaps:
        logger.info(f"[pipeline:{trace_id}] P2 数据缺口检测: {len(gaps)} 个 → {[(g['type']) for g in gaps]}")
    return gaps


def _compute_confidence(
    reflection_result: dict,
    specialist_count: int,
    conflict_count: int = 0,
    data_gaps: list = None,
) -> float:
    """计算综合答案的置信度（0-1）。

    基础值 0.7，根据 Reflection 调整、冲突数、专家数、数据完整性微调。
    P2: 数据缺失时降低 confidence，每个缺口 -0.05（最多 -0.2）。
    """
    base = 0.7
    if reflection_result:
        adj = reflection_result.get("confidence_adjustment", 0.0)
        base += adj
    # 冲突降低置信度
    if conflict_count > 0:
        base -= 0.05 * conflict_count
    # 多专家微增
    if specialist_count >= 3:
        base += 0.05
    # P2: 数据缺失降低置信度（每个 -0.05，最多 -0.2）
    if data_gaps:
        penalty = min(0.2, 0.05 * len(data_gaps))
        base -= penalty
    return round(max(0.1, min(1.0, base)), 2)


def _maybe_rerun_specialist(
    state: PipelineState,
    query: str,
    execution_result: dict,
    reflection_result: dict,
    blackboard: Blackboard,
    trace_id: str,
) -> Optional[dict]:
    """Reflection 自纠错：低置信度时重跑质量最差的专家。

    触发条件：
    1. reflection_self_correct_enabled 开关开启
    2. confidence_adjustment < threshold（默认 -0.2）
    3. missing_perspectives 命中已调用的某个专家

    重跑：注入反思反馈到专家 prompt，最多重跑 1 次。
    返回重跑后的 specialist dict，或 None（未触发/失败）。
    """
    if not reflection_result:
        return None

    try:
        self_correct_enabled = get_config_bool("pipeline.reflection_self_correct_enabled", True)
    except Exception:
        self_correct_enabled = True
    if not self_correct_enabled:
        return None

    threshold = -0.2
    try:
        threshold = float(get_config_int("pipeline.reflection_confidence_threshold", -20) / 100)
    except Exception:
        pass

    conf_adj = reflection_result.get("confidence_adjustment", 0.0)
    if conf_adj >= threshold:
        return None

    # 从 missing_perspectives 找到命中的已调用专家
    missing = reflection_result.get("missing_perspectives", [])
    if not missing:
        return None

    specialists = execution_result.get("specialists", [])
    if not specialists:
        return None

    # 匹配：missing_perspective 文本含专家名/关键词 → 该专家需要重跑
    rerun_target = None
    for s in specialists:
        agent_name = s.get("agent", "")
        agent_key = s.get("agent_key", "")
        for m in missing:
            if agent_name and agent_name in m:
                rerun_target = s
                break
            # 关键词匹配
            kw_map = {
                "valuation": ["估值", "PE", "PB", "分位"],
                "risk": ["风险", "止损", "回撤"],
                "macro": ["宏观", "趋势", "政策"],
                "fund": ["基金", "持仓", "经理"],
            }
            keywords = kw_map.get(agent_key, [])
            if any(kw in m for kw in keywords):
                rerun_target = s
                break
        if rerun_target:
            break

    if not rerun_target:
        # 默认重跑第一个专家
        rerun_target = specialists[0]

    agent_key = rerun_target.get("agent_key", "")
    agent_name = rerun_target.get("agent", "")
    logger.info(
        f"[pipeline:{trace_id}] Reflection 自纠错：重跑 {agent_name} "
        f"(confidence_adj={conf_adj}, threshold={threshold})"
    )

    try:
        from agent.multi_agent import run_specialist
        # 构建反思反馈注入
        feedback = "\n\n## 反思反馈（上一轮分析质量问题，请改进）\n"
        for issue in reflection_result.get("quality_issues", [])[:3]:
            feedback += f"- {issue}\n"
        for m in reflection_result.get("missing_perspectives", [])[:3]:
            feedback += f"- 缺失视角：{m}\n"
        enhanced_query = f"{query}{feedback}"

        result = run_specialist(
            agent_key=agent_key,
            query=enhanced_query,
            trace_id=f"{trace_id}#rerun",
            from_pipeline=True,
        )
        if result and result.get("analysis"):
            # 更新 blackboard 中的 entry
            if blackboard:
                try:
                    from agent.blackboard import extract_entry_from_result
                    entry = extract_entry_from_result(agent_key, agent_name, result)
                    blackboard.write(entry)
                except Exception as e:
                    logger.warning(f"[pipeline:{trace_id}] 重跑结果写黑板失败: {e}")
            logger.info(f"[pipeline:{trace_id}] 自纠错重跑完成: {agent_name}")
            return result
    except Exception as e:
        logger.warning(f"[pipeline:{trace_id}] 自纠错重跑失败: {e}")

    return None


def _replace_specialist_analysis(specialists: list, rerun_result: dict) -> list:
    """用重跑结果替换对应专家的分析。"""
    if not rerun_result:
        return specialists
    target_key = rerun_result.get("agent_key", "")
    target_name = rerun_result.get("agent", "")
    updated = []
    replaced = False
    for s in specialists:
        if (target_key and s.get("agent_key") == target_key) or (
            not target_key and s.get("agent") == target_name
        ):
            # 保留原 agent_key/name，替换 analysis
            new_s = dict(s)
            new_s["analysis"] = rerun_result.get("analysis", s.get("analysis", ""))
            new_s["rerun"] = True
            new_s["action_signals"] = rerun_result.get("action_signals", s.get("action_signals", []))
            updated.append(new_s)
            replaced = True
        else:
            updated.append(s)
    if not replaced and specialists:
        # 未匹配到：追加到末尾
        updated.append(rerun_result)
    return updated


def _synthesize_multiple_specialists(
    query: str,
    specialists: list,
    blackboard: Blackboard,
    trace_id: str,
    reflection_result: dict = None,
    debate_result: dict = None,
    data_gaps: list = None,
) -> str:
    """调用 LLM 综合多个专家的分析。"""
    from services.llm_service import _call_llm, MODEL

    # 构建综合 prompt
    parts = [f"## 用户问题\n{query}\n"]
    parts.append("## 各专家分析")

    for i, s in enumerate(specialists, 1):
        agent_name = s.get("agent", f"专家{i}")
        analysis = s.get("analysis", "")
        parts.append(f"\n### {agent_name}\n{analysis}")

    # 黑板关键数据
    if blackboard and blackboard.entry_count > 0:
        key_data = blackboard.get_key_data()
        if key_data:
            parts.append("\n## 关键数据汇总")
            for k, v in list(key_data.items())[:10]:
                parts.append(f"- {k}: {v}")

    # 冲突提示
    conflicts = blackboard.find_conflicts() if blackboard else []
    if conflicts:
        parts.append("\n## 检测到的冲突")
        for c in conflicts:
            parts.append(
                f"- {c['target']}: 买入方={c['buy_agents']}, 卖出方={c['sell_agents']}"
            )

    # P1: 注入辩论结论（如有）
    if debate_result:
        parts.append("\n## 对抗式辩论结论（权威，优先采纳）")
        if debate_result.get("bull_argument"):
            parts.append(f"### 看多方论证\n{debate_result['bull_argument']}")
        if debate_result.get("bear_argument"):
            parts.append(f"### 看空方论证\n{debate_result['bear_argument']}")
        if debate_result.get("arbitration"):
            arb = debate_result["arbitration"]
            parts.append(
                f"### 仲裁结论\n方向: {arb.get('direction','')} | "
                f"置信度: {arb.get('confidence','')} | 理由: {arb.get('reason','')}"
            )
            parts.append("综合回答必须与仲裁结论方向一致，不得与仲裁方向相悖。\n")

    # P3: 注入 Reflection 结果（质量问题 + 缺失视角）
    if reflection_result:
        quality_issues = reflection_result.get("quality_issues", [])
        missing = reflection_result.get("missing_perspectives", [])
        if quality_issues:
            parts.append("\n## 反思发现的质量问题（请在综合时修正）")
            for issue in quality_issues:
                parts.append(f"- {issue}")
        if missing:
            parts.append("\n## 反思发现的缺失视角（请补充）")
            for m in missing:
                parts.append(f"- {m}")

    # P1替代: 注入持仓约束提醒（零 LLM 成本，复用 Phase 1 预取的持仓）
    # 单专家场景已在 Phase 3 通过 build_specialist_context 注入完整持仓，此处仅多专家综合时提醒
    portfolio_line = ""
    try:
        from services.portfolio_context import build_portfolio_summary_line
        portfolio_line = build_portfolio_summary_line("default")
    except Exception as e:
        logger.debug(f"[pipeline] 综合阶段持仓摘要加载失败: {e}")
    if portfolio_line:
        parts.append(
            f"\n## 用户当前持仓（综合建议必须兼容此约束）\n{portfolio_line}\n"
            "提醒：\n"
            "- 任何加仓/减仓建议需结合现有持仓结构，不要脱离实际持仓空谈\n"
            "- 若某标的已重仓（占比>25%），继续加仓建议需额外谨慎\n"
            "- 若已盈利>15%或亏损>15%，需提示止盈/止损考量\n"
        )

    # P0-A: 风险否决约束（强制降级被否决的动作）
    vetoes = blackboard.get_vetoes() if blackboard else []
    if vetoes:
        parts.append("\n## ⚠️ 风险否决约束（强制遵守）")
        for v in vetoes:
            parts.append(
                f"- {v.get('source_agent_name','')} 否决 {v.get('vetoed_action','')} "
                f"{v.get('target','')}: {v.get('reason','')} "
                f"(严重度: {v.get('severity','')}, 建议: {v.get('suggested_action','')})"
            )
        parts.append(
            "强制要求：上述被否决的动作必须在综合建议中降级处理——"
            "若其他专家建议 BUY 但风险专家否决，最终建议必须降为 HOLD 或观察，不得保留 BUY。\n"
        )

    # P0-B: 持仓影响汇总（让仲裁 LLM 看到全局组合影响）
    impacts = blackboard.get_portfolio_impacts() if blackboard else []
    if impacts:
        parts.append("\n## 各专家建议对持仓的影响汇总")
        parts.append("| 专家 | 标的 | 动作 | 变动 | 当前占比 | 变动后 | 风险检查 |")
        parts.append("|------|------|------|------|----------|--------|----------|")
        for p in impacts:
            parts.append(
                f"| {p.get('source_agent_name','')} | {p.get('affected_holding','')} "
                f"| {p.get('action','')} | {p.get('suggested_change','')} "
                f"| {p.get('current_position_pct',0)}% | {p.get('post_change_pct',0)}% "
                f"| {p.get('risk_check','')} |"
            )
        parts.append("综合建议必须考虑组合层面影响，避免多专家建议叠加后单一标的超限。\n")

    # P2 置信度校准：注入数据缺口信息，引导 LLM 标注数据完整性
    if data_gaps:
        parts.append("\n## ⚠️ 数据完整性警告（必须在回答中体现）")
        for g in data_gaps:
            parts.append(f"- [{g.get('type','')}] {g.get('detail','')}")
        parts.append(
            "强制要求：\n"
            "1. 回答中必须明确标注哪些数据缺失，不要掩盖数据缺口\n"
            "2. 对缺失数据的标的，建议降级为「观察」而非「买入/卖出」\n"
            "3. 结尾「具体操作建议」段落必须包含置信度说明，"
            "如「由于XX估值数据缺失，本建议置信度较低，建议补充数据后再次评估」\n"
        )

    parts.append(
        "\n## 任务\n"
        "基于以上专家分析，生成综合回答：\n"
        "1. 整合各专家观点，去重避免重复\n"
        "2. 如有冲突，给出明确判断和理由\n"
        "3. 如有风险否决，必须遵守否决约束降级处理\n"
        "4. 如有数据缺口警告，回答中必须体现数据完整性说明\n"
        "5. 结尾包含「具体操作建议」段落\n"
        "6. 使用 Markdown 格式，禁止 emoji 标题\n"
    )

    prompt = "\n".join(parts)

    response = _call_llm(
        caller="pipeline_synthesizer",
        trace_id=trace_id,
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=3000,
    )
    answer = response.choices[0].message.content or ""

    # P2-问题6修复：风险否决代码层硬兜底
    # prompt 级降级依赖 LLM 遵守，此处做事后校验：
    # 若被否决标的在最终 answer 中仍出现 BUY/加仓 关键词，强制改写为 HOLD
    if vetoes:
        answer = _enforce_risk_veto(answer, vetoes, trace_id)

    return answer


def _enforce_risk_veto(answer: str, vetoes: list, trace_id: str) -> str:
    """风险否决硬兜底：检测被否决标的的 BUY 关键词并强制降级为 HOLD。

    仅作最保守的关键词匹配，避免误伤其他标的。
    """
    import re
    for v in vetoes:
        target = v.get("target", "")
        vetoed_action = v.get("vetoed_action", "")
        if not target or not vetoed_action:
            continue
        # 仅处理 BUY 类否决（加仓/买入）
        if vetoed_action not in ("buy", "加仓", "买入"):
            continue
        # 匹配 answer 中该标的附近的 BUY 关键词（同一行或相邻 20 字符内）
        buy_pattern = re.compile(
            rf'({re.escape(target)})'
            r'[^。\n]{0,20}?(加仓|买入|建仓|增持|买入加仓)',
            re.IGNORECASE
        )
        if buy_pattern.search(answer):
            # 将该标的附近的 BUY 关键词替换为 HOLD
            answer = buy_pattern.sub(
                rf'\1（风险否决：降级为持有/观望）',
                answer
            )
            changed = True
            logger.warning(
                f"[pipeline:{trace_id}] 风险否决硬兜底：{target} 的 BUY 建议已强制降级为 HOLD"
            )
    return answer


# ── Phase 3.7: 对抗式辩论 ──────────────────────────

def _phase_debate(
    state: PipelineState,
    query: str,
    execution_result: dict,
    blackboard: Blackboard,
    trace_id: str,
) -> dict:
    """Phase 3.7: 对抗式辩论 — 看多 vs 看空 + 仲裁。

    参考 TradingAgents 的 Bull/Bear Debate 机制。
    仅在 blackboard.find_conflicts() 检测到冲突时触发。

    流程（2 次 LLM 调用）:
    1. 看多/看空论证：把买卖双方观点合并为一次 prompt，让 LLM 分别论证
    2. 仲裁：综合两方 + 持仓约束 + 风险否决，给出明确方向

    Returns:
        {
            "conflicts": [...],
            "bull_argument": "看多方论证",
            "bear_argument": "看空方论证",
            "arbitration": {
                "direction": "BUY|HOLD|SELL",
                "confidence": 0.75,
                "reason": "仲裁理由",
                "conditions": "执行条件"
            }
        }
    """
    from services.llm_service import _call_llm, MODEL
    from agent.orchestrator import _get_model_for_agent, _is_cost_routing_enabled

    conflicts = blackboard.find_conflicts() if blackboard else []
    if not conflicts:
        return {"conflicts": [], "bull_argument": "", "bear_argument": "", "arbitration": None}

    # 收集看多/看空方专家的分析
    specialists = execution_result.get("specialists", [])
    bull_analyses = []
    bear_analyses = []
    for c in conflicts:
        target = c.get("target", "")
        for s in specialists:
            agent_name = s.get("agent", "")
            analysis = s.get("analysis", "") or ""
            if agent_name in c.get("buy_agents", []):
                bull_analyses.append(f"[{agent_name} 关于 {target}]\n{analysis[:800]}")
            elif agent_name in c.get("sell_agents", []):
                bear_analyses.append(f"[{agent_name} 关于 {target}]\n{analysis[:800]}")

    # 持仓约束
    portfolio_line = ""
    try:
        from services.portfolio_context import build_portfolio_summary_line
        portfolio_line = build_portfolio_summary_line("default")
    except Exception:
        pass

    # 风险否决
    vetoes = blackboard.get_vetoes() if blackboard else []
    veto_text = ""
    if vetoes:
        veto_lines = []
        for v in vetoes:
            veto_lines.append(
                f"- {v.get('source_agent_name','')} 否决 {v.get('vetoed_action','')} "
                f"{v.get('target','')}: {v.get('reason','')}"
            )
        veto_text = "\n风险否决:\n" + "\n".join(veto_lines)

    # ── Step 1: 看多/看空论证（单次 LLM 调用）──
    debate_prompt = f"""你是投资辩论主持人。针对以下冲突，分别给出看多方和看空方的论证。

## 用户问题
{query}

## 冲突标的
{chr(10).join([f"- {c['target']}: 买方={c['buy_agents']}, 卖方={c['sell_agents']}" for c in conflicts])}

## 看多方专家分析
{chr(10).join(bull_analyses) if bull_analyses else '（无看多方分析）'}

## 看空方专家分析
{chr(10).join(bear_analyses) if bear_analyses else '（无看空方分析）'}

## 用户当前持仓
{portfolio_line or '（无持仓）'}
{veto_text}

## 输出格式（严格 JSON）
```json
{{
  "bull_argument": "看多方核心论证（200字内，基于估值/政策/资金面等利好）",
  "bear_argument": "看空方核心论证（200字内，基于波动/风险/宏观等利空）"
}}
```
"""
    bull_arg = ""
    bear_arg = ""
    try:
        debate_model = MODEL
        if _is_cost_routing_enabled():
            debate_model = _get_model_for_agent("debate_arbitrator")
        response = _call_llm(
            caller="debate_argument",
            trace_id=trace_id,
            model=debate_model,
            messages=[{"role": "user", "content": debate_prompt}],
            temperature=0.3,
            max_tokens=1000,
        )
        content = response.choices[0].message.content or ""
        import json as _json
        import re as _re
        json_match = _re.search(r'\{.*\}', content, _re.DOTALL)
        if json_match:
            data = _json.loads(json_match.group(0))
            bull_arg = data.get("bull_argument", "")
            bear_arg = data.get("bear_argument", "")
    except Exception as e:
        logger.warning(f"[pipeline] 辩论论证失败: {e}")
        bull_arg = "（看多方论证生成失败）"
        bear_arg = "（看空方论证生成失败）"

    # ── Step 2: 仲裁（单次 LLM 调用）──
    arb_prompt = f"""你是投资仲裁专家。基于以下看多/看空论证，给出明确的仲裁方向。

## 用户问题
{query}

## 看多方论证
{bull_arg}

## 看空方论证
{bear_arg}

## 用户当前持仓
{portfolio_line or '（无持仓）'}
{veto_text}

## 仲裁原则
1. 必须给出明确方向：BUY / HOLD / SELL（不得模棱两可）
2. 如有风险否决，BUY 建议必须降级为 HOLD
3. 结合用户持仓现状，考虑实际可操作性
4. 置信度 0-1，反映判断的把握程度

## 输出格式（严格 JSON）
```json
{{
  "direction": "BUY|HOLD|SELL",
  "confidence": 0.75,
  "reason": "仲裁理由（150字内）",
  "conditions": "执行条件（如'分批建仓，单次不超过10%'）"
}}
```
"""
    arbitration = None
    try:
        arb_model = MODEL
        if _is_cost_routing_enabled():
            arb_model = _get_model_for_agent("debate_arbitrator")
        response = _call_llm(
            caller="debate_arbitrator",
            trace_id=trace_id,
            model=arb_model,
            messages=[{"role": "user", "content": arb_prompt}],
            temperature=0.2,
            max_tokens=600,
        )
        content = response.choices[0].message.content or ""
        import json as _json
        import re as _re
        json_match = _re.search(r'\{.*\}', content, _re.DOTALL)
        if json_match:
            arbitration = _json.loads(json_match.group(0))
    except Exception as e:
        logger.warning(f"[pipeline] 辩论仲裁失败: {e}")
        arbitration = {
            "direction": "HOLD",
            "confidence": 0.5,
            "reason": f"仲裁失败，默认 HOLD: {e}",
            "conditions": "建议人工复核",
        }

    logger.info(
        f"[pipeline] 辩论完成: 方向={arbitration.get('direction','')} "
        f"置信度={arbitration.get('confidence','')}"
    )

    return {
        "conflicts": conflicts,
        "bull_argument": bull_arg,
        "bear_argument": bear_arg,
        "arbitration": arbitration,
    }


# ── Phase 3.5: 反思 ──────────────────────────

def _phase_reflection(
    state: PipelineState,
    query: str,
    execution_result: dict,
    blackboard: Blackboard,
    trace_id: str,
) -> dict:
    """Phase 3.5: Reflection 节点 — 自评质量问题 + 冲突识别。

    4 层多重保险确保 JSON 解析成功：
    - Layer 1: prompt 明确要求 JSON 字段
    - Layer 2: response_format=json_object（保证返回合法 JSON）
    - Layer 3: 鲁棒解析（递归查找字段，容错嵌套结构）
    - Layer 4: 降级保护（失败返回默认值，但记录原始返回用于诊断）

    成本：约 800 token（单次调用）
    """
    from services.llm_service import _call_llm, MODEL

    specialists = execution_result.get("specialists", [])
    if not specialists:
        return {"quality_issues": [], "missing_perspectives": [], "conflict_resolutions": [], "confidence_adjustment": 0.0}

    # 黑板摘要（结论 + 冲突）
    bb_summary = blackboard.to_context_text(max_chars=1500) if blackboard else ""
    conflicts = blackboard.find_conflicts() if blackboard else []

    parts = [
        "你是质量审查员。请审查以下多专家分析的质量，识别问题。",
        f"\n## 用户问题\n{query}",
        f"\n## 专家分析摘要\n{bb_summary or '（无黑板数据）'}",
    ]

    if conflicts:
        parts.append("\n## 检测到的冲突")
        for c in conflicts:
            parts.append(f"- {c['target']}: 买入方={c['buy_agents']}, 卖出方={c['sell_agents']}")

    # 各专家分析预览（每个截断到 500 字）
    parts.append("\n## 各专家分析预览")
    for s in specialists[:5]:
        agent_name = s.get("agent", "")
        analysis = s.get("analysis", "")[:500]
        parts.append(f"\n### {agent_name}\n{analysis}")

    # Layer 1: prompt 明确要求字段名 + 严格 JSON 格式
    parts.append(
        "\n## 任务\n"
        "审查以上分析，严格输出以下 JSON 格式（不要输出任何其他文字、不要 markdown 代码块）：\n"
        "{\n"
        '  "quality_issues": ["质量问题1（如数据缺失/逻辑跳跃/证据不足）", "..."],\n'
        '  "missing_perspectives": ["未覆盖的视角1", "..."],\n'
        '  "conflict_resolutions": [{"target": "冲突标的", "resolution": "解决建议"}],\n'
        '  "confidence_adjustment": -0.1\n'
        "}\n"
        "要求：\n"
        "1. 必须包含上述 4 个字段，字段名完全一致\n"
        "2. quality_issues/missing_perspectives 是字符串数组\n"
        "3. confidence_adjustment 是数字，范围 [-0.3, 0.2]，负值表示需降级\n"
        "4. 如果没有问题，对应字段返回空数组 []\n"
    )

    prompt = "\n".join(parts)

    try:
        # Layer 2: response_format=json_object 保证返回合法 JSON
        response = _call_llm(
            caller="reflection",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
    except Exception as e:
        # response_format 不支持时降级
        logger.warning(f"[pipeline] Reflection LLM 调用失败（尝试降级）: {e}")
        try:
            response = _call_llm(
                caller="reflection",
                trace_id=trace_id,
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=800,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e2:
            logger.warning(f"[pipeline] Reflection LLM 降级也失败: {e2}")
            return {"quality_issues": [], "missing_perspectives": [], "conflict_resolutions": [], "confidence_adjustment": 0.0}

    # Layer 3: 鲁棒解析
    result = _parse_reflection_json(raw)

    logger.info(
        f"[pipeline] Reflection 完成: "
        f"{len(result['quality_issues'])} 质量问题, "
        f"{len(result['missing_perspectives'])} 缺失视角, "
        f"置信度调整={result['confidence_adjustment']}"
    )
    return result


def _parse_reflection_json(raw: str) -> dict:
    """Layer 3: 鲁棒解析 Reflection 的 JSON 返回。

    MIMO 可能返回：
    - 标准 flat 结构: {"quality_issues": [...], "missing_perspectives": [...]}
    - 嵌套结构: {"审查结果": {"潜在问题": {"问题": [...]}}}
    - markdown 包裹: ```json ... ```
    - 部分字段缺失

    策略：递归查找字段，支持中英文别名。
    """
    result = {
        "quality_issues": [],
        "missing_perspectives": [],
        "conflict_resolutions": [],
        "confidence_adjustment": 0.0,
    }
    if not raw:
        return result

    # 清理 markdown 包裹
    cleaned = raw.strip()
    if "```" in cleaned:
        parts_split = cleaned.split("```")
        if len(parts_split) >= 3:
            cleaned = parts_split[1]
        elif len(parts_split) == 2:
            cleaned = parts_split[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    # 尝试 JSON 解析
    parsed = None
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # 尝试从文本中提取第一个 JSON 对象
        try:
            import re
            match = re.search(r'\{[\s\S]*\}', cleaned)
            if match:
                parsed = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    if not isinstance(parsed, dict):
        logger.warning(f"[pipeline] Reflection JSON 解析完全失败，原始返回: {raw[:300]}")
        return result

    # Layer 3 核心：递归查找字段（支持中英文别名 + 嵌套结构）
    # 字段别名映射
    FIELD_ALIASES = {
        "quality_issues": ["quality_issues", "质量问题", "质量问题列表", "问题", "潜在问题", "issues"],
        "missing_perspectives": ["missing_perspectives", "缺失视角", "未覆盖视角", "缺失", "missing", "改进建议"],
        "conflict_resolutions": ["conflict_resolutions", "冲突解决", "冲突", "resolutions"],
        "confidence_adjustment": ["confidence_adjustment", "置信度调整", "置信度", "confidence"],
    }

    def _find_field(obj, aliases, depth=0):
        """递归查找字段，最多 3 层深度。"""
        if depth > 3 or not isinstance(obj, dict):
            return None
        for alias in aliases:
            if alias in obj:
                return obj[alias]
        # 递归查找子字典
        for v in obj.values():
            if isinstance(v, dict):
                found = _find_field(v, aliases, depth + 1)
                if found is not None:
                    return found
        return None

    # quality_issues（数组）
    qi = _find_field(parsed, FIELD_ALIASES["quality_issues"])
    if isinstance(qi, list):
        result["quality_issues"] = [str(x) for x in qi if x]
    elif isinstance(qi, str):
        result["quality_issues"] = [qi]

    # missing_perspectives（数组）
    mp = _find_field(parsed, FIELD_ALIASES["missing_perspectives"])
    if isinstance(mp, list):
        result["missing_perspectives"] = [str(x) for x in mp if x]
    elif isinstance(mp, str):
        result["missing_perspectives"] = [mp]

    # conflict_resolutions（对象数组）
    cr = _find_field(parsed, FIELD_ALIASES["conflict_resolutions"])
    if isinstance(cr, list):
        for item in cr:
            if isinstance(item, dict):
                result["conflict_resolutions"].append({
                    "target": str(item.get("target", item.get("标的", ""))),
                    "resolution": str(item.get("resolution", item.get("解决", item.get("建议", "")))),
                })
            elif isinstance(item, str):
                result["conflict_resolutions"].append({"target": "", "resolution": item})

    # confidence_adjustment（数字）
    ca = _find_field(parsed, FIELD_ALIASES["confidence_adjustment"])
    if isinstance(ca, (int, float)):
        result["confidence_adjustment"] = max(-0.3, min(0.2, float(ca)))
    elif isinstance(ca, str):
        try:
            result["confidence_adjustment"] = max(-0.3, min(0.2, float(ca)))
        except ValueError:
            pass

    # 诊断日志：如果关键字段都为空，记录原始结构
    if not result["quality_issues"] and not result["missing_perspectives"]:
        logger.warning(
            f"[pipeline] Reflection 解析后关键字段为空，原始 JSON keys: {list(parsed.keys())[:10]}, "
            f"原始返回前 300 字: {raw[:300]}"
        )

    return result


def _fallback_synthesize(query: str, specialists: list) -> str:
    """降级综合：简单拼接各专家分析。"""
    parts = [f"## 综合分析\n用户问题：{query}\n"]
    for s in specialists:
        agent_name = s.get("agent", "专家")
        analysis = s.get("analysis", "")
        parts.append(f"\n### {agent_name}\n{analysis}")
    return "\n".join(parts)


# ── Phase 5: 记忆持久化 ──────────────────────────

def _extract_and_save_recommendations(
    state: PipelineState,
    query_info: dict,
    conclusion_id,
    trace_id: str,
) -> dict:
    """P0-A 决策闭环：从 AI 最终回答中提取结构化建议并落库到 recommendations 表。

    流程：
    1. LLM 提取方向性建议（加仓/减仓/买入/卖出/持有），模糊建议（关注/留意）不提取
    2. 为每条建议关联标的获取 baseline（当前估值/净值）
    3. 调用 save_recommendations 落库，verify_days 默认 5 交易日
    4. 返回保存的 recommendation_ids + 详情，供前端展示卡片

    开关：pipeline.save_recommendations_enabled（默认 true）
    """
    result = {"enabled": False, "recommendation_ids": [], "recommendations": []}

    # 开关检查（默认开启）
    try:
        enabled = get_config_bool("pipeline.save_recommendations_enabled", True)
    except Exception:
        enabled = True
    if not enabled:
        return result
    result["enabled"] = True

    if not state.answer or len(state.answer) < 30:
        return result

    # 1. LLM 提取结构化建议
    recs = []
    try:
        from services.llm_service import _call_llm, MODEL
        prompt = (
            "你是投资建议提取器。从下面的投资分析中，提取【明确的方向性建议】。\n\n"
            "提取规则：\n"
            "- 只提取明确的方向性建议：加仓/减仓/买入/卖出/持有/清仓\n"
            "- 模糊建议（如「关注」「留意」「观察」）不要提取\n"
            "- 每条建议必须关联具体标的（指数名/指数代码/基金名/基金代码）\n"
            "- 没有明确标的或没有方向性建议时返回空数组 []\n"
            "- 最多 5 条\n\n"
            "输出严格 JSON 数组（不要 markdown 代码块），每项格式：\n"
            '{"index_name":"标的名称","index_code":"代码(可为空)","direction":"up|down|hold",'
            '"reason":"简短理由(≤50字)","confidence":"high|medium|low"}\n\n'
            "direction 取值：up=加仓/买入，down=减仓/卖出，hold=持有/观望\n\n"
            f"分析内容：\n{state.answer[:3000]}"
        )
        response = _call_llm(
            caller="extract_recommendations",
            trace_id=trace_id,
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是严格的结构化数据提取器，只输出 JSON 数组。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        content = (response.choices[0].message.content or "").strip()
        # 容错：剥离 markdown 代码块
        if content.startswith("```"):
            content = content.split("\n", 1)[-1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        # 提取首个 [ 到 ] 之间的 JSON
        lb = content.find("[")
        rb = content.rfind("]")
        if lb >= 0 and rb > lb:
            recs = json.loads(content[lb:rb + 1])
        if not isinstance(recs, list):
            recs = []
    except Exception as e:
        logger.warning(f"[pipeline:{trace_id}] 建议提取失败: {e}")
        return result

    if not recs:
        return result

    # 过滤无效项 + 标准化 direction
    # Phase D: 从 answer 中提取 referenced_books，用于归因「建议是否引用了书籍」
    import re
    referenced_books = list(set(re.findall(r"根据《(.+?)》", state.answer or "")))
    valid_recs = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        name = (r.get("index_name") or "").strip()
        code = (r.get("index_code") or "").strip()
        if not name and not code:
            continue
        direction = (r.get("direction") or "").strip().lower()
        if direction not in ("up", "down", "hold"):
            continue
        valid_recs.append({
            "index_name": name,
            "index_code": code,
            "direction": direction,
            "reason": (r.get("reason") or "")[:200],
            "confidence": (r.get("confidence") or "medium").lower(),
            "referenced_books": referenced_books,  # Phase D: answer 级引用，所有 recs 共享
        })
    if not valid_recs:
        return result

    # 2. 为每条建议获取 baseline（当前估值/净值）
    baselines = []
    for rec in valid_recs:
        bl = None
        try:
            target = rec["index_code"] or rec["index_name"]
            val = _lookup_valuation(target)
            if val:
                # 用 current_value 作为基线（PE/PB 数值）
                bl = {
                    "price": float(val.get("current_value") or 0) or None,
                    "date": val.get("snapshot_date") or "",
                }
        except Exception:
            bl = None
        baselines.append(bl)

    # 2.5 P2 执行落地：尝试关联 target_fund_code（持仓优先 → 内置映射表）
    try:
        from services.index_fund_mapper import find_funds_by_index
        for rec in valid_recs:
            if rec.get("target_fund_code"):
                continue  # 已填充，跳过
            idx_code = (rec.get("index_code") or "").strip()
            if not idx_code:
                continue
            candidates = find_funds_by_index(idx_code, user_holdings_only=True)
            if not candidates:
                continue
            # 优先选 in_holdings=True 的候选
            picked = next((c for c in candidates if c.get("in_holdings")), candidates[0])
            rec["target_fund_code"] = picked["fund_code"]
            rec["target_fund_name"] = picked["fund_name"]
    except Exception as e:
        logger.debug(f"[pipeline:{trace_id}] 关联 target_fund_code 失败（不影响主流程）: {e}")

    # 3. 落库
    try:
        from db.dashboard import save_recommendations
        analysis_id = f"dialogue_{state.conversation_id}_{state.message_id}"
        rec_ids = save_recommendations(
            valid_recs,
            analysis_id=analysis_id,
            baselines=baselines,
            verify_days=5,
        )
        result["recommendation_ids"] = rec_ids
        # 组装前端展示用详情（含 baseline）
        for i, rec in enumerate(valid_recs):
            bl = baselines[i] if i < len(baselines) else None
            rec_out = dict(rec)
            rec_out["id"] = rec_ids[i] if i < len(rec_ids) else None
            rec_out["baseline_value"] = bl["price"] if bl else None
            rec_out["baseline_date"] = bl["date"] if bl else None
            rec_out["verify_window_days"] = 5
            rec_out["conversation_id"] = state.conversation_id
            result["recommendations"].append(rec_out)
        logger.info(
            f"[pipeline:{trace_id}] 已保存 {len(rec_ids)} 条建议到 recommendations 表 "
            f"(analysis_id={analysis_id})"
        )
    except Exception as e:
        logger.warning(f"[pipeline:{trace_id}] 建议落库失败: {e}")

    return result


# 股票类指数关键词（用于 out_of_focus 判定）
_STOCK_INDEX_KEYWORDS = {"股票", "沪深300", "中证500", "中证1000", "上证50", "创业板", "科创", "证券", "银行", "白酒", "消费", "医药", "军工", "新能源", "半导体", "芯片"}


def _matches_focus(index_name: str, index_code: str, focus_assets: list) -> bool:
    """判断建议标的是否在用户关注品种范围内。

    focus_assets 形如 ["index", "fund", "bond", "stock", "gold", "cash"]。
    判定逻辑：
    - 若 focus_assets 含 'stock' → 视为关注股票，所有标的均匹配
    - 若 focus_assets 不含 'stock' 但标的为股票类指数 → 不匹配
    - 其余情况默认匹配（避免过度拦截）
    """
    if not focus_assets:
        return True  # 用户未设定关注品种，不拦截
    if "stock" in focus_assets:
        return True
    # 用户不关注股票，但标的为股票类指数 → 不匹配
    target = f"{index_name or ''} {index_code or ''}".strip()
    if not target:
        return True
    for kw in _STOCK_INDEX_KEYWORDS:
        if kw in target:
            return False
    return True


def _apply_kyc_guardrail(recommendations: list, user_profile: dict) -> list:
    """P1 Step5：根据用户画像对建议加 guardrail 标记。

    规则：
    1. risk_tolerance in (conservative, steady) 且 direction=up 且 confidence=low → risky_for_profile
    2. loss_tolerance=low 且 direction=down → 不拦截（减仓对低亏损承受者是保护）
    3. risk_tolerance in (conservative, steady) 且 focus_assets 不含 stock 但建议标的为股票指数 → out_of_focus
    4. max_single_position_pct 已设且建议涉及加仓 → position_limit_reminder

    直接修改 recommendations 中每条的 guardrail_flags 字段并返回。
    """
    if not recommendations or not user_profile:
        return recommendations

    risk_tolerance = (user_profile.get("risk_tolerance") or "").strip().lower()
    focus_assets_raw = user_profile.get("focus_assets", "[]")
    if isinstance(focus_assets_raw, str):
        try:
            import json as _json
            focus_assets = _json.loads(focus_assets_raw) or []
        except Exception:
            focus_assets = []
    else:
        focus_assets = focus_assets_raw or []
    max_pct = user_profile.get("max_single_position_pct")

    is_conservative = risk_tolerance in ("conservative", "steady")

    for rec in recommendations:
        if not isinstance(rec, dict):
            continue
        flags = []
        direction = (rec.get("direction") or "").strip().lower()
        confidence = (rec.get("confidence") or "medium").strip().lower()

        # 规则1：保守用户 + 低置信度加仓
        if is_conservative and direction == "up" and confidence == "low":
            flags.append("risky_for_profile")

        # 规则3：超出关注品种
        if is_conservative and not _matches_focus(
            rec.get("index_name", ""), rec.get("index_code", ""), focus_assets,
        ):
            flags.append("out_of_focus")

        # 规则4：持仓上限提醒
        if direction == "up" and max_pct:
            try:
                pct_val = float(max_pct)
                if pct_val > 0:
                    flags.append(f"position_limit:{pct_val:.0f}%")
            except (TypeError, ValueError):
                pass

        if flags:
            rec["guardrail_flags"] = flags

    return recommendations


def _apply_institutional_confirm(recommendations: list, trace_id: str) -> list:
    """机构动向共振检测（P0 新增）。

    北向资金实时数据 2024.8 后已停止公布，改用融资余额作为杠杆资金动向主信号。

    规则（仅打标记，不修改 confidence，不拦截建议）：
    - direction=up + 资金强净流入 → guardrail_flags 追加 "institutional_confirm"
    - direction=up + 资金强净流出 → guardrail_flags 追加 "against_institutional_flow"
    - direction=down + 资金强净流入 → guardrail_flags 追加 "against_institutional_flow"
    - direction=down + 资金强净流出 → guardrail_flags 追加 "institutional_confirm"
    - strength=weak 时不打标记（信号弱不足以作为参考）

    设计原则：保留原始判断可追溯，仅作辅助确认信号。
    """
    try:
        from services.institutional_flow import get_institutional_flow_signal
        signal = get_institutional_flow_signal()
    except Exception as e:
        logger.debug(f"[pipeline:{trace_id}] 获取机构动向信号失败（跳过共振检测）: {e}")
        return recommendations

    direction = signal.get("direction", "neutral")
    strength = signal.get("strength", "weak")

    # 信号弱时不打标记
    if strength == "weak" or direction == "neutral":
        return recommendations

    for rec in recommendations:
        if not isinstance(rec, dict):
            continue
        rec_direction = rec.get("direction", "hold")
        if rec_direction == "hold":
            continue

        flags = rec.setdefault("guardrail_flags", [])
        # direction=up 与资金流入共振，direction=down 与资金流出共振
        is_resonance = (
            (rec_direction == "up" and direction == "inflow") or
            (rec_direction == "down" and direction == "outflow")
        )
        if is_resonance:
            if "institutional_confirm" not in flags:
                flags.append("institutional_confirm")
        else:
            if "against_institutional_flow" not in flags:
                flags.append("against_institutional_flow")

    confirm_count = sum(1 for r in recommendations if "institutional_confirm" in (r.get("guardrail_flags") or []))
    against_count = sum(1 for r in recommendations if "against_institutional_flow" in (r.get("guardrail_flags") or []))
    if confirm_count or against_count:
        logger.info(
            f"[pipeline:{trace_id}] 机构动向共振检测：confirm={confirm_count} against={against_count} "
            f"(资金方向={direction} 强度={strength})"
        )
    return recommendations


def _phase_memory(
    state: PipelineState,
    synthesis_result: dict,
    query_info: dict,
    trace_id: str,
    info_gather_result: dict = None,
) -> dict:
    """Phase 5: 保存结论 + 更新摘要 + 提取用户偏好 + 提取建议落库（P0-A）。"""
    result = {
        "saved_conclusion_id": None,
        "updated_memory_ids": [],
        "summary_updated": False,
        "recommendations": {"enabled": False, "recommendation_ids": [], "recommendations": []},
    }

    # 1. 保存分析结论
    try:
        from db.analysis_conclusions import save_analysis_conclusion
        targets = query_info.get("targets", [])
        target_subject = "、".join(targets) if targets else state.original_query[:30]
        # Phase D: data_basis 改为真实值，区分 RAG 是否含 book、是否低质
        data_basis = ["portfolio", "tools"]
        rag_results = info_gather_result.get("rag", []) if info_gather_result else []
        book_hits = [r for r in rag_results if r.get("content_type") == "book"]
        if book_hits:
            data_basis.append("rag_book")
        elif rag_results:
            data_basis.append("rag_other")
        if info_gather_result and info_gather_result.get("rag_low_quality"):
            data_basis.append("rag_low_quality")
        conclusion_id = save_analysis_conclusion(
            source_system="ai_dialogue",
            source_type="orchestrator",
            source_id=state.message_id,
            target_subject=target_subject,
            action=None,
            summary=state.answer[:100] if state.answer else "",
            reasoning=state.answer,
            key_variables=targets,
            data_basis=data_basis,
            confidence=phase4_result.get("confidence", 0.7) if phase4_result else 0.7,
            urgent=0,
            conversation_id=state.conversation_id,
            message_id=state.message_id,
        )
        result["saved_conclusion_id"] = conclusion_id
        logger.info(f"[pipeline] 分析结论已保存: id={conclusion_id}")
    except Exception as e:
        logger.warning(f"[pipeline] 保存结论失败: {e}")
        conclusion_id = None

    # 2. P0-A 决策闭环：从回答中提取建议并落库
    try:
        rec_result = _extract_and_save_recommendations(
            state, query_info, conclusion_id, trace_id,
        )
        # P1 Step5：对建议加 KYC guardrail 标记
        try:
            guardrail_enabled = get_config_bool("pipeline.kyc_guardrail_enabled", True)
        except Exception:
            guardrail_enabled = True
        if guardrail_enabled and rec_result.get("recommendations"):
            try:
                from db import get_user_profile
                user_profile = get_user_profile("default") or {}
                rec_result["recommendations"] = _apply_kyc_guardrail(
                    rec_result["recommendations"], user_profile,
                )
                # 统计有标记的建议数
                flagged = sum(1 for r in rec_result["recommendations"] if r.get("guardrail_flags"))
                if flagged:
                    logger.info(
                        f"[pipeline:{trace_id}] KYC guardrail 标记 {flagged}/"
                        f"{len(rec_result['recommendations'])} 条建议"
                    )
            except Exception as e:
                logger.warning(f"[pipeline:{trace_id}] guardrail 标记失败: {e}")

        # P0 新增：机构动向共振检测（北向资金不可用，改用融资余额）
        try:
            inst_guardrail_enabled = get_config_bool("pipeline.institutional_guardrail_enabled", True)
        except Exception:
            inst_guardrail_enabled = True
        if inst_guardrail_enabled and rec_result.get("recommendations"):
            try:
                rec_result["recommendations"] = _apply_institutional_confirm(
                    rec_result["recommendations"], trace_id,
                )
            except Exception as e:
                logger.warning(f"[pipeline:{trace_id}] 机构动向共振检测失败: {e}")

        result["recommendations"] = rec_result
    except Exception as e:
        logger.warning(f"[pipeline] 建议提取落库失败: {e}")

    # 3. 更新对话摘要（异步）
    try:
        from agent.memory import update_conversation_summary
        update_conversation_summary(state.conversation_id)
        result["summary_updated"] = True
    except Exception as e:
        logger.debug(f"[pipeline] 摘要更新跳过: {e}")

    # 4. 提取用户偏好（受开关控制，默认关闭）
    try:
        extract_prefs = get_config_bool("agent.auto_extract_prefs", False)
        if extract_prefs and state.answer:
            from agent.feedback_learner import extract_preferences_from_conversation
            memory_ids = extract_preferences_from_conversation(
                state.conversation_id, state.original_query, state.answer
            )
            result["updated_memory_ids"] = memory_ids or []
    except Exception as e:
        logger.debug(f"[pipeline] 偏好提取跳过: {e}")

    # 5. 记录 token 审计
    try:
        from db.agents import record_token_audit
        record_token_audit(
            trace_id=trace_id,
            conversation_id=state.conversation_id,
            tokens_by_phase=state.tokens_used_by_phase,
            total_tokens=state.tokens_used,
        )
    except Exception as e:
        logger.debug(f"[pipeline] token 审计跳过: {e}")

    return result


# ── 简单闲聊处理 ──────────────────────────────

def _handle_simple_chat(
    query: str,
    state: PipelineState,
    trace_id: str,
) -> Generator[dict, None, None]:
    """处理简单闲聊，走快速路径不调专家。"""
    yield {"type": EVENT_SIMPLE_CHAT, "query": query}

    try:
        from services.llm_service import _call_llm, MODEL
        response = _call_llm(
            caller="simple_chat",
            trace_id=trace_id,
            model=MODEL,
            messages=[
                {"role": "system", "content": build_simple_chat_context(query)},
                {"role": "user", "content": query},
            ],
            temperature=0.5,
            max_tokens=200,
        )
        answer = response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"[pipeline] 简单闲聊 LLM 调用失败: {e}")
        answer = "您好！我是投资分析助手，可以帮您进行估值查询、持仓分析、投资策略咨询。请问有什么可以帮您？"

    state.answer = answer
    state.transition_to(PipelinePhase.SYNTHESIS)
    state.transition_to(PipelinePhase.MEMORY)
    state.transition_to(PipelinePhase.COMPLETED)

    yield {"type": EVENT_ANSWER, "content": answer,
           "specialist_results": [], "tool_calls": [],
           "simple_chat": True}


# ── 异常类 ──────────────────────────────────

class _PipelineCancelled(Exception):
    """Pipeline 被取消。"""
    pass


# ── Pipeline 开关检查 ──────────────────────────

def is_pipeline_enabled() -> bool:
    """检查 Pipeline 模式是否启用。

    默认 True，通过 orchestration_config.pipeline_enabled 控制。
    异常时自动降级到 ReAct，可放心开启。
    """
    try:
        from agent.orchestrator import get_orchestration_config
        return get_orchestration_config("pipeline_enabled", "true") == "true"
    except Exception:
        return True


def should_use_pipeline(query: str, history: list) -> bool:
    """判断是否应该使用 Pipeline 模式。

    策略：
    1. 全局开关未开 → False
    2. 简单闲聊 → True（走快速路径）
    3. 其他情况 → True
    """
    if not is_pipeline_enabled():
        return False
    return True
