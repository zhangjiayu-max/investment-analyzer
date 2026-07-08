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
EVENT_ANSWER = "answer"
EVENT_ERROR = "error"
EVENT_DEGRADE = "degrade"
EVENT_TERMINATED = "terminated"


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

        # 简单闲聊快速路径
        query_info = phase0_result.get("query_info", {})
        if is_simple_chat(query_info):
            yield from _handle_simple_chat(query, state, trace_id)
            return

        # 需要澄清
        need_clarify, clarify_q = needs_clarification(query_info)
        if need_clarify:
            yield {"type": EVENT_CLARIFICATION, "question": clarify_q,
                   "answer": clarify_q}
            state.answer = clarify_q
            state.transition_to(PipelinePhase.COMPLETED)
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

        # ── Phase 3.5: 反思（可选，默认关闭）──────────────
        # P3: Reflection 节点 — 自评质量问题 + 冲突识别，结果注入综合阶段
        reflection_result = None
        try:
            reflection_enabled = get_config_bool("pipeline.reflection_enabled", False)
        except Exception:
            reflection_enabled = False

        if reflection_enabled and phase3_result.get("specialists"):
            state.transition_to(PipelinePhase.REFLECTION)
            yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.REFLECTION.value}
            try:
                reflection_result = _phase_reflection(
                    state, query, phase3_result, blackboard, trace_id
                )
                state.set_phase_result(PipelinePhase.REFLECTION.value, reflection_result)
                yield {"type": EVENT_REFLECTION_DONE, "result": reflection_result}
            except Exception as refl_err:
                logger.warning(f"[pipeline] Reflection 失败，跳过: {refl_err}")
                reflection_result = None
            yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.REFLECTION.value}

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

        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.SYNTHESIS.value}
        yield {"type": EVENT_ANSWER, "content": state.answer,
               "specialist_results": phase3_specialists,
               "tool_calls": phase3_result.get("tool_calls", [])}

        # ── Phase 5: 记忆持久化 ──────────────────
        state.transition_to(PipelinePhase.MEMORY)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.MEMORY.value}
        phase5_result = _phase_memory(state, phase4_result, query_info, trace_id)
        state.set_phase_result(PipelinePhase.MEMORY.value, phase5_result)
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.MEMORY.value}

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
    try:
        from agent.query_rewriter import rewrite_query, needs_rewrite
        need_rewrite, reason = needs_rewrite(query)
        if need_rewrite:
            refined_query = rewrite_query(query, history)
            logger.info(f"[pipeline] Query 改写: '{query}' → '{refined_query}'")
    except Exception as e:
        logger.debug(f"[pipeline] Query 改写跳过: {e}")

    # 3. 复杂度（优先用 query_understander 的结果）
    complexity = query_info.get("complexity", "medium")

    return {
        "query_info": query_info,
        "refined_query": refined_query,
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

    # 1. RAG 检索
    try:
        from services.rag import build_rag_context_with_details
        enhanced_query = _enhance_rag_query(state.refined_query, query_info)
        rag_data = build_rag_context_with_details(enhanced_query, limit=8)
        results["rag"] = rag_data.get("results", []) if isinstance(rag_data, dict) else []
        results["rag_context"] = rag_data.get("context", "") if isinstance(rag_data, dict) else str(rag_data)
        logger.info(f"[pipeline] RAG 检索完成: {len(results['rag'])} 条结果")
    except Exception as e:
        logger.warning(f"[pipeline] RAG 检索失败: {e}")

    # 2. 持仓摘要 — P0 修复：改用 build_portfolio_context，含完整盈亏+交易记录+集中度
    try:
        from services.portfolio_context import build_portfolio_context
        results["portfolio"] = build_portfolio_context("default")
    except Exception as e:
        logger.debug(f"[pipeline] 持仓加载失败: {e}")

    # 3. 估值预取（基于 targets）
    targets = query_info.get("targets", [])
    if targets:
        for target in targets[:3]:  # 最多预取 3 个标的
            val = _lookup_valuation(target)
            if val:
                results["valuations"][target] = val

    # 4. 用户记忆
    try:
        from agent.memory import build_user_memory_context
        results["memory"] = build_user_memory_context("default")[:500]
    except Exception as e:
        logger.debug(f"[pipeline] 记忆加载失败: {e}")

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
) -> dict:
    """Phase 4: 交叉审阅 + 仲裁 + 生成最终回答。

    Args:
        reflection_result: P3 Reflection 阶段的产出（可选），包含 quality_issues 等
    """
    specialists = execution_result.get("specialists", [])
    tool_calls = execution_result.get("tool_calls", [])

    # 单个专家：直接用其结果
    if len(specialists) <= 1:
        answer = specialists[0].get("analysis", "") if specialists else "无法生成分析"
        return {
            "answer": answer,
            "cross_review": None,
            "arbitration": None,
            "specialist_count": len(specialists),
        }

    # 多专家：调用 LLM 综合
    try:
        answer = _synthesize_multiple_specialists(
            query, specialists, blackboard, trace_id,
            reflection_result=reflection_result,
        )
    except Exception as e:
        logger.error(f"[pipeline] 综合失败，降级拼接: {e}")
        answer = _fallback_synthesize(query, specialists)

    # 冲突检测
    conflicts = blackboard.find_conflicts() if blackboard else []

    return {
        "answer": answer,
        "cross_review": {"conflicts": conflicts},
        "arbitration": None,
        "specialist_count": len(specialists),
        "reflection": reflection_result,
    }


def _synthesize_multiple_specialists(
    query: str,
    specialists: list,
    blackboard: Blackboard,
    trace_id: str,
    reflection_result: dict = None,
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

    parts.append(
        "\n## 任务\n"
        "基于以上专家分析，生成综合回答：\n"
        "1. 整合各专家观点，去重避免重复\n"
        "2. 如有冲突，给出明确判断和理由\n"
        "3. 结尾包含「具体操作建议」段落\n"
        "4. 使用 Markdown 格式，禁止 emoji 标题\n"
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
    return response.choices[0].message.content or ""


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

def _phase_memory(
    state: PipelineState,
    synthesis_result: dict,
    query_info: dict,
    trace_id: str,
) -> dict:
    """Phase 5: 保存结论 + 更新摘要 + 提取用户偏好。"""
    result = {
        "saved_conclusion_id": None,
        "updated_memory_ids": [],
        "summary_updated": False,
    }

    # 1. 保存分析结论
    try:
        from db.analysis_conclusions import save_analysis_conclusion
        conclusion_id = save_analysis_conclusion(
            conversation_id=state.conversation_id,
            message_id=state.message_id,
            query=state.original_query,
            answer=state.answer,
            intent=query_info.get("intent", ""),
            targets=query_info.get("targets", []),
            trace_id=trace_id,
        )
        result["saved_conclusion_id"] = conclusion_id
        logger.info(f"[pipeline] 分析结论已保存: id={conclusion_id}")
    except Exception as e:
        logger.warning(f"[pipeline] 保存结论失败: {e}")

    # 2. 更新对话摘要（异步）
    try:
        from agent.memory import update_conversation_summary
        update_conversation_summary(state.conversation_id)
        result["summary_updated"] = True
    except Exception as e:
        logger.debug(f"[pipeline] 摘要更新跳过: {e}")

    # 3. 提取用户偏好（受开关控制，默认关闭）
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

    # 4. 记录 token 审计
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
