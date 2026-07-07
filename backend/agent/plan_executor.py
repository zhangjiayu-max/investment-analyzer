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
                    "depends_on": s.depends_on,
                    "status": s.status.value,
                    "duration_ms": s.duration_ms,
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

可用专家列表：
{available_specialists}

### 输出格式（严格 JSON）
```json
{{
  "reasoning": "规划理由：为什么选择这些专家，为什么是这个顺序",
  "steps": [
    {{
      "step_id": 1,
      "agent_key": "valuation_analyst",
      "query": "发给专家的具体问题（包含用户问题中的关键信息）",
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
"""


# ── Plan 生成 ─────────────────────────────────────


def generate_plan(
    user_query: str,
    refined_query: str,
    complexity: str,
    available_specialists: list[dict],
    trace_id: str,
) -> AnalysisPlan:
    """调用 LLM 生成分析计划。

    降级策略：LLM 调用失败或 JSON 解析失败时，降级为所有专家顺序执行。
    """
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"

    # 构建专家列表文本
    specialist_lines = []
    for s in available_specialists:
        specialist_lines.append(
            f"- {s['agent_key']}: {s['name']} — {s.get('description', '')}"
        )
    specialists_text = "\n".join(specialist_lines)

    prompt = _PLAN_GENERATION_PROMPT.format(
        user_query=user_query,
        refined_query=refined_query,
        complexity=complexity,
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
    except Exception as e:
        logger.warning(f"[trace:{trace_id}] Plan 生成 LLM 调用失败: {e}")

    # 降级：所有专家顺序执行
    if not plan_data:
        plan_data = {
            "reasoning": "Plan 生成失败，降级为全量顺序执行",
            "steps": [
                {"step_id": i + 1, "agent_key": s["agent_key"],
                 "query": refined_query, "depends_on": []}
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
) -> tuple[list[dict], list[dict]]:
    """按计划执行，返回 (specialist_results, all_tool_calls)。

    执行策略：
    - 无依赖的步骤可并行（ThreadPoolExecutor）
    - 有依赖的步骤等待依赖完成后执行
    - 步骤失败不阻塞（标记为 FAILED，继续执行其他步骤）
    - 单个 Specialist 内部仍是 ReAct（max 3 turns）
    """
    from agent.multi_agent import run_specialist

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

        # 执行
        if len(parallel_steps) == 1:
            step = parallel_steps[0]
            step.status = StepStatus.RUNNING
            if progress_callback:
                progress_callback(step.step_id, step.agent_name, "running")
            try:
                result = run_specialist(
                    agent_key=step.agent_key,
                    query=step.query,
                    prebuilt_context=prebuilt_context,
                    trace_id=plan.trace_id,
                )
                step.result = result
                step.status = StepStatus.DONE
                step.duration_ms = result.get("duration_ms", 0)
                specialist_results.append(result)
                all_tool_calls.extend(result.get("tool_calls", []))
                if progress_callback:
                    progress_callback(step.step_id, step.agent_name, "done")
            except Exception as e:
                step.status = StepStatus.FAILED
                step.error = str(e)
                logger.error(f"[trace:{plan.trace_id}] Step {step.step_id} ({step.agent_name}) 失败: {e}")
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
                        specialist_results.append(result)
                        all_tool_calls.extend(result.get("tool_calls", []))
                        if progress_callback:
                            progress_callback(step.step_id, step.agent_name, "done")
                    except Exception as e:
                        step.status = StepStatus.FAILED
                        step.error = str(e)
                        logger.error(f"[trace:{plan.trace_id}] Step {step.step_id} ({step.agent_name}) 并行失败: {e}")
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