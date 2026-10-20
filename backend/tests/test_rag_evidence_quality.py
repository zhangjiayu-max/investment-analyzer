"""RAG 相关性、原始证据和有限算术检查的确定性回归。"""
import copy
import json

import pytest

from db._conn import _get_conn
from services.rag import rag


def evidence(ref="41", body="资产配置应结合风险预算分散投资，检查股票债券现金比例。", **fields):
    return {"content_type": "book", "reference_id": ref, "title": "组合风险与资产配置",
            "body": body, "rank": -1, "_score": .15, **fields}


@pytest.fixture
def isolated_rag(monkeypatch):
    """只替换模型、召回和非本次范围检测；保留真实融合和质量验收。"""
    monkeypatch.setattr(rag, "_detect_index_names", lambda query: [])
    monkeypatch.setattr(rag, "_detect_fund_names", lambda query: [])
    monkeypatch.setattr(rag, "_inject_valuation_data", lambda *a: ("", []))
    monkeypatch.setattr(rag, "expand_query", lambda query: query)
    monkeypatch.setattr(rag, "_tokenize", lambda query: "组合 风险")
    monkeypatch.setattr(rag, "_filter_old_results", lambda results, **kw: (results, 0))
    monkeypatch.setattr(rag, "_relevance_score", lambda *a: (1, {}))
    monkeypatch.setattr(rag, "get_config", lambda key, default=None: default)
    monkeypatch.setattr(rag, "get_config_bool", lambda key, default=False: default)
    monkeypatch.setattr(rag, "get_config_int", lambda key, default=0: default)
    monkeypatch.setattr(rag, "get_rag_config", lambda key, default=None: default)
    monkeypatch.setattr(rag, "RERANK_ENABLED", True)
    monkeypatch.setattr(rag, "_get_reranker", lambda: object())
    monkeypatch.setattr(rag, "_cross_encoder_predict", lambda model, query, results: [.9] * len(results))
    monkeypatch.setattr(rag, "_apply_personalization_boost", lambda *a: None)
    monkeypatch.setattr(rag, "lightweight_rerank", lambda query, results, top_k=5: results[:top_k])
    monkeypatch.setattr(rag, "search_chroma", lambda *a, **kw: ([], 0))

    def retrieve(items):
        monkeypatch.setattr(rag, "search_knowledge", lambda *a, **kw: (copy.deepcopy(items), 0))
        return rag.build_rag_context_with_details("结合持仓分析组合风险和均衡性", limit=8)
    return retrieve


@pytest.mark.parametrize("scores", [[.001, .0089, .0941], [float("nan"), float("inf"), -.2]])
def test_semantic_gate_runs_before_personalization(monkeypatch, scores):
    monkeypatch.setattr(rag, "_get_reranker", lambda: object())
    monkeypatch.setattr(rag, "_cross_encoder_predict", lambda *a: scores + [.8])
    boosted = []
    def boost(results, user_id):
        boosted.extend(r["reference_id"] for r in results)
        for item in results:
            item["personal_boost"] = 100 if item["reference_id"] != "good" else 0
    monkeypatch.setattr(rag, "_apply_personalization_boost", boost)
    result = rag.rerank_results("组合均衡性", [evidence(str(i)) for i in range(3)] + [evidence("good")], user_id="u")
    assert [r["reference_id"] for r in result] == ["good"]
    assert boosted == ["good"]


def test_single_retrieval_is_still_semantically_validated(monkeypatch):
    monkeypatch.setattr(rag, "_get_reranker", lambda: object())
    monkeypatch.setattr(rag, "_cross_encoder_predict", lambda *a: [.001])
    assert rag.rerank_results("组合均衡性", [evidence()]) == []


def test_all_low_semantic_scores_return_explicit_low_quality(isolated_rag, monkeypatch):
    monkeypatch.setattr(rag, "_cross_encoder_predict", lambda *a: [.001])
    result = isolated_rag([evidence()])
    assert result["results"] == []
    assert result["rag_low_quality"] is True
    assert "semantic_relevance_below_threshold" in result["quality_reasons"]


def test_relevant_book_evidence_remains_usable(isolated_rag):
    result = isolated_rag([evidence()])
    assert result["rag_low_quality"] is False
    item = result["results"][0]
    assert item["evidence_id"] == "book:41"
    assert item["quality_status"] == "usable"
    assert item["validation_status"] == "limited_checks_passed"
    assert "book:41" in result["context"]


def test_fusion_score_labels_cannot_exaggerate_low_semantic_scores(isolated_rag, monkeypatch):
    monkeypatch.setattr(rag, "_cross_encoder_predict", lambda *a: [.15])
    result = isolated_rag([evidence()])
    assert "相关度:高" not in result["context"]
    assert "语义相关分:0.150" in result["context"]


