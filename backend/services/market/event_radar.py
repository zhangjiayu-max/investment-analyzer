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
# P0-F 修复（2026-07-20）：移除"机器人"从 AI 关键词（机器人是独立板块，应通过自己的 SECTOR_TO_INDEX 匹配）
# 移除"智能"从 AI 关键词（"智能"过宽，会匹配"智能制造"等无关基金）
_SECTOR_TO_NAME_KEYWORDS = {
    "半导体": ["半导体", "芯片", "集成电路", "存储"],
    "人工智能": ["人工智能", "AI", "算力", "大模型"],
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
    # P0-F: 机器人作为独立板块，独立配置关键词
    "机器人": ["机器人", "人形机器人", "自动化"],
}


# ── P0-J: 反向排除规则 ──
# 场景：基金名"生物医药科技"含"科技"但实为医药基金，不应被关联到科技事件
# 规则：若基金名含以下关键词，即使含板块关键词也不匹配该板块
# P0-F 修复（2026-07-20）：扩充排除规则
_SECTOR_EXCLUDE_RULES = {
    "科技": ["医药", "医疗", "生物", "健康", "恒生", "港股"],  # 排除港股科技（A 股 IPO 不影响港股）
    "消费": ["科技", "半导体", "芯片", "医药", "医疗"],
    "人工智能": ["医药", "医疗", "生物", "白酒", "食品", "消费"],
    "半导体": ["医药", "医疗", "生物", "白酒", "食品", "恒生", "港股"],
    "机器人": ["医药", "医疗", "生物", "白酒", "食品"],  # 机器人板块排除医药/消费基金
}

# P0-F 修复（2026-07-20）：名称模糊匹配的最小关键词命中数
# 原问题：单个关键词命中即匹配（如"科技"单独命中"生物医药科技"）
# 修复：板块要求至少 N 个关键词同时命中才视为匹配
_SECTOR_MIN_KEYWORD_HITS = {
    "科技": 2,        # "科技" 单独命中不算（"生物医药科技"只命中 1 个）
    "人工智能": 1,    # AI 关键词足够特异
    "半导体": 1,
    "新能源": 1,
    "消费": 1,
    "医药": 1,
    "机器人": 1,
}


def _should_exclude_for_sector(sector: str, fund_name: str, index_name: str) -> bool:
    """反向校验：基金是否应被排除在该板块之外（避免误关联）。

    场景：基金名"中信保诚中证800医药"含"科技"子串时被关联到"科技IPO"事件，
    实际是医药基金，应排除。
    """
    exclude_terms = _SECTOR_EXCLUDE_RULES.get(sector, [])
    if not exclude_terms:
        return False
    text = f"{fund_name} {index_name}".lower()
    return any(term in text for term in exclude_terms)


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


# P0-E 修复（2026-07-20）：按事件主题过滤相关 sources
# 原问题：scan_forward_events 中 sources = [n for n in news[:3]] 给所有事件塞同一批
# 修复：按 affected_sectors/affected_themes/title 关键词过滤，每个事件只保留真正相关 sources
def _filter_sources_by_relevance(event: dict, all_news: list[dict],
                                  max_per_event: int = 3) -> list[dict]:
    """按事件主题过滤相关新闻作为 sources。

    Args:
        event: 事件 dict（含 affected_sectors, affected_themes, title, summary）
        all_news: 全部采集的新闻列表
        max_per_event: 每个事件最多保留几条 sources
    Returns:
        排序后的相关 sources 列表；无相关新闻则返回 []（前端会展示"暂无来源"）
    """
    if not all_news:
        return []

    sectors = event.get("affected_sectors", []) or []
    themes = event.get("affected_themes", []) or []
    title = event.get("title", "") or ""
    summary = event.get("summary", "") or ""

    # 构建关键词集合：板块 + 主题 + 事件标题分词
    keywords = set()
    for s in sectors:
        if s:
            keywords.add(str(s).strip())
    for t in themes:
        if t:
            keywords.add(str(t).strip())
    # 标题关键词（去掉常见停用词）
    _stop_words = {"的", "了", "和", "与", "或", "在", "为", "是", "及",
                   "将", "上", "中", "下", "对", "等", "可", "可能"}
    for kw in _extract_keywords_from_text(title + " " + summary):
        if kw not in _stop_words and len(kw) >= 2:
            keywords.add(kw)

    if not keywords:
        # 关键词全空：回退用 news[:3]（兼容旧行为）
        return [
            {"title": n.get("news_title", n.get("title", "")),
             "url": n.get("news_url", n.get("url", "")),
             "publish_date": n.get("published_at", n.get("publish_date", ""))}
            for n in all_news[:max_per_event]
        ]

    # 对每条新闻打分
    scored = []
    for n in all_news:
        text = " ".join([
            str(n.get("news_title", "")) or "",
            str(n.get("title", "")) or "",
            str(n.get("news_summary", "")) or "",
            str(n.get("summary", "")) or "",
        ])
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, n))

    # 按相关度排序，取前 max_per_event 条
    scored.sort(key=lambda x: -x[0])
    return [
        {"title": n.get("news_title", n.get("title", "")),
         "url": n.get("news_url", n.get("url", "")),
         "publish_date": n.get("published_at", n.get("publish_date", ""))}
        for _, n in scored[:max_per_event]
    ]


