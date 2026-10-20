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
from openai import APIConnectionError, APITimeoutError

from db.config import get_config_bool, get_config_int
from db.agents import create_agent_run
from infra.execution_budget import ExecutionCancelled

from agent.state.pipeline_state import PipelineState, PipelinePhase, create_initial_state
from agent.query_understander import (
    understand_query, is_simple_chat, get_complexity,
    needs_clarification, INTENT_FINANCIAL_QUERY,
)
from agent.infra.blackboard import Blackboard, extract_entry_from_result, reset_global_blackboard
from agent.infra.convergence import ConvergenceDetector, reset_convergence_detector
from agent.infra.termination import TerminationChecker, get_token_budget_for_complexity
from agent.infra.tool_dedup import reset_tool_call_cache
from agent.core.context_builder import build_specialist_context, build_simple_chat_context

logger = logging.getLogger(__name__)


def _effective_complexity(initial: str, plan_result: dict | None = None,
                          specialists: list | None = None) -> str:
    """合并预处理、计划和实际专家规模，避免后续阶段沿用过低复杂度。"""
    rank = {"chat": 0, "simple": 1, "medium": 2, "complex": 3}
    values = [initial or "medium"]
    plan = (plan_result or {}).get("plan") if isinstance(plan_result, dict) else None
    if isinstance(plan, dict):
        values.append(plan.get("complexity", ""))
    if len(specialists or []) >= 2:
        values.append("medium")
    return max((v for v in values if v in rank), key=lambda v: rank[v], default="medium")


def _route_model(agent_key: str) -> str:
    """成本感知路由：按 agent_key 选择分级模型，避免全部用最贵模型。

    模块级函数（2026-07-29 修复）：原为 _phase_execution 内的嵌套函数，
    但 _execute_steps_parallel 和 _fallback_execution 是独立的模块级函数，
    无法通过闭包访问，导致并行/降级路径触发 NameError（conv 191 案例）。
    """
    from agent.core.orchestrator import _get_model_for_agent, _is_cost_routing_enabled, MODEL as _ORCH_MODEL
    return _get_model_for_agent(agent_key) if _is_cost_routing_enabled() else _ORCH_MODEL


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

# P2 修复（conv_150/155）：否定语境正则
# 匹配 "否定词 + (保证)? + 保本/稳赚/稳赢/稳赚不赔"，如 "不保本"/"不能保本"/
# "不保证稳赚"/"无法保本"/"并非稳赚"。命中此模式的属于风险提示而非违规承诺，不触发告警。
# 说明：(?:保证)? 兼容 "不能保证保本" 中间的 "保证"，使否定区间覆盖到关键词。
_NEGATED_COMPLIANCE_RE = __import__("re").compile(
    r"(?:不保证|不能|无法|不存在|没有|并非|难以|不|非)"
    r"(?:保证)?"
    r"(稳赚不赔|保本|稳赚|稳赢)"
)


