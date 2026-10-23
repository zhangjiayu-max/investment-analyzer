"""主题聚合分析单测 —— 主题归类 / HHI 口径 / 碎片字段 / 保留建议。

覆盖 2026-10-22 主题集中度页面优化引入的口径：
  - 盈亏之外的数据正确性（配色属前端职责）
  - `_fund_brief` 必须带 theme（碎片表主题列）
  - HHI 阈值前后端共用（HHI_MODERATE / HHI_HIGH），并输出 hhi_level
  - 防守盘（债券 / 固收+）不参与权益集中度
  - 重复持仓给出 keep_hint 行动指引
"""
from services.portfolio.theme_analysis import (
    DEFENSIVE_THEMES,
    FRAGMENT_THRESHOLD,
    HHI_HIGH,
    HHI_MODERATE,
    _fund_brief,
    _match_theme,
    analyze_theme_concentration,
)


def _h(code, name, category, value, cost=None, pnl=0.0, account="默认账户",
       index_code="", index_name=""):
    return {
        "fund_code": code,
        "fund_name": name,
        "fund_category": category,
        "account": account,
        "index_code": index_code,
        "index_name": index_name,
        "current_value": value,
        "total_cost": value if cost is None else cost,
        "profit_loss": pnl,
    }


def _patch_db(monkeypatch, holdings, cash=0.0):
    import db
    monkeypatch.setattr(db, "list_holdings", lambda user_id=None: list(holdings))
    monkeypatch.setattr(db, "get_cash_balance", lambda user_id=None: {"balance": cash})


# ── 主题归类 ──────────────────────────────────────────────


class TestMatchTheme:
    def test_bond_category_wins(self):
        assert _match_theme(_h("1", "XX纯债A", "bond_pure", 100)) == "债券"

    def test_white_liquor_maps_to_consumption(self):
        assert _match_theme(_h("2", "招商中证白酒指数(LOF)A", "index", 100)) == "大消费"

    def test_hstech_beats_generic_tech(self):
        # 「恒生科技」必须命中港股科技互联网，不能被宽泛的「科技」截走
        assert _match_theme(_h("3", "易方达恒生科技ETF联接A", "index", 100)) == "港股科技互联网"

    def test_medical_device_maps_to_medical(self):
        assert _match_theme(_h("4", "永赢中证全指医疗器械ETF发起联接A", "index", 100)) == "医疗医药"

    def test_hybrid_named_return_maps_to_fixed_income_plus(self):
        assert _match_theme(_h("5", "广发价值回报混合C", "hybrid", 100)) == "固收+"

    def test_broad_index(self):
        assert _match_theme(_h("6", "易方达沪深300ETF联接A", "index", 100)) == "宽基"

    def test_fallback_other(self):
        assert _match_theme(_h("7", "某只无关键词基金", "hybrid", 100)) == "其他"

    def test_gold(self):
        assert _match_theme(_h("8", "国泰黄金ETF联接C", "index", 100)) == "黄金/商品"


# ── 基金简表 ──────────────────────────────────────────────


class TestFundBrief:
    def test_includes_theme_field(self):
        h = _h("000001", "测试基金", "index", 100)
        h["_theme"] = "宽基"
        brief = _fund_brief(h)
        assert brief["theme"] == "宽基"

    def test_theme_defaults_empty(self):
        assert _fund_brief(_h("000001", "测试基金", "index", 100))["theme"] == ""


# ── 聚合分析 ──────────────────────────────────────────────


