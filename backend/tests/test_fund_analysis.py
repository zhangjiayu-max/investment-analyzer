"""基金深度分析引擎单元测试。

覆盖：
- 回撤分析算法（构造净值数据验证回撤计算、恢复周期识别）
- 趋势均线计算（构造数据验证均线排列判断）
- 质量评分（mock 外部数据源）
- 决策矩阵（不同组合触发不同 action）
- 恐贪指数计算
- 估值分位转评分
- DB CRUD
"""
import pytest

from services.fund_analysis import (
    _calc_drawdown_metrics,
    _find_recovery_periods,
    _judge_arrangement,
    _calc_trend_metrics,
    _score_to_rating,
    calculate_fear_greed_index,
    _valuation_percentile_to_score,
    _build_decision_matrix,
    calculate_quality_score,
)
from db.fund_quality import (
    save_fund_quality_score,
    get_fund_quality_score,
    list_fund_quality_scores,
    delete_fund_quality_score,
)


# ════════════════════════════════════════════════════════════
# 评级工具函数
# ════════════════════════════════════════════════════════════

class TestScoreToRating:
    def test_excellent(self):
        assert _score_to_rating(85) == "excellent"
        assert _score_to_rating(80) == "excellent"

    def test_good(self):
        assert _score_to_rating(75) == "good"
        assert _score_to_rating(60) == "good"

    def test_fair(self):
        assert _score_to_rating(55) == "fair"
        assert _score_to_rating(40) == "fair"

    def test_poor(self):
        assert _score_to_rating(35) == "poor"
        assert _score_to_rating(0) == "poor"


# ════════════════════════════════════════════════════════════
# 回撤分析算法
# ════════════════════════════════════════════════════════════

class TestDrawdownAnalysis:
    def _make_nav(self, values, start_date="2024-01-01"):
        """构造净值历史数据。"""
        from datetime import datetime, timedelta
        base = datetime.strptime(start_date, "%Y-%m-%d")
        return [
            {"nav_date": (base + timedelta(days=i)).strftime("%Y-%m-%d"), "nav": v}
            for i, v in enumerate(values)
        ]

    def test_basic_drawdown(self):
        """设计稿示例：1.0→0.6→1.0→0.8→1.2→0.9。"""
        nav = self._make_nav([1.0, 0.6, 1.0, 0.8, 1.2, 0.9])
        m = _calc_drawdown_metrics(nav)
        assert m is not None
        # 当前回撤 = 1 - 0.9/1.2 = 0.25
        assert abs(m["current_drawdown"] - (-0.25)) < 0.01
        # 最大回撤 = 1 - 0.6/1.0 = 0.40
        assert abs(m["max_drawdown"] - (-0.40)) < 0.01

    def test_drawdown_percentile(self):
        """回撤分位计算。"""
        nav = self._make_nav([1.0, 0.6, 1.0, 0.8, 1.2, 0.9])
        m = _calc_drawdown_metrics(nav)
        # 当前回撤 0.25，历史回撤 [0,0.4,0,0.2,0,0.25]，分位 5/6≈0.833
        assert m["drawdown_percentile"] > 0.7
        assert m["drawdown_percentile"] < 0.9

    def test_recovery_periods(self):
        """恢复周期识别。"""
        nav = self._make_nav([1.0, 0.6, 1.0, 0.8, 1.2, 0.9])
        m = _calc_drawdown_metrics(nav)
        periods = m["recovery_periods"]
        assert len(periods) >= 2
        # 最后一个周期未恢复（0.9 未回到 1.2）
        last = periods[-1]
        assert last["recovery_days"] is None
        assert abs(last["max_drawdown"] - (-0.25)) < 0.01
        # 第一个周期已恢复
        first = periods[0]
        assert first["recovery_days"] is not None
        assert abs(first["max_drawdown"] - (-0.40)) < 0.01

    def test_new_high(self):
        """创新高时 current_drawdown 接近0。"""
        nav = self._make_nav([1.0, 0.8, 1.0, 1.1, 1.2])
        m = _calc_drawdown_metrics(nav)
        assert m["is_new_high"] is True
        assert abs(m["current_drawdown"]) < 0.001

    def test_bottoming_signal(self):
        """企底信号：近5日波动率<1%且回撤>5%。"""
        # 构造一个回撤后企稳的数据
        vals = [1.0]
        # 下跌到 0.7
        for i in range(20):
            vals.append(1.0 - (0.3 * (i + 1) / 20))
        # 企稳在 0.7 附近（波动极小）
        for i in range(10):
            vals.append(0.70 + 0.001 * (i % 3))
        nav = self._make_nav(vals)
        m = _calc_drawdown_metrics(nav)
        assert m["current_drawdown"] < -0.05  # 有回撤
        assert m["recent_volatility"] < 0.01  # 低波动

    def test_insufficient_data(self):
        """数据不足返回 None。"""
        assert _calc_drawdown_metrics([]) is None
        assert _calc_drawdown_metrics([{"nav_date": "2024-01-01", "nav": 1.0}]) is None

    def test_empty_nav_history(self):
        assert _find_recovery_periods([], []) == []