def _find_compliance_hits(text: str) -> list:
    """检测违规表述，排除否定语境。

    conv_150/155 修复：专家回复常出现"不能保本""不保证稳赚"等否定形式，原逻辑
    仅按关键词命中即告警，误报率高。本函数先标记所有否定区间，再剔除落在否定区间
    内的关键词命中，仅保留肯定性承诺（如"保证保本"/"稳赚不赔"/"绝对盈利"）。

    其余绝对化表述（"必涨"/"零风险"/"无风险"/"包赚"等）保持原检测逻辑不变。
    """
    if not text:
        return []
    negated_spans = [m.span() for m in _NEGATED_COMPLIANCE_RE.finditer(text)]

    hits = []
    for m in _COMPLIANCE_RE.finditer(text):
        # 落在否定区间内的命中（如"不保本"中的"保本"）跳过
        if any(ns[0] <= m.start() < ns[1] for ns in negated_spans):
            continue
        hits.append(m.group(0))
    return hits


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
        hits = _find_compliance_hits(answer)
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
            from agent.safety.hallucination_guard import quick_check_fund_codes
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
    prepared_rag_context: str | None = None,
    prepared_rag_result: dict | None = None,
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
    blackboard = Blackboard(max_broadcasts=get_config_int("agent.tool_broadcast_max_entries", 10))

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
        phase1_result = _phase_info_gather(state, query_info, history, trace_id, prepared_rag_context=prepared_rag_context, prepared_rag_result=prepared_rag_result,
                                          cancel_event=cancel_event)
        state.set_phase_result(PipelinePhase.INFO_GATHER.value, phase1_result)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.INFO_GATHER.value,
               "result": {"rag_count": len(phase1_result.get("rag", [])),
                          "has_portfolio": bool(phase1_result.get("portfolio"))}}

        # ── Phase 2: 计划生成 ──────────────────
        state.transition_to(PipelinePhase.PLANNING)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.PLANNING.value}
        phase2_result = _phase_planning(state, query_info, phase1_result, trace_id)
        state.set_phase_result(PipelinePhase.PLANNING.value, phase2_result)
        complexity = _effective_complexity(complexity, phase2_result, phase2_result.get("specialists", []))
        state.set_phase_result("effective_complexity", {"value": complexity, "source": "preprocess+plan+expert_count"})
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
                reflection_result = _run_followup_phase(
                    _phase_reflection, (state, query, phase3_result, blackboard, trace_id),
                    cancel_event, "pipeline.reflection_timeout"
                )
                state.set_phase_result(PipelinePhase.REFLECTION.value, reflection_result)
                yield {"type": EVENT_REFLECTION_DONE, "result": reflection_result}

                # 自纠错循环：低置信度时重跑质量最差的专家
                rerun_result = _maybe_rerun_specialist(
                    state, query, phase3_result, reflection_result,
                    blackboard, trace_id, cancel_event=cancel_event,
                )
                if rerun_result:
                    # 重跑成功：更新 phase3_result 的对应专家分析
                    phase3_result["specialists"] = _replace_specialist_analysis(
                        phase3_result["specialists"], rerun_result
                    )
                    phase3_result["tool_calls"] = [tc for sr in phase3_result["specialists"] for tc in sr.get("tool_calls", [])]
                    state.set_phase_result(PipelinePhase.EXECUTION.value, phase3_result)
                    # 修复 conv 192：重跑事件必须补全 agent_key/icon/analysis 等字段，
                    # 否则 conversations.py:1879 的 event["agent_key"] 直接索引会抛 KeyError
                    yield {"type": EVENT_SPECIALIST_DONE,
                           "agent_key": rerun_result.get("agent_key", ""),
                           "agent": rerun_result.get("agent", ""),
                           "icon": rerun_result.get("icon", "🤖"),
                           "analysis": rerun_result.get("analysis", ""),
                           **{k: rerun_result[k] for k in ("verdict", "confidence", "status", "completion_status", "output_quality", "asset_actions") if k in rerun_result},
                           "duration_ms": rerun_result.get("duration_ms", 0),
                           "rerun": True}
            except (ExecutionCancelled, _PipelineCancelled):
                raise
            except Exception as refl_err:
                logger.warning(f"[pipeline] Reflection 失败，跳过: {refl_err}")
                reflection_result = {"status": "failed", "confidence_adjustment": -.3, "error": str(refl_err)}
                state.set_phase_result("reflection", reflection_result)
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
                    debate_result = _run_followup_phase(
                        _phase_debate, (state, query, phase3_result, blackboard, trace_id),
                        cancel_event, "pipeline.debate_timeout"
                    )
                    state.set_phase_result(PipelinePhase.DEBATE.value, debate_result)
                    yield {"type": EVENT_DEBATE_DONE, "result": debate_result}
                except (ExecutionCancelled, _PipelineCancelled):
                    raise
                except Exception as debate_err:
                    logger.warning(f"[pipeline] 辩论失败，跳过: {debate_err}")
                    debate_result = {"status": "failed", "error": str(debate_err)}
                    state.set_phase_result(PipelinePhase.DEBATE.value, debate_result)
                yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.DEBATE.value}
            else:
                logger.debug("[pipeline] 无冲突，跳过辩论节点")

        # ── Phase 3.8: 交叉审阅（P2-G 修复 conv#131）──────────────
        # 原 Pipeline 路径未触发 run_cross_review_opinion，导致 cross_review_results 恒为空
        # 此处补全：并行执行交叉审阅，emit cross_review_start/done 事件
        # 让 conversations.py 通过事件监听器正确写入 metadata.cross_review_results
        # R2（2026-10-15）：接入 orchestrator 的 should_run_cross_review 统一决策树，
        # 替换原先「仅专家数 >= 2」的粗判断，两引擎交叉审阅触发条件保持一致。
        _phase3_specialists_list = phase3_result.get("specialists", []) or []
        # 过滤掉交叉审阅结果（Phase 3 尚未有 cross_review，安全兜底）
        _phase3_originals = [sr for sr in _phase3_specialists_list if not sr.get("is_cross_review")]
        # 先安全初始化配置（保证异常时 else 分支与 except 分支可用）
        try:
            cross_review_enabled_cfg = get_config_bool("cross_review_enabled", True)
        except Exception:
            cross_review_enabled_cfg = True
        try:
            cross_review_min_spec = get_config_int("cross_review_min_specialists", 1)
        except Exception:
            cross_review_min_spec = 1
        _should_cross_review = False
        try:
            from agent.core.orchestrator import should_run_cross_review, detect_conflicts
            # 用关键词匹配版冲突检测构造 {detected, severity} 结构（不耗 LLM）
            _conflicts = detect_conflicts(_phase3_originals)
            _should_cross_review = should_run_cross_review(
                specialist_results=_phase3_originals,
                complexity=complexity,
                conflicts=_conflicts,
                force_skip=False,
                enabled=cross_review_enabled_cfg,
                min_specialists=cross_review_min_spec,
                min_severity="medium",
                predicted_need_cross_review=False,
                needs_arbitration=False,
            )
        except Exception as _cr_check_err:
            logger.warning(f"[pipeline] 交叉审阅决策失败，回退专家数判断: {_cr_check_err}")
            _should_cross_review = cross_review_enabled_cfg and len(_phase3_originals) >= 2

        if _should_cross_review and _followup_budget_exhausted(state, for_review=True):
            state.set_phase_result("cross_review_status", {"status": "budget_exhausted"})
            _should_cross_review = False

        if _should_cross_review:
            logger.info(
                f"[pipeline] 触发交叉审阅（{len(_phase3_originals)} 个专家）"
            )
            yield {
                "type": "status",
                "message": f"正在进行交叉审阅（{len(_phase3_originals)} 个专家并行互相验证）..."
            }

            # 构造 peer 分析字典（对原始专家做交叉审阅）
            _peer_analyses = {
                sr.get("agent_key", f"agent_{i}"): sr.get("analysis", "")
                for i, sr in enumerate(_phase3_originals)
            }
            _original_specialists = list(_phase3_originals)

            # 发送所有 cross_review_start 事件
            for sr in _original_specialists:
                yield {
                    "type": "cross_review_start",
                    "agent_key": sr.get("agent_key", ""),
                    "agent": sr.get("agent", sr.get("agent_key", "")),
                    "icon": sr.get("icon", "🤖"),
                }

            # 并行执行交叉审阅
            from infra.execution_budget import SPECIALIST_EXECUTOR
            from agent.core.multi_agent import run_cross_review_opinion as _run_cr_op

            review_output_limit = _review_tokens_remaining(state) // max(1, len(_original_specialists))

            def _review_single_pipeline(sr):
                peer = {k: v for k, v in _peer_analyses.items()
                        if k != sr.get("agent_key")}
                try:
                    return sr, _run_cr_op(
                        sr.get("agent_key", ""), state.refined_query,
                        sr.get("analysis", ""), peer,
                        max_output_tokens=max(1, review_output_limit),
                        trace_id=trace_id,
                        conversation_id=state.conversation_id,
                        message_id=state.message_id,
                    )
                except (ExecutionCancelled, _PipelineCancelled, TimeoutError):
                    raise
                except Exception as e:
                    logger.warning(
                        f"[pipeline] 交叉审阅失败 {sr.get('agent_key', '')}: {e}"
                    )
                    # 兜底：返回一个标记失败的 cross_review 结果
                    return sr, {
                        "agent_key": sr.get("agent_key", ""),
                        "agent": sr.get("agent", sr.get("agent_key", "")),
                        "icon": sr.get("icon", "🤖"),
                        "analysis": f"交叉审阅失败: {e}",
                        "status": "failed",
                        "tool_calls": [],
                        "duration_ms": 0,
                        "is_cross_review": True,
                        "opinion": {"agreements": [], "disagreements": [], "additions": []},
                    }

            _cr_results = []
            try:
                tasks = {i: (lambda sr=sr: _review_single_pipeline(sr))
                         for i, sr in enumerate(_original_specialists)}
                for index, future in _phase_futures(tasks, SPECIALIST_EXECUTOR,
                                                     get_config_int('agent.cross_review_timeout', 120), cancel_event):
                    sr = _original_specialists[index]
                    try:
                        _, cr_result = future.result()
                        state.record_phase_tokens('cross_review', cr_result.get('tokens_used', 0))
                        _cr_results.append(cr_result)
                        # 追加到 phase3_result 让综合阶段能看到
                        phase3_result.setdefault("specialists", []).append(cr_result)
                        yield {
                            "type": "cross_review_done",
                            "agent_key": sr.get("agent_key", ""),
                            "agent": sr.get("agent", sr.get("agent_key", "")),
                            "icon": sr.get("icon", "🤖"),
                            "analysis": cr_result.get("analysis", ""),
                            "status": cr_result.get("status", "success"),
                            "is_cross_review": True,
                            "duration_ms": cr_result.get("duration_ms", 0),
                        }
                    except (ExecutionCancelled, _PipelineCancelled, TimeoutError):
                        raise
                    except Exception as e:
                        logger.error(
                            f"[pipeline] 交叉审阅 future 失败 {sr.get('agent_key', '')}: {e}"
                        )
                        yield {
                            "type": "cross_review_done",
                            "agent_key": sr.get("agent_key", ""),
                            "agent": sr.get("agent", sr.get("agent_key", "")),
                            "icon": sr.get("icon", "🤖"),
                            "analysis": f"交叉审阅失败: {e}",
                            "duration_ms": 0,
                        }
            except (ExecutionCancelled, _PipelineCancelled):
                raise _PipelineCancelled()
            except Exception as cr_phase_err:
                logger.warning(f"[pipeline] 交叉审阅阶段失败，继续综合: {cr_phase_err}")

            if (_cr_results and _review_tokens_remaining(state) <= 0):
                state.set_phase_result("cross_review_status", {"status": "budget_exhausted"})
            elif len(_cr_results) != len(_original_specialists):
                state.set_phase_result("cross_review_status", {"status": "failed"})
            elif _cr_results:
                state.set_phase_result("cross_review_status", {"status": "completed"})
            if _cr_results:
                logger.info(
                    f"[pipeline] 交叉审阅完成，{len(_cr_results)} 个专家已互相验证"
                )
        else:
            logger.debug(
                f"[pipeline] 跳过交叉审阅（enabled={cross_review_enabled_cfg}, "
                f"specialists={len(_phase3_originals)})"
            )

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

        if cancel_event and cancel_event.is_set():
            raise _PipelineCancelled()
        phase4_result = _phase_synthesis(
            state, query, phase3_result, blackboard, trace_id,
            reflection_result=reflection_result,
            debate_result=debate_result,
            plan=phase2_result,
        )
        if cancel_event and cancel_event.is_set():
            raise _PipelineCancelled()
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
               "confidence": phase4_result.get("confidence", 0.0),
               "quality_status": phase4_result.get("quality_status", "degraded"),
               "quality_issues": phase4_result.get("quality_issues", []),
               "phase_timings": _phase_timings_ms(state),
               "arbitration": phase4_result.get("arbitration"),
               "cross_review_results": phase4_result.get("cross_review_results", []),
               "completion_status": phase4_result.get("completion_status", "completed"),
               "reflection": phase4_result.get("reflection"),
               "cross_review_status": state.get_phase_result("cross_review_status")}

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

    except (_PipelineCancelled, ExecutionCancelled):
        state.transition_to(PipelinePhase.CANCELLED)
        yield {"type": EVENT_TERMINATED, "reason": "用户取消"}
    except TimeoutError:
        # 工作线程可能仍在退出，终止本轮，禁止外层 ReAct 重复执行相同专家。
        state.transition_to(PipelinePhase.FAILED)
        state.error = "execution_timeout"
        yield {"type": EVENT_TERMINATED, "reason": "执行超时，请稍后重试"}
    except APIConnectionError as e:
        state.transition_to(PipelinePhase.FAILED)
        state.error = "model_connection_error"
        logger.warning("[pipeline:%s] 模型连接失败，终止本轮，不再降级 ReAct: %s", trace_id, e)
        reason = "模型请求超时，请稍后重试" if isinstance(e, APITimeoutError) else "模型服务连接失败，请检查网络及服务地址后重试"
        yield {"type": EVENT_TERMINATED, "reason": reason}
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
    message_id: int | None = None,
) -> Generator[dict, None, None]:
    """从澄清 checkpoint 恢复 Pipeline，用用户回答改写 query 后继续执行。

    跳过 Phase 0 的澄清检查，直接从 Phase 1（信息收集）开始。
    query 改写：original_query + user_answer 拼接。

    P0-3 修复 conv_189：增加 message_id 参数，覆写 state.message_id 为澄清后的新占位消息 id，
    避免 agent_runs 归属到旧的澄清消息（msg 549），导致恢复逻辑查不到（按 msg 551 查询）。
    """
    start_time = time.time()

    # 恢复状态
    state = PipelineState.from_dict(checkpoint)
    # P0-3: 覆写 message_id 为澄清续答后的新占位消息 id
    if message_id:
        old_msg_id = state.message_id
        state.message_id = message_id
        logger.info(f"[pipeline:{trace_id}] 澄清续答 message_id 覆写: {old_msg_id} → {message_id}")
    query_info = checkpoint.get("query_info", {})
    original_query = state.original_query

    # 用回答融合 query（LLM 语义融合，失败降级为拼接）
    from agent.query.query_rewriter import fuse_clarified_query
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
    blackboard = Blackboard(max_broadcasts=get_config_int("agent.tool_broadcast_max_entries", 10))

    try:
        # 直接进入 Phase 1（跳过 Phase 0 澄清检查）
        state.transition_to(PipelinePhase.INFO_GATHER)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.INFO_GATHER.value}
        phase1_result = _phase_info_gather(state, query_info, history, trace_id, cancel_event=cancel_event)
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
                reflection_result = _run_followup_phase(
                    _phase_reflection, (state, state.refined_query, phase3_result, blackboard, trace_id),
                    cancel_event, "pipeline.reflection_timeout"
                )
                state.set_phase_result(PipelinePhase.REFLECTION.value, reflection_result)
                yield {"type": EVENT_REFLECTION_DONE, "result": reflection_result}

                rerun_result = _maybe_rerun_specialist(
                    state, state.refined_query, phase3_result, reflection_result,
                    blackboard, trace_id, cancel_event=cancel_event,
                )
                if rerun_result:
                    phase3_result["specialists"] = _replace_specialist_analysis(
                        phase3_result["specialists"], rerun_result
                    )
                    state.set_phase_result(PipelinePhase.EXECUTION.value, phase3_result)
                    phase3_result["tool_calls"] = [tc for sr in phase3_result["specialists"] for tc in sr.get("tool_calls", [])]
                    yield {"type": EVENT_SPECIALIST_DONE,
                           **{k: rerun_result[k] for k in ("agent_key", "icon", "analysis", "verdict", "confidence", "status", "completion_status", "output_quality", "asset_actions") if k in rerun_result},
                           "agent": rerun_result.get("agent", ""),
                           "result": rerun_result,
                           "rerun": True}
            except (ExecutionCancelled, _PipelineCancelled):
                raise
            except Exception as refl_err:
                logger.warning(f"[pipeline:{trace_id}] 续答 Reflection 失败，跳过: {refl_err}")
                reflection_result = {"status": "failed", "confidence_adjustment": -.3, "error": str(refl_err)}
                state.set_phase_result("reflection", reflection_result)
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
                    debate_result = _run_followup_phase(
                        _phase_debate, (state, state.refined_query, phase3_result, blackboard, trace_id),
                        cancel_event, "pipeline.debate_timeout"
                    )
                    state.set_phase_result(PipelinePhase.DEBATE.value, debate_result)
                    yield {"type": EVENT_DEBATE_DONE, "result": debate_result}
                except (ExecutionCancelled, _PipelineCancelled):
                    raise
                except Exception as debate_err:
                    logger.warning(f"[pipeline:{trace_id}] 续答辩论失败，跳过: {debate_err}")
                    debate_result = {"status": "failed", "error": str(debate_err)}
                    state.set_phase_result(PipelinePhase.DEBATE.value, debate_result)
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

        if cancel_event and cancel_event.is_set():
            raise _PipelineCancelled()
        phase4_result = _phase_synthesis(
            state, state.refined_query, phase3_result, blackboard, trace_id,
            reflection_result=reflection_result,
            debate_result=debate_result,
            plan=phase2_result,
        )
        if cancel_event and cancel_event.is_set():
            raise _PipelineCancelled()
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
               "confidence": phase4_result.get("confidence", 0.0),
               "quality_status": phase4_result.get("quality_status", "degraded"),
               "quality_issues": phase4_result.get("quality_issues", []),
               "phase_timings": _phase_timings_ms(state),
               "arbitration": phase4_result.get("arbitration"),
               "cross_review_results": phase4_result.get("cross_review_results", []),
               "completion_status": phase4_result.get("completion_status", "completed"),
               "reflection": phase4_result.get("reflection"),
               "cross_review_status": state.get_phase_result("cross_review_status")}

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

    except (_PipelineCancelled, ExecutionCancelled):
        state.transition_to(PipelinePhase.CANCELLED)
        yield {"type": EVENT_TERMINATED, "reason": "用户取消"}
    except TimeoutError:
        state.transition_to(PipelinePhase.FAILED)
        state.error = "execution_timeout"
        yield {"type": EVENT_TERMINATED, "reason": "执行超时，请稍后重试"}
    except APIConnectionError as e:
        state.transition_to(PipelinePhase.FAILED)
        state.error = "model_connection_error"
        logger.warning("[pipeline:%s] 续答模型连接失败，终止本轮: %s", trace_id, e)
        reason = "模型请求超时，请稍后重试" if isinstance(e, APITimeoutError) else "模型服务连接失败，请检查网络及服务地址后重试"
        # 续答路由直接消费流水线事件，error 才会写入消息失败状态并结束 SSE。
        yield {"type": EVENT_ERROR, "error": str(e), "message": reason, "terminal": True}
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
    from agent.memory.memory import build_user_memory_context
    from agent.memory.feedback_learner import get_preference_context

    # 1. Query 理解
    history_summary = _summarize_history_for_understanding(history)
    # 2026-07-29: 分离 KYC 和持仓上下文，避免 LLM 因看不到 KYC 而频繁澄清（conv 191 案例）
    full_memory = build_user_memory_context(user_id) if user_id else ""
    portfolio_summary = full_memory[:500]  # 持仓+历史主题+记忆段
    kyc_summary = get_preference_context(user_id)[:500] if user_id else ""  # KYC 画像段
    query_info = understand_query(
        query, history_summary, portfolio_summary, trace_id, kyc_summary
    )

    # 2. Query 改写（复用现有 query_rewriter）
    refined_query = query
    rewrite_reason = ""
    try:
        from agent.query.query_rewriter import rewrite_query, needs_rewrite
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

    # 2.5 文章内容抓取与注入（G-article-fix, 2026-07-24）
    # 根因：pipeline 路径未调用 enrich_query_with_article，导致非 article_expert 专家
    #   （如 risk_assessor）收不到文章内容，只能回复"我无法访问链接"。
    # 修复：检测 URL 后抓取文章，注入到 refined_query，使所有专家都能看到文章。
    # article_expert 也不再需要自行调用 fetch_article 工具，节省 1 轮检索。
    article_context = ""
    try:
        from agent.core.orchestrator import enrich_query_with_article, detect_urls
        if detect_urls(query):
            enriched_query, article_context = enrich_query_with_article(query)
            if article_context and not article_context.startswith("[抓取失败]"):
                # 用 enriched_query 替换 refined_query（保留文章内容）
                # 注意：只在抓取成功时替换，失败时保持原 query 让专家引导用户
                refined_query = enriched_query
                logger.info(f"[pipeline] 文章内容已注入 refined_query ({len(article_context)} 字符)")
            else:
                logger.warning(f"[pipeline] 文章抓取失败，专家将引导用户粘贴正文")
    except Exception as e:
        logger.warning(f"[pipeline] 文章抓取注入失败（不阻塞主流程）: {e}")

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
        "article_context": article_context,
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
    prepared_rag_context: str | None = None,
    prepared_rag_result: dict | None = None,
    cancel_event=None,
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
    from agent.core.answer_policy import has_attribution_intent, has_explicit_action_intent
    import re
    original_query = state.original_query or state.refined_query
    market_only = (has_attribution_intent(original_query) and not has_explicit_action_intent(original_query)
                   and not re.search(r"我的?持仓|我的?基金|我买|我持有|对我|组合敞口|持仓影响", original_query))

    # 并行执行 RAG 检索 + 持仓 + 估值 + 记忆（省 3-5 秒串行等待）
    from infra.execution_budget import DATA_EXECUTOR

    def _rag_task():
        """RAG 检索（子查询展开 + intent 驱动 content_types + rag_logs 日志）"""
        if prepared_rag_context is not None:
            details = prepared_rag_result or {}
            return {'rag': details.get('results', []), 'rag_context': prepared_rag_context,
                    'rag_low_quality': details.get('rag_low_quality', True)}
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
                    from agent.query.query_rewriter import expand_query
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
            retrieval_degraded = False
            for sq in sub_queries:
                rag_data = build_rag_context_with_details(sq, limit=5, content_types=content_types)
                if isinstance(rag_data, dict):
                    retrieval_degraded = retrieval_degraded or rag_data.get("rag_low_quality", True)
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
            from services.rag.evidence_quality import hydrate_evidence
            from db._conn import _get_conn
            hydrate_evidence(deduped, _get_conn)

            # 构建上下文
            rag_context = _build_rag_context_from_results(deduped[:8])

            logger.info(
                f"[pipeline] RAG 检索完成: {len(deduped)} 条结果 "
                f"(子查询 {len(sub_queries)}, FTS {total_fts}, Chroma {total_chroma})"
            )

            # RAG 命中质量检测
            max_score = max((float(r.get("_score", 0)) for r in deduped), default=0.0)
            rag_low_quality = retrieval_degraded or any(r.get("quality_status") != "usable" for r in deduped)
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
            return {"rag": [], "rag_context": "", "rag_low_quality": True}

    def _portfolio_task():
        """持仓摘要（含完整盈亏+交易记录+集中度）"""
        try:
            from services.portfolio_context import build_portfolio_context
            from db.portfolio import get_portfolio_snapshot
            snapshot = get_portfolio_snapshot("default")
            return {"portfolio": build_portfolio_context("default", snapshot=snapshot), "portfolio_snapshot": snapshot}
        except Exception as e:
            logger.debug(f"[pipeline] 持仓加载失败: {e}")
            return {"portfolio": "", "portfolio_snapshot": {}}

    def _valuation_task():
        """估值预取（基于 targets + 持仓行业指数遍历）"""
        valuations = {}
        queried = set()  # 去重

        # 1. 基于用户问题中提取的 targets
        targets = query_info.get("targets", [])
        if targets:
            for target in targets[:3]:
                if target in queried:
                    continue
                val = _lookup_valuation(target)
                if val:
                    valuations[target] = val
                    queried.add(target)

        # 2. 修复：遍历持仓中的行业/主题指数，自动补全估值查询
        # 原问题：只查用户文本中提及的 targets（最多3个），持仓中的其他行业指数被遗漏
        if market_only:
            return valuations
        try:
            from db.portfolio import list_holdings
            holdings = list_holdings()
            for h in holdings:
                fname = h.get("fund_name", "") or ""
                fcode = h.get("fund_code", "") or ""
                # 从基金名称中提取行业关键词
                industry_keywords = _extract_industry_from_fund_name(fname)
                for kw in industry_keywords:
                    if kw in queried:
                        continue
                    val = _lookup_valuation(kw)
                    if val:
                        valuations[kw] = val
                        queried.add(kw)
                    if len(valuations) >= 8:  # 最多预取 8 个，避免过多
                        break
                if len(valuations) >= 8:
                    break
        except Exception as e:
            logger.debug(f"[pipeline] 持仓行业指数估值预取失败: {e}")

        return valuations

    def _memory_task():
        """用户记忆"""
        try:
            from agent.memory.memory import build_user_memory_context
            return build_user_memory_context("default")[:500]
        except Exception as e:
            logger.debug(f"[pipeline] 记忆加载失败: {e}")
            return ""

    tasks = {'rag': _rag_task, 'portfolio': _portfolio_task,
             'valuations': _valuation_task, 'memory': _memory_task}
    if market_only:
        tasks = {'rag': _rag_task, 'valuations': _valuation_task}
    for key, future in _phase_futures(tasks, DATA_EXECUTOR,
                                      get_config_int('pipeline.info_gather_timeout', 60), cancel_event):
        value = future.result()
        if key in {'rag', 'portfolio'}:
            results.update(value)
        else:
            results[key] = value

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
        score = r.get("rerank_score")
        part = f"[{label}] [evidence_id:{r.get('evidence_id', '')}] {title}"
        part += f" [quality:{r.get('quality_status', 'unknown')}; semantic_score:{score}]"
        if source:
            part += f" [来源: {source}]"
        part += f" [as_of:{r.get('as_of_date') or r.get('time') or 'unknown'}; usage_scope:{r.get('usage_scope', 'background_only')}]"
        part += f"\n{body}"
        for key in ("limitations", "counterpoints", "quality_issues"):
            if r.get(key):
                part += f"\n{key}: {r[key]}"
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


