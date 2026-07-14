"""单专家自我反思模块测试 — 纯函数逻辑（不依赖 LLM 调用）。

覆盖：
- _parse_reflection_json JSON 解析三级容错（直接/markdown/正则）
- _validate_reflection_dict 字段规范化与校验
- _calc_reflection_score 综合得分计算
- build_retry_prompt 重试提示构建
- _build_tool_summary 工具调用摘要
- is_self_reflection_enabled / get_max_retry 配置开关
"""
import pytest

from agent.self_reflection import (
    _parse_reflection_json,
    _validate_reflection_dict,
    _calc_reflection_score,
    build_retry_prompt,
    _build_tool_summary,
    is_self_reflection_enabled,
    get_max_retry,
)


# ── _parse_reflection_json ─────────────────────────

def test_parse_json_direct():
    """直接 JSON 解析。"""
    response = '{"sufficient": true, "confidence": 0.8, "gaps": [], "issues": [], "need_retry": false}'
    result = _parse_reflection_json(response)
    assert result is not None
    assert result["sufficient"] is True
    assert result["confidence"] == 0.8


def test_parse_json_markdown_code_block():
    """markdown 代码块包裹的 JSON。"""
    response = '''这是评估结果：
```json
{"sufficient": false, "confidence": 0.3, "gaps": ["缺数据"], "need_retry": true}
```
以上是评估。'''
    result = _parse_reflection_json(response)
    assert result is not None
    assert result["sufficient"] is False
    assert result["need_retry"] is True
    assert "缺数据" in result["gaps"]


def test_parse_json_embedded_in_text():
    """JSON 嵌在文本中（正则提取）。"""
    response = '我认为这份分析 sufficient=true, confidence=0.7'
    # 这个不含完整 JSON 对象，应返回 None 或通过正则匹配
    # 正则要求 "sufficient" 在 {} 内
    result = _parse_reflection_json(response)
    # 此处文本没有 {} 包裹，无法提取
    assert result is None


def test_parse_json_empty_returns_none():
    """空字符串返回 None。"""
    assert _parse_reflection_json("") is None
    assert _parse_reflection_json(None) is None


def test_parse_json_invalid_returns_none():
    """无效 JSON 返回 None。"""
    assert _parse_reflection_json("not json at all") is None
    assert _parse_reflection_json("{broken") is None


def test_parse_json_without_sufficient_returns_none():
    """缺少 sufficient 字段返回 None（校验失败）。"""
    result = _parse_reflection_json('{"confidence": 0.8}')
    assert result is None


# ── _validate_reflection_dict ──────────────────────

def test_validate_normalizes_fields():
    """规范化字段类型。"""
    d = {
        "sufficient": "true",  # 字符串
        "confidence": "0.85",  # 字符串
        "gaps": ["gap1"],
        "issues": ["issue1"],
        "need_retry": "true",
    }
    result = _validate_reflection_dict(d)
    assert result is not None
    assert result["sufficient"] is True
    assert result["confidence"] == 0.85
    assert result["need_retry"] is True
    assert result["gaps"] == ["gap1"]


def test_validate_clamps_confidence_range():
    """置信度限制在 0-1 范围。"""
    result = _validate_reflection_dict({"sufficient": True, "confidence": 1.5})
    assert result["confidence"] == 1.0

    result = _validate_reflection_dict({"sufficient": True, "confidence": -0.3})
    assert result["confidence"] == 0.0


def test_validate_defaults_missing_fields():
    """缺失字段使用默认值。"""
    result = _validate_reflection_dict({"sufficient": True})
    assert result["confidence"] == 0.5
    assert result["gaps"] == []
    assert result["issues"] == []
    assert result["need_retry"] is False


def test_validate_non_dict_returns_none():
    """非 dict 返回 None。"""
    assert _validate_reflection_dict("string") is None
    assert _validate_reflection_dict([1, 2]) is None
    assert _validate_reflection_dict(None) is None


def test_validate_gaps_non_list_becomes_empty():
    """gaps 非 list 时为空列表。"""
    result = _validate_reflection_dict({"sufficient": True, "gaps": "not a list"})
    assert result["gaps"] == []


# ── _calc_reflection_score ─────────────────────────

def test_score_sufficient_high_confidence():
    """充分 + 高置信度 → 高分。"""
    result = {"sufficient": True, "confidence": 0.9, "gaps": [], "issues": []}
    score = _calc_reflection_score(result)
    # base 0.7 + 0.9*0.2 = 0.88
    assert score == pytest.approx(0.88)
    assert 0.0 <= score <= 1.0


def test_score_insufficient_low_score():
    """不充分 → 低基础分。"""
    result = {"sufficient": False, "confidence": 0.3, "gaps": [], "issues": []}
    score = _calc_reflection_score(result)
    # base 0.4 + 0.3*0.2 = 0.46
    assert score == pytest.approx(0.46)


