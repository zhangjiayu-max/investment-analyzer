"""前瞻性事件雷达 — 服务层测试。"""
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from services.event_radar import _collect_news, SECTOR_TO_INDEX


def test_collect_news_returns_list():
    """_collect_news 返回新闻列表（mock MCP）。"""
    fake_news = [
        {"news_title": "SpaceX 宣布 7 月 18 日星舰试飞", "news_summary": "首次尝试助推器回收",
         "news_source": "新华财经", "news_url": "http://x", "published_at": "2026-07-10"},
        {"news_title": "美联储 7 月议息会议", "news_summary": "预计讨论降息",
         "news_source": "路透", "news_url": "http://y", "published_at": "2026-07-10"},
    ]
    with patch("services.event_radar._fetch_news_from_mcp", return_value=fake_news):
        result = _collect_news()
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all("news_title" in n for n in result)


def test_collect_news_failure_returns_empty():
    """MCP 失败时返回空列表，不抛异常。"""
    with patch("services.event_radar._fetch_news_from_mcp", side_effect=Exception("MCP down")):
        result = _collect_news()
    assert result == []


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
    relevance, matched, candidates = _determine_relevance(event, holdings)
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
        relevance, matched, candidates = _determine_relevance(event, holdings)

    assert relevance == "opportunity"
    assert matched == []
    assert len(candidates) >= 1


def test_determine_relevance_market_watch():
    """无板块对应 → market_watch。"""
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": []}  # 宏观事件无板块
    holdings = []
    relevance, matched, candidates = _determine_relevance(event, holdings)
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
