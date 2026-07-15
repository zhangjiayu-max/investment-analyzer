"""前瞻性事件雷达 — 从每日新闻提取未来 1-2 周市场事件。

调度：每晚 20:00（app.py 的 _auto_event_radar_scan）
流程：新闻采集 → LLM 提取 → 去重写表 → 状态流转 → 板块匹配 → 3 级 alert
设计稿：doc/plans/2026-07-10-forward-looking-event-radar.md
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from db._conn import _get_conn
from db.config import get_config, get_config_bool, get_config_int, get_config_float, get_config_list
# 模块级导入 _call_llm / MODEL：使 patch("services.event_radar._call_llm") 生效
# （局部 import 会在调用时重新绑定，导致 mock 失效）
from services.llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)


# ── 板块 → 跟踪指数映射 ──────────────────────────────
# 参考 hotspots.py 的 sector_keywords，覆盖 18 个板块。
# 指数代码需对照 fund_metadata.tracking_index 实际数据校准。
SECTOR_TO_INDEX = {
    "半导体": ["990001", "H30184"],
    "人工智能": ["930713", "931071"],
    "新能源": ["399808", "931151"],
    "消费": ["000932", "399932"],
    "医药": ["930791", "000993"],
    "金融": ["399949", "930601"],
    "地产": ["931775", "399393"],
    "军工": ["399967", "930798"],
    "教育": ["930711"],
    "体育": ["930711"],
    "传媒": ["930681", "930901"],
    "汽车": ["930758", "399975"],
    "基建": ["399388", "930608"],
    "科技": ["931087", "930986"],
    "农业": ["930687", "000936"],
    "环保": ["930790", "930615"],
    "有色": ["930708", "399395"],
    "化工": ["930695", "930751"],
}

# 板块别名 → SECTOR_TO_INDEX key（LLM 常见同义词归一化）
# 解决问题：LLM 提取"国防军工"但表里是"军工"导致匹配失败
_SECTOR_ALIASES = {
    "国防军工": "军工", "航天": "军工", "航天航空": "军工", "商业航天": "军工",
    "生物医药": "医药", "医疗": "医药", "医疗器械": "医药", "医药生物": "医药",
    "银行": "金融", "证券": "金融", "保险": "金融",
    "房地产": "地产",
    "新能源汽车": "新能源", "新能源车": "新能源", "光伏": "新能源", "锂电": "新能源",
    "食品饮料": "消费", "白酒": "消费", "零售": "消费",
    "电子": "半导体", "芯片": "半导体",
    "互联网": "科技", "软件": "科技", "计算机": "科技",
    "煤炭": "有色", "钢铁": "有色", "黄金": "有色",
    "国防": "军工",
}

# 板块 → 持仓 index_name 关键词（用于持仓指数名模糊匹配）
# 场景：用户持仓 index_name="中证银行指数"，事件板块="金融"
# 银行属于金融板块，通过关键词"银行"命中
_SECTOR_TO_NAME_KEYWORDS = {
    "半导体": ["半导体", "芯片", "集成电路", "存储"],
    "人工智能": ["人工智能", "AI", "机器人", "智能"],
    "新能源": ["新能源", "光伏", "锂电", "碳中和", "电池"],
    "消费": ["消费", "白酒", "食品", "零售", "畜牧"],
    "医药": ["医药", "医疗", "生物", "健康"],
    "金融": ["银行", "证券", "保险", "金融"],
    "地产": ["地产", "房地产", "REIT"],
    "军工": ["军工", "国防", "航天", "航空"],
    "教育": ["教育"],
    "体育": ["体育"],
    "传媒": ["传媒", "媒体", "影视", "游戏"],
    "汽车": ["汽车", "新能源车", "智能驾驶"],
    "基建": ["基建", "建筑", "建材"],
    "科技": ["科技", "互联网", "软件", "计算机", "信息"],
    "农业": ["农业", "养殖", "种植"],
    "环保": ["环保", "环境", "新能源"],
    "有色": ["有色", "煤炭", "钢铁", "黄金", "金属"],
    "化工": ["化工", "化学", "材料"],
}


def _normalize_sector(sector: str) -> str:
    """板块名归一化：把 LLM 常见别名映射到 SECTOR_TO_INDEX 的标准 key。

    Args:
        sector: LLM 提取的板块名（可能是"国防军工"等别名）

    Returns:
        归一化后的板块名（在 SECTOR_TO_INDEX 中则返回对应 key，否则原样返回）
    """
    if not sector:
        return ""
    sector = sector.strip()
    # 已是标准 key，直接返回
    if sector in SECTOR_TO_INDEX:
        return sector
    # 查别名表
    return _SECTOR_ALIASES.get(sector, sector)


def _fetch_news_from_mcp(keyword: str = "", limit: int = 50) -> list[dict]:
    """从盈米 MCP 检索财经新闻（复用 alert_news_service 的调用模式）。

    Args:
        keyword: 检索关键词（空则检索综合财经新闻）
        limit: 最多返回条数

    Returns:
        [{news_title, news_summary, news_source, news_url, published_at}, ...]
        失败返回空列表。
    """
    try:
        from services.alert_news_service import _fetch_news_from_mcp as _mcp_fetch
        # 复用现有 MCP 检索实现（已封装好响应解析 + 异常吞掉）
        kw = keyword or "财经"
        return _mcp_fetch(kw, limit=limit)
    except Exception as e:
        logger.warning(f"[event_radar] MCP 新闻检索失败: {e}")
        return []


def _call_akshare_with_timeout(fn, timeout: int = 15, **kwargs):
    """带超时调用 akshare 函数（akshare 偶发卡死，需超时保护）。

    Args:
        fn: akshare 函数（如 ak.stock_news_em）
        timeout: 超时秒数
        **kwargs: 传给 fn 的关键字参数

    Returns:
        函数返回值；超时或异常返回 None。
    """
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(f"[event_radar] akshare {fn.__name__} 超时({timeout}s)")
            return None
        except Exception as e:
            logger.warning(f"[event_radar] akshare {fn.__name__} 调用失败: {e}")
            return None


def _fetch_news_from_akshare(limit: int = 30) -> list[dict]:
    """从 akshare 获取财经新闻（东财新闻 + 央视新闻补充）。

    主接口 stock_news_em 返回东方财富新闻（含标题/内容/链接/时间，数据最丰富），
    数据不足时用 news_cctv 补充央视新闻联播（金融关键词过滤）。

    Args:
        limit: 最多返回条数

    Returns:
        [{news_title, news_summary, news_source, news_url, published_at}, ...]
        异常时返回空列表，不阻塞流程。
    """
    result: list[dict] = []
    try:
        import akshare as ak
    except ImportError:
        logger.warning("[event_radar] akshare 未安装，跳过 akshare 新闻源")
        return []

    # 主接口：东方财富财经新闻（含 URL，数据最丰富）
    try:
        df = _call_akshare_with_timeout(ak.stock_news_em, timeout=15, symbol="A股")
        if df is not None and len(df) > 0:
            for _, row in df.head(limit).iterrows():
                title = str(row.get("新闻标题", "")).strip()
                if not title:
                    continue
                result.append({
                    "news_title": title,
                    "news_summary": str(row.get("新闻内容", ""))[:200],
                    "news_source": str(row.get("文章来源", "东方财富")),
                    "news_url": str(row.get("新闻链接", "")),
                    "published_at": str(row.get("发布时间", "")),
                })
    except Exception as e:
        logger.warning(f"[event_radar] akshare stock_news_em 失败: {e}")

    # 补充：央视新闻联播（金融相关过滤，弥补条数不足）
    if len(result) < limit:
        try:
            today = datetime.now().strftime("%Y%m%d")
            df2 = _call_akshare_with_timeout(ak.news_cctv, timeout=15, date=today)
            if df2 is not None and len(df2) > 0:
                finance_keywords = (
                    "股", "基金", "央行", "利率", "经济", "金融",
                    "市场", "投资", "GDP", "通胀", "行情", "债", "汇率",
                )
                for _, row in df2.head(limit - len(result)).iterrows():
                    title = str(row.get("title", "")).strip()
                    if not title or not any(kw in title for kw in finance_keywords):
                        continue
                    result.append({
                        "news_title": title,
                        "news_summary": str(row.get("content", ""))[:200],
                        "news_source": "央视新闻",
                        "news_url": "",
                        "published_at": str(row.get("date", "")),
                    })
        except Exception as e:
            logger.warning(f"[event_radar] akshare news_cctv 失败: {e}")

    return result[:limit]


def _fetch_news_from_eastmoney(limit: int = 20) -> list[dict]:
    """从东方财富妙想 MCP 获取热点新闻（MCP 不可用时降级到百度财经）。

    复用 mcp.eastmoney_client 的 stock_hotspot 接口获取市场热点文本，
    按行分割为多条新闻。MCP 不可用或无结果时降级到 akshare news_economic_baidu。

    Args:
        limit: 最多返回条数

    Returns:
        [{news_title, news_summary, news_source, news_url, published_at}, ...]
        异常时返回空列表，不阻塞流程。
    """
    # 优先：东方财富妙想 MCP 热点发现
    try:
        from mcp.eastmoney_client import get_eastmoney_client
        client = get_eastmoney_client()
        text = client.stock_hotspot("今日财经热点新闻")
        if text and len(text) > 10:
            result: list[dict] = []
            # MCP 返回文本块，按换行分割为多条热点
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for line in text.split("\n"):
                line = line.strip()
                if len(line) < 10:
                    continue
                # 去除 markdown 列表符号前缀（如 "1." "•" "-" "*"）
                clean = re.sub(r"^[•\-\*\d+\.、]+", "", line).strip()
                if len(clean) < 10:
                    continue
                result.append({
                    "news_title": clean[:80],
                    "news_summary": clean[:200],
                    "news_source": "东方财富妙想",
                    "news_url": "",
                    "published_at": now_str,
                })
                if len(result) >= limit:
                    break
            if result:
                return result
    except Exception as e:
        logger.warning(f"[event_radar] 东方财富 MCP 热点获取失败: {e}")

    # 降级：akshare 百度财经新闻
    try:
        import akshare as ak
        df = _call_akshare_with_timeout(ak.news_economic_baidu, timeout=15)
        if df is not None and len(df) > 0:
            result = []
            for _, row in df.head(limit).iterrows():
                # 兼容不同版本列名差异
                title = str(row.get("title", row.get("新闻标题", ""))).strip()
                if not title:
                    continue
                result.append({
                    "news_title": title,
                    "news_summary": str(row.get("abstract", row.get("新闻内容", "")))[:200],
                    "news_source": str(row.get("source", "百度财经")),
                    "news_url": str(row.get("url", row.get("新闻链接", ""))),
                    "published_at": str(row.get("date", row.get("发布时间", ""))),
                })
            return result
    except Exception as e:
        logger.warning(f"[event_radar] akshare news_economic_baidu 失败: {e}")

    return []


def _collect_news() -> list[dict]:
    """采集最近 24 小时财经新闻（三源融合 + 跨源去重）。

    数据源优先级：盈米 MCP → akshare → 东方财富/百度
    每个数据源 try-except 隔离，单源失败不影响其他源。
    单次最多 50 条，跨源标题精确去重。
    """
    max_news = 50
    all_news: list[dict] = []

    # 数据源1：盈米 MCP（优先）
    try:
        mcp_news = _fetch_news_from_mcp(limit=max_news)
        if mcp_news:
            all_news.extend(mcp_news)
            logger.info(f"[event_radar] 盈米MCP 采集 {len(mcp_news)} 条")
    except Exception as e:
        logger.warning(f"[event_radar] 盈米MCP 采集异常: {e}")

    # 数据源2：akshare 财经新闻
    try:
        ak_news = _fetch_news_from_akshare(limit=max_news)
        if ak_news:
            all_news.extend(ak_news)
            logger.info(f"[event_radar] akshare 采集 {len(ak_news)} 条")
    except Exception as e:
        logger.warning(f"[event_radar] akshare 采集异常: {e}")

    # 数据源3：东方财富妙想/百度财经
    try:
        em_news = _fetch_news_from_eastmoney(limit=max_news)
        if em_news:
            all_news.extend(em_news)
            logger.info(f"[event_radar] 东方财富/百度 采集 {len(em_news)} 条")
    except Exception as e:
        logger.warning(f"[event_radar] 东方财富/百度 采集异常: {e}")

    if not all_news:
        logger.info("[event_radar] 三源均未采集到新闻，跳过本次扫描")
        return []

    # 跨源去重：标题精确匹配（复用现有去重逻辑）
    seen_titles = set()
    unique = []
    for n in all_news:
        title = n.get("news_title", "").strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            unique.append(n)

    logger.info(
        f"[event_radar] 三源合计 {len(all_news)} 条，去重后 {len(unique)} 条"
    )
    return unique[:max_news]


def _extract_trends_from_articles(article_content: str, article_title: str = "") -> list[dict]:
    """从深度分析文章中提取中长期行业趋势和投资机会。

    与新闻事件提取的区别：
    - 新闻提取：未来1-2周即将发生的具体事件
    - 趋势提取：中长期（1-6个月）的行业发展趋势和投资主题

    Args:
        article_content: 文章正文内容
        article_title: 文章标题

    Returns:
        趋势列表 [{title, summary, event_type, direction, affected_sectors,
                  affected_themes, confidence, time_frame, evidence}]
    """
    if not article_content or len(article_content) < 500:
        return []

    known_sectors = "/".join(SECTOR_TO_INDEX.keys())
    
    prompt = f"""你是一位资深行业分析师。请从以下深度分析文章中提取中长期（1-6个月）的行业趋势和投资机会。