def test_score_gaps_penalty():
    """gaps 数量扣分（每个 0.1，最多 0.3）。"""
    result = {"sufficient": True, "confidence": 0.5, "gaps": ["g1", "g2"], "issues": []}
    score = _calc_reflection_score(result)
    # 0.7 + 0.5*0.2 - 0.2 = 0.6
    assert score == pytest.approx(0.6)


def test_score_gaps_penalty_capped():
    """gaps 扣分上限 0.3。"""
    result = {"sufficient": True, "confidence": 0.5, "gaps": ["g1", "g2", "g3", "g4", "g5"], "issues": []}
    score = _calc_reflection_score(result)
    # 0.7 + 0.1 - 0.3 = 0.5
    assert score == pytest.approx(0.5)


def test_score_issues_penalty():
    """issues 数量扣分（每个 0.05，最多 0.2）。"""
    result = {"sufficient": True, "confidence": 0.5, "gaps": [], "issues": ["i1", "i2"]}
    score = _calc_reflection_score(result)
    # 0.7 + 0.1 - 0.1 = 0.7
    assert score == pytest.approx(0.7)


def test_score_never_below_zero():
    """得分不低于 0。"""
    result = {"sufficient": False, "confidence": 0.0, "gaps": ["g"] * 10, "issues": ["i"] * 10}
    score = _calc_reflection_score(result)
    assert score >= 0.0


def test_score_never_above_one():
    """得分不高于 1。"""
    result = {"sufficient": True, "confidence": 1.0, "gaps": [], "issues": []}
    score = _calc_reflection_score(result)
    assert score <= 1.0


# ── build_retry_prompt ─────────────────────────────

def test_build_retry_prompt_with_gaps_and_issues():
    """有 gaps 和 issues 时构建重试提示。"""
    reflection = {
        "gaps": ["未验证基金代码", "未考虑持仓影响"],
        "issues": ["数据来源不清"],
    }
    prompt = build_retry_prompt(reflection)
    assert "自我反思" in prompt
    assert "未验证基金代码" in prompt
    assert "未考虑持仓影响" in prompt
    assert "数据来源不清" in prompt
    assert "补充" in prompt


def test_build_retry_prompt_empty_returns_empty():
    """无 gaps 和 issues 时返回空字符串。"""
    reflection = {"gaps": [], "issues": []}
    assert build_retry_prompt(reflection) == ""


def test_build_retry_prompt_only_gaps():
    """只有 gaps 时仍构建提示。"""
    reflection = {"gaps": ["缺口1"], "issues": []}
    prompt = build_retry_prompt(reflection)
    assert "缺口1" in prompt
    assert "信息缺口" in prompt


# ── _build_tool_summary ────────────────────────────

def test_build_tool_summary_empty():
    """无工具调用时返回提示文本。"""
    summary = _build_tool_summary([])
    assert "无工具调用" in summary


def test_build_tool_summary_with_calls():
    """有工具调用时构建摘要。"""
    tool_calls = [
        {
            "name": "query_valuation",
            "arguments": {"index_code": "000300", "index_name": "沪深300"},
            "result_preview": "PE=12.5, PB=1.3",
        },
        {
            "name": "search_knowledge",
            "arguments": {"query": "估值"},
            "result_preview": "找到3篇文档",
        },
    ]
    summary = _build_tool_summary(tool_calls)
    assert "query_valuation" in summary
    assert "000300" in summary
    assert "search_knowledge" in summary


def test_build_tool_summary_truncates_args():
    """参数超过3个时只显示前3个。"""
    tool_calls = [{
        "name": "test_tool",
        "arguments": {"a": 1, "b": 2, "c": 3, "d": 4},
        "result_preview": "result",
    }]
    summary = _build_tool_summary(tool_calls)
    assert "a=1" in summary
    assert "d=4" not in summary


# ── 配置开关 ───────────────────────────────────────

def test_is_self_reflection_enabled_default():
    """默认开启（配置项在 init_db 中注册为 true）。"""
    # conftest 已 init_db，配置项默认 true
    assert is_self_reflection_enabled() is True


def test_get_max_retry_default():
    """默认最大重试次数为 1。"""
    assert get_max_retry() == 1


def test_is_self_reflection_enabled_can_disable():
    """可通过配置关闭。"""
    from db.config import update_config
    update_config("agent.self_reflection_enabled", "false")
    assert is_self_reflection_enabled() is False
    # 恢复
    update_config("agent.self_reflection_enabled", "true")


def test_get_max_retry_configurable():
    """可配置最大重试次数。"""
    from db.config import update_config
    update_config("agent.self_reflection_max_retry", "3")
    assert get_max_retry() == 3
    # 恢复
    update_config("agent.self_reflection_max_retry", "1")