# 基金名称 → 行业指数关键词映射（用于持仓估值预取）
_FUND_INDUSTRY_MAP = {
    "白酒": "白酒", "消费": "消费", "医疗": "医疗", "医药": "医药",
    "医疗器械": "医疗器械", "生物": "生物医药", "创新药": "创新药",
    "恒生科技": "恒生科技", "港股通": "港股通", "恒生互联网": "恒生互联网",
    "房地产": "房地产", "地产": "房地产", "畜牧": "畜牧养殖", "养殖": "畜牧养殖",
    "新能源": "新能源", "光伏": "光伏", "半导体": "半导体", "芯片": "芯片",
    "军工": "军工", "国防": "军工", "银行": "银行", "券商": "券商",
    "保险": "保险", "红利": "红利", "科技": "科技", "互联网": "互联网",
    "食品饮料": "食品饮料", "食品": "食品饮料", "消费红利": "消费红利",
    "创业板": "创业板", "沪深300": "沪深300", "中证500": "中证500",
    "中证1000": "中证1000", "科创板": "科创板", "上证50": "上证50",
}

def _extract_industry_from_fund_name(fund_name: str) -> list[str]:
    """从基金名称中提取对应的行业指数关键词。

    用于持仓估值预取：遍历持仓基金名称，提取行业关键词，
    自动查询对应的行业指数估值，避免只查用户提及的标的。
    """
    keywords = []
    if not fund_name:
        return keywords
    for kw, index_name in _FUND_INDUSTRY_MAP.items():
        if kw in fund_name and index_name not in keywords:
            keywords.append(index_name)
    return keywords


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
        from agent.core.plan_executor import generate_plan
        from db.agents import load_specialist_agents
        from agent.core.router import SmartRouter

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
            # P0 修复 conv_190：路由用 original_query，避免文章正文注入 refined_query 后污染路由
            # 根因：Phase 0 的文章注入会把 7000 字文章正文追加到 refined_query，
            #   导致关键词路由命中"科技/半导体/跌"等，错误路由到 5 个通用专家。
            # 方案：路由基于用户原始 query，refined_query（含文章内容）仅供专家执行使用。
            _route_query = state.original_query or state.refined_query
            logger.info(f"[pipeline] P4 路由参数: route_query='{_route_query[:50]}', refined='{state.refined_query[:50]}', portfolio_len={len(portfolio_summary)}")
            # P1-5 修复 conv_189：传入 original_query，避免澄清融合丢失"加仓"等动作关键词
            route_result = router.route(_route_query, history_summary, portfolio_summary,
                                        original_query=state.original_query or "")
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
    from agent.core.multi_agent import run_specialist

    specialists_result = []
    all_tool_calls = []
    connection_error = None

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
    # 配置开关 pipeline.parallel_execution 默认 true；依赖步骤走顺序路径。
    try:
        parallel_enabled = get_config_bool("pipeline.parallel_execution", True)
    except Exception:
        parallel_enabled = True

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
            query=state.original_query or state.refined_query,
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
            result = _run_pipeline_specialist(run_specialist, cancel_event, budget_state=state,
                agent_key=agent_key,
                query=step_query,
                prebuilt_context=ctx,
                trace_id=trace_id,
                from_pipeline=True,
                model=_route_model(agent_key),
                conversation_id=state.conversation_id,
                message_id=state.message_id,
                blackboard=blackboard,
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
                    result=result.get("analysis", "")[:16000],
                    tool_calls=result.get("tool_calls", []),
                    evidence={k: result[k] for k in ("verdict", "confidence", "action_signals", "asset_actions", "portfolio_stance", "structured") if k in result},
                    duration_ms=duration_ms_for_log,
                    trace_id=trace_id,
                    status=result.get("status", "success"),
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
                "verdict": result.get("verdict", "unknown"),
                "confidence": result.get("confidence", 0.0),
                "asset_actions": result.get("asset_actions", []),
                "completion_status": result.get("completion_status"),
                "output_quality": result.get("output_quality"),
                "status": result.get("status", "success"),
                "error": result.get("status") == "failed",
                "duration_ms": duration_ms,
            }

        except (ExecutionCancelled, _PipelineCancelled, TimeoutError):
            raise
        except Exception as e:
            logger.error(f"[pipeline] 专家 {agent_name} 执行失败: {e}")
            if isinstance(e, APIConnectionError):
                connection_error = e
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

    if cancel_event and cancel_event.is_set():
        raise _PipelineCancelled()
    if connection_error and not any(s.get("status") not in {"failed", "error", "timeout"} for s in specialists_result):
        raise connection_error
    return {
        "specialists": specialists_result,
        "tool_calls": all_tool_calls,
    }