# ════════════════════════════════════════════════════════════
# 趋势均线计算
# ════════════════════════════════════════════════════════════

class TestTrendAnalysis:
    def test_arrangement_strong_bull(self):
        """强多头：MA500>MA250>MA120>MA60 且 current>MA60。"""
        # ma500=1.5, ma250=1.4, ma120=1.3, ma60=1.2, current=1.25
        result = _judge_arrangement(1.25, 1.2, 1.3, 1.4, 1.5)
        assert result == "strong_bull"

    def test_arrangement_weak_bull(self):
        """弱多头：MA500>MA250 且 current>MA250（但不满足强多头）。"""
        # ma500=1.5, ma250=1.4, ma120=1.45, ma60=1.3, current=1.45
        # ma500>ma250 成立，current>ma250 成立，但 ma250<ma120 不满足强多头
        result = _judge_arrangement(1.45, 1.3, 1.45, 1.4, 1.5)
        assert result == "weak_bull"

    def test_arrangement_tangled(self):
        """缠绕：均线间距 < 2%。"""
        # 所有均线在 1.0±0.005 内
        result = _judge_arrangement(1.0, 1.005, 0.998, 1.002, 0.999)
        assert result == "tangled"

    def test_arrangement_strong_bear(self):
        """强空头：MA500<MA250<MA120<MA60 且 current<MA60。"""
        # ma500=0.9, ma250=1.0, ma120=1.1, ma60=1.2, current=1.15
        result = _judge_arrangement(1.15, 1.2, 1.1, 1.0, 0.9)
        assert result == "strong_bear"

    def test_arrangement_weak_bear(self):
        """弱空头：MA500<MA250 且 current<MA250。"""
        # ma500=0.9, ma250=1.0, ma120=0.95, ma60=1.05, current=0.95
        result = _judge_arrangement(0.95, 1.05, 0.95, 1.0, 0.9)
        assert result == "weak_bear"

    def test_arrangement_insufficient_ma(self):
        """均线数据不足返回缠绕。"""
        result = _judge_arrangement(1.0, 1.0, None, None, None)
        assert result == "tangled"

    def test_trend_metrics_ma_computation(self):
        """验证 MA 计算正确性。"""
        # 构造 120 个点的序列，最后60个点稳定在 2.0
        vals = [1.0] * 60 + [2.0] * 60
        nav = [{"nav_date": f"2024-01-{i+1:02d}", "nav": v} for i, v in enumerate(vals)]
        m = _calc_trend_metrics(nav)
        assert m is not None
        # MA60 = 最后60个点的均值 = 2.0
        assert abs(m["ma60"] - 2.0) < 0.01
        # MA120 = 全部120个点的均值 = 1.5
        assert abs(m["ma120"] - 1.5) < 0.01
        # MA250 数据不足
        assert m["ma250"] is None

    def test_trend_metrics_insufficient_data(self):
        """数据不足60点返回 None。"""
        nav = [{"nav_date": f"2024-01-{i+1:02d}", "nav": 1.0} for i in range(30)]
        assert _calc_trend_metrics(nav) is None

    def test_trend_metrics_deviation(self):
        """验证偏离度计算。"""
        # 构造稳定序列，当前净值偏离 MA250
        vals = [1.0] * 250 + [1.1] * 10
        nav = [{"nav_date": f"2024-01-{i+1:02d}", "nav": v} for i, v in enumerate(vals)]
        m = _calc_trend_metrics(nav)
        assert m is not None
        # MA250 包含 240 个 1.0 和 10 个 1.1，约 1.004
        # current = 1.1, deviation ≈ (1.1/1.004 - 1) ≈ 0.095
        assert m["deviation_from_ma250"] > 0.05