def test_reranker_unavailable_is_explicitly_unchecked(isolated_rag, monkeypatch):
    monkeypatch.setattr(rag, "_get_reranker", lambda: False)
    result = isolated_rag([evidence()])
    assert result["rag_low_quality"] is True
    assert result["results"][0]["quality_status"] == "unverified_relevance"


@pytest.mark.parametrize("failure,reason", [(TimeoutError(), "reranker_timeout"), (ValueError(), "reranker_failed")])
def test_reranker_failure_remains_explicitly_unchecked(isolated_rag, monkeypatch, failure, reason):
    def predict(*args):
        raise failure
    monkeypatch.setattr(rag, "_cross_encoder_predict", predict)
    result = isolated_rag([evidence()])
    assert result["rag_low_quality"] is True
    assert reason in result["quality_reasons"]
    assert result["results"][0]["quality_status"] == "unverified_relevance"


def test_partial_score_response_cannot_certify_evidence(isolated_rag, monkeypatch):
    monkeypatch.setattr(rag, "_cross_encoder_predict", lambda *args: [])
    result = isolated_rag([evidence()])
    assert result["rag_low_quality"] is True
    assert "reranker_failed" in result["quality_reasons"]


def test_disabled_reranker_does_not_certify_lexical_evidence(isolated_rag, monkeypatch):
    monkeypatch.setattr(rag, "RERANK_ENABLED", False)
    result = isolated_rag([evidence()])
    assert result["rag_low_quality"] is True
    assert result["results"][0]["quality_status"] == "unverified_relevance"


def test_empty_retrieval_is_explicit_low_quality(isolated_rag):
    result = isolated_rag([])
    assert result["results"] == []
    assert result["rag_low_quality"] is True


def test_all_lexically_irrelevant_fallback_is_flagged(isolated_rag, monkeypatch):
    monkeypatch.setattr(rag, "_relevance_score", lambda *a: (.01, {}))
    result = isolated_rag([evidence()])
    assert result["rag_low_quality"] is True
    assert "lexical_relevance_fallback" in result["quality_reasons"]


def test_trading_rule_cannot_satisfy_allocation_question(isolated_rag):
    result = isolated_rag([evidence(body="月度交易止损规则，亏损超过2%卖出；年化收益预期20%-40%。")])
    assert result["results"] == []
    assert result["rag_low_quality"] is True
    assert "allocation_scope_mismatch" in result["quality_reasons"]


@pytest.mark.parametrize("text, expected_issue", [
    ("传统资产下跌30%、保护资产上涨10%，各占50%，组合收益为正5%。", True),
    ("传统资产下跌20%、保护资产上涨5%，各占50%，组合收益为正40%。", True),
    ("传统资产下跌30%、保护资产上涨10%，配置比例为50%传统资产和50%保护资产时，组合结果为正5%。可能因杠杆而偏离简单计算。", True),
    ("传统资产下跌30%、保护资产上涨10%，各占50%，组合收益为负10%。", False),
    ("股票下跌20%、债券上涨5%，各占50%，组合收益为-7.5%。", False),
    ("传统资产过去下跌30%，保护资产预测上涨10%，建议配置50%，不承诺组合收益。", False),
    ("传统资产下跌30%、保护资产上涨10%，配置比例为30%保护资产和70%传统资产，组合收益为负18%。", False),
    ("传统资产下跌30%、保护资产上涨10%，配置比例为70%传统资产和30%保护资产，组合收益为正18%。", True),
    ("传统资产下跌30%、保护资产上涨10%，配置比例为70%和30%，组合收益为正40%。", False),
    ("去年股票下跌20%，次年债券上涨5%，各占50%，累计组合收益为正40%。", False),
    ("错误示例：传统资产下跌30%、保护资产上涨10%，各占50%，组合收益为正5%。正确结果应为负10%。", False),
    ("传统资产下跌30%、保护资产上涨10%，各占50%，组合收益为正5%是计算错误，实际应为负10%。", False),
])
def test_limited_numeric_checks_only_flag_explicit_weighted_return_conflicts(text, expected_issue):
    from services.rag.evidence_quality import numeric_consistency_issues
    issues = numeric_consistency_issues(text)
    assert bool(issues) is expected_issue
    if expected_issue:
        assert issues[0]["code"] == "weighted_return_inconsistency"
        assert issues[0]["status"] == "requires_source_check"


