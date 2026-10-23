"""fetch-recent 宽基估值的百分位单位契约，以及脏数据修复脚本的边界。

背景：get_index_valuation().pe_percentile 是 0-1 比例，曾被直接当百分制写入
index_valuations，导致「47% 分位」存成 0.47，宽基分位整体缩小 100 倍。
"""
from datetime import date

import pytest

from db import valuations as v


def _fake_valuation(pe=None, pct=None, name="沪深300", day=None):
    return {
        "index_name": name,
        "pe": pe,
        "pe_percentile": pct,
        "date": day or date.today().isoformat(),
    }


def test_fetch_recent_stores_percent_points(monkeypatch):
    import services.market_data as market

    monkeypatch.setattr(market, "get_index_valuation",
                        lambda code: _fake_valuation(pe=14.49, pct=0.4697))
    result = v.fetch_recent_wide_base_valuations([("000300", "沪深300")])

    assert result["fetched"] == 1 and result["errors"] == []
    row = v.get_latest_valuation("000300", "市盈率")
    assert row["percentile"] == pytest.approx(46.97, abs=1e-4)
    assert row["percentile_unit"] == "percent"
    assert row["percentile_input_unit"] == "ratio"
    assert row["percentile_raw"] == pytest.approx(0.4697)


def test_fetch_recent_keeps_true_sub_one_percent(monkeypatch):
    """真实 0.31% 分位经 ratio 换算后仍是 0.31，不会被放大成 31。"""
    import services.market_data as market

    monkeypatch.setattr(market, "get_index_valuation",
                        lambda code: _fake_valuation(pe=1.33, pct=0.0031, name="恒生科技"))
    v.fetch_recent_wide_base_valuations([("HSTECH", "恒生科技")])

    assert v.get_latest_valuation("HSTECH", "市盈率")["percentile"] == pytest.approx(0.31)


@pytest.mark.parametrize("bad", [1.5, 46.97, -0.1, "0.47", None, True])
def test_fetch_recent_rejects_values_outside_ratio_contract(monkeypatch, bad):
    """越界不再写入：契约是 0-1，越界说明源端改了口径，宁可跳过。"""
    import services.market_data as market

    monkeypatch.setattr(market, "get_index_valuation",
                        lambda code: _fake_valuation(pe=14.49, pct=bad))
    result = v.fetch_recent_wide_base_valuations([("000300", "沪深300")])

    assert result["fetched"] == 0
    assert v.get_latest_valuation("000300", "市盈率") is None
    if bad is not None:
        assert result["errors"], f"{bad!r} 应被跳过并记录"


def test_fetch_recent_isolates_single_index_failure(monkeypatch):
    import services.market_data as market

    def _boom(code):
        if code == "000905":
            raise RuntimeError("source down")
        return _fake_valuation(pe=14.49, pct=0.5)

    monkeypatch.setattr(market, "get_index_valuation", _boom)
    result = v.fetch_recent_wide_base_valuations([("000300", "沪深300"), ("000905", "中证500")])

    assert result["fetched"] == 1
    assert any("中证500" in e for e in result["errors"])


def _seed_history(code, metric, values, source_image=None, history_window=None):
    for idx, pct in enumerate(values):
        v.save_valuation({
            "index_code": code,
            "index_name": code,
            "metric_type": metric,
            "current_value": 15.0,
            "percentile": pct,
            "history_window": history_window,
        }, source_image=source_image,
            snapshot_date=f"2026-09-{idx + 1:02d}")


def test_repair_script_only_touches_sourceless_calculated_rows(tmp_db):
    from db._conn import _get_conn
    from scripts.fix_fetch_recent_percentile_ratio import find_candidates

    # 同组历史是百分制，且这批是无来源的计算值 → 应修复
    _seed_history("000300", "市盈率", [51.38, 51.45, 57.0, 58.2, 60.1])
    v.save_valuation({"index_code": "000300", "index_name": "沪深300", "metric_type": "市盈率",
                      "current_value": 14.49, "percentile": 0.4697},
                     snapshot_date="2026-10-22")

    # 图片解析入库：带 source_image 与历史窗口 → 即使值小于 1 也不动
    _seed_history("HSTECH", "市销率", [11.54, 13.1, 21.97, 31.0, 7.26],
                  source_image="HSTECH_市销率.png", history_window="10Y")
    v.save_valuation({"index_code": "HSTECH", "index_name": "恒生科技", "metric_type": "市销率",
                      "current_value": 1.33, "percentile": 0.31, "history_window": "10Y"},
                     source_image="HSTECH_市销率.png", snapshot_date="2026-10-22")

    conn = _get_conn()
    try:
        fixable, skipped = find_candidates(conn)
    finally:
        conn.close()

    assert [item["index_code"] for item in fixable] == ["000300"]
    assert fixable[0]["before"] == pytest.approx(0.4697)
    assert fixable[0]["after"] == pytest.approx(46.97)
    assert [item["index_code"] for item in skipped] == ["HSTECH"]


def test_repair_apply_writes_value_and_contract(tmp_db, tmp_path):
    from db._conn import _get_conn
    from scripts.fix_fetch_recent_percentile_ratio import apply_fixes, find_candidates

    _seed_history("000300", "市盈率", [51.38, 51.45, 57.0, 58.2, 60.1])
    v.save_valuation({"index_code": "000300", "index_name": "沪深300", "metric_type": "市盈率",
                      "current_value": 14.49, "percentile": 0.4697},
                     snapshot_date="2026-10-22")

    conn = _get_conn()
    try:
        fixable, _ = find_candidates(conn)
        backup = tmp_path / "backup.db"
        apply_fixes(conn, fixable, backup)
    finally:
        conn.close()

    assert backup.exists()
    row = v.get_latest_valuation("000300", "市盈率")
    assert row["percentile"] == pytest.approx(46.97, abs=1e-4)
    assert row["percentile_input_unit"] == "ratio"
    assert row["percentile_raw"] == pytest.approx(0.4697)
    # 修复后不再被识别为脏数据（幂等）
    conn = _get_conn()
    try:
        assert find_candidates(conn)[0] == []
    finally:
        conn.close()