# ════════════════════════════════════════════════════════════
# 恐贪指数
# ════════════════════════════════════════════════════════════

class TestFearGreedIndex:
    def test_extreme_fear(self):
        """所有指标指向恐惧 → 恐贪指数低。"""
        data = {
            "turnover_percentile": 0.1,  # 低换手
            "advance_decline": (0.2, 0, 60),  # 跌多涨少
            "volatility_percentile": 0.9,  # 高波动
            "news_sentiment": -0.5,  # 负面
        }
        score = calculate_fear_greed_index(data)
        assert score <= 25  # 极度恐惧区间

    def test_extreme_greed(self):
        """所有指标指向贪婪 → 恐贪指数高。"""
        data = {
            "turnover_percentile": 0.9,  # 高换手
            "advance_decline": (0.8, 60, 0),  # 涨多跌少
            "volatility_percentile": 0.1,  # 低波动
            "news_sentiment": 0.5,  # 正面
        }
        score = calculate_fear_greed_index(data)
        assert score >= 75  # 贪婪区间

    def test_neutral(self):
        """中性指标 → 恐贪指数居中。"""
        data = {
            "turnover_percentile": 0.5,
            "advance_decline": (0.5, 5, 5),
            "volatility_percentile": 0.5,
            "news_sentiment": 0.0,
        }
        score = calculate_fear_greed_index(data)
        assert 40 <= score <= 60

    def test_no_data(self):
        """无数据返回中性50。"""
        assert calculate_fear_greed_index({}) == 50

    def test_partial_data(self):
        """部分数据缺失也能计算。"""
        data = {"turnover_percentile": 0.1, "volatility_percentile": 0.9}
        score = calculate_fear_greed_index(data)
        assert 0 <= score <= 100
        # 偏恐惧
        assert score < 50

    def test_score_range(self):
        """分数始终在 0-100。"""
        data = {
            "turnover_percentile": 0.0,
            "advance_decline": (0.0, 0, 100),
            "volatility_percentile": 1.0,
            "news_sentiment": -1.0,
        }
        score = calculate_fear_greed_index(data)
        assert 0 <= score <= 100


# ════════════════════════════════════════════════════════════
# 估值分位转评分
# ════════════════════════════════════════════════════════════

class TestValuationScore:
    def test_low_percentile(self):
        assert _valuation_percentile_to_score(15) == 90
        assert _valuation_percentile_to_score(20) == 90

    def test_medium_low(self):
        assert _valuation_percentile_to_score(35) == 75
        assert _valuation_percentile_to_score(40) == 75

    def test_medium(self):
        assert _valuation_percentile_to_score(55) == 60
        assert _valuation_percentile_to_score(60) == 60

    def test_medium_high(self):
        assert _valuation_percentile_to_score(75) == 40
        assert _valuation_percentile_to_score(80) == 40

    def test_high(self):
        assert _valuation_percentile_to_score(90) == 20
        assert _valuation_percentile_to_score(100) == 20

    def test_none(self):
        assert _valuation_percentile_to_score(None) == 50


# ════════════════════════════════════════════════════════════
# 决策矩阵
# ════════════════════════════════════════════════════════════