def _completed_with_deadline(future_to_meta, cancel_event, timeout):
    """没有完成事件时也检查取消与截止，不在退出时等待线程。"""
    from infra.execution_budget import remaining_seconds
    import concurrent.futures
    pending = set(future_to_meta)
    deadline = time.monotonic()+timeout
    try:
        while pending:
            if cancel_event and cancel_event.is_set():
                raise _PipelineCancelled()
            remaining = min(deadline-time.monotonic(),remaining_seconds(timeout))
            if remaining <= 0:
                raise TimeoutError('阶段执行超时')
            done, pending = concurrent.futures.wait(pending,timeout=min(.05,remaining),
                                                   return_when=concurrent.futures.FIRST_COMPLETED)
            yield from done
    except ExecutionCancelled:
        raise _PipelineCancelled()
    finally:
        for future in future_to_meta:
            future.cancel()


def _phase_futures(tasks, executor, timeout, cancel_event):
    """阶段共用一次截止时间，提交失败/取消/退出均取消尚未开始的工作。"""
    from infra.execution_budget import execution_budget, remaining_seconds
    futures = {}
    try:
        with execution_budget(timeout, cancel_event):
            remaining_seconds(timeout)
            for key, task in tasks.items():
                remaining_seconds(timeout)
                futures[executor.submit(task)] = key
            for future in _completed_with_deadline(futures, cancel_event, timeout):
                yield futures[future], future
    except ExecutionCancelled:
        raise _PipelineCancelled()
    finally:
        for future in futures:
            future.cancel()


def _specialist_output_limit(state, count=1):
    """分摊输出额度；真实输入 usage 另行入账，避免严格输入预检拒绝所有首轮。"""
    if not state.token_budget:
        return get_config_int('llm.max_tokens_agent', 8000)
    budget = state.token_budget
    remaining = max(0, budget.get('execution', 4000) - state.tokens_used_by_phase.get('execution', 0))
    if budget.get('max_total'):
        reserved = budget.get('review', 1500) + budget.get('synthesis', 0)
        remaining = min(remaining, max(0, budget['max_total'] - reserved - state.tokens_used))
    return remaining // max(1, count)


def _exhausted_specialist(agent_key):
    return {'agent_key': agent_key, 'agent': agent_key, 'analysis': '专家预算已用尽，未执行追加分析。',
            'status': 'budget_exhausted', 'completion_status': 'degraded', 'tokens_used': 0,
            'verdict': 'unknown', 'confidence': 0.0, 'asset_actions': [], 'tool_calls': []}


def _run_pipeline_specialist(func, cancel_event, budget_state=None, **kwargs):
    """顺序及计划降级路径复用同一共享池、输出上限与预算等待。"""
    from infra.execution_budget import SPECIALIST_EXECUTOR
    if budget_state is not None:
        output_limit = _specialist_output_limit(budget_state)
        if output_limit <= 0:
            return _exhausted_specialist(kwargs.get('agent_key', ''))
        kwargs['max_output_tokens'] = output_limit
    for _, future in _phase_futures({0: lambda: func(**kwargs)}, SPECIALIST_EXECUTOR,
                                    get_config_int('agent.specialist_timeout', 600), cancel_event):
        return future.result()