def _sample_holdings():
    return [
        # 跨账户重复：白酒（花无缺 3 万 > 小鱼儿 2.4 万）
        _h("161725", "招商中证白酒指数(LOF)A", "index", 30000, pnl=-3000,
           account="花无缺", index_code="399997.SZ", index_name="中证白酒"),
        _h("161725", "招商中证白酒指数(LOF)A", "index", 24157, pnl=-2000,
           account="小鱼儿", index_code="399997.SZ", index_name="中证白酒"),
        # 同指数重复：恒生科技两只（易方达 1.5 万 > 汇添富 1 万）
        _h("013126", "易方达恒生科技ETF联接A", "index", 15000, pnl=-1500,
           index_code="HSTECH", index_name="恒生科技指数"),
        _h("013127", "汇添富恒生科技ETF联接发起式C", "index", 10000, pnl=-800,
           index_code="HSTECH", index_name="恒生科技指数"),
        # 防守盘
        _h("014846", "博时恒乐债券A", "bond", 50000, pnl=500),
        _h("004333", "广发价值回报混合C", "hybrid", 40000, pnl=1000),
        # 碎片仓位（< 1 万）
        _h("014674", "富国中证港股通互联网ETF发起式联接C", "index", 8000, pnl=-400,
           index_code="931638.CSI", index_name="中证港股通互联网指数"),
        _h("012725", "国泰中证畜牧养殖ETF联接C", "index", 5000, pnl=-100,
           index_code="930707.CSI", index_name="中证畜牧养殖"),
    ]


class TestAnalyze:
    def test_hhi_level_and_thresholds_share_one_policy(self, monkeypatch):
        _patch_db(monkeypatch, _sample_holdings())
        s = analyze_theme_concentration()["summary"]
        assert s["hhi_thresholds"] == {"moderate": HHI_MODERATE, "high": HHI_HIGH}
        hhi = s["hhi"]
        expected = "high" if hhi >= HHI_HIGH else ("moderate" if hhi >= HHI_MODERATE else "low")
        assert s["hhi_level"] == expected

    def test_equity_pnl_excludes_defensive_themes(self, monkeypatch):
        _patch_db(monkeypatch, _sample_holdings())
        d = analyze_theme_concentration()
        s = d["summary"]
        equity_pnl = sum(t["pnl"] for t in d["themes"] if not t["is_defensive"])
        assert s["equity_pnl"] == round(equity_pnl, 2)
        # 防守盘不参与权益口径（债券 +500、固收+ +1000 不能混进来）
        defensive_pnl = sum(t["pnl"] for t in d["themes"] if t["is_defensive"])
        assert abs(s["equity_pnl"] - (s["total_pnl"] - defensive_pnl)) < 0.01

    def test_defensive_themes_excluded_from_hhi(self, monkeypatch):
        _patch_db(monkeypatch, _sample_holdings())
        d = analyze_theme_concentration()
        assert all(t["theme"] in DEFENSIVE_THEMES for t in d["themes"] if t["is_defensive"])
        assert d["summary"]["theme_count"] == len(
            [t for t in d["themes"] if not t["is_defensive"]]
        )

    def test_fragments_carry_theme_and_dynamic_impact(self, monkeypatch):
        _patch_db(monkeypatch, _sample_holdings(), cash=20000)
        d = analyze_theme_concentration()
        s = d["summary"]
        assert s["fragment_threshold"] == FRAGMENT_THRESHOLD
        assert d["fragments"], "应识别出碎片仓位"
        assert all(f["theme"] for f in d["fragments"]), "碎片必须带主题列"
        total_assets = s["total_value"] + s["cash_balance"]
        max_frag = max(f["value"] for f in d["fragments"])
        assert abs(s["fragment_max_impact_pct"] - round(max_frag / total_assets * 0.2 * 100, 3)) < 0.01

    def test_cross_account_keep_hint_prefers_larger_position(self, monkeypatch):
        _patch_db(monkeypatch, _sample_holdings())
        dups = analyze_theme_concentration()["duplicates"]["cross_account"]
        assert len(dups) == 1
        hint = dups[0]["keep_hint"]
        assert "花无缺" in hint  # 3 万 > 2.4 万
        assert "小鱼儿" not in hint

    def test_same_index_keep_hint_prefers_larger_fund(self, monkeypatch):
        _patch_db(monkeypatch, _sample_holdings())
        dups = analyze_theme_concentration()["duplicates"]["same_index"]
        assert len(dups) == 1
        hint = dups[0]["keep_hint"]
        assert "易方达恒生科技" in hint  # 1.5 万 > 1 万
        assert "费率" in hint

    def test_advice_uses_shared_hhi_levels(self, monkeypatch):
        _patch_db(monkeypatch, _sample_holdings())
        advice = analyze_theme_concentration()["advice"]
        # 样本 HHI 高度集中（白酒独大），advice 必须按同一阈值给出「高度集中」
        assert any("高度集中" in a for a in advice)