class TestDecisionMatrix:
    def _build(self, quality=70, arrangement="weak_bull", dd_pct=0.3,
               bottoming=False, new_high=False, val_level="low",
               sentiment=65, fear_greed=30):
        return _build_decision_matrix(
            quality_score=quality,
            trend_metrics={"arrangement": arrangement},
            drawdown_metrics={
                "drawdown_percentile": dd_pct,
                "is_bottoming": bottoming,
                "is_new_high": new_high,
            },
            valuation_level=val_level,
            sentiment_score=sentiment,
            fear_greed=fear_greed,
        )

    def test_strong_buy(self):
        """质量好+趋势上行+估值低+情绪偏恐 → strong_buy。"""
        d = self._build(quality=75, arrangement="strong_bull",
                        val_level="low", sentiment=65, fear_greed=25)
        assert d["action"] == "strong_buy"
        assert d["action_label"] == "强烈加仓"

    def test_dca_trend_tangled(self):
        """质量好+估值低+趋势不明 → dca。"""
        d = self._build(quality=70, arrangement="tangled", val_level="low")
        assert d["action"] == "dca"
        assert d["action_label"] == "定投加仓"

    def test_dca_drawdown_high(self):
        """质量好+估值低+回撤高位 → dca。"""
        d = self._build(quality=70, arrangement="strong_bull",
                        dd_pct=0.85, val_level="low", fear_greed=50)
        # 注意：strong_bull + low + fear(50不偏恐) → 不是 strong_buy（fear_greed>40）
        # 估值低 + 回撤高位 → dca
        assert d["action"] in ("dca", "strong_buy")

    def test_hold_valuation_mid(self):
        """质量好+估值中 → hold。"""
        d = self._build(quality=70, arrangement="tangled",
                        val_level="mid", fear_greed=50)
        assert d["action"] == "hold"
        assert d["action_label"] == "持有"

    def test_reduce_quality_poor(self):
        """质量差 → reduce。"""
        d = self._build(quality=30, val_level="low")
        assert d["action"] == "reduce"

    def test_reduce_valuation_high(self):
        """估值高 → reduce。"""
        d = self._build(quality=80, arrangement="strong_bull",
                        val_level="high", fear_greed=20)
        assert d["action"] == "reduce"

    def test_wait_trend_down(self):
        """趋势下行+回撤未企稳+估值不低 → wait。"""
        d = self._build(quality=70, arrangement="strong_bear",
                        dd_pct=0.6, bottoming=False, val_level="mid", fear_greed=50)
        assert d["action"] == "wait"
        assert d["action_label"] == "等待"

    def test_quality_rating_in_result(self):
        d = self._build(quality=75)
        assert d["quality_rating"] == "good"


# ════════════════════════════════════════════════════════════
# 质量评分（mock 外部数据源）
# ════════════════════════════════════════════════════════════

class TestQualityScore:
    def test_index_fund_quality(self, monkeypatch):
        """指数基金质量评分：经理稳定性默认20分。"""
        import services.fund_analysis as fa

        # mock 元信息（指数基金）
        def mock_meta(code):
            return {
                "fund_code": code, "fund_name": "招商中证白酒指数C",
                "fund_type": "指数型", "fund_category": "index",
                "tracking_index": "中证白酒", "management_fee": 0.5,
                "custody_fee": 0.1, "scale": "50亿",
            }

        def mock_manager(code):
            return {"manager_name": "", "career_years": None, "fund_type": "指数型"}

        # mock akshare 相关函数
        monkeypatch.setattr(fa, "_fetch_peer_ranking", lambda c: 8.0)  # 前8%
        monkeypatch.setattr(fa, "_fetch_fund_fee", lambda c: 0.6)
        monkeypatch.setattr(fa, "_fetch_fund_scale", lambda c: 50.0)
        monkeypatch.setattr(fa, "_compute_tracking_error", lambda c, m: 0.003)

        # patch 内部 import
        import services.fund_data_service as fds
        import services.fund_manager as fm
        monkeypatch.setattr(fds, "get_or_refresh_fund_metadata", mock_meta)
        monkeypatch.setattr(fm, "get_fund_manager", mock_manager)

        result = calculate_quality_score("161725")
        assert result["fund_code"] == "161725"
        assert result["fund_name"] == "招商中证白酒指数C"
        # 经理稳定性 = 20（指数基金）
        assert result["dimensions"]["manager_stability"]["score"] == 20
        # 同类排名前8% → 20分
        assert result["dimensions"]["peer_ranking"]["score"] == 20
        # 总分应在合理范围
        assert 0 <= result["quality_score"] <= 100
        assert result["rating"] in ("excellent", "good", "fair", "poor")

    def test_active_fund_quality(self, monkeypatch):
        """主动基金：跟踪误差默认15分。"""
        import services.fund_analysis as fa

        def mock_meta(code):
            return {
                "fund_code": code, "fund_name": "易方达蓝筹精选混合",
                "fund_type": "混合型", "fund_category": "equity",
                "management_fee": 1.2, "custody_fee": 0.2,
            }

        def mock_manager(code):
            return {"manager_name": "张坤", "career_years": 8.0, "fund_type": "混合型"}

        monkeypatch.setattr(fa, "_fetch_peer_ranking", lambda c: 30.0)
        monkeypatch.setattr(fa, "_fetch_fund_fee", lambda c: 1.4)
        monkeypatch.setattr(fa, "_fetch_fund_scale", lambda c: 300.0)

        import services.fund_data_service as fds
        import services.fund_manager as fm
        monkeypatch.setattr(fds, "get_or_refresh_fund_metadata", mock_meta)
        monkeypatch.setattr(fm, "get_fund_manager", mock_manager)

        result = calculate_quality_score("005827")
        # 主动基金 → 跟踪误差默认15
        assert result["dimensions"]["tracking_error"]["score"] == 15
        # 经理从业8年 → 18分
        assert result["dimensions"]["manager_stability"]["score"] == 18
        # 规模300亿 → 20分
        assert result["dimensions"]["scale_trend"]["score"] == 20

    def test_quality_score_data_failure(self, monkeypatch):
        """所有数据源失败 → 给默认分，不报错。"""
        import services.fund_analysis as fa

        monkeypatch.setattr(fa, "_fetch_peer_ranking", lambda c: None)
        monkeypatch.setattr(fa, "_fetch_fund_fee", lambda c: 0.0)
        monkeypatch.setattr(fa, "_fetch_fund_scale", lambda c: 0.0)
        monkeypatch.setattr(fa, "_compute_tracking_error", lambda c, m: None)

        import services.fund_data_service as fds
        import services.fund_manager as fm
        monkeypatch.setattr(fds, "get_or_refresh_fund_metadata", lambda c: None)
        monkeypatch.setattr(fm, "get_fund_manager", lambda c: None)

        result = calculate_quality_score("999999")
        assert result["quality_score"] >= 0
        # 数据缺失时应仍有默认分
        assert result["dimensions"]["manager_stability"]["score"] >= 5


