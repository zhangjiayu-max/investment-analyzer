"""统一证据层测试。"""


def test_build_unified_evidence_combines_all_blocks(monkeypatch):
    from services import unified_evidence

    monkeypatch.setattr(unified_evidence, "_build_market_evidence", lambda **_: {
        "summary": "市场：1 只可上车",
        "chips": [{"label": "可上车", "value": 1, "tone": "good"}],
        "highlights": ["市场热点：半导体"],
        "watchlist_items": [],
        "active_events": [],
        "verified_events": [],
        "event_accuracy": {"total": 1, "accuracy": 1.0},
    })
    monkeypatch.setattr(unified_evidence, "_build_opportunity_evidence", lambda **_: {
        "summary": "机会：2 个主题可小仓试投",
        "chips": [{"label": "可买主题", "value": 2, "tone": "good"}],
        "highlights": ["主题机会：AI·can_buy·82分"],
        "opportunity_items": [{"theme": "AI"}],
        "track_stats": {"total": 1, "open_tracks": 1, "due_reviews": 0, "average_return_pct": 3.2},
    })
    monkeypatch.setattr(unified_evidence, "_build_decision_evidence", lambda **_: {
        "summary": "决策：待执行 2",
        "chips": [{"label": "待执行", "value": 2, "tone": "warn"}],
        "highlights": ["优先处理到期复盘决策"],
        "candidate_items": [],
        "stats": {"total": 2, "reviewed": 1},
    })
    monkeypatch.setattr(unified_evidence, "_build_knowledge_evidence", lambda **_: {
        "summary": "知识：3 条教训",
        "chips": [{"label": "教训条数", "value": 3, "tone": "warn"}],
        "highlights": ["最近教训：回归纪律"],
        "recent_lessons": [{"title": "回归纪律"}],
        "rag_context": "",
        "rag_items": [],
        "knowledge_stats": {"total": 10},
        "feedback_stats": {"total_lessons": 3, "useful_lessons": 2},
    })
    monkeypatch.setattr(unified_evidence, "_build_regression_evidence", lambda **_: {
        "summary": "回归：准确率 70%",
        "chips": [{"label": "建议准确率", "value": "70%", "tone": "good"}],
        "highlights": ["最近验证：指数A"],
        "recent_verified": [{"index_name": "指数A"}],
        "accuracy_stats": {"overall": {"verified": 1, "accuracy": 0.7}},
        "adoption_stats": {"total_marked": 1, "adopted_correct_rate": 0.8, "rejected_correct_rate": 0.5},
        "event_stats": {"total": 1, "accuracy": 1.0},
    })

    evidence = unified_evidence.build_unified_evidence(user_id="u1", query="问题", scenario_type="buy_decision")

    assert "市场：1 只可上车" in evidence["summary"]
    assert "机会：2 个主题可小仓试投" in evidence["summary"]
    assert "决策：待执行 2" in evidence["summary"]
    assert "知识：3 条教训" in evidence["summary"]
    assert "回归：准确率 70%" in evidence["summary"]
    assert "共享证据" in evidence["prompt_context"]
    assert "市场信号" in evidence["prompt_context"]
    assert "主题机会" in evidence["prompt_context"]
    assert "知识库证据" in evidence["prompt_context"]
    assert evidence["opportunity"]["summary"] == "机会：2 个主题可小仓试投"
    assert evidence["recommendation"]


def test_shared_signals_wrapper_returns_unified_snapshot(monkeypatch):
    from services import shared_signals

    monkeypatch.setattr(shared_signals, "build_unified_evidence", lambda **_: {"summary": "ok"})

    assert shared_signals.build_shared_signals() == {"summary": "ok"}
