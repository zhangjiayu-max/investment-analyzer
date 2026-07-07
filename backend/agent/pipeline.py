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

import logging
import time
from typing import Optional, Any, Generator

from db.config import get_config_bool, get_config_int

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

        # ── Phase 4: 综合 ──────────────────
        state.transition_to(PipelinePhase.SYNTHESIS)
        yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.SYNTHESIS.value}
        phase4_result = _phase_synthesis(
            state, query, phase3_result, blackboard, trace_id
        )
        state.set_phase_result(PipelinePhase.SYNTHESIS.value, phase4_result)
        state.answer = phase4_result.get("answer", "")
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.SYNTHESIS.value}
        yield {"type": EVENT_ANSWER, "content": state.answer,
               "specialist_results": phase3_result.get("specialists", []),
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

    # 2. 持仓摘要
    try:
        from agent.memory import build_user_memory_context
        results["portfolio"] = build_user_memory_context("default")[:800]
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
    """Phase 2: 调用 plan_executor 生成分析计划。"""
    try:
        from agent.plan_executor import generate_plan
        from db.agents import load_specialist_agents

        # 加载可用专家
        specialists = load_specialist_agents()
        if not specialists:
            return {"plan": None, "specialists": [], "fallback": True}

        # 调用 Plan 生成
        plan = generate_plan(
            user_query=state.original_query,
            refined_query=state.refined_query,
            complexity=query_info.get("complexity", "medium"),
            available_specialists=specialists,
            trace_id=trace_id,
        )
        return {
            "plan": plan.to_dict(),
            "specialists": specialists,
            "fallback": False,
        }
    except Exception as e:
        logger.warning(f"[pipeline] Plan 生成失败，降级全量执行: {e}")
        # 降级：返回空 plan，Phase 3 会走 fallback
        return {"plan": None, "specialists": [], "fallback": True, "error": str(e)}


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

    # 按 depends_on 分组执行
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

            # 发送 specialist_done 事件
            duration_ms = int((time.time() - step_start) * 1000)
            yield {
                "type": EVENT_SPECIALIST_DONE,
                "agent_key": agent_key,
                "agent": agent_name,
                "icon": agent_icon,
                "analysis": result.get("analysis", ""),
                "duration_ms": duration_ms,
            }

        except Exception as e:
            logger.error(f"[pipeline] 专家 {agent_name} 执行失败: {e}")
            checker.record_failure(str(e))
            # 发送失败的 specialist_done 事件
            duration_ms = int((time.time() - step_start) * 1000)
            yield {
                "type": EVENT_SPECIALIST_DONE,
                "agent_key": agent_key,
                "agent": agent_name,
                "icon": agent_icon,
                "analysis": f"（执行失败：{e}）",
                "duration_ms": duration_ms,
                "error": True,
            }
            continue

        completed_step_ids.add(step.get("step_id"))

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
            )
            results.append(result)
            tool_calls.extend(result.get("tool_calls", []))

            entry = extract_entry_from_result(
                agent_key, agent_name, result,
                tokens_used=result.get("tokens_used", 0),
            )
            blackboard.write(entry)

            duration_ms = int((time.time() - step_start) * 1000)
            yield {
                "type": EVENT_SPECIALIST_DONE,
                "agent_key": agent_key,
                "agent": agent_name,
                "icon": agent_icon,
                "analysis": result.get("analysis", ""),
                "duration_ms": duration_ms,
            }
        except Exception as e:
            logger.error(f"[pipeline] 降级执行 {agent_name} 失败: {e}")
            duration_ms = int((time.time() - step_start) * 1000)
            yield {
                "type": EVENT_SPECIALIST_DONE,
                "agent_key": agent_key,
                "agent": agent_name,
                "icon": agent_icon,
                "analysis": f"（执行失败：{e}）",
                "duration_ms": duration_ms,
                "error": True,
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
) -> dict:
    """Phase 4: 交叉审阅 + 仲裁 + 生成最终回答。"""
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
            query, specialists, blackboard, trace_id
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
    }


def _synthesize_multiple_specialists(
    query: str,
    specialists: list,
    blackboard: Blackboard,
    trace_id: str,
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
        from db.agents import save_analysis_conclusion
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