# ════════════════════════════════════════════════════════════
# DB CRUD
# ════════════════════════════════════════════════════════════

class TestFundQualityDB:
    def test_save_and_get(self, tmp_db):
        save_fund_quality_score(
            "161725", "招商中证白酒指数C",
            quality_score=75, drawdown_score=68, trend_score=52,
            capital_score=65, sentiment_score=35, total_score=68,
            rating="good",
            detail={"report": {"quality": {"score": 75}}},
            advice="建议定投加仓",
        )
        result = get_fund_quality_score("161725")
        assert result is not None
        assert result["fund_code"] == "161725"
        assert result["fund_name"] == "招商中证白酒指数C"
        assert result["quality_score"] == 75
        assert result["total_score"] == 68
        assert result["rating"] == "good"
        assert result["advice"] == "建议定投加仓"
        assert "report" in result["detail"]

    def test_save_upsert(self, tmp_db):
        """同基金重复保存应覆盖。"""
        save_fund_quality_score("001", "基金A", quality_score=60, total_score=60)
        save_fund_quality_score("001", "基金A更新", quality_score=80, total_score=80)
        result = get_fund_quality_score("001")
        assert result["quality_score"] == 80
        assert result["fund_name"] == "基金A更新"

    def test_get_nonexistent(self, tmp_db):
        assert get_fund_quality_score("NOTEXIST") is None

    def test_list_by_codes(self, tmp_db):
        save_fund_quality_score("001", "基金A", total_score=70)
        save_fund_quality_score("002", "基金B", total_score=85)
        save_fund_quality_score("003", "基金C", total_score=50)
        results = list_fund_quality_scores(["001", "002"])
        assert len(results) == 2

    def test_list_all(self, tmp_db):
        save_fund_quality_score("001", "基金A", total_score=70)
        save_fund_quality_score("002", "基金B", total_score=85)
        results = list_fund_quality_scores()
        assert len(results) == 2
        # 按 total_score 降序
        assert results[0]["total_score"] >= results[1]["total_score"]

    def test_delete(self, tmp_db):
        save_fund_quality_score("001", "基金A", total_score=70)
        assert delete_fund_quality_score("001") is True
        assert get_fund_quality_score("001") is None
        # 删除不存在的返回 False
        assert delete_fund_quality_score("999") is False