【文章标题】
{article_title}

【文章内容】
{article_content[:3000]}

【输出要求】
仅输出 JSON 数组，每个趋势包含：
- title: 趋势标题（≤50字，主谓宾完整，如"液冷技术渗透率快速提升"）
- summary: 趋势摘要（≤200字，说明逻辑和影响）
- event_type: 事件分类（trend/theme/technology/policy/capital）
- direction: 影响方向（positive/negative/neutral）
- affected_sectors: 受影响板块（数组，从以下标准板块选取：{known_sectors}）
- affected_themes: 受影响主题（数组，自由文本如"液冷""CPO""存算一体"）
- confidence: 置信度（0-1，1表示高度确定）
- time_frame: 时间跨度（short=1-3个月, medium=3-6个月, long=6-12个月）
- evidence: 文章中的关键证据（≤100字，引用文章中的数据或事实）

【过滤规则】
1. 只提取有明确投资逻辑的趋势，不提取纯科普内容
2. 必须能从文章中找到支撑证据
3. 最多输出 5 个趋势
4. affected_sectors 必须从标准板块名列表选取

只输出 JSON 数组，不要其他解释。"""

    try:
        resp = _call_llm(
            caller="event_radar_trend_extractor",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=3000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        if not raw.startswith("["):
            start = raw.find("[")
            end = raw.rfind("]")
            if start != -1 and end != -1 and end > start:
                raw = raw[start:end + 1]
        trends = json.loads(raw)
        if not isinstance(trends, list):
            return []
    except Exception as e:
        logger.warning(f"[event_radar] 趋势提取失败: {e}")
        return []

    filtered = []
    for trend in trends:
        if not trend.get("title"):
            continue
        if trend.get("confidence", 0) < 0.5:
            continue
        filtered.append(trend)

    logger.info(f"[event_radar] 从文章提取趋势 {len(filtered)} 个")
    return filtered


def _extract_events_from_news(news_list: list[dict], trace_id: str = "") -> list[dict]:
    """用 LLM 从新闻列表中提取未来 1-2 周将发生的市场事件。

    Args:
        news_list: 新闻列表 [{news_title, news_summary, ...}]
        trace_id: 追踪 ID

    Returns:
        事件列表 [{title, summary, event_type, direction, expected_date,
                  affected_sectors, affected_themes, confidence}]
        过滤规则：expected_date 在 [今天, 今天+14天]、confidence >= 0.4
    """
    if not news_list:
        logger.info(f"[event_radar:{trace_id}] 无新闻输入，跳过 LLM 提取")
        return []

    max_events = get_config_int("alerts.event_radar_max_events", 15)
    lookforward_days = get_config_int("alerts.event_radar_lookforward_days", 14)
    min_confidence = get_config_float("alerts.event_radar_min_confidence", 0.4)

    today = datetime.now().strftime("%Y-%m-%d")
    future_limit = (datetime.now() + timedelta(days=lookforward_days)).strftime("%Y-%m-%d")

    # 构造新闻摘要 JSON（控制 token）
    news_for_llm = [
        {"title": n.get("news_title", ""), "summary": n.get("news_summary", "")[:100]}
        for n in news_list[:50]
    ]

    known_sectors = "/".join(SECTOR_TO_INDEX.keys())
    prompt = f"""你是一位资深财经分析师。请从以下新闻列表中提取「即将在未来 {lookforward_days} 天内发生」的市场事件。

