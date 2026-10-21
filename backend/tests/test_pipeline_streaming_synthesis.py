"""综合阶段流式输出的回归。

对话在 pipeline 路径上曾出现「生成 100 秒、前端一个 chunk 都没收到」：
最终答案走非流式 _call_llm，用户只能等全部生成完才看到内容。
这里锁定：综合阶段必须能通过回调逐块吐出增量，且同步契约不变。
"""
def _drain(gen):
    """迭代生成器，返回 yield 出的事件与 StopIteration.value。"""
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value


def _specialists():
    return [
        {"agent_key": "risk_assessor", "agent": "风险管理师",
         "analysis": "风险分析正文，集中度高需要控制。" * 20, "status": "success", "tool_calls": []},
        {"agent_key": "valuation_expert", "agent": "估值专家",
         "analysis": "估值分析正文，分位处于历史低位。" * 20, "status": "success", "tool_calls": []},
    ]


def _state(query="创业板能补仓吗"):
    """最小 state 替身：只提供综合阶段读取的字段与方法。"""
    class _State:
        original_query = query
        refined_query = query
        conversation_id = None
        message_id = None
        answer = ""
        token_budget = {}

        def get_phase_result(self, key):
            return {}

        def set_phase_result(self, key, value):
            pass

        def record_phase_tokens(self, phase, tokens):
            pass

    return _State()