def _run_followup_phase(func, args, cancel_event, config_key):
    """反思/辩论共用一次阶段预算，传播到连续模型调用。"""
    if cancel_event and cancel_event.is_set():
        raise _PipelineCancelled()
    state = args[0] if args else None
    if isinstance(state, PipelineState) and _followup_budget_exhausted(state, for_review=True):
        return {"status": "budget_exhausted", "confidence_adjustment": -.2,
                "quality_issues": ["预算已用尽，未执行追加审查"], "missing_perspectives": []}
    from infra.execution_budget import SPECIALIST_EXECUTOR
    for _, future in _phase_futures({0: lambda: func(*args)}, SPECIALIST_EXECUTOR,
                                    get_config_int(config_key, 180), cancel_event):
        return future.result()


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
    from agent.core.multi_agent import run_specialist
    from agent.core.context_builder import build_specialist_context

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
            query=state.original_query or state.refined_query,
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

    # 并行工作由共享有界池限制总在途数量。
    specialists_result = []
    all_tool_calls = []
    connection_error = None

    output_limit = _specialist_output_limit(state, len(step_ctxs))

    def _run_one(step, ctx, agent_key):
        if output_limit <= 0:
            return _exhausted_specialist(agent_key)
        step_query = step.get("query", state.refined_query)
        return run_specialist(
            agent_key=agent_key,
            max_output_tokens=output_limit,
            query=step_query,
            prebuilt_context=ctx,
            trace_id=trace_id,
            from_pipeline=True,
            model=_route_model(agent_key),
            conversation_id=state.conversation_id,
            message_id=state.message_id,
            blackboard=blackboard,
        )

    from infra.execution_budget import SPECIALIST_EXECUTOR
    tasks = {i: (lambda step=step, ctx=ctx: _run_one(step, ctx, step.get("agent_key")))
             for i, (step, _, _, ctx) in enumerate(step_ctxs)}
    phase_started = time.monotonic()

    def timed_out_result(index):
        step, config, icon, _ = step_ctxs[index]
        key = step.get("agent_key")
        result = {"agent_key": key, "agent": step.get("agent_name", config.get("name", key)),
                  "icon": icon, "analysis": "专家执行超时，本轮未取得完整分析。",
                  "status": "timeout", "completion_status": "failed", "verdict": "unknown",
                  "confidence": 0.0, "asset_actions": [], "tool_calls": [],
                  "duration_ms": int((time.monotonic() - phase_started) * 1000)}
        try:
            create_agent_run(conversation_id=state.conversation_id, message_id=state.message_id,
                agent_key=key, agent_name=result["agent"], query=step.get("query", state.refined_query),
                result=result["analysis"], duration_ms=result["duration_ms"], trace_id=trace_id, status="timeout")
        except Exception as log_err:
            logger.warning("[pipeline-parallel] 超时记录写入失败 (%s): %s", key, log_err)
        return result

    try:
        for index, future in _phase_futures(tasks, SPECIALIST_EXECUTOR,
                                            get_config_int('agent.specialist_timeout', 600), cancel_event):
            if cancel_event and cancel_event.is_set():
                raise _PipelineCancelled()

            step, agent_config, agent_icon, _ = step_ctxs[index]
            agent_key = step.get("agent_key")
            agent_name = step.get("agent_name", agent_key)
            step_query = step.get("query", state.refined_query)
            step_start = time.time()

            try:
                result = future.result()
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
                        result=result.get("analysis", "")[:16000],
                        tool_calls=result.get("tool_calls", []),
                        evidence={k: result[k] for k in ("verdict", "confidence", "action_signals", "asset_actions", "portfolio_stance", "structured") if k in result},
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        status=result.get("status", "success"),
                    )
                except Exception as log_err:
                    logger.warning(f"[pipeline-parallel] agent_runs 写入失败 ({agent_key}): {log_err}")

                yield {
                    "type": EVENT_SPECIALIST_DONE,
                    "agent_key": agent_key,
                    "agent": agent_name,
                    "icon": agent_icon,
                    "analysis": result.get("analysis", ""),
                    "verdict": result.get("verdict", "unknown"),
                    "confidence": result.get("confidence", 0.0),
                    "asset_actions": result.get("asset_actions", []),
                    "completion_status": result.get("completion_status"),
                    "output_quality": result.get("output_quality"),
                    "status": result.get("status", "success"),
                    "error": result.get("status") == "failed",
                    "duration_ms": duration_ms,
                }
            except (ExecutionCancelled, _PipelineCancelled):
                raise
            except TimeoutError:
                # 单个 worker 耗尽自己的期限，不影响同批中已完成/仍可完成的专家。
                result = timed_out_result(index)
                specialists_result.append(result)
                state.set_phase_result("execution_status", {"status": "timeout"})
                yield {"type": EVENT_SPECIALIST_DONE, **result, "error": True}
            except Exception as e:
                logger.error(f"[pipeline-parallel] 专家 {agent_name} 执行失败: {e}")
                if isinstance(e, APIConnectionError):
                    connection_error = e
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

    except TimeoutError as exc:
        if not any(s.get("status") not in {"timeout", "failed", "error"} for s in specialists_result):
            raise
        # 阶段超时不丢弃已完成专家；缺席专家显式标记，综合质量门槛会降级。
        logger.warning("[pipeline:%s] 专家阶段超时，保留 %s 份结果继续综合: %s",
                       trace_id, len(specialists_result), exc)
        state.set_phase_result("execution_status", {"status": "timeout"})
        completed = {result.get("agent_key") for result in specialists_result}
        for index, (step, config, icon, _) in enumerate(step_ctxs):
            if cancel_event and cancel_event.is_set():
                raise _PipelineCancelled()
            key = step.get("agent_key")
            if key in completed:
                continue
            result = timed_out_result(index)
            specialists_result.append(result)
            yield {"type": EVENT_SPECIALIST_DONE, **result, "error": True}

    if cancel_event and cancel_event.is_set():
        raise _PipelineCancelled()
    if specialists_result and all(s.get("status") == "timeout" for s in specialists_result):
        raise TimeoutError("全部专家执行超时")
    if connection_error and not any(s.get("status") not in {"failed", "error", "timeout"} for s in specialists_result):
        raise connection_error

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
    from agent.core.multi_agent import run_specialist

    specialists = plan_result.get("specialists", [])
    if not specialists:
        return {"specialists": [], "tool_calls": []}

    rag_context = info_gather_result.get("rag_context", "")
    portfolio = info_gather_result.get("portfolio", "")
    valuations = info_gather_result.get("valuations", {})
    rag_low_quality = bool(info_gather_result.get("rag_low_quality", False))

    results = []
    tool_calls = []
    connection_error = None

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
            query=state.original_query or state.refined_query,
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
            result = _run_pipeline_specialist(run_specialist, cancel_event, budget_state=state,
                agent_key=agent_key, query=query,
                prebuilt_context=ctx, trace_id=trace_id,
                from_pipeline=True,
                model=_route_model(agent_key),
                conversation_id=state.conversation_id,
                message_id=state.message_id,
                blackboard=blackboard,
            )
            state.record_phase_tokens('execution', result.get('tokens_used', 0))
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
                    result=result.get("analysis", "")[:16000],
                    tool_calls=result.get("tool_calls", []),
                    evidence={k: result[k] for k in ("verdict", "confidence", "action_signals", "asset_actions", "portfolio_stance", "structured") if k in result},
                    duration_ms=duration_ms_for_log,
                    trace_id=trace_id,
                    status=result.get("status", "success"),
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
                "verdict": result.get("verdict", "unknown"),
                "confidence": result.get("confidence", 0.0),
                "asset_actions": result.get("asset_actions", []),
                "completion_status": result.get("completion_status"),
                "output_quality": result.get("output_quality"),
                "status": result.get("status", "success"),
                "error": result.get("status") == "failed",
                "duration_ms": duration_ms,
            }
        except (ExecutionCancelled, _PipelineCancelled, TimeoutError):
            raise
        except Exception as e:
            logger.error(f"[pipeline] 降级执行 {agent_name} 失败: {e}")
            if isinstance(e, APIConnectionError):
                connection_error = e
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

    if cancel_event and cancel_event.is_set():
        raise _PipelineCancelled()
    if connection_error and not any(s.get("status") not in {"failed", "error", "timeout"} for s in results):
        raise connection_error
    return {"specialists": results, "tool_calls": tool_calls}


def _find_agent_config(agent_key: str, specialists: list) -> Optional[dict]:
    """从专家列表中查找指定 agent_key 的配置。"""
    for s in specialists:
        if s.get("agent_key") == agent_key:
            return s
    return None


# ── Phase 4: 综合 ──────────────────────────

class _ReviewBudgetExhausted(RuntimeError):
    """模型审查缺少额度；与完成审查后的无问题结果严格区分。"""


def _review_tokens_remaining(state):
    budget = state.token_budget
    used = sum(state.tokens_used_by_phase.get(phase, 0) for phase in ('reflection', 'cross_review', 'debate'))
    remaining = max(0, budget.get('review', 1500) - used)
    if budget.get('max_total'):
        remaining = min(remaining, max(0, budget['max_total'] - budget.get('synthesis', 0) - state.tokens_used))
    return remaining