【新闻列表】
{json.dumps(news_for_llm, ensure_ascii=False)}

【输出要求】
仅输出 JSON 数组，每个事件包含：
- title: 事件标题（≤50 字，主谓宾完整）
- summary: 事件摘要（≤200 字，说明影响）
- event_type: 事件分类（policy/industry/earnings/capital/macro/theme）
- direction: 影响方向（positive/negative/neutral）
- expected_date: 预期发生日期（YYYY-MM-DD 格式，从新闻推断）
- affected_sectors: 受影响板块（数组，必须严格从以下标准板块名中选取：{known_sectors}）
  注意：不要使用"国防军工""生物医药"等同义词，必须用标准名"军工""医药"
- affected_themes: 受影响主题（数组，自由文本如"国产替代""火箭回收"）
- confidence: 置信度（0-1，1 表示高度确定会发生）

【过滤规则】
1. 只提取"即将发生"的事件，不提取"已经发生"的新闻
2. 跳过模糊时间（如"近期""未来"），必须能推断到具体日期
3. 跳过无市场影响的事件（如纯娱乐八卦）
4. 单条新闻可提取 0-2 个事件，最多输出 {max_events} 个事件
5. expected_date 必须在 [{today}, {future_limit}] 范围内
6. affected_sectors 必须从标准板块名列表选取，不要自创板块名