def _patch_stream(monkeypatch, chunks):
    from types import SimpleNamespace

    from agent.core import orchestrator, pipeline
    import services.llm_service as llm_service

    def fake_stream(**kwargs):
        for text in chunks:
            yield {"content": text, "reasoning": ""}

    def fake_call(**kwargs):
        """非流式回退通道，返回同样的文本，避免单测打真实模型。"""
        return SimpleNamespace(
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="".join(chunks), reasoning_content=None))],
            usage={"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5})

    monkeypatch.setattr(llm_service, "_call_llm_stream", fake_stream, raising=False)
    monkeypatch.setattr(llm_service, "_call_llm", fake_call, raising=False)
    # conftest 默认关闭流式（防止误打真实通道），这里显式打开。
    monkeypatch.setattr(pipeline, "_synthesis_stream_enabled", lambda: True, raising=False)
    # 综合阶段在函数内导入读取开关，需 patch 源模块；关闭仲裁注入避免真实模型调用。
    monkeypatch.setattr(orchestrator, "get_orchestration_config", lambda key, default=None: "false")
    return pipeline


def test_synthesis_emits_chunks_through_callback(monkeypatch):
    pipeline = _patch_stream(monkeypatch, ["结论：", "维持观望。", "风险已标注。"])
    from agent.infra.blackboard import Blackboard

    emitted = []
    result = pipeline._synthesize_multiple_specialists(
        "创业板能补仓吗", _specialists(), Blackboard(), "trace-stream",
        data_gaps=[], plan={}, state=None, chunk_emitter=emitted.append)

    assert [event["type"] for event in emitted] == ["answer_chunk"] * 3
    assert "".join(e["content"] for e in emitted) == "结论：维持观望。风险已标注。"
    # 同步契约不变：仍直接返回三元组，供降级路径与既有测试使用
    assert isinstance(result, tuple) and len(result) == 3
    assert result[0] == "结论：维持观望。风险已标注。"
    assert result[2] == "completed"


def test_synthesis_without_emitter_keeps_previous_behaviour(monkeypatch):
    """不传 chunk_emitter 时不得产生任何回调副作用。"""
    pipeline = _patch_stream(monkeypatch, ["只生成文本。"])
    from agent.infra.blackboard import Blackboard

    result = pipeline._synthesize_multiple_specialists(
        "怎么看", _specialists(), Blackboard(), "trace-noemit", data_gaps=[], plan={}, state=None)

    assert result[0] == "只生成文本。"


def test_phase_synthesis_forwards_stream_to_callers(monkeypatch):
    pipeline = _patch_stream(monkeypatch, ["综合结论：", "分批小额试探。"])
    from agent.infra.blackboard import Blackboard

    emitted = []
    result = pipeline._phase_synthesis(
        _state(), "创业板能补仓吗", {"specialists": _specialists(), "tool_calls": []},
        Blackboard(), "trace-phase", chunk_emitter=emitted.append)

    assert "".join(e["content"] for e in emitted) == "综合结论：分批小额试探。"
    assert result["answer"].startswith("综合结论：")
    assert result["specialist_count"] == 2


def test_streaming_wrapper_yields_events_and_returns_result(monkeypatch):
    """run_pipeline 依赖这层桥接：既收到增量，又能拿到最终 dict。"""
    pipeline = _patch_stream(monkeypatch, ["第一段。", "第二段。"])
    from agent.infra.blackboard import Blackboard

    gen = pipeline._phase_synthesis_streaming(
        _state(), "怎么看", {"specialists": _specialists(), "tool_calls": []},
        Blackboard(), "trace-bridge")
    events, result = _drain(gen)

    assert [e["type"] for e in events] == ["answer_chunk", "answer_chunk"]
    assert isinstance(result, dict)
    # 最终答案后面可能追加置信度校准说明，只校验正文前缀
    assert result["answer"].startswith("第一段。第二段。")


def test_single_specialist_path_returns_without_streaming(monkeypatch):
    """单专家直接复用其结果，不该触发额外模型调用。"""
    pipeline = _patch_stream(monkeypatch, ["不应被调用"])
    from agent.infra.blackboard import Blackboard

    emitted = []
    result = pipeline._phase_synthesis(
        _state("怎么看"), "怎么看", {"specialists": _specialists()[:1], "tool_calls": []},
        Blackboard(), "trace-single", chunk_emitter=emitted.append)

    assert emitted == []
    assert result["answer"].startswith("风险分析正文")
    assert result["specialist_count"] == 1


def test_stream_failure_falls_back_to_non_stream_synthesis(monkeypatch):
    """流式通道异常时必须回退，不能把答案丢成空串。"""
    pipeline = _patch_stream(monkeypatch, ["占位"])
    import services.llm_service as llm_service
    from services.llm import text_completion

    def broken_stream(**kwargs):
        raise RuntimeError("stream channel closed")

    monkeypatch.setattr(llm_service, "_call_llm_stream", broken_stream, raising=False)
    monkeypatch.setattr(
        text_completion, "complete_text_analysis_sync",
        lambda *args, **kwargs: ("回退生成的完整答案" * 10, 0, "completed"))

    from agent.infra.blackboard import Blackboard
    emitted = []
    result = pipeline._synthesize_multiple_specialists(
        "怎么看", _specialists(), Blackboard(), "trace-fallback",
        data_gaps=[], plan={}, state=None, chunk_emitter=emitted.append)

    assert emitted == []
    assert result[0].startswith("回退生成的完整答案")


def test_run_pipeline_emits_answer_chunks_to_consumer(monkeypatch):
    """端到端：run_pipeline 迭代时必须把综合增量 yield 给 SSE 消费方。"""
    import threading
    from importlib import import_module

    pipeline = _patch_stream(monkeypatch, ["流式结论：", "建议维持观望。"])
    multi_agent = import_module("agent.core.multi_agent")
    builder = import_module("agent.core.context_builder")

    specs = [{"agent_key": key, "name": key, "system_prompt": "configured"} for key in ("fast", "second")]
    plan = {"specialists": specs,
            "plan": {"steps": [{"agent_key": s["agent_key"]} for s in specs]}}
    analysis = "已核对可用持仓证据，风险敞口应依据总资产口径评估，缺失信息需要保留并补查。" * 10

    monkeypatch.setattr(pipeline, "_phase_preprocess", lambda *a: {"complexity": "medium", "query_info": {}})
    monkeypatch.setattr(pipeline, "is_simple_chat", lambda *a: False)
    monkeypatch.setattr(pipeline, "needs_clarification", lambda *a: (False, ""))
    monkeypatch.setattr(pipeline, "_phase_info_gather", lambda *a, **kw: {})
    monkeypatch.setattr(pipeline, "_phase_planning", lambda *a: plan)
    monkeypatch.setattr(pipeline, "get_config_bool",
                        lambda key, default: key == "pipeline.parallel_execution")
    monkeypatch.setattr(builder, "build_specialist_context", lambda **kw: "prepared")
    monkeypatch.setattr(pipeline, "_route_model", lambda *a: "mimo")
    monkeypatch.setattr(pipeline, "_phase_memory", lambda *a, **kw: {})
    monkeypatch.setattr(multi_agent, "run_specialist", lambda **kw: {
        "agent_key": kw["agent_key"], "agent": kw["agent_key"], "analysis": analysis,
        "status": "success", "tool_calls": [{"name": "query_portfolio", "result": "{}"}]})

    events = list(pipeline.run_pipeline("怎么看", [], None, None, "trace-e2e"))
    types = [e.get("type") for e in events if isinstance(e, dict)]

    chunks = [e for e in events if isinstance(e, dict) and e.get("type") == "answer_chunk"]
    assert chunks, f"没有收到任何流式增量，实际事件: {types}"
    assert "".join(c["content"] for c in chunks) == "流式结论：建议维持观望。"
    # 最终 answer 事件仍然存在并携带完整答案，前端以它为准
    final = [e for e in events if isinstance(e, dict) and e.get("type") == "answer"]
    assert final and final[-1].get("content", "").startswith("流式结论：")