def _call_review_model(state, phase, call, **kwargs):
    """逐调用记完整 usage；输出有硬上限，总输入为软预算，超额禁止继续。

    供应商 tokenization 与本地估算不同，无法承诺网络总 usage 不超额。
    已完成请求仍必须入账；超出审查或综合保留额度时返回未完成状态。
    """
    remaining = _review_tokens_remaining(state)
    if remaining <= 0:
        raise _ReviewBudgetExhausted('模型审查预算已耗尽，确定性事实检查仍会运行')
    kwargs['max_tokens'] = min(int(kwargs.get('max_tokens') or remaining), remaining)
    response = call(**kwargs)
    usage = getattr(response, 'usage', None)
    def field(key):
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    tokens = field('total_tokens')
    if tokens is None:
        prompt, completion = field('prompt_tokens'), field('completion_tokens')
        if prompt is not None and completion is not None:
            tokens = prompt + completion
    if not tokens:
        texts = [str(kwargs.get('messages') or '')]
        for choice in getattr(response, 'choices', []) or []:
            message = getattr(choice, 'message', None)
            texts.extend(str(getattr(message, key, '') or '') for key in ('content', 'reasoning_content'))
        tokens = max(1, (sum(map(len, texts)) + 2) // 3)
    state.record_phase_tokens(phase, tokens)
    review_used = sum(state.tokens_used_by_phase.get(p, 0) for p in ('reflection', 'cross_review', 'debate'))
    total_limit = state.token_budget.get('max_total')
    if (review_used > state.token_budget.get('review', 1500)
            or (total_limit and state.tokens_used > total_limit - state.token_budget.get('synthesis', 0))):
        raise _ReviewBudgetExhausted('实际审查 usage 超出剩余额度，停止追加模型调用')
    return response


def _followup_budget_exhausted(state: PipelineState, *, for_review=False) -> bool:
    total = state.token_budget.get("max_total")
    execution = state.token_budget.get("execution")
    timed_out = (state.get_phase_result("execution_status") or {}).get("status") == "timeout"
    if for_review:
        # 专家额度耗尽只禁止追加专家；独立审查额度仍需留下综合空间。
        return bool(timed_out or _review_tokens_remaining(state) <= 0)
    return bool(timed_out or (total and state.tokens_used >= total) or
                (execution and state.tokens_used_by_phase.get("execution", 0) >= execution))


def _validate_final_facts(state, answer, completion_status, confidence, tool_calls, actions=None):
    from services.conversation.factual_checks import check_final_facts
    from services.conversation.quality_contract import calibrate_confidence_text, action_constraint_issues
    snapshot = (state.get_phase_result("info_gather") or {}).get("portfolio_snapshot")
    issues = check_final_facts(answer, snapshot, tool_calls) + action_constraint_issues(actions, tool_calls)
    from agent.core.answer_policy import has_explicit_action_intent
    if not has_explicit_action_intent(state.original_query or state.refined_query):
        import re
        actionable = any(isinstance(a, dict) and str(a.get("type") or a.get("action") or "").upper()
                         in {"BUY", "ADD", "SELL", "REDUCE", "REDUCE_CONCENTRATION"} for a in actions or [])
        trade_plan = any(re.search(r"建议|每月|每期|分批|立即|执行", s)
                         and re.search(r"买入|卖出|加仓|减仓|补仓|定投", s)
                         and re.search(r"\d+(?:\.\d+)?\s*(?:元|万元|%)", s)
                         and not re.search(r"不建议|不要|不能|不应|未建议|历史|旧报告|假如|如果", s)
                         for s in re.split(r"[。；\n]", answer or ""))
        if actionable or trade_plan:
            issues.append("unrequested_trade_plan")
    review_status = (state.get_phase_result("cross_review_status") or {}).get("status")
    if review_status in {"failed", "budget_exhausted", "degraded"}:
        issues.append("cross_review_" + review_status)
    for phase in ("execution_status", "debate"):
        status = (state.get_phase_result(phase) or {}).get("status")
        if status in {"failed", "timeout", "budget_exhausted"}:
            issues.append(phase + "_" + status)
    if issues:
        completion_status = "degraded"
        confidence = min(confidence, .45)
        labels = {"bond_temperature_falsely_missing": "债市温度与原始工具记录不一致",
                  "kyc_single_limit_value_mismatch": "单基金上限与用户画像不一致",
                  "kyc_single_limit_denominator_mismatch": "单基金上限的资产口径不一致",
                  "recent_30d_trade_count_mismatch": "近30天交易笔数与原始记录不一致",
                  "unsupported_market_causation": "资金或基本面归因超出已有证据",
                  "incomparable_return_windows": "不同时间窗口的跌幅不能直接证明企稳",
                  "scenario_is_not_maximum_loss": "情景损失被误写为最大损失",
                  "market_quote_falsely_missing": "报告称点位缺失，但原始工具已有带时间的点位",
                  "unrequested_trade_plan": "用户未请求交易方案，报告却给出了操作金额",
                  "conflicting_asset_actions": "同一标的存在未消解的买卖方向冲突",
                  "asset_action_limit_violation": "操作金额超出用户配置上限、可用现金或持有金额"}
        answer += "\n\n质量校验未通过，以上分析仅供复核，暂不生成操作候选。待复核：" + "、".join(labels.get(i, "追加审查未完成") for i in issues)
        answer = calibrate_confidence_text(answer, confidence)
    return answer, completion_status, confidence, issues

def _phase_timings_ms(state: PipelineState) -> dict:
    """从状态机真实阶段时间生成毫秒耗时，尚未结束的阶段计算至当前时刻。"""
    now = time.time()
    return {f"{phase}_ms": max(0, int((state.phase_finished_at.get(phase, now) - started) * 1000))
            for phase, started in state.phase_started_at.items()}


def _validate_synthesis(answer, completion_status, specialists, reflection_result, confidence):
    """结构验收与统一置信度；失败专家或失败审查不得进入可执行链。"""
    from agent.infra.output_quality import is_valid_analysis, validate_specialist_output
    from services.conversation.quality_contract import calibrate_confidence_text
    invalid = not specialists or any(not validate_specialist_output(s)["valid"] for s in specialists)
    review_failed = bool(reflection_result and reflection_result.get("status") not in {"completed", "success"})
    if not is_valid_analysis(answer, completion_status=completion_status):
        completion_status = "degraded" if answer.strip() else "failed"
    if invalid or review_failed:
        completion_status = "degraded" if answer.strip() else "failed"
    if completion_status != "completed":
        confidence = min(confidence, .45)
    quality_status = "validated" if completion_status == "completed" else "degraded"
    return calibrate_confidence_text(answer, confidence), completion_status, quality_status, confidence


def _phase_synthesis(
    state: PipelineState,
    query: str,
    execution_result: dict,
    blackboard: Blackboard,
    trace_id: str,
    reflection_result: dict = None,
    debate_result: dict = None,
    plan: dict = None,
) -> dict:
    """Phase 4: 交叉审阅 + 仲裁 + 生成最终回答。

    Args:
        reflection_result: P3 Reflection 阶段的产出（可选），包含 quality_issues 等
        debate_result: P1 辩论节点的产出（可选），包含 bull/bear/arbitration 等
        plan: Phase 2 生成的执行计划（可选），供仲裁阶段引用任务清单
    """
    from services.conversation.quality_contract import selected_asset_actions
    specialists = execution_result.get("specialists", [])
    tool_calls = execution_result.get("tool_calls", [])
    expected_steps = ((plan or {}).get("plan") or {}).get("steps", [])
    present = {s.get("agent_key") for s in specialists if not s.get("is_cross_review")}
    for step in expected_steps:
        key = step.get("agent_key")
        if key and key not in present:
            specialists.append({"agent_key": key, "agent": step.get("agent_name", key),
                                "analysis": "计划中的专家未产出可验收分析。", "status": "failed",
                                "verdict": "unknown", "confidence": 0.0, "asset_actions": []})
            present.add(key)
    snapshot = (state.get_phase_result("info_gather") or {}).get("portfolio_snapshot")
    if snapshot and not any(t.get("evidence_id") == "portfolio_snapshot" for t in tool_calls if isinstance(t, dict)):
        tool_calls = [*tool_calls, {"name": "get_portfolio_snapshot", "evidence_id": "portfolio_snapshot", "result": snapshot}]
        execution_result["tool_calls"] = tool_calls

    # P2 置信度校准：检测数据缺失信号
    data_gaps = _detect_data_gaps(state, tool_calls, specialists, trace_id)

    # 单个专家：直接用其结果
    if len(specialists) <= 1:
        answer = specialists[0].get("analysis", "") if specialists else "无法生成分析"
        confidence = _compute_confidence(
            reflection_result, 1, conflict_count=0, data_gaps=data_gaps,
        )
        answer, completion_status, quality_status, confidence = _validate_synthesis(
            answer, "completed" if answer.strip() else "failed", specialists, reflection_result, confidence)
        answer, completion_status, confidence, fact_issues = _validate_final_facts(
            state, answer, completion_status, confidence, tool_calls, selected_asset_actions(None, specialists))
        quality_status = "validated" if completion_status == "completed" else "degraded"
        return {
            "answer": answer,
            "quality_status": quality_status,
            "quality_issues": fact_issues,
            "cross_review": None,
            "arbitration": None,
            "specialist_count": len(specialists),
            "confidence": confidence,
            "data_gaps": data_gaps,
            "completion_status": completion_status,
        }

    # 多专家：调用 LLM 综合
    arbitration_summary = None
    try:
        answer, arbitration_summary, completion_status = _synthesize_multiple_specialists(
            query, specialists, blackboard, trace_id,
            reflection_result=reflection_result,
            debate_result=debate_result,
            data_gaps=data_gaps,
            plan=plan,
            portfolio_context=(state.get_phase_result("info_gather") or {}).get("portfolio", ""),
            tool_calls=tool_calls,
            state=state,
        )
    except Exception as e:
        logger.error(f"[pipeline] 综合失败，降级拼接: {e}")
        answer = _fallback_synthesize(query, specialists)
        completion_status = "degraded"

    # 冲突检测
    conflicts = blackboard.find_conflicts() if blackboard else []
    # P0-A: 收集风险否决供前端展示
    vetoes = blackboard.get_vetoes() if blackboard else []
    # P0-B: 收集持仓影响供前端展示
    impacts = blackboard.get_portfolio_impacts() if blackboard else []

    # P2-G: 提取交叉审阅结果（在 Phase 3.8 已追加到 specialists 列表）
    # 让 _phase_synthesis 返回值携带 cross_review_results，供 metadata 写入
    cross_review_results = [
        s for s in specialists if s.get("is_cross_review")
    ]

    # 综合置信度（基于 Reflection 调整 + 专家数 + 冲突数 + 数据缺失）
    confidence = _compute_confidence(
        reflection_result, len(specialists), len(conflicts), data_gaps=data_gaps,
    )

    answer, completion_status, quality_status, confidence = _validate_synthesis(
        answer, completion_status, specialists, reflection_result, confidence)
    answer, completion_status, confidence, fact_issues = _validate_final_facts(
        state, answer, completion_status, confidence, tool_calls, selected_asset_actions(arbitration_summary, specialists))
    quality_status = "validated" if completion_status == "completed" else "degraded"
    if arbitration_summary:
        arbitration_summary = dict(arbitration_summary)
        arbitration_summary["confidence_score"] = confidence
        arbitration_summary["confidence"] = "high" if confidence >= .75 else "medium" if confidence >= .5 else "low"
    return {
        "answer": answer,
        "quality_status": quality_status,
        "quality_issues": fact_issues,
        "cross_review": {"conflicts": conflicts},
        "cross_review_results": cross_review_results,
        "arbitration": arbitration_summary,
        "specialist_count": len({s.get("agent_key") for s in specialists if not s.get("is_cross_review") and not s.get("is_arbitration")}),
        "reflection": reflection_result,
        "risk_vetoes": vetoes,
        "portfolio_impacts": impacts,
        "confidence": confidence,
        "data_gaps": data_gaps,
        "completion_status": completion_status,
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

        # 2026-07-30 新增 conv#193 修复：基金穿透失败检测
        # 场景：query_fund_info 返回 _data_source=akshare_partial_failure（top_stocks/bond_holdings 为空），
        # 说明未能穿透到基金底层持仓，专家基于宏观推断而非真实持仓分析
        elif name == "query_fund_info":
            if "akshare_partial_failure" in preview:
                gaps.append({
                    "type": "fund_holdings_missing",
                    "tool": name,
                    "detail": "基金持仓穿透失败：akshare/ttfund 均未返回重仓股/债券明细，专家基于宏观推断而非真实持仓分析",
                })
            elif '"top_stocks": []' in preview.replace(" ", "") or '"bond_holdings":[]' in preview.replace(" ", ""):
                gaps.append({
                    "type": "fund_holdings_missing",
                    "tool": name,
                    "detail": "基金持仓穿透数据为空：top_stocks 或 bond_holdings 为空数组",
                })
            # 检测防幻觉纠正（LLM 用错基金代码）
            if "防幻觉纠正" in preview or "_name_mismatch_warning" in preview:
                gaps.append({
                    "type": "fund_code_correction",
                    "tool": name,
                    "detail": "基金代码被防幻觉机制纠正，原代码可能错误，需核对分析是否基于正确基金",
                })

        # 2026-07-30 新增 conv#194 修复：交易记录查询防幻觉检测
        # 场景：专家凭记忆猜 008928，实际持仓是 008929，query_transaction_history 返回空
        elif name == "query_transaction_history":
            if "防幻觉纠正" in preview:
                gaps.append({
                    "type": "fund_code_correction",
                    "tool": name,
                    "detail": "交易记录查询的基金代码被防幻觉机制纠正，原代码不在持仓表中，需核对分析是否基于正确基金",
                })
            # 空结果但未触发防幻觉（可能 fund_code 在持仓表但确实无交易记录）
            elif '"count": 0' in preview and '"error"' not in preview:
                gaps.append({
                    "type": "transaction_history_empty",
                    "tool": name,
                    "detail": "交易记录查询返回空：该基金无真实交易记录（假设补仓已过滤），应参考 holding_summary 中的持仓盈亏数据",
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
    # 专家数量本身不是证据，不能凭“专家更多”提高置信度；只有 reflection
    # 明确完成且有依据时才允许其调整，缺少审查应保守处理。
    if not reflection_result or reflection_result.get("status") == "failed":
        base -= 0.15
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
    cancel_event=None,
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

    from agent.infra.output_quality import validate_specialist_output
    invalid = [s for s in execution_result.get("specialists", [])
               if not s.get("is_cross_review") and not validate_specialist_output(s)["valid"]]
    conf_adj = reflection_result.get("confidence_adjustment", 0.0)
    if conf_adj >= threshold and not invalid:
        return None
    if _followup_budget_exhausted(state):
        return None

    # 从 missing_perspectives 找到命中的已调用专家
    missing = reflection_result.get("missing_perspectives", [])
    if not missing and not invalid:
        return None

    specialists = execution_result.get("specialists", [])
    if not specialists:
        return None

    # 匹配：missing_perspective 文本含专家名/关键词 → 该专家需要重跑
    rerun_target = invalid[0] if invalid else None
    for s in ([] if rerun_target else specialists):
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
        from agent.core.multi_agent import run_specialist
        from agent.core.orchestrator import _get_model_for_agent, _is_cost_routing_enabled, MODEL as _ORCH_MODEL
        # 构建反思反馈注入
        feedback = "\n\n## 反思反馈（上一轮分析质量问题，请改进）\n"
        for issue in reflection_result.get("quality_issues", [])[:3]:
            feedback += f"- {issue}\n"
        for m in reflection_result.get("missing_perspectives", [])[:3]:
            feedback += f"- 缺失视角：{m}\n"
        enhanced_query = f"{query}{feedback}"

        info = state.get_phase_result("info_gather") or {}
        agent_cfg = _find_agent_config(agent_key, (state.get_phase_result("planning") or {}).get("specialists", [])) or {}
        if not agent_cfg.get("system_prompt"):
            from agent.core.multi_agent import load_specialist_agents
            agent_cfg = load_specialist_agents().get(agent_key, agent_cfg)
        rerun_context = "\n\n".join(str(part) for part in (agent_cfg.get("system_prompt", ""), info.get("portfolio", ""), info.get("rag_context", ""),
            json.dumps({"portfolio_snapshot": info.get("portfolio_snapshot"), "valuations": info.get("valuations", {})}, ensure_ascii=False)) if part)
        _rerun_model = _get_model_for_agent(agent_key) if _is_cost_routing_enabled() else _ORCH_MODEL
        result = _run_pipeline_specialist(run_specialist, cancel_event, budget_state=state,
            agent_key=agent_key,
            query=enhanced_query,
            trace_id=f"{trace_id}#rerun",
            prebuilt_context=rerun_context,
            conversation_id=state.conversation_id, message_id=state.message_id,
            from_pipeline=True,
            model=_rerun_model,
            blackboard=blackboard,
        )
        if result:
            state.record_phase_tokens('execution', result.get('tokens_used', 0))
        if result and validate_specialist_output(result)["valid"]:
            # 更新 blackboard 中的 entry
            if blackboard:
                try:
                    from agent.infra.blackboard import extract_entry_from_result
                    entry = extract_entry_from_result(agent_key, agent_name, result)
                    blackboard.write(entry)
                except Exception as e:
                    logger.warning(f"[pipeline:{trace_id}] 重跑结果写黑板失败: {e}")
            logger.info(f"[pipeline:{trace_id}] 自纠错重跑完成: {agent_name}")
            return result
    except (ExecutionCancelled, _PipelineCancelled, TimeoutError):
        raise
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
            new_s.update(rerun_result)
            new_s["rerun"] = True
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
    plan: dict = None,
    portfolio_context: str = "",
    tool_calls: list = None,
    state: PipelineState = None,
) -> tuple:
    """调用 LLM 综合多个专家的分析。

    Returns:
        (answer, arbitration_summary, completion_status)
        - answer: 综合回答文本
        - arbitration_summary: 仲裁 Agent 的结构化裁决摘要（dict 或 None）
    """
    from services.llm_service import _call_llm, MODEL

    # 构建综合 prompt
    from agent.core.answer_policy import build_answer_policy, has_explicit_action_intent
    action_requested = has_explicit_action_intent(query)
    parts = [f"## 用户问题\n{query}\n"]
    parts.append("## 各专家分析")

    for i, s in enumerate(specialists, 1):
        agent_name = s.get("agent", f"专家{i}")
        analysis = s.get("analysis", "")
        parts.append(f"\n### {agent_name}\n{analysis}")

    from services.conversation.quality_contract import compact_tool_evidence
    raw_tools = tool_calls if tool_calls is not None else [call for specialist in specialists for call in specialist.get("tool_calls", [])]
    parts.append("\n## 原始工具证据\n" + json.dumps(compact_tool_evidence(raw_tools), ensure_ascii=False))

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
    if portfolio_context:
        parts.append(f"\n## 本轮持仓快照及确定性计算\n{portfolio_context}\n")

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

    # ── 综合阶段仲裁：调用 arbitrate_results 注入结构化裁决摘要 ──
    # 修复 conv 123：原 pipeline 路径未触发仲裁，导致用户期望的
    # 「需求澄清→规划→拆解→仲裁给最终结果」四阶段缺最后一环。
    # 开关：orchestration_config.arbitration_in_synthesis_enabled（默认 true）
    arbitration_summary = None
    try:
        from agent.core.orchestrator import get_orchestration_config
        arb_enabled = get_orchestration_config("arbitration_in_synthesis_enabled", "true") == "true"
    except Exception:
        arb_enabled = True

    if action_requested and arb_enabled and len(specialists) >= 2:
        try:
            from agent.core.arbitration import arbitrate_results
            arbitration_summary = arbitrate_results(
                query, specialists, blackboard=blackboard, plan=plan
            )
            if arbitration_summary:
                parts.append("\n## 仲裁 Agent 的结构化裁决（综合回答必须遵守）")
                parts.append(
                    f"```json\n{json.dumps(arbitration_summary, ensure_ascii=False, indent=2)}\n```"
                )
                parts.append(
                    "强制要求：综合回答必须与仲裁裁决方向一致，"
                    "不得与仲裁结论相悖。如有冲突，以仲裁结论为准。\n"
                )
                logger.info(
                    f"[trace:{trace_id}] [pipeline] 仲裁已注入综合阶段: "
                    f"verdict={arbitration_summary.get('verdict', '')[:50]}"
                )
        except Exception as e:
            logger.warning(f"[trace:{trace_id}] [pipeline] 仲裁注入失败（继续无仲裁综合）: {e}")

    # P0 修复 conv#192：注入工具结果汇总（与 ReAct 路径 _build_final_synthesis_prompt 对齐）
    # Pipeline 路径原本不读取 tool_broadcasts，导致估值等工具结果只通过专家 analysis 间接传递
    # 综合LLM 看不到完整的工具结果，容易遗漏关键估值数据（如恒生科技/医药50估值查到但未引用）
    if blackboard and hasattr(blackboard, "get_tool_broadcasts"):
        try:
            tool_broadcasts = blackboard.get_tool_broadcasts()
            if tool_broadcasts:
                parts.append("\n## 工具结果汇总（综合回答必须引用）")
                for tb in tool_broadcasts[:10]:
                    tool_name = getattr(tb, "tool_name", "") or (tb.get("tool_name", "") if isinstance(tb, dict) else "")
                    query_text = getattr(tb, "query", "") or (tb.get("query", "") if isinstance(tb, dict) else "")
                    summary_text = getattr(tb, "summary", "") or (tb.get("summary", "") if isinstance(tb, dict) else "")
                    key_fields = getattr(tb, "key_fields", None)
                    if key_fields is None and isinstance(tb, dict):
                        key_fields = tb.get("key_fields")
                    parts.append(f"\n- {tool_name}「{query_text}」: {summary_text}")
                    if key_fields:
                        try:
                            parts.append(f"\n  关键字段: {json.dumps(key_fields, ensure_ascii=False)}")
                        except Exception:
                            pass
                parts.append("\n约束：涉及估值/盈亏/补仓金额时，必须引用上述工具结果，禁止编造未出现的数据。\n")
        except Exception as e:
            logger.debug(f"[pipeline] 工具结果汇总注入失败: {e}")

    # M5：综合报告深度保留 — 5段结构（与 ReAct 路径 _build_final_synthesis_prompt 对齐）
    # 修复 conv#192：Pipeline 路径综合报告结构不规范，缺少推理链条和分歧反驳
    try:
        from db.config import get_config_bool as _gcb_ds
        deep_synthesis = _gcb_ds("agent.deep_synthesis_enabled", True)
    except Exception:
        deep_synthesis = True

    if not action_requested:
        parts.append(build_answer_policy(query))
    elif deep_synthesis:
        parts.append(
            "\n## 任务\n"
            "基于以上专家分析，生成综合回答，必须按以下 5 段结构组织，每段缺一不可：\n\n"
            "### 第 1 段：核心结论\n"
            "- 1-2 句话直接回答用户问题，不要绕弯\n"
            "- 标注置信度 [高置信度/中置信度/低置信度]\n"
            "- 如有仲裁裁决，最终结论必须与仲裁方向一致\n\n"
            "### 第 2 段：推理链条（必须保留，不得省略）\n"
            "列出支撑结论的关键推理步骤，每步标注数据来源：\n"
            "- 步骤 A：[数据来源] → [推理过程] → [子结论]\n"
            "- 步骤 B：[数据来源] → [推理过程] → [子结论]\n"
            "- 至少列出 2 条推理步骤，展示「为什么得出这个结论」的完整逻辑链\n"
            "- 禁止：把多个专家结论压缩成一句话\n\n"
            "### 第 3 段：分歧与反驳（如有）\n"
            "- 列出专家间的分歧点（谁说买、谁说卖、各自理由）\n"
            "- 对每个分歧给出你的判断倾向和理由\n"
            "- 如有冲突，给出明确判断和理由\n"
            "- 如无分歧，说明「各专家方向一致」并指出共同依赖的前置假设\n\n"
            "### 第 4 段：操作建议（如适用）\n"
            "- 具体到金额/比例/触发条件\n"
            "- 操作表格：基金 | 操作 | 金额 | 理由 | 前提条件\n"
            "- 如有风险否决，必须遵守否决约束降级处理\n"
            "- 减仓应基于风险预算、流动性需求、集中度或基本面证据，不以买入成本作为禁止减仓的理由\n"
            "- 情景损失只能使用明确输入作确定性计算；缺少模型/数据时不得编造破产概率、模拟回撤或收益预测\n\n"
            "### 第 5 段：风险提示与盲点\n"
            "- 每个操作建议的风险说明\n"
            "- 标注置信度（高/中/低）及原因\n"
            "- 列出未覆盖的盲点（哪些维度没有专家分析、哪些数据缺失）\n"
            "- 如有数据缺口警告，回答中必须体现数据完整性说明\n"
            "- 给出总体风险判断（高/中/低）\n\n"
            "## 通用要求\n"
            "- 使用 Markdown 格式，禁止 emoji 标题\n"
            "- 使用「我的持仓」「我的方案」「我该怎么做」的用户视角\n"
        )
    else:
        parts.append(
            "\n## 任务\n"
            "基于以上专家分析，生成综合回答：\n"
            "1. 整合各专家观点，去重避免重复\n"
            "2. 如有冲突，给出明确判断和理由\n"
            "3. 如有风险否决，必须遵守否决约束降级处理\n"
            "4. 如有数据缺口警告，回答中必须体现数据完整性说明\n"
            "5. 如有仲裁裁决，最终结论必须与仲裁方向一致\n"
            "6. 结尾包含「具体操作建议」段落\n"
            "7. 使用 Markdown 格式，禁止 emoji 标题\n"
        )

    if action_requested:
        parts.append(build_answer_policy(query))
    prompt = "\n".join(parts)

    from services.llm.text_completion import complete_text_analysis_sync
    answer, _tokens, completion_status = complete_text_analysis_sync(
        _call_llm,
        caller="pipeline_synthesizer",
        trace_id=trace_id,
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=3000,
    )
    if state is not None:
        state.record_phase_tokens("synthesis", _tokens)
    if completion_status == "failed":
        raise RuntimeError("综合回答为空")

    # P2-问题6修复：风险否决代码层硬兜底
    # prompt 级降级依赖 LLM 遵守，此处做事后校验：
    # 若被否决标的在最终 answer 中仍出现 BUY/加仓 关键词，强制改写为 HOLD
    if vetoes:
        answer = _enforce_risk_veto(answer, vetoes, trace_id)

    # P0-A 仲裁-综合一致性硬校验（conv#131 修复）
    # 检测综合报告操作建议与仲裁裁决方向是否冲突，冲突时追加警告
    # 开关：agent.arbitration_consistency_guard_enabled（默认 false）
    try:
        from agent.safety.arbitration_guard import enforce_arbitration_consistency
        answer, _guard_warnings = enforce_arbitration_consistency(
            answer, arbitration_summary, trace_id=trace_id
        )
        if _guard_warnings:
            logger.info(
                f"[trace:{trace_id}] [pipeline] 仲裁一致性校验触发: {_guard_warnings}"
            )
    except Exception as _guard_err:
        logger.warning(
            f"[trace:{trace_id}] [pipeline] 仲裁一致性校验失败: {_guard_err}"
        )

    # P1-E 卖出操作时机守卫（conv#131 修复）
    # 检测卖出/减仓/清仓等操作建议时，强制要求 answer 包含估值分位和盈亏状态
    # 开关：agent.sell_timing_guard_enabled（默认 false）
    try:
        from agent.safety.sell_timing_guard import enforce_sell_timing_guard
        answer, _timing_warnings = enforce_sell_timing_guard(
            answer, trace_id=trace_id, arbitration_summary=arbitration_summary
        )
        if _timing_warnings:
            completion_status = "degraded"
            logger.info(
                f"[trace:{trace_id}] [pipeline] 卖出时机守卫触发: {_timing_warnings}"
            )
    except Exception as _timing_err:
        logger.warning(
            f"[trace:{trace_id}] [pipeline] 卖出时机守卫失败: {_timing_err}"
        )

    return answer, arbitration_summary, completion_status


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
    from agent.core.orchestrator import _get_model_for_agent, _is_cost_routing_enabled

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

    # 使用本轮相同持仓快照，辩论不能重新读取并改变事实基准。
    portfolio_line = (state.get_phase_result("info_gather") or {}).get("portfolio", "")

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
        response = _call_review_model(state, 'debate', _call_llm,
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
    except _ReviewBudgetExhausted as exc:
        return {'status': 'budget_exhausted', 'error': str(exc), 'arbitration': None}
    except (ExecutionCancelled, _PipelineCancelled, TimeoutError):
        raise
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
        response = _call_review_model(state, 'debate', _call_llm,
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
    except _ReviewBudgetExhausted as exc:
        return {'status': 'budget_exhausted', 'error': str(exc), 'arbitration': None}
    except (ExecutionCancelled, _PipelineCancelled, TimeoutError):
        raise
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
    bb_summary = blackboard.to_context_text(max_chars=5000) if blackboard else ""
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

    from services.conversation.quality_contract import compact_tool_evidence
    parts.append("\n## 原始工具证据\n" + json.dumps(
        compact_tool_evidence(execution_result.get("tool_calls", [])), ensure_ascii=False))

    # 各专家分析预览（每个截断到 500 字）
    parts.append("\n## 各专家分析预览")
    for s in specialists[:5]:
        agent_name = s.get("agent", "")
        full_analysis = s.get("analysis", "")
        analysis = full_analysis if len(full_analysis) <= 10000 else full_analysis[:7000] + "\n[中段省略，仅凭预览不可断言全文缺失]\n" + full_analysis[-3000:]
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

    # 有界重试结构化审查；解析失败绝不能等价于“没有问题”。
    last_error = "invalid_json"
    for attempt in range(2):
        try:
            response = _call_review_model(state, 'reflection', _call_llm,
                caller="reflection", trace_id=trace_id, model=MODEL,
                messages=[{"role": "user", "content": prompt + (
                    "\n上次输出无效，请压缩为简短、完整的 JSON 对象。" if attempt else "")}],
                temperature=0.2, max_tokens=2400,
                **({"response_format": {"type": "json_object"}} if not attempt else {}),
            )
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                last_error = "truncated"
                continue
            result = _parse_reflection_json(choice.message.content or "")
            if result.get("status") == "completed":
                from services.conversation.quality_contract import reconcile_reflection_evidence
                return reconcile_reflection_evidence(result, execution_result.get("tool_calls", []))
            last_error = "invalid_json"
        except _ReviewBudgetExhausted as exc:
            return {**_failed_reflection(str(exc)), 'status': 'budget_exhausted'}
        except (ExecutionCancelled, _PipelineCancelled, TimeoutError):
            raise
        except Exception as exc:
            last_error = str(exc)[:200]
            logger.warning("[pipeline] Reflection 尝试 %s 失败: %s", attempt + 1, last_error)
    return _failed_reflection(last_error)


def _failed_reflection(reason: str) -> dict:
    return {
        "status": "failed", "error": reason,
        "quality_issues": ["质量审查未完成，不能据此判定报告通过；操作建议需复核"],
        "missing_perspectives": [], "conflict_resolutions": [],
        "confidence_adjustment": -0.3,
    }


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
        return _failed_reflection("empty")

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
        return _failed_reflection("invalid_json")

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

    required = ["quality_issues", "missing_perspectives", "conflict_resolutions", "confidence_adjustment"]
    values = {key: _find_field(parsed, FIELD_ALIASES[key]) for key in required}
    if (any(values[key] is None for key in required)
            or any(not isinstance(values[key], list) for key in required[:3])
            or not isinstance(values["confidence_adjustment"], (int, float))):
        return _failed_reflection("invalid_schema")
    result["status"] = "completed"

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
    1. 复用有来源且通过最终校验的逐标的动作，禁止再次从正文推断方向
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

    # 与决策候选共用相同门禁，避免二次模型提取改变标的或买卖方向。
    from services.conversation.quality_contract import build_chat_candidates, selected_asset_actions
    synthesis = state.get_phase_result("synthesis") or {}
    execution = state.get_phase_result("execution") or {}
    arbitration = synthesis.get("arbitration") or {}
    actions = selected_asset_actions(arbitration, execution.get("specialists", []))
    payloads = build_chat_candidates(state.answer, actions, execution.get("tool_calls", []),
        conversation_id=state.conversation_id, message_id=state.message_id,
        completion_status=synthesis.get("completion_status", "unverified"),
        quality_status=synthesis.get("quality_status", "unverified"))
    recs = [{"index_name": p["target_name"] or p["target_code"], "index_code": p["target_code"],
             "target_fund_code": p["target_code"], "target_fund_name": p["target_name"], "suggested_amount": p["suggested_amount"],
             "direction": "up" if p["action_type"] == "buy" else "down",
             "reason": p["rationale"], "confidence": p["confidence"], "signal_type": "value_dip"}
            for p in payloads]

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
        # 2026-07-13: signal_type 标准化
        signal_type = (r.get("signal_type") or "value_dip").strip().lower()
        if signal_type not in ("value_dip", "momentum_breakout"):
            signal_type = "value_dip"
        valid_recs.append({
            "index_name": name,
            "index_code": code,
            "target_fund_code": r.get("target_fund_code"),
            "target_fund_name": r.get("target_fund_name"),
            "suggested_amount": r.get("suggested_amount"),
            "direction": direction,
            "reason": (r.get("reason") or "")[:200],
            "confidence": (r.get("confidence") or "medium").lower(),
            "referenced_books": referenced_books,  # Phase D: answer 级引用，所有 recs 共享
            "signal_type": signal_type,
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

    if synthesis_result.get("completion_status", "completed") != "completed":
        result["skipped_reason"] = "incomplete_synthesis"
        return result

    # 1. 保存分析结论
    try:
        from db.analysis_conclusions import save_analysis_conclusion
        targets = query_info.get("targets", [])
        target_subject = "、".join(targets) if targets else state.original_query[:30]
        # Phase D: data_basis 改为真实值，区分 RAG 是否含 book、是否低质
        data_basis = ["portfolio", "tools"]
        snapshot = (info_gather_result or {}).get("portfolio_snapshot")
        if snapshot:
            from services.portfolio.analysis_contract import portfolio_analysis_basis
            data_basis.append(portfolio_analysis_basis(snapshot,
                validation_status=synthesis_result.get("quality_status", "unverified")))
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
            confidence=synthesis_result.get("confidence", 0.7) if synthesis_result else 0.7,
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
        from agent.memory.memory import update_conversation_summary
        update_conversation_summary(state.conversation_id)
        result["summary_updated"] = True
    except Exception as e:
        logger.debug(f"[pipeline] 摘要更新跳过: {e}")

    # 4. 提取用户偏好（受开关控制，默认关闭）
    try:
        extract_prefs = get_config_bool("agent.auto_extract_prefs", False)
        if extract_prefs and state.answer:
            from agent.memory.feedback_learner import extract_preferences_from_conversation
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
        from agent.core.orchestrator import get_orchestration_config
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
