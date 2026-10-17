"""Agent 执行健康回归：隔离 DB 和 LLM，验证真实执行链及协议。"""
import asyncio
import copy
import importlib
import json
import socket
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """覆盖共享 fixture，所有依赖注入，不初始化数据库。"""
    import db._conn as conn
    import db.config as config
    monkeypatch.setattr(conn, "DB_PATH", tmp_path / "unused.db")
    monkeypatch.setattr(config, "get_config", lambda key, default="": default)
    for name in ("get_config_bool", "get_config_int", "get_config_float"):
        monkeypatch.setattr(config, name, lambda key, default: default)
    network_attempts = []
    def reject_network(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("Agent 单元测试禁止真实网络访问")
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    yield
    assert not network_attempts, "测试中发生了被异常处理吞掉的网络访问"


def completion(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=SimpleNamespace(total_tokens=42),
    )


@pytest.fixture
def core(monkeypatch):
    module = importlib.import_module("agent.core.multi_agent")
    for name in ("get_config_bool", "get_config_int", "get_config_float"):
        monkeypatch.setattr(module, name, lambda key, default: default)
    return module


@pytest.fixture
def reflection(monkeypatch):
    module = importlib.import_module("agent.infra.self_reflection")
    monkeypatch.setitem(sys.modules, "agent.core.orchestrator", SimpleNamespace(
        _get_model_for_agent=lambda key: "test-model"))
    monkeypatch.setattr(module, "_is_cross_check_enabled", lambda: False)
    return module


def test_reflection_reads_completion_content_and_renders_json_prompt(reflection, monkeypatch):
    llm_service = importlib.import_module("services.llm_service")
    calls = []

    def llm(**kwargs):
        calls.append(kwargs)
        return completion('{"sufficient": false, "need_retry": true, "gaps": ["缺数据"]}')

    monkeypatch.setattr(llm_service, "_call_llm", llm)
    result = reflection.evaluate_analysis("已有分析", [], "我的持仓如何？")
    assert result is not None
    assert result["need_retry"] is True
    assert result["gaps"] == ["缺数据"]
    assert '"sufficient": true/false' in calls[0]["messages"][1]["content"]


def test_compression_preserves_every_tool_response_and_reasoning(core):
    messages = [
        {"role": "assistant", "content": None, "reasoning_content": "先查全部持仓",
         "tool_calls": [{"id": code, "function": {"name": "query_portfolio"}} for code in ("a", "b")]},
        {"role": "tool", "tool_call_id": "a", "content": "A" * 900},
        {"role": "tool", "tool_call_id": "b", "content": "B" * 900},
        {"role": "assistant", "content": None, "reasoning_content": "再查基金",
         "tool_calls": [{"id": "c", "function": {"name": "query_fund_info"}}]},
        {"role": "tool", "tool_call_id": "c", "content": "最新数据" * 100},
    ]
    original = copy.deepcopy(messages)
    core._compress_prior_tool_messages(messages)
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == ["a", "b", "c"]
    assert len(messages) == len(original)
    assert messages[0] == original[0]
    assert messages[3] == original[3]
    assert messages[-1] == original[-1]
    assert len(messages[1]["content"]) < 900
    assert len(messages[2]["content"]) < 900
    compressed = copy.deepcopy(messages)
    core._compress_prior_tool_messages(messages)
    assert messages == compressed


@pytest.mark.parametrize("arguments", [{"fund_code": "007679"}, '{"fund_code": "007679"}'])
@pytest.mark.parametrize("result", [{"basic_info": {"fund_name": "实际另一只基金"}},
                                    '{"basic_info": {"fund_name": "实际另一只基金"}}'])
def test_holding_code_guard_accepts_structured_arguments_and_full_result(core, monkeypatch, arguments, result):
    import db
    monkeypatch.setattr(db, "list_holdings", lambda: [{"fund_code": "014846"}])
    hints = core._detect_fund_code_hallucination(
        [{"name": "query_fund_info", "arguments": arguments, "result": result}],
        {"agent_key": "fund_analyst"}, query="分析我持仓中的博时恒乐债券")
    assert "007679" in hints
    assert "实际另一只基金" in hints


@pytest.mark.parametrize("query", ["查询007679这只新基金", "我的持仓之外，想看看007679能不能买", "我的持仓之外想看看易方达新基金", "比较014846和007679", ""])
def test_holding_code_guard_allows_explicit_non_holding_fund_queries(core, monkeypatch, query):
    import db
    monkeypatch.setattr(db, "list_holdings", lambda: [{"fund_code": "014846"}])
    hints = core._detect_fund_code_hallucination(
        [{"name": "query_fund_info", "arguments": {"fund_code": "007679"}}],
        {"agent_key": "fund_analyst"}, query=query)
    assert hints == ""


def test_text_tool_call_keeps_full_result_for_code_validation(core, monkeypatch):
    raw = json.dumps({"padding": "x" * 300, "basic_info": {"fund_name": "基金真实名称"}}, ensure_ascii=False)
    monkeypatch.setattr(core, "execute_tool", lambda *args, **kwargs: raw)
    log = []
    assert core._process_text_tool_calls(
        '<tool_call><function=query_fund_info><parameter=fund_code>007679</parameter></function></tool_call>',
        [], log, "基金分析师")
    assert log[0]["result"] == raw


@pytest.fixture
def specialist_setup(core, reflection, monkeypatch):
    agent = {"name": "基金分析师", "agent_key": "fund_analyst", "icon": "F",
             "tools": [], "system_prompt": "原有系统提示"}
    monkeypatch.setattr(core, "load_specialist_agents", lambda: {"fund_analyst": agent})
    monkeypatch.setattr(core, "_estimate_specialist_tokens", lambda *args: 100)
    monkeypatch.setattr(core, "_parse_structured_output", lambda *args: {})
    monkeypatch.setattr(reflection, "is_self_reflection_enabled", lambda: True)
    monkeypatch.setattr(reflection, "get_max_retry", lambda: 1)
    return agent


def test_specialist_reflection_need_retry_replaces_answer_with_completion_text(core, reflection, specialist_setup, monkeypatch):
    llm_service = importlib.import_module("services.llm_service")
    original, improved = "初次分析" * 60, "补充可靠数据后的分析" * 30
    monkeypatch.setattr(llm_service, "_call_llm", lambda **kwargs: completion(
        '{"sufficient": false, "need_retry": true, "gaps": ["补充来源"]}'))
    calls = []

    def llm(**kwargs):
        calls.append(kwargs)
        return completion(improved if "self_reflection_retry" in kwargs["caller"] else original)

    monkeypatch.setattr(core, "_call_llm", llm)
    result = core.run_specialist("fund_analyst", "分析已有持仓", from_pipeline=True,
                                 prebuilt_context="已构建上下文")
    assert result["analysis"] == improved
    assert result["self_reflection"]["gaps_resolved"] is True
    assert len(calls) == 2
    assert "补充来源" in calls[-1]["messages"][-1]["content"]
    assert any(m.get("content") == original for m in calls[-1]["messages"])


@pytest.mark.parametrize("closure", [False, True])
@pytest.mark.parametrize("reasoning_location", ["model_extra", "attribute"])
def test_reflection_retry_preserves_answer_reasoning(core, reflection, specialist_setup, monkeypatch,
                                                   closure, reasoning_location):
    """MIMO 的最终回答与收口回答在续写时也必须回传 reasoning_content。"""
    calls = []
    monkeypatch.setattr(reflection, "evaluate_analysis", lambda **kwargs: {
        "need_retry": True, "gaps": ["补充来源"]})

    def llm(**kwargs):
        calls.append(kwargs)
        is_summary = kwargs["caller"].endswith("#summary")
        response = completion("初次短答" if closure and not is_summary and len(calls) == 1
                              else "有来源的专业分析" * 40)
        reasoning = "收口推理" if is_summary else "初次推理"
        message = response.choices[0].message
        if reasoning_location == "model_extra":
            message.model_extra = {"reasoning_content": reasoning}
        else:
            message.reasoning_content = reasoning
        return response

    monkeypatch.setattr(core, "_call_llm", llm)
    core.run_specialist("fund_analyst", "分析已有持仓", from_pipeline=True,
                        prebuilt_context="已构建上下文")
    retry_call = next(call for call in calls if "self_reflection_retry" in call["caller"])
    assistant = retry_call["messages"][-2]
    assert assistant["role"] == "assistant"
    assert assistant["reasoning_content"] == ("收口推理" if closure else "初次推理")


def test_specialist_prompt_override_preserves_cached_agent(core, reflection, specialist_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(reflection, "is_self_reflection_enabled", lambda: False)
    monkeypatch.setattr(core, "_inject_kyc_profile", lambda text, agent: text + "\nKYC保留")

    def llm(**kwargs):
        calls.append(kwargs)
        return completion("基于配置提示分析" * 30)

    monkeypatch.setattr(core, "_call_llm", llm)
    result = core.run_specialist("fund_analyst", "基金分析", prebuilt_context="持仓保留",
                                 system_prompt_override="数据库启用的提示")
    system = calls[0]["messages"][0]["content"]
    assert "数据库启用的提示" in system
    assert "KYC保留" in system and "持仓保留" in system
    assert "原有系统提示" not in system
    assert specialist_setup["system_prompt"] == "原有系统提示"
    assert result["agent_key"] == "fund_analyst"


@pytest.fixture
def runner_setup(monkeypatch):
    import db
    import db.agents
    runner = importlib.import_module("agent.core.analysis_runner")
    monkeypatch.setattr(db, "get_analysis_agent", lambda agent_id: {"name": "配置专家", "system_prompt": "分析提示"})
    monkeypatch.setattr(runner, "_call_llm", lambda **kwargs: completion("可靠分析"))
    runs = []
    histories = []
    monkeypatch.setattr(db.agents, "create_agent_run", lambda **kwargs: runs.append(kwargs) or 9)

    def save(index_code, index_name, agent_id, agent_name, prompt_used, news_context,
             valuation_context, result="", token_usage=0, status="done", error_msg=""):
        histories.append(locals())
        return 7

    monkeypatch.setattr(db, "create_analysis_history", save)
    return runner, runs, histories


def test_runner_saves_history_with_required_schema_fields(runner_setup):
    runner, runs, histories = runner_setup
    result = asyncio.run(runner.run_agent_analysis(3, "portfolio", {"持仓": "持仓数据"}, "持仓健康度", record_type="panorama"))
    assert result["record_id"] == 7
    assert histories[0]["index_code"] == ""
    assert histories[0]["index_name"] == ""
    assert histories[0]["valuation_context"] == ""
    assert histories[0]["status"] == "done"
    assert runs[0]["status"] == "success"


def test_runner_rejects_disabled_agent_before_llm_and_history(runner_setup, monkeypatch):
    import db
    runner, runs, histories = runner_setup
    monkeypatch.setattr(db, "get_analysis_agent", lambda agent_id: {
        "name": "禁用专家", "system_prompt": "配置提示", "is_active": 0})
    def unexpected_llm(**kwargs):
        pytest.fail("禁用 Agent 不得发起 LLM 请求")
    monkeypatch.setattr(runner, "_call_llm", unexpected_llm)
    with pytest.raises(ValueError, match="禁用"):
        asyncio.run(runner.run_agent_analysis(3, "portfolio", {}, "分析", record_type="panorama"))
    assert not histories and not runs


def test_runner_propagates_history_save_failure_and_records_failed_run(runner_setup, monkeypatch):
    import db
    runner, runs, histories = runner_setup

    def fail(**kwargs):
        raise RuntimeError("history database unavailable")

    monkeypatch.setattr(db, "create_analysis_history", fail)
    with pytest.raises(RuntimeError, match="history database unavailable"):
        asyncio.run(runner.run_agent_analysis(3, "portfolio", {}, "分析", record_type="panorama"))
    assert runs[-1]["status"] == "failed"


@pytest.mark.parametrize("content", [None, "", "  \n "])
def test_runner_rejects_empty_model_response_and_records_failure(runner_setup, monkeypatch, content):
    runner, runs, histories = runner_setup
    monkeypatch.setattr(runner, "_call_llm", lambda **kwargs: completion(content))
    with pytest.raises(ValueError, match="空"):
        asyncio.run(runner.run_agent_analysis(3, "portfolio", {}, "分析", record_type="panorama"))
    assert runs[-1]["status"] == "failed"
    assert histories[-1]["status"] == "error"


@pytest.mark.parametrize("response", [None, SimpleNamespace(choices=[], usage=None)])
def test_runner_rejects_missing_completion_with_failed_history(runner_setup, monkeypatch, response):
    runner, runs, histories = runner_setup
    monkeypatch.setattr(runner, "_call_llm", lambda **kwargs: response)
    with pytest.raises(ValueError, match="空"):
        asyncio.run(runner.run_agent_analysis(3, "portfolio", {}, "分析", record_type="panorama"))
    assert runs[-1]["status"] == "failed"
    assert histories[-1]["error_msg"]
    assert histories[-1]["status"] == "error"
