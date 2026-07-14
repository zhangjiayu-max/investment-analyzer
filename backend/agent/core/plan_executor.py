"""Plan & Execute 编排器 — 先规划再执行，可审计可中断可重规划。

流程：
  Phase 0: generate_plan() — LLM 生成分析计划（JSON）
  Phase 1: execute_plan() — 按计划执行，无依赖步骤并行
  Phase 2: 交叉审阅 + 仲裁（复用 orchestrator.py 现有逻辑）
"""

import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class PlanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLANNED = "replanned"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class PlanStep:
    """单个执行步骤。"""
    step_id: int
    agent_key: str
    agent_name: str
    query: str
    depends_on: list[int] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Optional[dict] = None
    error: str = ""
    duration_ms: int = 0
    # Pipeline Phase C 扩展字段
    token_budget: int = 0        # 该步骤的 token 预算
    tokens_used: int = 0         # 实际消耗
    blackboard_entry: Optional[dict] = None  # 写入黑板的条目
    # P3: Plan-and-Execute 强化字段
    sub_query: str = ""          # 拆解后的具体子问题
    allowed_tools: list = field(default_factory=list)  # 工具白名单
    needs_debate: bool = False   # 该步骤后是否需要辩论


@dataclass
class AnalysisPlan:
    """分析计划。"""
    plan_id: str
    trace_id: str
    user_query: str
    refined_query: str
    complexity: str            # simple | medium | complex
    reasoning: str = ""        # 规划理由（LLM 生成）
    steps: list[PlanStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.PENDING
    created_at: str = ""
    updated_at: str = ""

    @property
    def completed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == StepStatus.DONE]

    @property
    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status in (StepStatus.PENDING, StepStatus.FAILED)]

    @property
    def next_step(self) -> Optional[PlanStep]:
        """获取下一个可执行的步骤（依赖已满足）。"""
        for s in self.steps:
            if s.status != StepStatus.PENDING:
                continue
            deps_met = all(
                any(ds.step_id == dep_id and ds.status == StepStatus.DONE
                    for ds in self.steps)
                for dep_id in s.depends_on
            )
            if deps_met:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "trace_id": self.trace_id,
            "user_query": self.user_query,
            "refined_query": self.refined_query,
            "complexity": self.complexity,
            "reasoning": self.reasoning,
            "steps": [
                {
                    "step_id": s.step_id,
                    "agent_key": s.agent_key,
                    "agent_name": s.agent_name,
                    "query": s.query,
                    "sub_query": s.sub_query,
                    "allowed_tools": s.allowed_tools,
                    "needs_debate": s.needs_debate,
                    "depends_on": s.depends_on,
                    "status": s.status.value,
                    "duration_ms": s.duration_ms,
                    "token_budget": s.token_budget,
                    "tokens_used": s.tokens_used,
                }
                for s in self.steps
            ],
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── Plan 生成 Prompt ──────────────────────────────

_PLAN_GENERATION_PROMPT = """## 任务：为以下用户问题生成分析计划

用户问题：{user_query}
优化后问题：{refined_query}
复杂度：{complexity}
用户当前持仓：{portfolio_summary}

可用专家列表：
{available_specialists}

### 输出格式（严格 JSON）
```json
{{
  "reasoning": "规划理由：为什么选择这些专家，为什么是这个顺序",
  "steps": [
    {{
      "step_id": 1,
      "agent_key": "valuation_expert",
      "query": "发给专家的具体问题（包含用户问题中的关键信息）",
      "sub_query": "拆解后的具体子问题（比 query 更聚焦，如'中证500当前PE分位是多少'）",
      "allowed_tools": ["query_valuation", "get_valuation_list"],
      "needs_debate": false,
      "depends_on": []
    }}
  ]
}}
```

### 规划原则
1. 估值分析先于买卖建议（先看估值再看行动）
2. 宏观分析作为背景，可以与估值并行
3. 风险评估在配置建议之前
4. simple 问题 1-2 个专家，medium 2-4 个，complex 3-6 个
5. 无关专家不要加（如用户问估值就别加文章解读）
6. depends_on 表示该步骤依赖前面步骤的结果，大多数步骤可以并行
7. P3 强化：sub_query 必须比 query 更具体，让专家聚焦回答
8. P3 强化：allowed_tools 限制专家可用工具，避免无效调用（从专家 tools 列表选取）
9. P3 强化：涉及买卖分歧的步骤 needs_debate=true（会触发对抗式辩论）
"""


# ── Plan 生成 ─────────────────────────────────────