def _extract_keywords_from_text(text: str) -> list[str]:
    """从文本中提取候选关键词（简单版：2-6 字 CJK 子串）。

    用于事件 sources 过滤。后续可换成 jieba 分词。
    """
    if not text:
        return []
    import re
    # 提取 2-6 个连续中文字符
    matches = re.findall(r'[\u4e00-\u9fa5]{2,6}', text)
    # 去重保留顺序
    seen = set()
    result = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result[:20]  # 最多 20 个关键词


# P1-C 修复（2026-07-20）：影响量化字段规则化兜底
# 原问题：alerts.event_impact_quantification_enabled 默认关闭 + LLM 可能不返回 → 字段全 None
# 修复：基于事件类型+方向+置信度，按规则估算影响幅度
def _analyze_event_impact_by_rule(event: dict, calibrated_dir: str = "") -> dict:
    """基于规则估算事件影响幅度（不调 LLM，避免成本）。

    Args:
        event: 事件 dict
        calibrated_dir: 校准后的方向（up/down/neutral）
    Returns:
        {expected_impact_pct, impact_direction, impact_duration, impact_analysis}
    """
    direction = calibrated_dir or event.get("direction", "neutral")
    confidence = float(event.get("confidence", 0.5) or 0.5)
    event_type = event.get("event_type", "theme")

    # 基础影响幅度（基于事件类型经验值）
    base_map = {
        "policy": 2.5,       # 政策类影响较大
        "industry": 1.5,     # 行业类中等
        "earnings": 2.0,     # 业绩类较大
        "capital": 1.8,      # 资金类中等
        "macro": 1.0,        # 宏观类偏小
        "theme": 1.2,        # 主题类偏小
    }
    base_pct = base_map.get(event_type, 1.0)

    # 方向调整
    if "down" in direction or "neg" in direction:
        impact_pct = -base_pct
        impact_dir = "down"
    elif "up" in direction or "pos" in direction:
        impact_pct = base_pct
        impact_dir = "up"
    else:
        impact_pct = base_pct * 0.3  # 中性影响小
        impact_dir = "flat"

    # 置信度调整（置信度高的影响幅度大）
    impact_pct = round(impact_pct * (0.5 + confidence), 2)

    # 持续期：政策类 long，行业类 medium，其他 short
    duration_map = {
        "policy": "long_term",
        "industry": "medium_term",
        "macro": "medium_term",
        "earnings": "short_term",
        "capital": "short_term",
        "theme": "short_term",
    }
    impact_duration = duration_map.get(event_type, "short_term")

    impact_analysis = (
        f"基于规则估算（事件类型={event_type}, 方向={impact_dir}, "
        f"置信度={confidence:.2f}）：预估影响 {impact_pct}%，持续期 {impact_duration}"
    )

    return {
        "expected_impact_pct": impact_pct,
        "impact_direction": impact_dir,
        "impact_duration": impact_duration,
        "impact_analysis": impact_analysis,
    }


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

    # Batch1 增强点 3：事件影响量化字段（开关控制，默认关闭）
    try:
        impact_quant_enabled = get_config_bool("alerts.event_impact_quantification_enabled", False)
    except Exception:
        impact_quant_enabled = False

    impact_fields_prompt = ""
    if impact_quant_enabled:
        impact_fields_prompt = """
- expected_impact_pct: 预估影响幅度（正负数，如 3.5 表示预估涨 3.5%，-2.0 表示预估跌 2.0%）
- impact_direction: 影响方向（up=上涨 / down=下跌 / flat=无影响）
- impact_duration: 影响持续期（short_term=1-3天 / medium_term=1-2周 / long_term=超过2周）"""

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
- confidence: 置信度（0-1，1 表示高度确定会发生）{impact_fields_prompt}

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
        try:
            events = json.loads(raw)
        except json.JSONDecodeError as je:
            # P1-3 修复：首次解析失败时，二次重试（提高 temperature，加更强约束）
            logger.warning(
                f"[event_radar:{trace_id}] LLM 首次解析失败({je})，二次重试"
            )
            logger.debug(f"[event_radar:{trace_id}] 首次原始响应: {raw[:300]}")
            retry_prompt = (
                prompt
                + "\n\n【重要】上次返回的内容不是合法 JSON 数组，请严格只输出 "
                "JSON 数组，以 [ 开头、] 结尾，不要任何其他文字、代码块或解释。"
            )
            retry_resp = _call_llm(
                caller="event_radar_extractor_retry",
                trace_id=trace_id,
                model=MODEL,
                messages=[{"role": "user", "content": retry_prompt}],
                temperature=0.0,  # 更确定性的输出
                max_tokens=4000,
            )
            raw = (retry_resp.choices[0].message.content or "").strip()
            # 再次清理代码块
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            if not raw.startswith("["):
                start = raw.find("[")
                end = raw.rfind("]")
                if start != -1 and end != -1 and end > start:
                    raw = raw[start:end + 1]
            events = json.loads(raw)
        if not isinstance(events, list):
            return []
    except Exception as e:
        logger.warning(f"[event_radar:{trace_id}] LLM 事件提取失败(含重试): {e}")
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
        # P0-F 修复：增加最小关键词命中数检查，避免单"科技"匹配"生物医药科技"
        if not matched_holdings and name_keywords:
            min_hits = _SECTOR_MIN_KEYWORD_HITS.get(sector, 1)
            for h in user_holdings:
                if h.get("fund_code") in matched_holding_codes:
                    continue
                h_index_name = (h.get("index_name") or "").strip()
                h_fund_name = (h.get("fund_name") or "").strip()
                # ── P0-J: 反向校验 ──
                # 避免医药基金（含"医药/医疗/生物"）被关联到科技事件
                if _should_exclude_for_sector(sector, h_fund_name, h_index_name):
                    continue
                # P0-F: 统计命中的关键词数
                hit_kws = [kw for kw in name_keywords
                           if kw in h_index_name or kw in h_fund_name]
                if len(hit_kws) >= min_hits and hit_kws:
                    matched_holdings.append({
                        "fund_code": h.get("fund_code", ""),
                        "fund_name": h.get("fund_name", ""),
                        "match_type": "holding",
                        "match_reason": f"持仓名称含 {sector} 关键词 '{','.join(hit_kws[:3])}'" + (
                            f"（命中 {len(hit_kws)} 个）" if len(hit_kws) > 1 else ""
                        ),
                    })
                    matched_holding_codes.add(h.get("fund_code"))

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
            # P0-F 修复：同样应用最小命中数检查
            if not matched_watchlist and name_keywords:
                min_hits = _SECTOR_MIN_KEYWORD_HITS.get(sector, 1)
                for w in user_watchlist:
                    if w.get("fund_code") in matched_watchlist_codes:
                        continue
                    w_index_name = (w.get("index_name") or "").strip()
                    w_fund_name = (w.get("fund_name") or "").strip()
                    # ── P0-J: 反向校验（关注列表同样适用）──
                    if _should_exclude_for_sector(sector, w_fund_name, w_index_name):
                        continue
                    hit_kws = [kw for kw in name_keywords
                               if kw in w_index_name or kw in w_fund_name]
                    if len(hit_kws) >= min_hits and hit_kws:
                        matched_watchlist.append({
                            "fund_code": w.get("fund_code", ""),
                            "fund_name": w.get("fund_name", ""),
                            "match_reason": f"关注基金名称含 {sector} 关键词 '{','.join(hit_kws[:3])}'",
                        })
                        matched_watchlist_codes.add(w.get("fund_code"))

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
            # ── P0-G 修复：写入 expired_date 字段 ──
            # 原bug：只更新 status，未写 expired_date，导致 _time_decay_factor() 计算时
            # expired_date 为 NULL，fallback 到 0.5，时间衰减功能完全失效
            # 修复：同步写入 expired_date，让 Batch2 时间衰减能正确计算
            try:
                from db.market_events import update_market_event_fields
                update_market_event_fields(ev["event_id"], {"expired_date": today})
            except Exception as e:
                logger.warning(f"[event_radar] 写入 expired_date 失败 {ev.get('event_id')}: {e}")
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
        # P0-E 修复（2026-07-20）：按事件主题过滤相关 sources
        # 原问题：所有事件塞同一批 news[:3]，导致 sources 张冠李戴
        sources = _filter_sources_by_relevance(ev, news)
        try:
            from db.market_events import get_market_event, _gen_event_id
            expected_date = ev.get("expected_date", "")
            
            eid = _gen_event_id(ev["title"], expected_date)
            existing = get_market_event(eid)
            
            is_trend = ev.get("time_frame") or ev.get("evidence")
            # P0-3: 趋势事件也参与校准（移除豁免），校准后置信度更真实
            conf = _calibrate_confidence(
                float(ev.get("confidence", 0.5)),
                ev.get("affected_sectors", []),
            )
            # P1-1: 方向错误惩罚 — 高错误率板块方向降级
            calibrated_dir, _dir_reason = _calibrate_direction(
                ev.get("direction", "neutral"),
                ev.get("affected_sectors", []),
            )
            original_conf = float(ev.get("confidence", 0.5))
            original_dir = ev.get("direction", "neutral")
            
            create_market_event(
                title=ev["title"],
                summary=ev.get("summary", ""),
                event_type=ev.get("event_type", "theme"),
                direction=calibrated_dir,  # P1-1: 使用校准后的方向
                expected_date=expected_date,
                affected_sectors=ev.get("affected_sectors", []),
                affected_themes=ev.get("affected_themes", []),
                confidence=conf,
                sources=sources,
                time_frame=ev.get("time_frame", ""),
                evidence=ev.get("evidence", ""),
                original_confidence=original_conf,  # P1-2: 保留原始置信度供前端展示
                original_direction=original_dir,    # 保留原始方向
            )
            # Batch1 增强点 3：写入影响量化字段（仅开关开启且 LLM 返回了数据时）
            try:
                impact_quant_enabled = get_config_bool("alerts.event_impact_quantification_enabled", False)
            except Exception:
                impact_quant_enabled = False
            if impact_quant_enabled:
                impact_fields = {}
                if ev.get("expected_impact_pct") is not None:
                    try:
                        impact_fields["expected_impact_pct"] = float(ev.get("expected_impact_pct"))
                    except (TypeError, ValueError):
                        pass
                if ev.get("impact_direction"):
                    impact_fields["impact_direction"] = ev.get("impact_direction")
                if ev.get("impact_duration"):
                    impact_fields["impact_duration"] = ev.get("impact_duration")
                if impact_fields:
                    try:
                        from db.market_events import update_market_event_fields as _update_fields
                        _update_fields(eid, impact_fields)
                    except Exception as _e:
                        logger.debug(f"[event_radar:{trace_id}] 影响量化字段写入失败: {_e}")

            # P1-C 修复（2026-07-20）：影响量化字段规则化兜底
            # 原问题：alerts.event_impact_quantification_enabled 默认关闭 + LLM 可能不返回 → 字段全 None
            # 修复：开关关闭或 LLM 没返回时，基于事件类型+方向用规则填充
            try:
                rule_based_enabled = get_config_bool("alerts.event_impact_rule_based_enabled", True)
            except Exception:
                rule_based_enabled = True
            if rule_based_enabled and not existing:
                rule_impact = _analyze_event_impact_by_rule(ev, calibrated_dir)
                if rule_impact:
                    try:
                        from db.market_events import update_market_event_fields as _update_fields
                        _update_fields(eid, rule_impact)
                    except Exception as _e:
                        logger.debug(f"[event_radar:{trace_id}] 规则化影响字段写入失败: {_e}")
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
    1. 遍历 affected_sectors 所有板块，对每个有指数映射的板块分别验证
    2. 获取 materialized_date 和 materialized_date+window_days 的收盘价
    3. 计算各板块涨跌幅
    4. 多板块加权平均 + 多数投票判定最终方向

    Returns:
        验证结果 dict 或 None（无法验证时）
    """
    sectors = json.loads(event.get("affected_sectors") or "[]")
    if not sectors:
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

    direction = event.get("direction", "neutral")
    THRESHOLD = 1.0

    # 遍历所有板块，分别验证
    sector_results = []
    for s in sectors:
        key = _normalize_sector(s)
        if not key or key not in SECTOR_TO_INDEX:
            continue
        codes = SECTOR_TO_INDEX[key]
        if not codes:
            continue
        idx_code = codes[0]

        prices = _fetch_index_close_prices(idx_code, mat_date, end_date)
        if not prices or len(prices) < 2:
            continue

        sorted_dates = sorted(prices.keys())
        base_price = None
        for d in sorted_dates:
            if d >= mat_date:
                base_price = prices[d]
                break
        if base_price is None:
            continue

        verify_price = prices[sorted_dates[-1]]
        if verify_price == base_price:
            continue

        s_change = (verify_price - base_price) / base_price * 100

        # 单板块方向判定
        if abs(s_change) < THRESHOLD:
            s_status = "flat"
        elif direction == "positive" and s_change > 0:
            s_status = "correct"
        elif direction == "negative" and s_change < 0:
            s_status = "correct"
        elif direction == "neutral":
            # ── P0-H 修复：原逻辑直接判 flat，不看幅度 ──
            # 问题案例：SK海力士事件预测 neutral，实际跌 -16.36%，竟判为"平淡"
            # 修复策略：neutral 方向 + 涨跌幅超阈值（abs(s_change) >= 3%）应判为 wrong
            # （预测"无影响"但实际有大波动，说明预测错误）
            if abs(s_change) >= 3.0:
                s_status = "wrong"
            else:
                s_status = "flat"
        else:
            s_status = "wrong"

        sector_results.append({
            "sector": s,
            "index_code": idx_code,
            "index_name": key,
            "change_pct": round(s_change, 2),
            "status": s_status,
            "base_price": round(base_price, 2),
            "verify_price": round(verify_price, 2),
        })

    if not sector_results:
        return None

    # 多板块综合判定：多数投票 + 加权平均涨跌幅
    correct_count = sum(1 for r in sector_results if r["status"] == "correct")
    wrong_count = sum(1 for r in sector_results if r["status"] == "wrong")
    flat_count = sum(1 for r in sector_results if r["status"] == "flat")

    if correct_count > wrong_count:
        final_status = "correct"
    elif wrong_count > correct_count:
        final_status = "wrong"
    else:
        final_status = "flat"

    avg_change = sum(r["change_pct"] for r in sector_results) / len(sector_results)
    # 单板块时保留原逻辑（阈值判定）
    if len(sector_results) == 1:
        final_status = sector_results[0]["status"]

    # 取第一个板块作为主指数（兼容旧前端展示）
    primary = sector_results[0]

    return {
        "status": final_status,
        "change_pct": round(avg_change, 2),
        "verified_date": today,
        "index_code": primary["index_code"],
        "index_name": primary["index_name"],
        "direction_predicted": direction,
        "window_days": window_days,
        "base_price": primary["base_price"],
        "verify_price": primary["verify_price"],
        "sector_results": sector_results,
        "sector_summary": {
            "correct": correct_count,
            "wrong": wrong_count,
            "flat": flat_count,
            "total": len(sector_results),
        },
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

    # P0-2: 验证完成后回溯校准已存在的 upcoming/imminent 事件
    try:
        recal_counts = recalibrate_existing_events(trace_id)
        counts["recalibrated"] = recal_counts["recalibrated"]
        counts["confidence_changed"] = recal_counts["confidence_changed"]
        counts["direction_changed"] = recal_counts["direction_changed"]
    except Exception as e:
        logger.warning(f"[event_radar:{trace_id}] 回溯校准失败: {e}")
        counts["recalibrated"] = 0

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


def _calibrate_direction(original_direction: str, sectors: list) -> tuple[str, str]:
    """根据板块历史方向错误率校准事件方向。

    若某板块近 10 个验证事件中方向错误率 > 40%，将该板块的新事件方向降级为 neutral。

    Returns:
        (calibrated_direction, reason) — reason 为空字符串表示未降级
    """
    if original_direction == "neutral":
        return original_direction, ""

    stats = get_sector_accuracy_stats()
    by_sector = stats.get("by_sector", {})

    for s in sectors:
        key = _normalize_sector(s) or s
        s_stat = by_sector.get(key)
        if not s_stat or s_stat.get("total", 0) < 5:  # 最少 5 个样本才校准方向
            continue
        wrong_rate = s_stat.get("wrong", 0) / max(s_stat["total"], 1)
        if wrong_rate > 0.4:
            reason = f"板块「{key}」方向错误率 {wrong_rate:.0%}（{s_stat['wrong']}/{s_stat['total']}），降级为中性"
            return "neutral", reason

    return original_direction, ""


def recalibrate_existing_events(trace_id: str = "") -> dict:
    """回溯校准已存在的 upcoming/imminent 事件。

    在 verify_materialized_events 完成后调用，用最新的板块准确率重新校准
    尚未落地的事件的 confidence 和 direction。

    Returns:
        {"recalibrated": int, "confidence_changed": int, "direction_changed": int}
    """
    from db.market_events import list_active_events, update_market_event_fields
    from db.market_events import update_market_event_status

    trace_id = trace_id or datetime.now().strftime("%Y%m%d%H%M%S")
    active = list_active_events()
    if not active:
        return {"recalibrated": 0, "confidence_changed": 0, "direction_changed": 0}

    counts = {"recalibrated": 0, "confidence_changed": 0, "direction_changed": 0}
    for ev in active:
        try:
            sectors = json.loads(ev.get("affected_sectors") or "[]")
            if not sectors:
                continue

            old_conf = float(ev.get("confidence", 0.5))
            old_dir = ev.get("direction", "neutral")

            # 重新校准 confidence
            new_conf = _calibrate_confidence(old_conf, sectors)
            # 重新校准 direction
            new_dir, dir_reason = _calibrate_direction(old_dir, sectors)

            conf_changed = abs(new_conf - old_conf) > 0.05
            dir_changed = new_dir != old_dir

            if not conf_changed and not dir_changed:
                continue

            # 更新事件字段
            updates = {}
            if conf_changed:
                updates["confidence"] = new_conf
                counts["confidence_changed"] += 1
            if dir_changed:
                updates["direction"] = new_dir
                counts["direction_changed"] += 1

            update_market_event_fields(ev["event_id"], updates)

            # 追加 timeline
            timeline_msgs = []
            if conf_changed:
                timeline_msgs.append(f"回溯校准置信度：{old_conf:.0%}→{new_conf:.0%}")
            if dir_changed:
                timeline_msgs.append(f"回溯校准方向：{old_dir}→{new_dir}（{dir_reason}）")
            if timeline_msgs:
                update_market_event_status(ev["event_id"], ev.get("status", "upcoming"),
                                           timeline_note="；".join(timeline_msgs))

            counts["recalibrated"] += 1
        except Exception as e:
            logger.warning(f"[event_radar:{trace_id}] 回溯校准事件 {ev.get('event_id')} 失败: {e}")

    logger.info(f"[event_radar:{trace_id}] 回溯校准完成: {counts}")
    return counts


# ── Batch1 增强点 3：事件深度解读（LLM 个性化影响分析）──────────────

def analyze_event_impact(event_id: str, trace_id: str = "") -> dict:
    """LLM 深度解读事件影响，结合用户持仓生成个性化影响分析。

    - 开关：alerts.event_impact_analysis_enabled（默认 false）
    - 缓存：alerts.event_impact_analysis_cache_days（默认 7 天）
    - LLM 调用：传入事件标题/摘要/affected_sectors + 用户持仓 → 输出个性化影响分析

    Returns:
        {
            "event_id": str,
            "analysis": str,  # LLM 分析全文
            "analyzed_at": str,
            "cached": bool,   # 是否命中缓存
        }
    """
    try:
        enabled = get_config_bool("alerts.event_impact_analysis_enabled", False)
    except Exception:
        enabled = False
    if not enabled:
        return {"event_id": event_id, "analysis": "", "error": "深度解读开关未开启", "cached": False}

    # 查事件
    from db.market_events import get_market_event, update_market_event_fields
    event = get_market_event(event_id)
    if not event:
        return {"event_id": event_id, "analysis": "", "error": "事件不存在", "cached": False}

    # 缓存检查
    cache_days = get_config_int("alerts.event_impact_analysis_cache_days", 7)
    cached_analysis = event.get("impact_analysis")
    cached_at = event.get("impact_analyzed_at")
    if cached_analysis and cached_at:
        try:
            cached_dt = datetime.strptime(cached_at[:19], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - cached_dt).days < cache_days:
                return {
                    "event_id": event_id,
                    "analysis": cached_analysis,
                    "analyzed_at": cached_at,
                    "cached": True,
                }
        except Exception:
            pass  # 缓存解析失败，重新调用

    title = event.get("title", "")
    summary = event.get("summary", "")
    direction = event.get("direction", "neutral")
    sectors = json.loads(event.get("affected_sectors") or "[]")
    themes = json.loads(event.get("affected_themes") or "[]")
    expected_impact_pct = event.get("expected_impact_pct")
    impact_duration = event.get("impact_duration")

    # 查用户持仓
    try:
        from db.portfolio import list_holdings
        holdings = list_holdings() or []
    except Exception:
        holdings = []
    holdings_summary = "\n".join(
        f"- {h.get('fund_name','')}（{h.get('fund_code','')}）：占比 {h.get('weight',0):.1%}"
        for h in holdings[:10]
    ) or "暂无持仓"

    # 构造 prompt
    impact_hint = ""
    if expected_impact_pct is not None:
        impact_hint += f"\n- 预估影响幅度: {expected_impact_pct}%"
    if impact_duration:
        impact_hint += f"\n- 影响持续期: {impact_duration}"

    prompt = f"""你是资深投资顾问。请针对以下市场事件，结合用户当前持仓，生成个性化的影响分析。

