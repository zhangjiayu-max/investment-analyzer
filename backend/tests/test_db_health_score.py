"""DB Health Score 层测试。"""
import pytest
from db.health_score import save_health_score, get_health_score, list_health_scores


class TestSaveHealthScore:
    def test_save_new(self, tmp_db):
        save_health_score(
            score_date="2026-06-23", total_score=750,
            score_quality=160, score_diversification=150,
            score_valuation=170, score_behavior=140, score_risk=130,
            advice=["建议A", "建议B"],
            detail={"quality": {"matched_count": 5}},
        )
        score = get_health_score("2026-06-23")
        assert score is not None
        assert score["total_score"] == 750
        assert score["score_quality"] == 160

    def test_save_overwrite(self, tmp_db):
        """同一天重复保存应覆盖。"""
        save_health_score("2026-06-23", 700, 140, 140, 140, 140, 140, [], {})
        save_health_score("2026-06-23", 800, 160, 160, 160, 160, 160, [], {})
        score = get_health_score("2026-06-23")
        assert score is not None
        assert score["total_score"] >= 700


class TestGetHealthScore:
    def test_get_existing(self, tmp_db):
        save_health_score("2026-06-23", 600, 120, 120, 120, 120, 120, [], {})
        score = get_health_score("2026-06-23")
        assert score is not None
        assert score["total_score"] == 600

    def test_get_nonexistent(self, tmp_db):
        assert get_health_score("2099-01-01") is None


class TestListHealthScores:
    def test_empty(self, tmp_db):
        assert list_health_scores() == []

    def test_multiple(self, tmp_db):
        save_health_score("2026-06-21", 700, 140, 140, 140, 140, 140, [], {})
        save_health_score("2026-06-22", 750, 150, 150, 150, 150, 150, [], {})
        save_health_score("2026-06-23", 800, 160, 160, 160, 160, 160, [], {})
        scores = list_health_scores()
        assert len(scores) == 3
        # 按日期降序
        assert scores[0]["score_date"] == "2026-06-23"

    def test_limit(self, tmp_db):
        for i in range(10):
            save_health_score(f"2026-06-{13+i:02d}", 700, 140, 140, 140, 140, 140, [], {})
        scores = list_health_scores(limit=3)
        assert len(scores) == 3
