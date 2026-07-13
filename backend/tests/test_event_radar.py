"""前瞻性事件雷达 — 服务层测试。"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from services.event_radar import _collect_news, SECTOR_TO_INDEX


def test_collect_news_returns_list():
    """_collect_news 返回新闻列表（mock MCP，其他源返回空避免真实网络调用）。"""
    fake_news = [
        {"news_title": "SpaceX 宣布 7 月 18 日星舰试飞", "news_summary": "首次尝试助推器回收",
         "news_source": "新华财经", "news_url": "http://x", "published_at": "2026-07-10"},
        {"news_title": "美联储 7 月议息会议", "news_summary": "预计讨论降息",
         "news_source": "路透", "news_url": "http://y", "published_at": "2026-07-10"},
    ]
    with patch("services.event_radar._fetch_news_from_mcp", return_value=fake_news), \
         patch("services.event_radar._fetch_news_from_akshare", return_value=[]), \
         patch("services.event_radar._fetch_news_from_eastmoney", return_value=[]):
        result = _collect_news()
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all("news_title" in n for n in result)


def test_collect_news_failure_returns_empty():
    """三源均失败时返回空列表，不抛异常。"""
    with patch("services.event_radar._fetch_news_from_mcp", side_effect=Exception("MCP down")), \
         patch("services.event_radar._fetch_news_from_akshare", return_value=[]), \
         patch("services.event_radar._fetch_news_from_eastmoney", return_value=[]):
        result = _collect_news()
    assert result == []


def test_collect_news_multi_source_dedup():
    """三源融合 + 跨源标题去重：相同标题只保留一条。"""
    mcp_news = [
        {"news_title": "央行降息", "news_summary": "降息25基点",
         "news_source": "新华财经", "news_url": "http://x", "published_at": "2026-07-10"},
    ]
    ak_news = [
        {"news_title": "央行降息", "news_summary": " duplicate from akshare",
         "news_source": "东方财富", "news_url": "http://y", "published_at": "2026-07-10"},
        {"news_title": "美联储议息", "news_summary": "讨论利率",
         "news_source": "央视新闻", "news_url": "", "published_at": "2026-07-10"},
    ]
    em_news = [
        {"news_title": "半导体板块大涨", "news_summary": "芯片需求回暖",
         "news_source": "东方财富妙想", "news_url": "", "published_at": "2026-07-10"},
    ]
    with patch("services.event_radar._fetch_news_from_mcp", return_value=mcp_news), \
         patch("services.event_radar._fetch_news_from_akshare", return_value=ak_news), \
         patch("services.event_radar._fetch_news_from_eastmoney", return_value=em_news):
        result = _collect_news()
    titles = [n["news_title"] for n in result]
    # "央行降息" 应只出现一次（跨源去重）
    assert titles.count("央行降息") == 1
    # 三条不同标题都应保留
    assert len(result) == 3
    assert set(titles) == {"央行降息", "美联储议息", "半导体板块大涨"}


def test_sector_to_index_has_all_known_sectors():
    """SECTOR_TO_INDEX 覆盖 hotspots.py 的 18 个板块。"""
    expected = {"半导体", "人工智能", "新能源", "消费", "医药", "金融", "地产", "军工",
                "教育", "体育", "传媒", "汽车", "基建", "科技", "农业", "环保", "有色", "化工"}
    assert expected.issubset(set(SECTOR_TO_INDEX.keys()))


def test_extract_events_from_news_llm_success():
    """LLM 成功返回事件列表。"""
    from services.event_radar import _extract_events_from_news

    fake_news = [
        {"news_title": "SpaceX 宣布 7 月 18 日星舰第六次试飞",
         "news_summary": "首次尝试用机械臂回收超重型助推器",
         "news_source": "新华财经", "news_url": "http://x", "published_at": "2026-07-10"},
    ]
    fake_llm_resp = MagicMock()
    fake_llm_resp.choices = [MagicMock()]
    fake_llm_resp.choices[0].message.content = '''[
        {
            "title": "SpaceX 星舰第六次试飞首次尝试助推器回收",
            "summary": "7 月 18 日星舰试飞首次尝试用机械臂回收超重型助推器",
            "event_type": "industry",
            "direction": "positive",
            "expected_date": "2026-07-18",
            "affected_sectors": ["军工"],
            "affected_themes": ["火箭回收", "商业航天"],
            "confidence": 0.95
        }
    ]'''

    with patch("services.event_radar._call_llm", return_value=fake_llm_resp):
        events = _extract_events_from_news(fake_news, trace_id="test")

    assert len(events) == 1
    assert events[0]["title"] == "SpaceX 星舰第六次试飞首次尝试助推器回收"
    assert events[0]["affected_sectors"] == ["军工"]
    assert events[0]["confidence"] == 0.95


def test_extract_events_filter_out_of_range_date():
    """过滤 expected_date 超出 [今天, 今天+14天] 的事件。"""
    from services.event_radar import _extract_events_from_news

    future_over_14 = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    in_range = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")

    fake_llm_resp = MagicMock()
    fake_llm_resp.choices = [MagicMock()]
    fake_llm_resp.choices[0].message.content = f'''[
        {{"title": "超范围事件", "summary": "", "event_type": "theme", "direction": "neutral",
          "expected_date": "{future_over_14}", "affected_sectors": [], "affected_themes": [], "confidence": 0.8}},
        {{"title": "范围内事件", "summary": "", "event_type": "theme", "direction": "neutral",
          "expected_date": "{in_range}", "affected_sectors": [], "affected_themes": [], "confidence": 0.8}}
    ]'''

    # 注意：传入非空新闻列表，否则 _extract_events_from_news 会在空列表时提前返回
    with patch("services.event_radar._call_llm", return_value=fake_llm_resp):
        events = _extract_events_from_news([{"news_title": "x"}], trace_id="test")

    titles = [e["title"] for e in events]
    assert "范围内事件" in titles
    assert "超范围事件" not in titles


def test_extract_events_low_confidence_filtered():
    """confidence < 0.4 的事件被过滤。"""
    from services.event_radar import _extract_events_from_news

    in_range = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    fake_llm_resp = MagicMock()
    fake_llm_resp.choices = [MagicMock()]
    fake_llm_resp.choices[0].message.content = f'''[
        {{"title": "低置信度", "summary": "", "event_type": "theme", "direction": "neutral",
          "expected_date": "{in_range}", "affected_sectors": [], "affected_themes": [], "confidence": 0.2}}
    ]'''

    with patch("services.event_radar._call_llm", return_value=fake_llm_resp):
        events = _extract_events_from_news([{"news_title": "x"}], trace_id="test")

    assert len(events) == 0


def test_extract_events_llm_failure_returns_empty():
    """LLM 异常时返回空列表，不抛异常。"""
    from services.event_radar import _extract_events_from_news

    with patch("services.event_radar._call_llm", side_effect=Exception("LLM down")):
        events = _extract_events_from_news([{"news_title": "x"}], trace_id="test")

    assert events == []


def test_determine_relevance_holding_impact():
    """事件命中持仓 → holding_impact。"""
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": ["军工"]}
    holdings = [{"fund_code": "161031", "fund_name": "军工ETF", "index_code": "399967"}]
    relevance, matched, matched_wl, candidates = _determine_relevance(event, holdings)
    assert relevance == "holding_impact"
    assert len(matched) == 1
    assert matched[0]["fund_code"] == "161031"
    assert candidates == []


def test_determine_relevance_opportunity():
    """未命中持仓但有候选基金 → opportunity。"""
    from services.event_radar import _determine_relevance, _find_candidate_funds

    event = {"affected_sectors": ["半导体"]}
    holdings = []  # 无持仓

    # mock _find_candidate_funds 返回候选
    fake_candidates = [
        {"fund_code": "159995", "fund_name": "芯片ETF", "match_reason": "跟踪半导体指数"}
    ]
    with patch("services.event_radar._find_candidate_funds", return_value=fake_candidates):
        relevance, matched, matched_wl, candidates = _determine_relevance(event, holdings)

    assert relevance == "opportunity"
    assert matched == []
    assert len(candidates) >= 1


def test_determine_relevance_market_watch():
    """无板块对应 → market_watch。"""
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": []}  # 宏观事件无板块
    holdings = []
    relevance, matched, matched_wl, candidates = _determine_relevance(event, holdings)
    assert relevance == "market_watch"
    assert matched == []
    assert candidates == []


def test_find_candidate_funds_excludes_holdings(tmp_db):
    """候选基金排除已持仓。"""
    from services.event_radar import _find_candidate_funds
    from db import _get_conn

    # 准备 fund_metadata 测试数据
    # 注意：tmp_db fixture 已通过 init_db() 创建 fund_metadata 表（含 13 列），
    # 此处 CREATE TABLE IF NOT EXISTS 为 no-op，INSERT 必须显式指定列名以匹配表结构
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_metadata (
            fund_code TEXT PRIMARY KEY, fund_name TEXT, fund_type TEXT,
            tracking_index TEXT
        )
    """)
    conn.execute(
        "INSERT OR REPLACE INTO fund_metadata (fund_code, fund_name, fund_type, tracking_index) VALUES (?, ?, ?, ?)",
        ("159995", "芯片ETF", "ETF", "990001"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO fund_metadata (fund_code, fund_name, fund_type, tracking_index) VALUES (?, ?, ?, ?)",
        ("161031", "军工ETF", "ETF", "399967"),
    )
    conn.commit()
    conn.close()

    # 查询半导体候选基金，排除持仓 161031
    candidates = _find_candidate_funds("990001", exclude_codes={"161031"})
    codes = [c["fund_code"] for c in candidates]
    assert "159995" in codes
    assert "161031" not in codes


def test_update_event_statuses_upcoming_to_imminent(tmp_db):
    """距 expected_date ≤ 3 天的 upcoming → imminent。"""
    from services.event_radar import _update_event_statuses
    from db.market_events import create_market_event, get_market_event

    # 2 天后发生
    exp_date = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    eid = create_market_event(
        title="即将事件", summary="", event_type="theme", direction="neutral",
        expected_date=exp_date, affected_sectors=[], affected_themes=[],
        confidence=0.7, sources=[],
    )
    _update_event_statuses()
    ev = get_market_event(eid)
    assert ev["status"] == "imminent"


def test_update_event_statuses_to_materialized(tmp_db):
    """today >= expected_date → materialized。"""
    from services.event_radar import _update_event_statuses
    from db.market_events import create_market_event, get_market_event

    # 今天就是 expected_date
    today = datetime.now().strftime("%Y-%m-%d")
    eid = create_market_event(
        title="今日事件", summary="", event_type="theme", direction="neutral",
        expected_date=today, affected_sectors=[], affected_themes=[],
        confidence=0.7, sources=[],
    )
    _update_event_statuses()
    ev = get_market_event(eid)
    assert ev["status"] == "materialized"
    assert ev["materialized_date"] == today


def test_update_event_statuses_to_expired(tmp_db):
    """today > expected_date + 7 天 → expired。"""
    from services.event_radar import _update_event_statuses
    from db.market_events import create_market_event, get_market_event

    # 10 天前发生
    exp_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    eid = create_market_event(
        title="过期事件", summary="", event_type="theme", direction="neutral",
        expected_date=exp_date, affected_sectors=[], affected_themes=[],
        confidence=0.7, sources=[],
    )
    _update_event_statuses()
    ev = get_market_event(eid)
    assert ev["status"] == "expired"
    assert ev["expired_date"] is not None


def test_scan_forward_events_integration(tmp_db):
    """scan_forward_events 端到端：新闻→LLM→写表→状态流转→alert 生成。"""
    from services.event_radar import scan_forward_events
    from db.market_events import list_market_events
    from db.config import update_config

    # 默认配置 alerts.event_radar_enabled=false，需手动开启才能进入扫描主流程
    update_config("alerts.event_radar_enabled", "true")

    fake_news = [
        {"news_title": "SpaceX 7 月 18 日星舰试飞", "news_summary": "回收测试",
         "news_source": "x", "news_url": "http://x", "published_at": "2026-07-10"},
    ]
    in_range = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    fake_llm_resp = MagicMock()
    fake_llm_resp.choices = [MagicMock()]
    fake_llm_resp.choices[0].message.content = f'''[{{
        "title": "星舰试飞回收测试",
        "summary": "回收测试",
        "event_type": "industry",
        "direction": "positive",
        "expected_date": "{in_range}",
        "affected_sectors": ["军工"],
        "affected_themes": ["火箭回收"],
        "confidence": 0.9
    }}]'''

    with patch("services.event_radar._collect_news", return_value=fake_news), \
         patch("services.event_radar._call_llm", return_value=fake_llm_resp), \
         patch("services.event_radar._determine_relevance",
               return_value=("market_watch", [], [])), \
         patch("db.portfolio.list_holdings", return_value=[]):
        result = scan_forward_events(trace_id="test")

    assert "extracted" in result
    assert "new" in result
    assert "alerts_created" in result
    assert result["extracted"] == 1
    # 验证事件已写表
    events = list_market_events()
    assert any(e["title"] == "星舰试飞回收测试" for e in events)


def test_scan_forward_events_disabled():
    """开关关闭时返回 skipped。"""
    from services.event_radar import scan_forward_events

    with patch("services.event_radar.get_config", return_value="false"):
        result = scan_forward_events()
    assert result.get("skipped") == "disabled"


# ── P1 优化：板块同义词 + 持仓 index_name 匹配 + 候选基金兜底 ──────

def test_normalize_sector_handles_synonyms():
    """_normalize_sector 把 LLM 常见的板块别名归一化到 SECTOR_TO_INDEX 的 key。"""
    from services.event_radar import _normalize_sector, SECTOR_TO_INDEX

    # 国防军工 → 军工
    assert _normalize_sector("国防军工") == "军工"
    # 生物医药 → 医药
    assert _normalize_sector("生物医药") == "医药"
    # 已在表中的板块名不变
    assert _normalize_sector("半导体") == "半导体"
    # 未知板块原样返回
    assert _normalize_sector("未知板块") == "未知板块"
    # 归一化结果必须在 SECTOR_TO_INDEX 中（除了未知板块）
    for alias in ["国防军工", "生物医药", "医疗", "航天航空", "新能源车"]:
        normalized = _normalize_sector(alias)
        if normalized != alias:
            assert normalized in SECTOR_TO_INDEX, f"{alias} 归一化到 {normalized} 但不在 SECTOR_TO_INDEX"


def test_determine_relevance_matches_holding_by_index_name():
    """持仓 index_name 含板块关键词时也能命中 holding_impact。

    场景：用户持仓 index_name='中证银行指数'，事件板块='金融'。
    银行属于金融板块，应通过 index_name 关键词匹配命中。
    """
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": ["金融"]}
    holdings = [
        {"fund_code": "009860", "fund_name": "易方达中证银行ETF联接C",
         "index_code": "399986.SZ", "index_name": "中证银行指数"}
    ]
    relevance, matched, matched_wl, candidates = _determine_relevance(event, holdings)
    assert relevance == "holding_impact"
    assert len(matched) >= 1
    assert matched[0]["fund_code"] == "009860"


def test_determine_relevance_sector_synonym_matching():
    """LLM 提取的板块别名（国防军工）能匹配到持仓。"""
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": ["国防军工"]}  # 别名，SECTOR_TO_INDEX 里是"军工"
    holdings = [
        {"fund_code": "161031", "fund_name": "军工ETF", "index_code": "399967"}
    ]
    relevance, matched, matched_wl, candidates = _determine_relevance(event, holdings)
    assert relevance == "holding_impact"
    assert len(matched) == 1


def test_determine_relevance_watchlist_impact():
    """事件命中关注列表（但非持仓）→ watchlist_impact。"""
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": ["军工"]}
    holdings = []  # 无持仓
    watchlist = [{"fund_code": "161031", "fund_name": "军工ETF", "index_code": "399967"}]
    relevance, matched, matched_wl, candidates = _determine_relevance(
        event, holdings, watchlist
    )
    assert relevance == "watchlist_impact"
    assert matched == []
    assert len(matched_wl) == 1
    assert matched_wl[0]["fund_code"] == "161031"


def test_determine_relevance_holding_takes_priority_over_watchlist():
    """事件同时命中持仓和关注列表 → 优先 holding_impact。"""
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": ["军工"]}
    holdings = [{"fund_code": "512660", "fund_name": "军工ETF场内", "index_code": "399967"}]
    watchlist = [{"fund_code": "161031", "fund_name": "军工ETF", "index_code": "399967"}]
    relevance, matched, matched_wl, candidates = _determine_relevance(
        event, holdings, watchlist
    )
    assert relevance == "holding_impact"
    assert len(matched) == 1
    assert matched[0]["fund_code"] == "512660"


def test_find_candidate_funds_fallback_to_builtin_map(tmp_db):
    """fund_metadata 表为空时，回退到 index_fund_mapper 内置映射表。"""
    from services.event_radar import _find_candidate_funds
    from db import _get_conn

    # 确认 fund_metadata 表存在但为空
    conn = _get_conn()
    cnt = conn.execute("SELECT COUNT(*) FROM fund_metadata").fetchone()[0]
    conn.close()
    assert cnt == 0, "前置条件：fund_metadata 应为空"

    # 查询白酒指数 399997 的候选基金（内置映射表有 161725）
    candidates = _find_candidate_funds("399997")
    codes = [c["fund_code"] for c in candidates]
    # 内置映射表 _INDEX_FUND_MAP["399997"] = 161725 招商中证白酒指数A
    assert len(candidates) >= 1, f"内置映射兜底应返回候选基金，实际: {candidates}"
    assert "161725" in codes, f"应包含白酒指数基金 161725，实际: {codes}"


# ── 事件落地验证 ──────────────────────────────────────


def test_verify_single_event_positive_correct():
    """正向事件 + 指数上涨>1% → correct。"""
    from services.event_radar import _verify_single_event

    # 落地日取 30 天前，确保验证窗口（落地日+7天）已过
    mat_dt = datetime.now() - timedelta(days=30)
    mat_date = mat_dt.strftime("%Y-%m-%d")
    verify_date = (mat_dt + timedelta(days=7)).strftime("%Y-%m-%d")

    event = {
        "affected_sectors": '["半导体"]',
        "materialized_date": mat_date,
        "direction": "positive",
    }
    prices = {mat_date: 100.0, verify_date: 102.0}

    with patch("services.event_radar._fetch_index_close_prices", return_value=prices):
        result = _verify_single_event(event, window_days=3)

    assert result is not None
    assert result["status"] == "correct"
    assert result["change_pct"] == 2.0
    assert result["index_code"] == "990001"
    assert result["index_name"] == "半导体"
    assert result["direction_predicted"] == "positive"


def test_verify_single_event_positive_wrong():
    """正向事件 + 指数下跌>1% → wrong。"""
    from services.event_radar import _verify_single_event

    mat_dt = datetime.now() - timedelta(days=30)
    mat_date = mat_dt.strftime("%Y-%m-%d")
    verify_date = (mat_dt + timedelta(days=7)).strftime("%Y-%m-%d")

    event = {
        "affected_sectors": '["半导体"]',
        "materialized_date": mat_date,
        "direction": "positive",
    }
    prices = {mat_date: 100.0, verify_date: 98.0}

    with patch("services.event_radar._fetch_index_close_prices", return_value=prices):
        result = _verify_single_event(event, window_days=3)

    assert result is not None
    assert result["status"] == "wrong"
    assert result["change_pct"] == -2.0


def test_verify_single_event_flat():
    """指数涨跌幅<1% → flat。"""
    from services.event_radar import _verify_single_event

    mat_dt = datetime.now() - timedelta(days=30)
    mat_date = mat_dt.strftime("%Y-%m-%d")
    verify_date = (mat_dt + timedelta(days=7)).strftime("%Y-%m-%d")

    event = {
        "affected_sectors": '["半导体"]',
        "materialized_date": mat_date,
        "direction": "positive",
    }
    prices = {mat_date: 100.0, verify_date: 100.5}

    with patch("services.event_radar._fetch_index_close_prices", return_value=prices):
        result = _verify_single_event(event, window_days=3)

    assert result is not None
    assert result["status"] == "flat"
    assert result["change_pct"] == 0.5


def test_verify_single_event_negative_correct():
    """负向事件 + 指数下跌>1% → correct。"""
    from services.event_radar import _verify_single_event

    mat_dt = datetime.now() - timedelta(days=30)
    mat_date = mat_dt.strftime("%Y-%m-%d")
    verify_date = (mat_dt + timedelta(days=7)).strftime("%Y-%m-%d")

    event = {
        "affected_sectors": '["半导体"]',
        "materialized_date": mat_date,
        "direction": "negative",
    }
    prices = {mat_date: 100.0, verify_date: 98.0}

    with patch("services.event_radar._fetch_index_close_prices", return_value=prices):
        result = _verify_single_event(event, window_days=3)

    assert result is not None
    assert result["status"] == "correct"
    assert result["change_pct"] == -2.0
    assert result["direction_predicted"] == "negative"


def test_verify_single_event_no_price_data():
    """akshare 返回空数据 → None（无法验证）。"""
    from services.event_radar import _verify_single_event

    mat_dt = datetime.now() - timedelta(days=30)
    mat_date = mat_dt.strftime("%Y-%m-%d")

    event = {
        "affected_sectors": '["半导体"]',
        "materialized_date": mat_date,
        "direction": "positive",
    }

    with patch("services.event_radar._fetch_index_close_prices", return_value={}):
        result = _verify_single_event(event, window_days=3)

    assert result is None


def test_calibrate_confidence_with_enough_samples():
    """样本充足时按板块准确率校准：0.9 * 0.8 = 0.72。"""
    from services.event_radar import _calibrate_confidence

    fake_stats = {
        "overall": {"total": 10, "correct": 8, "wrong": 2, "flat": 0, "accuracy": 0.8},
        "by_sector": {
            "半导体": {"total": 10, "correct": 8, "wrong": 2, "flat": 0, "accuracy": 0.8}
        },
    }
    with patch("services.event_radar.get_sector_accuracy_stats", return_value=fake_stats):
        result = _calibrate_confidence(0.9, ["半导体"])

    assert result == pytest.approx(0.72)


def test_calibrate_confidence_insufficient_samples():
    """板块样本<3 时不校准，返回原始置信度。"""
    from services.event_radar import _calibrate_confidence

    fake_stats = {
        "overall": {"total": 2, "correct": 2, "wrong": 0, "flat": 0, "accuracy": 1.0},
        "by_sector": {
            "半导体": {"total": 2, "correct": 2, "wrong": 0, "flat": 0, "accuracy": 1.0}
        },
    }
    with patch("services.event_radar.get_sector_accuracy_stats", return_value=fake_stats):
        result = _calibrate_confidence(0.9, ["半导体"])

    assert result == 0.9


def test_calibrate_confidence_floor():
    """校准后置信度不低于 0.1（下限保护）。"""
    from services.event_radar import _calibrate_confidence

    # 准确率 0.1（样本充足），0.9 * 0.1 = 0.09 < 0.1 → 触发下限保护
    fake_stats = {
        "overall": {"total": 10, "correct": 1, "wrong": 9, "flat": 0, "accuracy": 0.1},
        "by_sector": {
            "半导体": {"total": 10, "correct": 1, "wrong": 9, "flat": 0, "accuracy": 0.1}
        },
    }
    with patch("services.event_radar.get_sector_accuracy_stats", return_value=fake_stats):
        result = _calibrate_confidence(0.9, ["半导体"])

    assert result == pytest.approx(0.1)


def test_get_sector_accuracy_stats_with_records():
    """有验证记录时统计正确（accuracy = correct/(correct+wrong)，flat 不计入）。"""
    from services.event_radar import get_sector_accuracy_stats

    verified_events = [
        {"affected_sectors": '["半导体"]', "verification_result": '{"status": "correct"}'},
        {"affected_sectors": '["半导体"]', "verification_result": '{"status": "wrong"}'},
        {"affected_sectors": '["半导体"]', "verification_result": '{"status": "flat"}'},
        {"affected_sectors": '["军工"]', "verification_result": '{"status": "correct"}'},
    ]

    with patch("db.market_events.list_verified_events", return_value=verified_events):
        stats = get_sector_accuracy_stats()

    # overall: total=4, correct=2, wrong=1, flat=1
    # accuracy = 2/(2+1) = 0.67
    assert stats["overall"]["total"] == 4
    assert stats["overall"]["correct"] == 2
    assert stats["overall"]["wrong"] == 1
    assert stats["overall"]["flat"] == 1
    assert stats["overall"]["accuracy"] == round(2 / 3, 2)

    # 半导体: total=3, correct=1, wrong=1, flat=1
    # accuracy = 1/(1+1) = 0.5
    assert stats["by_sector"]["半导体"]["total"] == 3
    assert stats["by_sector"]["半导体"]["correct"] == 1
    assert stats["by_sector"]["半导体"]["wrong"] == 1
    assert stats["by_sector"]["半导体"]["accuracy"] == 0.5

    # 军工: total=1, correct=1, wrong=0
    # accuracy = 1/1 = 1.0
    assert stats["by_sector"]["军工"]["total"] == 1
    assert stats["by_sector"]["军工"]["accuracy"] == 1.0


def test_get_sector_accuracy_stats_empty():
    """无验证记录时返回空结果。"""
    from services.event_radar import get_sector_accuracy_stats

    with patch("db.market_events.list_verified_events", return_value=[]):
        stats = get_sector_accuracy_stats()

    assert stats["overall"]["total"] == 0
    assert stats["overall"]["correct"] == 0
    assert stats["overall"]["wrong"] == 0
    assert stats["overall"]["flat"] == 0
    assert stats["overall"]["accuracy"] == 0.0
    assert stats["by_sector"] == {}


def test_verify_materialized_events_batch():
    """批量验证已落地事件：统计 verified/correct/wrong 计数并生成 alert。"""
    from services.event_radar import verify_materialized_events

    pending = [
        {"event_id": "ev1", "title": "事件A", "affected_sectors": '["半导体"]',
         "materialized_date": "2026-06-01", "direction": "positive"},
        {"event_id": "ev2", "title": "事件B", "affected_sectors": '["军工"]',
         "materialized_date": "2026-06-01", "direction": "negative"},
    ]

    verify_results = {
        "ev1": {"status": "correct", "change_pct": 2.0, "verified_date": "2026-07-13",
                "index_code": "990001", "index_name": "半导体",
                "direction_predicted": "positive", "window_days": 3,
                "base_price": 100.0, "verify_price": 102.0},
        "ev2": {"status": "wrong", "change_pct": 1.5, "verified_date": "2026-07-13",
                "index_code": "399967", "index_name": "军工",
                "direction_predicted": "negative", "window_days": 3,
                "base_price": 100.0, "verify_price": 101.5},
    }

    def fake_verify(ev, window_days=3):
        return verify_results.get(ev["event_id"])

    with patch("db.market_events.list_pending_verification_events", return_value=pending), \
         patch("services.event_radar._verify_single_event", side_effect=fake_verify), \
         patch("db.market_events.update_event_verification", return_value=True), \
         patch("db.portfolio.create_alert", return_value=1):
        result = verify_materialized_events(trace_id="test")

    assert result["verified"] == 2
    assert result["correct"] == 1
    assert result["wrong"] == 1
    assert result["flat"] == 0
    assert result["alerts_created"] == 2