【事件信息】
- 标题: {title}
- 摘要: {summary}
- 影响方向: {direction}
- 受影响板块: {", ".join(sectors)}
- 受影响主题: {", ".join(themes)}{impact_hint}

【用户当前持仓】
{holdings_summary}

【输出要求】
1. 用 markdown 格式输出
2. 包含以下章节：
   ### 事件核心
   一句话说明事件本质和影响时点

   ### 对用户持仓的影响
   - 逐个分析用户持仓中受此事件影响的基金（按受影响程度排序）
   - 标注影响程度（高/中/低）和方向（利好/利空/中性）
   - 给出具体的影响金额估算（基于持仓占比和预估影响幅度）

   ### 操作建议
   - 是否需要调仓？如果需要，给出 2-3 条具体建议
   - 短期（1周）和中期（1月）的应对策略

   ### 风险提示
   - 此事件的不确定性因素
   - 最坏情况下的应对预案

3. 不要使用"建议立即买卖"等绝对化表述
4. 最多 800 字
"""

    try:
        resp = _call_llm(
            caller="event_impact_analyzer",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2000,
        )
        analysis = (resp.choices[0].message.content or "").strip()
        if not analysis:
            return {"event_id": event_id, "analysis": "", "error": "LLM 返回空内容", "cached": False}
    except Exception as e:
        logger.warning(f"[event_radar:{trace_id}] 影响分析失败: {e}")
        return {"event_id": event_id, "analysis": "", "error": f"LLM 调用失败: {e}", "cached": False}

    # 写回 market_events 表（缓存）
    analyzed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        update_market_event_fields(event_id, {
            "impact_analysis": analysis,
            "impact_analyzed_at": analyzed_at,
        })
    except Exception as _e:
        logger.debug(f"[event_radar:{trace_id}] 影响分析缓存写入失败: {_e}")
    return {
        "event_id": event_id,
        "analysis": analysis,
        "analyzed_at": analyzed_at,
        "cached": False,
    }


# ── Batch2 增强点 2：事件影响金额估算 ───────────────────────────────────────

def estimate_event_impact_amount(event: dict, holdings: list = None,
                                  portfolio_total: float = None) -> dict:
    """估算事件对用户持仓的金额影响（纯计算，无 LLM 调用）。

    公式：影响金额 = expected_impact_pct × holding_value / 100

    Args:
        event: market_events 行（含 expected_impact_pct, affected_sectors 等）
        holdings: 用户持仓列表（list_holdings 输出）；None 时实时查询
        portfolio_total: 持仓总市值；None 时自动汇总

    Returns:
        {
            "event_id": str,
            "title": str,
            "total_impact_amount": float,    # 总影响金额（正=利好，负=利空）
            "impact_pct": float,              # 事件预估影响幅度
            "affected_holdings": [            # 受影响的持仓列表
                {
                    "fund_code": str,
                    "fund_name": str,
                    "weight": float,         # 持仓占比（%）
                    "holding_value": float,  # 持仓市值
                    "impact_pct": float,
                    "impact_amount": float,  # 影响金额
                    "match_reason": str,
                }
            ],
            "portfolio_total": float,
            "estimated_at": str,
            "reason": str,                   # 空数据时说明原因
        }
    """
    estimated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    event_id = event.get("event_id", "")
    title = event.get("title", "")

    impact_pct = event.get("expected_impact_pct")
    if impact_pct is None:
        return {
            "event_id": event_id, "title": title,
            "total_impact_amount": 0.0, "impact_pct": 0.0,
            "affected_holdings": [], "portfolio_total": 0.0,
            "estimated_at": estimated_at,
            "reason": "事件未启用影响量化（expected_impact_pct 为空）",
        }

    try:
        impact_pct = float(impact_pct)
    except (TypeError, ValueError):
        return {
            "event_id": event_id, "title": title,
            "total_impact_amount": 0.0, "impact_pct": 0.0,
            "affected_holdings": [], "portfolio_total": 0.0,
            "estimated_at": estimated_at,
            "reason": "expected_impact_pct 非法",
        }

    # 实时查询持仓
    if holdings is None:
        try:
            from db.portfolio import list_holdings
            holdings = list_holdings() or []
        except Exception as e:
            logger.warning(f"[event_radar] estimate_event_impact_amount 查询持仓失败: {e}")
            holdings = []

    if not holdings:
        return {
            "event_id": event_id, "title": title,
            "total_impact_amount": 0.0, "impact_pct": impact_pct,
            "affected_holdings": [], "portfolio_total": 0.0,
            "estimated_at": estimated_at,
            "reason": "用户无持仓",
        }

    # 自动计算 portfolio_total
    if portfolio_total is None:
        portfolio_total = sum(float(h.get("current_value") or 0) for h in holdings)

    if portfolio_total <= 0:
        return {
            "event_id": event_id, "title": title,
            "total_impact_amount": 0.0, "impact_pct": impact_pct,
            "affected_holdings": [], "portfolio_total": 0.0,
            "estimated_at": estimated_at,
            "reason": "持仓总市值 ≤ 0",
        }

    # 调用 _determine_relevance 判定受影响持仓
    # 注意：DB 中 affected_sectors/affected_themes 是 JSON 字符串，需先解析回 list
    event_for_match = dict(event)
    try:
        raw_sectors = event_for_match.get("affected_sectors")
        if isinstance(raw_sectors, str):
            event_for_match["affected_sectors"] = json.loads(raw_sectors or "[]")
        raw_themes = event_for_match.get("affected_themes")
        if isinstance(raw_themes, str):
            event_for_match["affected_themes"] = json.loads(raw_themes or "[]")
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        _, matched_holdings, _, _ = _determine_relevance(event_for_match, holdings)
    except Exception as e:
        logger.warning(f"[event_radar] estimate_event_impact_amount 判定关联失败: {e}")
        matched_holdings = []

    # 用 fund_code 关联回 holdings 拿 weight/holding_value
    holding_map = {h.get("fund_code"): h for h in holdings}
    affected = []
    for m in matched_holdings:
        fund_code = m.get("fund_code")
        h = holding_map.get(fund_code, {})
        current_value = float(h.get("current_value") or 0)
        if current_value <= 0:
            continue
        weight = (current_value / portfolio_total * 100) if portfolio_total > 0 else 0
        impact_amount = impact_pct / 100.0 * current_value
        affected.append({
            "fund_code": fund_code,
            "fund_name": h.get("fund_name") or m.get("fund_name") or "",
            "weight": round(weight, 2),
            "holding_value": round(current_value, 2),
            "impact_pct": impact_pct,
            "impact_amount": round(impact_amount, 2),
            "match_reason": m.get("match_reason", ""),
        })

    affected.sort(key=lambda x: abs(x["impact_amount"]), reverse=True)
    total = round(sum(a["impact_amount"] for a in affected), 2)

    return {
        "event_id": event_id,
        "title": title,
        "total_impact_amount": total,
        "impact_pct": impact_pct,
        "affected_holdings": affected,
        "portfolio_total": round(portfolio_total, 2),
        "estimated_at": estimated_at,
        "reason": "",
    }


# ── Batch2 增强点 3：事件置信度时间衰减 ─────────────────────────────────────

def _time_decay_factor(event: dict, now: datetime = None) -> float:
    """计算事件置信度的时间衰减因子。

    衰减规则：
    - status=upcoming/imminent/materialized：factor=1.0（不衰减）
    - status=expired & verification_result 非空：factor=1.0（已验证，不衰减）
    - status=expired & verification_result 为空 & 过期 ≤ 30 天：factor=0.7
    - status=expired & verification_result 为空 & 过期 > 30 天：factor=0.3
    - 其他异常情况：factor=0.5

    Args:
        event: market_events 行
        now: 当前时间（测试注入用）

    Returns:
        0.0 ~ 1.0 的衰减因子
    """
    if now is None:
        now = datetime.now()

    status = event.get("status", "upcoming")
    verification = event.get("verification_result")

    # 非 expired 状态不衰减
    if status != "expired":
        return 1.0

    # 已验证的过期事件不衰减
    if verification:
        return 1.0

    # 未验证的过期事件按过期天数衰减
    expired_date = event.get("expired_date")
    if not expired_date:
        return 0.5  # 无过期时间，默认衰减到 0.5

    try:
        exp_dt = datetime.strptime(str(expired_date)[:10], "%Y-%m-%d")
        days_since_expired = (now - exp_dt).days
        if days_since_expired <= 30:
            return 0.7
        else:
            return 0.3
    except Exception:
        return 0.5


def apply_time_decay_to_confidence(event: dict, now: datetime = None) -> float:
    """对事件置信度应用时间衰减，返回有效置信度。

    effective_confidence = original_confidence × time_decay_factor

    Args:
        event: market_events 行
        now: 当前时间（测试注入用）

    Returns:
        衰减后的有效置信度（0.0 ~ 1.0，保留 3 位小数）
    """
    try:
        original = float(event.get("confidence") or 0.5)
    except (TypeError, ValueError):
        original = 0.5
    factor = _time_decay_factor(event, now=now)
    return round(original * factor, 3)


def attach_effective_confidence(events: list[dict] | dict, now: datetime = None) -> None:
    """给事件列表（或单个事件）附加 effective_confidence 字段（in-place 修改）。

    开关 alerts.event_confidence_time_decay_enabled 控制是否附加：
    - 开启：附加 effective_confidence 字段
    - 关闭：不修改事件数据（保持原状）

    Args:
        events: list[dict] 或单个 dict
        now: 当前时间（测试注入用）
    """
    try:
        from db.config import get_config_bool
        enabled = get_config_bool("alerts.event_confidence_time_decay_enabled", False)
    except Exception:
        enabled = False

    if not enabled:
        return

    if isinstance(events, dict):
        events["effective_confidence"] = apply_time_decay_to_confidence(events, now=now)
    elif isinstance(events, list):
        for ev in events:
            if isinstance(ev, dict):
                ev["effective_confidence"] = apply_time_decay_to_confidence(ev, now=now)