def generate_plan(
    user_query: str,
    refined_query: str,
    complexity: str,
    available_specialists: list[dict],
    trace_id: str,
    routed_specialists: list[str] = None,
    portfolio_summary: str = "",
) -> AnalysisPlan:
    """调用 LLM 生成分析计划。

    P4: 若 routed_specialists 非空，优先使用路由结果，跳过 LLM 调用（省 token）。
    P3: 增加 portfolio_summary 参数，让 plan 感知持仓；LLM 生成时输出 sub_query/allowed_tools/needs_debate。
    仅当路由未命中时才调用 LLM 生成 plan。

    降级策略：LLM 调用失败或 JSON 解析失败时，降级为所有专家顺序执行。
    """
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"

    # P4: 优先使用路由结果，跳过 LLM 调用
    if routed_specialists:
        # 过滤掉不在 available_specialists 中的专家
        available_keys = {s["agent_key"] for s in available_specialists}
        valid_routed = [k for k in routed_specialists if k in available_keys]
        if valid_routed:
            logger.info(f"[trace:{trace_id}] P4 使用路由结果生成 plan: {valid_routed} (跳过 LLM)")
            # 构造 plan_data，最多 3 个专家
            valid_routed = valid_routed[:3]
            steps_data = []
            for i, agent_key in enumerate(valid_routed):
                agent_name = next(
                    (s["name"] for s in available_specialists if s["agent_key"] == agent_key),
                    agent_key,
                )
                steps_data.append({
                    "step_id": i + 1,
                    "agent_key": agent_key,
                    "agent_name": agent_name,
                    "query": refined_query,
                    "depends_on": [],
                })
            plan_data = {
                "reasoning": f"路由命中专家: {valid_routed}",
                "steps": steps_data,
            }
            # 直接构造 AnalysisPlan，跳过 LLM
            steps = [
                PlanStep(
                    step_id=s["step_id"],
                    agent_key=s["agent_key"],
                    agent_name=s["agent_name"],
                    query=s.get("query", refined_query),
                    depends_on=s.get("depends_on", []),
                )
                for s in steps_data
            ]
            return AnalysisPlan(
                plan_id=plan_id,
                trace_id=trace_id,
                user_query=user_query,
                refined_query=refined_query,
                complexity=complexity,
                reasoning=plan_data["reasoning"],
                steps=steps,
                status=PlanStatus.PENDING,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )

    # 构建专家列表文本（含工具列表，供 LLM 选择 allowed_tools）
    specialist_lines = []
    for s in available_specialists:
        tools_str = ", ".join(s.get("tools", [])) if s.get("tools") else ""
        specialist_lines.append(
            f"- {s['agent_key']}: {s['name']} — {s.get('description', '')} [工具: {tools_str}]"
        )
    specialists_text = "\n".join(specialist_lines)

    prompt = _PLAN_GENERATION_PROMPT.format(
        user_query=user_query,
        refined_query=refined_query,
        complexity=complexity,
        portfolio_summary=portfolio_summary or "（无持仓）",
        available_specialists=specialists_text,
    )

    plan_data = None
    try:
        from services.llm_service import _call_llm
        from services.llm_service import MODEL
        response = _call_llm(
            caller="plan_generator",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        content = response.choices[0].message.content or ""
        json_match = re.search(r'```json\s*\n(.*?)\n```', content, re.DOTALL)
        if json_match:
            plan_data = json.loads(json_match.group(1))
        else:
            # 尝试直接解析 JSON
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                plan_data = json.loads(json_match.group(0))
    except Exception as e:
        logger.warning(f"[trace:{trace_id}] Plan 生成 LLM 调用失败: {e}")

    # 降级：所有专家顺序执行
    if not plan_data:
        plan_data = {
            "reasoning": "Plan 生成失败，降级为全量顺序执行",
            "steps": [
                {"step_id": i + 1, "agent_key": s["agent_key"],
                 "query": refined_query, "sub_query": refined_query,
                 "allowed_tools": s.get("tools", []), "needs_debate": False,
                 "depends_on": []}
                for i, s in enumerate(available_specialists[:3])  # 降级最多 3 个
            ],
        }

    steps = [
        PlanStep(
            step_id=s["step_id"],
            agent_key=s["agent_key"],
            agent_name=next(
                (sp["name"] for sp in available_specialists
                 if sp["agent_key"] == s["agent_key"]),
                s["agent_key"],
            ),
            query=s.get("query", refined_query),
            sub_query=s.get("sub_query", ""),
            allowed_tools=s.get("allowed_tools", []),
            needs_debate=bool(s.get("needs_debate", False)),
            depends_on=s.get("depends_on", []),
        )
        for s in plan_data.get("steps", [])
    ]

    if not steps:
        # 极端降级：至少有一个专家
        if available_specialists:
            _s = available_specialists[0]
            steps = [PlanStep(step_id=1, agent_key=_s["agent_key"],
                              agent_name=_s["name"], query=refined_query)]

    return AnalysisPlan(
        plan_id=plan_id,
        trace_id=trace_id,
        user_query=user_query,
        refined_query=refined_query,
        complexity=complexity,
        reasoning=plan_data.get("reasoning", ""),
        steps=steps,
        status=PlanStatus.PENDING,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )


# ── Plan 执行 ─────────────────────────────────────


def execute_plan(
    plan: AnalysisPlan,
    prebuilt_context: str,
    cancel_event=None,
    progress_callback: Optional[Callable] = None,
    blackboard=None,
    convergence_detector=None,
    termination_checker=None,
) -> tuple[list[dict], list[dict]]:
    """按计划执行，返回 (specialist_results, all_tool_calls)。

    执行策略：
    - 无依赖的步骤可并行（ThreadPoolExecutor）
    - 有依赖的步骤等待依赖完成后执行
    - 步骤失败不阻塞（标记为 FAILED，继续执行其他步骤）
    - 单个 Specialist 内部仍是 ReAct（max 3 turns）

    Pipeline Phase C 扩展参数：
    - blackboard: Blackboard 实例，写入专家结论供后续专家参考
    - convergence_detector: ConvergenceDetector 实例，跳过已调用的相似 query
    - termination_checker: TerminationChecker 实例，多维度终止检查
    """
    from agent.core.multi_agent import run_specialist

    plan.status = PlanStatus.RUNNING
    specialist_results: list[dict] = []
    all_tool_calls: list[dict] = []

    # 步骤数上限保护
    MAX_STEPS = 8
    if len(plan.steps) > MAX_STEPS:
        logger.warning(f"[trace:{plan.trace_id}] 计划步骤数 {len(plan.steps)} 超过上限 {MAX_STEPS}，截断")
        plan.steps = plan.steps[:MAX_STEPS]

    while plan.pending_steps:
        if cancel_event and cancel_event.is_set():
            logger.info(f"[trace:{plan.trace_id}] Plan 执行被取消")
            break

        # 终止条件检查（Pipeline 扩展）
        if termination_checker:
            try:
                should_stop, reason = termination_checker.check(
                    called_agent_count=len({s.agent_key for s in plan.steps if s.status == StepStatus.DONE}),
                    current_phase="execution",
                )
                if should_stop:
                    logger.info(f"[trace:{plan.trace_id}] Plan 执行终止: {reason}")
                    break
            except Exception as e:
                logger.debug(f"[trace:{plan.trace_id}] 终止检查异常: {e}")

        # 收集所有可并行执行的步骤（同依赖级别）
        next_step = plan.next_step
        if next_step is None:
            break

        parallel_steps = []
        for s in plan.steps:
            if s.status != StepStatus.PENDING:
                continue
            if s.depends_on == next_step.depends_on:
                parallel_steps.append(s)

        # 收敛检测：过滤掉已调用的相似 query（Pipeline 扩展）
        if convergence_detector:
            filtered = []
            for s in parallel_steps:
                if not convergence_detector.should_skip_agent(s.agent_key, s.query):
                    filtered.append(s)
                else:
                    s.status = StepStatus.SKIPPED
                    logger.info(f"[trace:{plan.trace_id}] 跳过 {s.agent_name}：已调用过相似 query")
            parallel_steps = filtered

        if not parallel_steps:
            continue

        # 执行
        if len(parallel_steps) == 1:
            step = parallel_steps[0]
            step.status = StepStatus.RUNNING
            if progress_callback:
                progress_callback(step.step_id, step.agent_name, "running")
            # 收敛检测：记录调用
            if convergence_detector:
                convergence_detector.record_call(step.agent_key, step.query)
            try:
                # 注入黑板摘要到上下文（Pipeline 扩展）
                ctx = prebuilt_context
                if blackboard and blackboard.entry_count > 0:
                    bb_summary = blackboard.to_context_text(exclude_agent=step.agent_key)
                    if bb_summary:
                        ctx = prebuilt_context + "\n\n" + bb_summary

                result = run_specialist(
                    agent_key=step.agent_key,
                    query=step.query,
                    prebuilt_context=ctx,
                    trace_id=plan.trace_id,
                )
                step.result = result
                step.status = StepStatus.DONE
                step.duration_ms = result.get("duration_ms", 0)
                step.tokens_used = result.get("tokens_used", 0)
                specialist_results.append(result)
                all_tool_calls.extend(result.get("tool_calls", []))

                # 写入黑板（Pipeline 扩展）
                if blackboard:
                    try:
                        from agent.infra.blackboard import extract_entry_from_result
                        entry = extract_entry_from_result(
                            agent_key=step.agent_key,
                            agent_name=step.agent_name,
                            result=result,
                            tokens_used=step.tokens_used,
                        )
                        blackboard.write(entry)
                        step.blackboard_entry = entry.to_dict()
                    except Exception as e:
                        logger.debug(f"[trace:{plan.trace_id}] 黑板写入失败: {e}")

                # 终止检查器：记录成功
                if termination_checker:
                    termination_checker.record_success()

                if progress_callback:
                    progress_callback(step.step_id, step.agent_name, "done")
            except Exception as e:
                step.status = StepStatus.FAILED
                step.error = str(e)
                logger.error(f"[trace:{plan.trace_id}] Step {step.step_id} ({step.agent_name}) 失败: {e}")
                if termination_checker:
                    termination_checker.record_failure(str(e))
                if progress_callback:
                    progress_callback(step.step_id, step.agent_name, "failed")
        else:
            # 多步骤并行执行
            with ThreadPoolExecutor(max_workers=len(parallel_steps)) as executor:
                future_map = {}
                for step in parallel_steps:
                    step.status = StepStatus.RUNNING
                    if progress_callback:
                        progress_callback(step.step_id, step.agent_name, "running")
                    if convergence_detector:
                        convergence_detector.record_call(step.agent_key, step.query)
                    future = executor.submit(
                        run_specialist,
                        agent_key=step.agent_key,
                        query=step.query,
                        prebuilt_context=prebuilt_context,
                        trace_id=plan.trace_id,
                    )
                    future_map[future] = step

                for future in as_completed(future_map):
                    step = future_map[future]
                    try:
                        result = future.result()
                        step.result = result
                        step.status = StepStatus.DONE
                        step.duration_ms = result.get("duration_ms", 0)
                        step.tokens_used = result.get("tokens_used", 0)
                        specialist_results.append(result)
                        all_tool_calls.extend(result.get("tool_calls", []))

                        # 写入黑板（Pipeline 扩展）
                        if blackboard:
                            try:
                                from agent.infra.blackboard import extract_entry_from_result
                                entry = extract_entry_from_result(
                                    agent_key=step.agent_key,
                                    agent_name=step.agent_name,
                                    result=result,
                                    tokens_used=step.tokens_used,
                                )
                                blackboard.write(entry)
                                step.blackboard_entry = entry.to_dict()
                            except Exception as e:
                                logger.debug(f"[trace:{plan.trace_id}] 黑板写入失败（并行）: {e}")

                        if termination_checker:
                            termination_checker.record_success()

                        if progress_callback:
                            progress_callback(step.step_id, step.agent_name, "done")
                    except Exception as e:
                        step.status = StepStatus.FAILED
                        step.error = str(e)
                        logger.error(f"[trace:{plan.trace_id}] Step {step.step_id} ({step.agent_name}) 并行失败: {e}")
                        if termination_checker:
                            termination_checker.record_failure(str(e))
                        if progress_callback:
                            progress_callback(step.step_id, step.agent_name, "failed")

    plan.status = PlanStatus.COMPLETED if plan.completed_steps else PlanStatus.FAILED
    plan.updated_at = datetime.now().isoformat()

    return specialist_results, all_tool_calls


# ── 重规划支持 ────────────────────────────────────


def should_replan(plan: AnalysisPlan, new_info: str) -> bool:
    """判断是否需要重规划。"""
    # 场景1：超过 30% 步骤失败
    if plan.completed_steps and len(plan.pending_steps) > len(plan.steps) * 0.3:
        return True

    # 场景2：执行结果中发现新标的
    new_targets = re.findall(r'建议分析[：:]\s*(.+?)[\n。]', new_info)
    if new_targets:
        return True

    return False


def replan(plan: AnalysisPlan, new_info: str, trace_id: str) -> AnalysisPlan:
    """基于新信息重规划，保留已完成步骤，追加新步骤。"""
    # 简化版：追加新步骤（不调用 LLM 重规划，避免额外成本）
    new_step_id = len(plan.steps) + 1
    for target in re.findall(r'建议分析[：:]\s*(.+?)[\n。]', new_info):
        plan.steps.append(PlanStep(
            step_id=new_step_id,
            agent_key="fund_analyst",
            agent_name="基金分析师",
            query=f"分析{target}",
            depends_on=[s.step_id for s in plan.completed_steps],
        ))
        new_step_id += 1

    plan.status = PlanStatus.REPLANNED
    plan.updated_at = datetime.now().isoformat()
    return plan