def test_numeric_conflict_cannot_enter_context_as_strong_evidence(isolated_rag):
    body = "传统资产下跌30%、保护资产上涨10%，各占50%，组合收益为正5%。资产配置应检查风险预算。"
    result = isolated_rag([evidence(body=body, evidence_level="strong")])
    assert result["results"] == []
    assert result["rag_low_quality"] is True
    assert "weighted_return_inconsistency" in result["quality_reasons"]


def test_source_formula_examples_with_unspecified_leverage_require_source_check():
    from services.rag.evidence_quality import numeric_consistency_issues
    text = ("以传统资产下跌30%、保护资产上涨10%为例：当配置比例为50%传统资产和50%保护资产时，组合结果为正5%，"
            "相比全部传统资产亏损30%，实现了正收益缓冲。计算公式为两类资产收益的加权和，但实际数据可能因保护策略的杠杆而偏离简单计算。"
            "表格还显示其他情景：如传统下跌20%、保护上涨5%时，50%配置组合结果为正40%。")
    issues = numeric_consistency_issues(text)
    assert [i["expected_simple_return_pct"] for i in issues] == [-10, -7.5]
    assert all(i["status"] == "requires_source_check" for i in issues)


def test_quality_status_is_not_inherited_across_new_rerank_attempts(monkeypatch):
    item = evidence(quality_reason="reranker_failed", quality_status="unverified_relevance")
    monkeypatch.setattr(rag, "_get_reranker", lambda: object())
    monkeypatch.setattr(rag, "_cross_encoder_predict", lambda *a: [.8])
    result = rag.rerank_results("组合均衡性", [item])
    assert result[0]["quality_status"] == "usable"
    assert "quality_reason" not in result[0]


def test_original_evidence_is_hydrated_by_id_and_category(isolated_rag):
    conn = _get_conn()
    try:
        conn.execute("INSERT INTO knowledge_base(id,category,title,content,source,limitations,evidence_level) VALUES (101,'book','原书唯一标题','原始正文；只适用于长期配置。','原书第3章','[\"不适用于短线\"]','strong')")
        conn.execute("INSERT INTO knowledge_base(id,category,title,content,source) VALUES (102,'book','过期索引标题','错误的其他原文','其他书籍')")
        conn.commit()
    finally:
        conn.close()
    result = isolated_rag([evidence("101", title="过期索引标题", body="组合 风险 分词 正文")])
    item = result["results"][0]
    assert item["body"] == "原始正文；只适用于长期配置。"
    assert item["source"] == "原书第3章"
    assert item["limitations"] == ["不适用于短线"]
    assert item["retrieval_source"] == "fts"


def test_tool_preserves_stable_id_original_content_source_and_limits(monkeypatch):
    import services.rag as public_rag
    from tools import _search_knowledge
    original = "原始未分词内容。" * 100
    conn = _get_conn()
    try:
        conn.execute("INSERT INTO knowledge_base(id,category,title,content,source,limitations) VALUES (71,'book','唯一原书标题',?,'原书第7页','[\"不保证未来收益\"]')", (original,))
        conn.execute("INSERT INTO knowledge_base(id,category,title,content,source) VALUES (72,'book','重复显示标题','另一本书','错误来源')")
        conn.commit()
    finally:
        conn.close()
    item = evidence("71", title="重复显示标题", body="FTS 分词 预览")
    monkeypatch.setattr(public_rag, "build_rag_context_with_details", lambda *a, **kw: {"results": [item], "rag_low_quality": True})
    logged = []
    monkeypatch.setattr(public_rag, "log_rag_search", lambda **kw: logged.append(kw))
    result = json.loads(_search_knowledge({"query": "资产配置"}))
    item = result["results"][0]
    assert item["reference_id"] == "71"
    assert item["evidence_id"] == "book:71"
    assert item["content"] == original
    assert item["source"] == "原书第7页"
    assert item["limitations"] == ["不保证未来收益"]
    assert result["rag_low_quality"] is True
    assert logged[0]["rag_low_quality"] is True


def test_tool_hydration_failure_is_also_logged_as_low_quality(monkeypatch):
    import services.rag as public_rag
    from services.rag import evidence_quality
    from tools import _search_knowledge
    monkeypatch.setattr(public_rag, "build_rag_context_with_details", lambda *a, **kw: {"results": [evidence()], "rag_low_quality": False})
    def unavailable(*args):
        raise RuntimeError("temporary database unavailable")
    monkeypatch.setattr(evidence_quality, "hydrate_evidence", unavailable)
    logged = []
    monkeypatch.setattr(public_rag, "log_rag_search", lambda **kw: logged.append(kw))
    result = json.loads(_search_knowledge({"query": "资产配置"}))
    assert result["rag_low_quality"] is True
    assert logged[0]["rag_low_quality"] is True