只输出 JSON 数组，不要其他解释。"""

    try:
        resp = _call_llm(
            caller="event_radar_extractor",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # 容错1：剥离 markdown 代码块包裹
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        # 容错2：LLM 可能在前缀加说明文字，提取第一个 [ 到最后一个 ]
        if not raw.startswith("["):
            start = raw.find("[")
            end = raw.rfind("]")
            if start != -1 and end != -1 and end > start:
                raw = raw[start:end + 1]
        events = json.loads(raw)
        if not isinstance(events, list):
            return []
    except Exception as e:
        logger.warning(f"[event_radar:{trace_id}] LLM 事件提取失败: {e}")
        # 记录原始响应片段便于调试（不超过 200 字）
        logger.debug(f"[event_radar:{trace_id}] LLM 原始响应: {raw[:200] if 'raw' in dir() else 'N/A'}")
        return []

    # 二次过滤：日期范围 + 置信度
    filtered = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        exp_date = ev.get("expected_date", "")
        conf = float(ev.get("confidence", 0))
        if not exp_date:
            continue
        if exp_date < today or exp_date > future_limit:
            continue
        if conf < min_confidence:
            continue
        filtered.append(ev)

    logger.info(
        f"[event_radar:{trace_id}] LLM 提取 {len(events)} 个事件，"
        f"过滤后 {len(filtered)} 个"
    )
    return filtered[:max_events]


def _find_candidate_funds(index_code: str, exclude_codes: set = None) -> list[dict]:
    """查询跟踪该指数的候选建仓基金（排除已持仓）。

    优先级：
    1. fund_metadata 表精确匹配 tracking_index
    2. 回退到 index_fund_mapper 内置映射表（_INDEX_FUND_MAP）
       —— 解决 fund_metadata 表为空时 candidate_funds 永远为空的问题
    """
    exclude_codes = exclude_codes or set()
    results = []

    # 1. fund_metadata 表查询
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fund_metadata'"
        ).fetchone()
        if row:
            placeholders = ",".join("?" * len(exclude_codes)) if exclude_codes else "''"
            sql = f"""
                SELECT fund_code, fund_name, fund_type, tracking_index
                FROM fund_metadata
                WHERE tracking_index = ?
                  AND fund_code NOT IN ({placeholders})
                ORDER BY fund_type, fund_code
                LIMIT 5
            """
            params = [index_code] + list(exclude_codes)
            rows = conn.execute(sql, params).fetchall()
            for r in rows:
                results.append({
                    "fund_code": r["fund_code"],
                    "fund_name": r["fund_name"],
                    "fund_type": r["fund_type"],
                    "match_reason": f"跟踪指数 {index_code}",
                })
    except Exception as e:
        logger.warning(f"[event_radar] 查询候选基金失败 index={index_code}: {e}")
    finally:
        conn.close()

    # 2. fund_metadata 无结果时回退到内置映射表
    if not results:
        try:
            from services.index_fund_mapper import find_funds_by_index
            # user_holdings_only=False 表示只查内置映射表，不重复查持仓
            builtin = find_funds_by_index(index_code, user_holdings_only=False)
            for item in builtin:
                code = item.get("fund_code", "")
                if code in exclude_codes:
                    continue
                results.append({
                    "fund_code": code,
                    "fund_name": item.get("fund_name", ""),
                    "fund_type": "ETF" if "ETF" in item.get("fund_name", "") else "",
                    "match_reason": f"内置映射跟踪指数 {index_code}",
                })
        except Exception as e:
            logger.warning(f"[event_radar] 内置映射兜底失败 index={index_code}: {e}")

    return results[:5]


def _determine_relevance(
    event: dict,
    user_holdings: list[dict],
    user_watchlist: list[dict] = None,
) -> tuple[str, list[dict], list[dict], list[dict]]:
    """判定推送分级。

    Args:
        event: 事件 dict（含 affected_sectors）
        user_holdings: 用户持仓 [{fund_code, fund_name, index_code, index_name}, ...]
        user_watchlist: 用户关注列表 [{fund_code, fund_name, index_code, index_name}, ...]

    Returns:
        (relevance, matched_holdings, matched_watchlist, candidate_funds)
        relevance: holding_impact / watchlist_impact / opportunity / market_watch

    匹配策略（增强版）：
    1. 板块名归一化（"国防军工"→"军工"）
    2. 持仓匹配优先级：index_code 精确匹配 → index_name 关键词模糊匹配
    3. 关注列表匹配（同持仓逻辑，命中则标记 watchlist_impact）
    4. 无持仓/关注命中时收集候选建仓基金（fund_metadata → 内置映射兜底）
    """
    affected_sectors = event.get("affected_sectors", [])
    if not affected_sectors:
        return "market_watch", [], [], []

    if user_watchlist is None:
        user_watchlist = []

    matched_holdings = []
    matched_watchlist = []
    candidate_funds = []
    holding_codes = {h.get("fund_code") for h in user_holdings}
    watchlist_codes = {w.get("fund_code") for w in user_watchlist}
    matched_holding_codes = set()
    matched_watchlist_codes = set()
    exclude_codes = holding_codes | watchlist_codes

    from services.index_fund_mapper import _normalize_index_code

    for raw_sector in affected_sectors:
        sector = _normalize_sector(raw_sector)
        index_codes = SECTOR_TO_INDEX.get(sector, [])
        name_keywords = _SECTOR_TO_NAME_KEYWORDS.get(sector, [])

        # 1a. 持仓匹配：index_code 精确匹配
        for idx_code in index_codes:
            for h in user_holdings:
                if h.get("fund_code") in matched_holding_codes:
                    continue
                h_index = h.get("index_code") or ""
                if _normalize_index_code(h_index) == _normalize_index_code(idx_code):
                    matched_holdings.append({
                        "fund_code": h.get("fund_code", ""),
                        "fund_name": h.get("fund_name", ""),
                        "match_type": "holding",
                        "match_reason": f"跟踪 {sector} 相关指数 {idx_code}",
                    })
                    matched_holding_codes.add(h.get("fund_code"))

        # 1b. 持仓匹配：index_name 关键词模糊匹配
        if not matched_holdings and name_keywords:
            for h in user_holdings:
                if h.get("fund_code") in matched_holding_codes:
                    continue
                h_index_name = (h.get("index_name") or "").strip()
                h_fund_name = (h.get("fund_name") or "").strip()
                for kw in name_keywords:
                    if kw in h_index_name or kw in h_fund_name:
                        matched_holdings.append({
                            "fund_code": h.get("fund_code", ""),
                            "fund_name": h.get("fund_name", ""),
                            "match_type": "holding",
                            "match_reason": f"持仓名称含 {sector} 关键词 '{kw}'",
                        })
                        matched_holding_codes.add(h.get("fund_code"))
                        break

        # 1c. 关注列表匹配：index_code 精确匹配（持仓未命中时）
        if not matched_holdings:
            for idx_code in index_codes:
                for w in user_watchlist:
                    if w.get("fund_code") in matched_watchlist_codes:
                        continue
                    w_index = w.get("index_code") or ""
                    if _normalize_index_code(w_index) == _normalize_index_code(idx_code):
                        matched_watchlist.append({
                            "fund_code": w.get("fund_code", ""),
                            "fund_name": w.get("fund_name", ""),
                            "match_reason": f"关注基金跟踪 {sector} 相关指数 {idx_code}",
                        })
                        matched_watchlist_codes.add(w.get("fund_code"))

            # 1d. 关注列表匹配：index_name 关键词模糊匹配
            if not matched_watchlist and name_keywords:
                for w in user_watchlist:
                    if w.get("fund_code") in matched_watchlist_codes:
                        continue
                    w_index_name = (w.get("index_name") or "").strip()
                    w_fund_name = (w.get("fund_name") or "").strip()
                    for kw in name_keywords:
                        if kw in w_index_name or kw in w_fund_name:
                            matched_watchlist.append({
                                "fund_code": w.get("fund_code", ""),
                                "fund_name": w.get("fund_name", ""),
                                "match_reason": f"关注基金名称含 {sector} 关键词 '{kw}'",
                            })
                            matched_watchlist_codes.add(w.get("fund_code"))
                            break

        # 2. 若无持仓/关注命中，收集候选建仓基金
        if not matched_holdings and not matched_watchlist:
            for idx_code in index_codes:
                cands = _find_candidate_funds(idx_code, exclude_codes=exclude_codes)
                existing_codes = {c["fund_code"] for c in candidate_funds}
                for c in cands:
                    if c["fund_code"] not in existing_codes:
                        candidate_funds.append(c)

    # 分级优先级：持仓 > 关注 > 候选基金 > 市场关注
    if matched_holdings:
        return "holding_impact", matched_holdings, [], []
    elif matched_watchlist:
        return "watchlist_impact", [], matched_watchlist, []
    elif candidate_funds:
        max_cands = get_config_int("alerts.event_radar_max_candidate_funds", 5)
        return "opportunity", [], [], candidate_funds[:max_cands]
    else:
        return "market_watch", [], [], []


def _update_event_statuses() -> dict:
    """扫描所有 upcoming/imminent 事件，按日期更新状态。

    规则：
    - today >= expected_date → materialized
    - today > expected_date + 7 → expired
    - today > expected_date - 3 且 status=upcoming → imminent

    Returns:
        {"imminent": int, "materialized": int, "expired": int}
    """
    from db.market_events import list_active_events, update_market_event_status

    today = datetime.now().strftime("%Y-%m-%d")
    today_dt = datetime.now()
    counts = {"imminent": 0, "materialized": 0, "expired": 0}

    active = list_active_events()
    for ev in active:
        exp_date_str = ev.get("expected_date")
        if not exp_date_str:
            continue
        try:
            exp_dt = datetime.strptime(exp_date_str, "%Y-%m-%d")
        except ValueError:
            continue

        status = ev["status"]
        days_to_event = (exp_dt - today_dt).days

        # today > expected_date + 7 → expired
        if days_to_event < -7:
            update_market_event_status(ev["event_id"], "expired")
            counts["expired"] += 1
        # today >= expected_date → materialized
        elif days_to_event <= 0:
            update_market_event_status(ev["event_id"], "materialized")
            counts["materialized"] += 1
        # today > expected_date - 3 且 status=upcoming → imminent
        elif days_to_event <= 3 and status == "upcoming":
            update_market_event_status(ev["event_id"], "imminent")
            counts["imminent"] += 1

    if any(counts.values()):
        logger.info(f"[event_radar] 状态流转: {counts}")
    return counts


def scan_forward_events(trace_id: str = "") -> dict:
    """前瞻性事件雷达主扫描函数。

    流程：
    1. 检查开关
    2. 采集新闻
    3. LLM 提取未来事件（短期）
    4. 提取行业趋势（中长期）
    5. 写入 market_events 表（去重）
    6. 状态流转扫描
    7. 板块匹配 + 3 级分级
    8. 生成 alert

    Returns:
        {"extracted": int, "new": int, "updated": int,
         "alerts_created": int, "skipped": str?}
    """
    if not get_config_bool("alerts.event_radar_enabled", False):
        return {"skipped": "disabled", "extracted": 0, "new": 0, "updated": 0, "alerts_created": 0}

    trace_id = trace_id or datetime.now().strftime("%Y%m%d%H%M%S")
    logger.info(f"[event_radar:{trace_id}] 开始扫描")

    # 1. 采集新闻
    news = _collect_news()
    if not news:
        return {"extracted": 0, "new": 0, "updated": 0, "alerts_created": 0, "reason": "no_news"}

    # 2. LLM 提取短期事件
    events = _extract_events_from_news(news, trace_id=trace_id)

    # 3. 提取中长期行业趋势（从新闻中筛选深度分析内容）
    # 移动端扫描超时风险：每篇新闻1次LLM调用，限制为2篇控制耗时
    trends = []
    try:
        for n in news[:2]:
            summary = n.get("news_summary", "")
            if len(summary) > 300:
                trend_results = _extract_trends_from_articles(summary, n.get("news_title", ""))
                trends.extend(trend_results)
        logger.info(f"[event_radar:{trace_id}] 提取趋势 {len(trends)} 个")
    except Exception as e:
        logger.warning(f"[event_radar:{trace_id}] 趋势提取异常: {e}")

    # 合并事件和趋势
    all_items = events + trends
    if not all_items:
        return {"extracted": 0, "new": 0, "updated": 0, "alerts_created": 0, "reason": "no_events"}

    # 4. 写入 market_events 表（幂等）
    from db.market_events import create_market_event, update_event_relevance
    new_count = 0
    for ev in all_items:
        sources = [
            {"title": n.get("news_title", ""), "url": n.get("news_url", ""),
             "publish_date": n.get("published_at", "")}
            for n in news[:3]
        ]
        try:
            from db.market_events import get_market_event, _gen_event_id
            expected_date = ev.get("expected_date", "")
            
            eid = _gen_event_id(ev["title"], expected_date)
            existing = get_market_event(eid)
            
            is_trend = ev.get("time_frame") or ev.get("evidence")
            conf = _calibrate_confidence(
                float(ev.get("confidence", 0.5)),
                ev.get("affected_sectors", []),
            ) if not is_trend else float(ev.get("confidence", 0.5))
            
            create_market_event(
                title=ev["title"],
                summary=ev.get("summary", ""),
                event_type=ev.get("event_type", "theme"),
                direction=ev.get("direction", "neutral"),
                expected_date=expected_date,
                affected_sectors=ev.get("affected_sectors", []),
                affected_themes=ev.get("affected_themes", []),
                confidence=conf,
                sources=sources,
                time_frame=ev.get("time_frame", ""),
                evidence=ev.get("evidence", ""),
            )
            if not existing:
                new_count += 1
        except Exception as e:
            logger.warning(f"[event_radar:{trace_id}] 写入事件失败 '{ev.get('title','')}': {e}")

    # 5. 状态流转
    status_counts = _update_event_statuses()

    # 6. 板块匹配 + 分级 + 生成 alert
    from db.portfolio import list_holdings, create_alert
    from db.watchlist import list_watchlist
    holdings = list_holdings()
    watchlist = list_watchlist(status="watching")  # 只查关注中的
    alerts_created = 0

    # 对所有 upcoming/imminent 事件重新计算分级
    from db.market_events import list_active_events
    active = list_active_events()
    for ev_row in active:
        try:
            affected = json.loads(ev_row.get("affected_sectors") or "[]")
            event_dict = {"affected_sectors": affected}
            relevance, matched, matched_wl, candidates = _determine_relevance(
                event_dict, holdings, watchlist,
            )

            # 更新事件的分级字段（关注基金合并到 matched_holdings 存储，加 match_type 区分）
            all_matched = matched + [
                {**w, "match_type": "watchlist"} for w in matched_wl
            ]
            update_event_relevance(ev_row["event_id"], relevance, all_matched, candidates)

            # 仅对新生成的事件（本次扫描首次检测）生成 alert，避免重复推送
            ev_id = ev_row["event_id"]
            ev_detected = ev_row.get("detected_date", "")
            today = datetime.now().strftime("%Y-%m-%d")
            if not ev_detected.startswith(today):
                continue

            # 构造 alert
            severity = {
                "holding_impact": "warning",
                "watchlist_impact": "info",
                "opportunity": "info",
                "market_watch": "info",
            }.get(relevance, "info")

            title_prefix = {
                "holding_impact": "持仓影响", "watchlist_impact": "关注机会",
                "opportunity": "建仓机会", "market_watch": "市场关注",
            }.get(relevance, "市场关注")
            alert_title = f"[{title_prefix}] {ev_row['title']}"
            content_parts = [f"预期日期：{ev_row.get('expected_date','')}"]
            if matched:
                codes = [m["fund_name"] for m in matched[:3]]
                content_parts.append(f"关联持仓：{', '.join(codes)}")
            if matched_wl:
                codes = [w["fund_name"] for w in matched_wl[:3]]
                content_parts.append(f"关注基金：{', '.join(codes)}")
            if candidates:
                codes = [c["fund_name"] for c in candidates[:3]]
                content_parts.append(f"候选基金：{', '.join(codes)}")
            content = " | ".join(content_parts)

            create_alert(
                alert_type="event_radar",
                title=alert_title,
                content=content,
                severity=severity,
                source="event_radar",
            )
            alerts_created += 1

            # P2 闭环：高影响持仓事件 → 自动创建决策候选
            # 仅对 holding_impact 级别 + 暂停/复牌/强制退市/大股东变更等高影响事件类型
            if relevance == "holding_impact" and matched:
                _event_to_candidates(ev_row, matched, relevance)
        except Exception as e:
            logger.warning(f"[event_radar:{trace_id}] 生成 alert 失败 ev={ev_row.get('event_id')}: {e}")

    result = {
        "extracted": len(events),
        "new": new_count,
        "updated": status_counts,
        "alerts_created": alerts_created,
    }
    logger.info(f"[event_radar:{trace_id}] 扫描完成: {result}")
    return result


def _event_to_candidates(event_row: dict, matched_holdings: list[dict], relevance: str) -> None:
    """将高影响持仓事件自动转为决策候选（去重：14天内同事件+同标的同来源不重复）。

    开关：alerts.event_auto_candidate_enabled（默认 false，遵循项目规范）。
    """
    from db.config import get_config_bool
    if not get_config_bool("alerts.event_auto_candidate_enabled", False):
        return

    HIGH_IMPACT_TYPES = {
        "manager_change", "regulatory_penalty", "major_dividend",
        "delisting_risk", "suspension", "policy", "earnings",
    }
    event_type = event_row.get("event_type", "")
    if event_type not in HIGH_IMPACT_TYPES:
        return

    try:
        from db.decisions import create_candidate_from_structured_recommendation
        event_id = event_row.get("event_id", "")
        event_title = event_row.get("title", "")
        expected_date = event_row.get("expected_date", "")

        for h in matched_holdings:
            fund_code = h.get("fund_code", "")
            fund_name = h.get("fund_name", "")
            if not fund_code:
                continue
            create_candidate_from_structured_recommendation({
                "source_type": "event_radar",
                "action_type": "watch",
                "target_type": "fund",
                "target_code": fund_code,
                "target_name": fund_name,
                "summary": f"事件预警：{event_title[:80]}",
                "rationale": f"事件类型：{event_type}，预期日期：{expected_date}，关联基金：{fund_name}",
                "confidence": "high",
                "dedupe_key": f"event_{event_id}_{fund_code}",
                "priority": 1,
                "source_snapshot": {
                    "event_id": event_id,
                    "event_type": event_type,
                    "expected_date": expected_date,
                },
            })
    except Exception as e:
        logger.debug(f"[event_radar] 自动创建决策候选失败: {e}")


# ── 事件落地验证 ──────────────────────────────────────


def _fetch_index_close_prices(index_code: str, start_date: str, end_date: str) -> dict:
    """获取指数在 [start_date, end_date] 区间的每日收盘价。

    支持上证(sh)、深证(sz)、中证(CSI)系列指数。中证系列先尝试 sh/sz 前缀，
    失败后用 akshare 的 index_zh_a_hist 接口（按指数代码直接查）。

    Returns:
        {"YYYY-MM-DD": float, ...} 或空 dict
    """
    import akshare as ak
    base = index_code.replace(".SZ", "").replace(".SH", "").replace(".CSI", "")
    prices = {}

    # 策略1：sh/sz 前缀（新浪接口，覆盖上证/深证）
    for prefix in ["sh", "sz"]:
        sina_code = f"{prefix}{base}"
        try:
            df = ak.stock_zh_index_daily(symbol=sina_code)
            if df is None or df.empty:
                continue
            date_col = "date" if "date" in df.columns else df.columns[0]
            close_col = "close" if "close" in df.columns else df.columns[-1]
            for _, row in df.iterrows():
                d = str(row[date_col])[:10]
                if start_date <= d <= end_date:
                    prices[d] = float(row[close_col])
            if prices:
                return prices
        except Exception:
            continue

    # 策略2：akshare index_zh_a_hist（中证系列 930xxx/931xxx 等）
    if not prices:
        try:
            df = ak.index_zh_a_hist(symbol=base, period="daily",
                                    start_date=start_date.replace("-", ""),
                                    end_date=end_date.replace("-", ""))
            if df is not None and not df.empty:
                date_col = "日期" if "日期" in df.columns else df.columns[0]
                close_col = "收盘" if "收盘" in df.columns else df.columns[-1]
                for _, row in df.iterrows():
                    d = str(row[date_col])[:10]
                    if start_date <= d <= end_date:
                        prices[d] = float(row[close_col])
                if prices:
                    return prices
        except Exception:
            pass

    # 策略3：基金净值回退（如果指数取不到，用相关基金净值近似）
    if not prices:
        try:
            from db.portfolio import list_holdings
            holdings = list_holdings()
            for h in holdings:
                if h.get("index_code", "").replace(".CSI", "").replace(".SZ", "").replace(".SH", "") == base:
                    fund_code = h.get("fund_code", "")
                    if fund_code:
                        from services.market_data import get_fund_nav
                        nav = get_fund_nav(fund_code)
                        if nav and nav.get("nav"):
                            prices[start_date] = float(nav["nav"])
                            return prices
        except Exception:
            pass

    return prices


def _verify_single_event(event: dict, window_days: int = 3) -> dict | None:
    """验证单个已落地事件的方向预测是否正确。

    逻辑：
    1. 从 affected_sectors 取第一个板块对应的指数代码
    2. 获取 materialized_date 和 materialized_date+window_days 的收盘价
    3. 计算涨跌幅
    4. 对比 direction 与实际涨跌方向

    Returns:
        验证结果 dict 或 None（无法验证时）
    """
    sectors = json.loads(event.get("affected_sectors") or "[]")
    if not sectors:
        return None

    # 取第一个有指数映射的板块
    index_code = None
    index_name = None
    for s in sectors:
        key = _normalize_sector(s)
        if key and key in SECTOR_TO_INDEX:
            codes = SECTOR_TO_INDEX[key]
            if codes:
                index_code = codes[0]
                index_name = key
                break

    if not index_code:
        return None

    mat_date = event.get("materialized_date") or event.get("expected_date")
    if not mat_date:
        return None

    try:
        mat_dt = datetime.strptime(mat_date[:10], "%Y-%m-%d")
    except ValueError:
        return None

    end_dt = mat_dt + timedelta(days=window_days + 4)  # 多取几天确保有交易日数据
    end_date = end_dt.strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    if end_date > today:
        return None  # 验证窗口未到

    prices = _fetch_index_close_prices(index_code, mat_date, end_date)
    if not prices or len(prices) < 2:
        return None

    # 取落地日收盘价（或之后最近交易日）
    sorted_dates = sorted(prices.keys())
    base_price = None
    for d in sorted_dates:
        if d >= mat_date:
            base_price = prices[d]
            break
    if base_price is None:
        return None

    # 取验证窗口结束日收盘价
    verify_price = prices[sorted_dates[-1]]
    if verify_price == base_price:
        return None

    change_pct = (verify_price - base_price) / base_price * 100

    # 方向判定（阈值 1%）
    direction = event.get("direction", "neutral")
    THRESHOLD = 1.0
    if abs(change_pct) < THRESHOLD:
        status = "flat"
    elif direction == "positive" and change_pct > 0:
        status = "correct"
    elif direction == "negative" and change_pct < 0:
        status = "correct"
    elif direction == "neutral":
        status = "flat"
    else:
        status = "wrong"

    return {
        "status": status,
        "change_pct": round(change_pct, 2),
        "verified_date": today,
        "index_code": index_code,
        "index_name": index_name,
        "direction_predicted": direction,
        "window_days": window_days,
        "base_price": round(base_price, 2),
        "verify_price": round(verify_price, 2),
    }


def verify_materialized_events(trace_id: str = "") -> dict:
    """批量验证已落地且超过 T+3 窗口的事件。

    Returns:
        {"verified": int, "correct": int, "wrong": int, "flat": int, "alerts_created": int}
    """
    from db.market_events import (
        list_pending_verification_events, update_event_verification,
    )
    from db.portfolio import create_alert

    window = get_config_int("alerts.event_radar_verify_window_days", 3)
    pending = list_pending_verification_events(window)
    if not pending:
        return {"verified": 0, "correct": 0, "wrong": 0, "flat": 0, "alerts_created": 0}

    trace_id = trace_id or datetime.now().strftime("%Y%m%d%H%M%S")
    logger.info(f"[event_radar:{trace_id}] 待验证事件 {len(pending)} 个")

    counts = {"verified": 0, "correct": 0, "wrong": 0, "flat": 0, "alerts_created": 0}
    for ev in pending:
        try:
            result = _verify_single_event(ev, window)
            if not result:
                logger.debug(f"[event_radar:{trace_id}] 事件 {ev['event_id']} 无法验证（无指数数据）")
                continue

            update_event_verification(ev["event_id"], result)
            counts["verified"] += 1
            counts[result["status"]] += 1

            # 推送验证结果 alert
            status_label = {"correct": "验证正确 ✅", "wrong": "验证偏差 ⚠️", "flat": "波动平淡 ➡️"}
            title = f"[事件验证] {ev['title'][:30]}"
            content = (
                f"{status_label.get(result['status'], '')} | "
                f"{result['index_name']} 涨跌幅 {result['change_pct']:+.2f}% | "
                f"预测方向：{result['direction_predicted']}"
            )
            severity = {"correct": "info", "wrong": "warning", "flat": "info"}.get(result["status"], "info")
            create_alert(
                alert_type="event_radar_verified",
                title=title,
                content=content,
                severity=severity,
                source="event_radar",
            )
            counts["alerts_created"] += 1
        except Exception as e:
            logger.warning(f"[event_radar:{trace_id}] 验证事件 {ev.get('event_id')} 失败: {e}")

    logger.info(f"[event_radar:{trace_id}] 验证完成: {counts}")
    return counts


def get_sector_accuracy_stats() -> dict:
    """统计各板块的验证准确率（用于置信度校准和前端展示）。

    Returns:
        {
            "overall": {"total": int, "correct": int, "wrong": int, "flat": int, "accuracy": float},
            "by_sector": {
                "半导体": {"total": int, "correct": int, "wrong": int, "accuracy": float},
                ...
            }
        }
    """
    from db.market_events import list_verified_events

    verified = list_verified_events(limit=200)
    if not verified:
        return {"overall": {"total": 0, "correct": 0, "wrong": 0, "flat": 0, "accuracy": 0.0},
                "by_sector": {}}

    overall = {"total": 0, "correct": 0, "wrong": 0, "flat": 0}
    by_sector: dict[str, dict] = {}

    for ev in verified:
        result = json.loads(ev.get("verification_result") or "{}")
        if not result:
            continue
        status = result.get("status", "flat")
        sectors = json.loads(ev.get("affected_sectors") or "[]")

        overall["total"] += 1
        overall[status] += 1

        for s in sectors:
            key = _normalize_sector(s) or s
            if key not in by_sector:
                by_sector[key] = {"total": 0, "correct": 0, "wrong": 0, "flat": 0}
            by_sector[key]["total"] += 1
            by_sector[key][status] += 1

    # 计算准确率：correct / (correct + wrong)，flat 不计入
    def _calc_acc(d):
        judged = d["correct"] + d["wrong"]
        return round(d["correct"] / judged, 2) if judged > 0 else 0.0

    overall["accuracy"] = _calc_acc(overall)
    for s, d in by_sector.items():
        d["accuracy"] = _calc_acc(d)

    return {"overall": overall, "by_sector": by_sector}


def _calibrate_confidence(original_confidence: float, sectors: list) -> float:
    """根据板块历史准确率校准事件置信度。

    校准公式：calibrated = original × sector_accuracy
    若板块无历史数据（样本<3），不做校准返回原值。

    Returns:
        校准后的置信度 [0, 1]
    """
    stats = get_sector_accuracy_stats()
    by_sector = stats.get("by_sector", {})

    # 取所有相关板块中样本最多（最有参考价值）的板块准确率
    best_acc = None
    best_samples = 0
    for s in sectors:
        key = _normalize_sector(s) or s
        s_stat = by_sector.get(key)
        if s_stat and s_stat["total"] >= 3:  # 最少 3 个样本才校准
            if s_stat["total"] > best_samples:
                best_samples = s_stat["total"]
                best_acc = s_stat["accuracy"]

    if best_acc is None or best_acc == 0.0:
        return original_confidence  # 无足够数据，不校准

    calibrated = original_confidence * best_acc
    # 下限 0.1，避免过度惩罚
    return max(calibrated, 0.1)
