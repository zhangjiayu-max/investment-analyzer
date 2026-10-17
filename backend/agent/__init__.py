"""agent 包 — 已按功能重组为子目录。

通过 meta path finder 维持向后兼容：所有旧的 ``from agent.xxx import yyy``
会自动重定向到新的 ``from agent.<subdir>.xxx import yyy``。

子目录布局：
    core/   — orchestrator, pipeline, multi_agent, router, context_builder, plan_executor, analysis_runner
    memory/ — memory, memory_lifecycle, feedback_learner, kyc_learner
    eval/   — conversation_evaluator, conversation_evolution, eval_scorer, llm_evaluator_agent, rag_evaluator, regression
    safety/ — hallucination_guard, prompt_defense, input_sanitizer, data_gate, validator
    infra/  — cache, convergence, termination, tool_dedup, tool_tracker, tool_broadcast, session_signals, blackboard, message_protocol, react_loop, self_reflection, hooks, orchestrator_optimizer
    query/  — query_rewriter, multi_hop_rag
    kyc/    — kyc, wealth_advisor
    state/  — pipeline_state, ab_testing

注意：``memory`` 和 ``kyc`` 因与子目录同名，由各自包的 __init__.py 重导出处理，
不走 finder 重定向，以保证 ``from agent.memory.xxx import`` 等子包导入正常。

services 的兼容路径由 services 包独立处理，避免两个 finder 互相覆盖。
"""
import importlib
import importlib.util
import sys

# ── agent 模块显式重定向映射 ──
# 注意：agent.memory 和 agent.kyc 不在此表中——它们与子目录同名，
# 由各自包的 __init__.py 重导出处理，以保证 from agent.memory.xxx import 正常
_RELOCATED_MODULES = {
    # core
    'agent.orchestrator': 'agent.core.orchestrator',
    'agent.pipeline': 'agent.core.pipeline',
    'agent.multi_agent': 'agent.core.multi_agent',
    'agent.router': 'agent.core.router',
    'agent.context_builder': 'agent.core.context_builder',
    'agent.plan_executor': 'agent.core.plan_executor',
    'agent.analysis_runner': 'agent.core.analysis_runner',
    # memory（memory 本身由包 __init__.py 重导出）
    'agent.memory_lifecycle': 'agent.memory.memory_lifecycle',
    'agent.feedback_learner': 'agent.memory.feedback_learner',
    'agent.kyc_learner': 'agent.memory.kyc_learner',
    # eval
    'agent.conversation_evaluator': 'agent.eval.conversation_evaluator',
    'agent.conversation_evolution': 'agent.eval.conversation_evolution',
    'agent.eval_scorer': 'agent.eval.eval_scorer',
    'agent.llm_evaluator_agent': 'agent.eval.llm_evaluator_agent',
    'agent.rag_evaluator': 'agent.eval.rag_evaluator',
    'agent.regression': 'agent.eval.regression',
    # safety
    'agent.hallucination_guard': 'agent.safety.hallucination_guard',
    'agent.prompt_defense': 'agent.safety.prompt_defense',
    'agent.input_sanitizer': 'agent.safety.input_sanitizer',
    'agent.data_gate': 'agent.safety.data_gate',
    'agent.validator': 'agent.safety.validator',
    # infra
    'agent.cache': 'agent.infra.cache',
    'agent.convergence': 'agent.infra.convergence',
    'agent.termination': 'agent.infra.termination',
    'agent.tool_dedup': 'agent.infra.tool_dedup',
    'agent.tool_tracker': 'agent.infra.tool_tracker',
    'agent.tool_broadcast': 'agent.infra.tool_broadcast',
    'agent.session_signals': 'agent.infra.session_signals',
    'agent.blackboard': 'agent.infra.blackboard',
    'agent.message_protocol': 'agent.infra.message_protocol',
    'agent.react_loop': 'agent.infra.react_loop',
    'agent.self_reflection': 'agent.infra.self_reflection',
    'agent.hooks': 'agent.infra.hooks',
    'agent.orchestrator_optimizer': 'agent.infra.orchestrator_optimizer',
    # query
    'agent.query_rewriter': 'agent.query.query_rewriter',
    'agent.multi_hop_rag': 'agent.query.multi_hop_rag',
    # kyc（kyc 本身由包 __init__.py 重导出）
    'agent.wealth_advisor': 'agent.kyc.wealth_advisor',
    # state
    'agent.pipeline_state': 'agent.state.pipeline_state',
    'agent.ab_testing': 'agent.state.ab_testing',
}

class _ProxyLoader:
    """延迟加载规范模块，别名共享对象且保留规范 spec。"""

    def __init__(self, real_name):
        self.real_name = real_name

    def create_module(self, spec):
        module = importlib.import_module(self.real_name)
        self._canonical_spec = module.__spec__
        return module

    def exec_module(self, module):
        module.__spec__ = self._canonical_spec


class _AgentRelocationFinder:
    """将旧模块路径重定向到新位置。

    agent.xxx → agent.<subdir>.xxx（显式映射）
    多级名称（如 agent.core.orchestrator）不在映射中，自动跳过。
    """

    _agent_relocation_finder = True

    def find_spec(self, name, path=None, target=None):
        # 1) 显式映射（agent 模块）
        new_name = _RELOCATED_MODULES.get(name)
        if new_name:
            return self._make_spec(name, new_name)

        return None

    @staticmethod
    def _make_spec(old_name, new_name):
        """仅创建 spec；加载交给 Loader，避免 finder 中重入导入。"""
        return importlib.util.spec_from_loader(
            old_name, loader=_ProxyLoader(new_name)
        )


# 在 sys.meta_path 前部安装 finder
if not any(getattr(f, "_agent_relocation_finder", False) for f in sys.meta_path):
    sys.meta_path.insert(0, _AgentRelocationFinder())
