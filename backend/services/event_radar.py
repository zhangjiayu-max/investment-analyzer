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


def _collect_news() -> list[dict]:
    """采集最近 24 小时财经新闻（多源融合 + 去重）。

    数据源优先级：盈米 MCP → 东财妙想 → akshare（当前仅实现盈米）
    单次最多 50 条，跨源去重。
    """
    max_news = 50
    try:
        news = _fetch_news_from_mcp(limit=max_news)
    except Exception as e:
        logger.warning(f"[event_radar] 新闻采集异常: {e}")
        news = []

    if not news:
        logger.info("[event_radar] 未采集到新闻，跳过本次扫描")
        return []

    # 去重：按标题相似度（简化版：完全一致去重）
    seen_titles = set()
    unique = []
    for n in news:
        title = n.get("news_title", "").strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            unique.append(n)

    logger.info(f"[event_radar] 采集新闻 {len(news)} 条，去重后 {len(unique)} 条")
    return unique[:max_news]


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
- affected_sectors: 受影响板块（数组，从以下选取：{known_sectors}）
- affected_themes: 受影响主题（数组，自由文本如"国产替代""火箭回收"）
- confidence: 置信度（0-1，1 表示高度确定会发生）

【过滤规则】
1. 只提取"即将发生"的事件，不提取"已经发生"的新闻
2. 跳过模糊时间（如"近期""未来"），必须能推断到具体日期
3. 跳过无市场影响的事件（如纯娱乐八卦）
4. 单条新闻可提取 0-2 个事件，最多输出 {max_events} 个事件
5. expected_date 必须在 [{today}, {future_limit}] 范围内

只输出 JSON 数组，不要其他解释。"""

    try:
        resp = _call_llm(
            caller="event_radar_extractor",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # 容错：剥离可能的 markdown 代码块包裹
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        events = json.loads(raw)
        if not isinstance(events, list):
            return []
    except Exception as e:
        logger.warning(f"[event_radar:{trace_id}] LLM 事件提取失败: {e}")
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

    复用 fund_metadata.tracking_index 列做精确匹配。
    """
    exclude_codes = exclude_codes or set()
    conn = _get_conn()
    try:
        # 检查 fund_metadata 表是否存在
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fund_metadata'"
        ).fetchone()
        if not row:
            return []

        # 动态构造 NOT IN 占位
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
        return [
            {
                "fund_code": r["fund_code"],
                "fund_name": r["fund_name"],
                "fund_type": r["fund_type"],
                "match_reason": f"跟踪指数 {index_code}",
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"[event_radar] 查询候选基金失败 index={index_code}: {e}")
        return []
    finally:
        conn.close()


def _determine_relevance(
    event: dict,
    user_holdings: list[dict],
) -> tuple[str, list[dict], list[dict]]:
    """判定推送分级。

    Args:
        event: 事件 dict（含 affected_sectors）
        user_holdings: 用户持仓 [{fund_code, fund_name, index_code}, ...]

    Returns:
        (relevance, matched_holdings, candidate_funds)
        relevance: holding_impact / opportunity / market_watch
    """
    affected_sectors = event.get("affected_sectors", [])
    if not affected_sectors:
        return "market_watch", [], []

    matched_holdings = []
    candidate_funds = []
    holding_codes = {h.get("fund_code") for h in user_holdings}

    for sector in affected_sectors:
        index_codes = SECTOR_TO_INDEX.get(sector, [])
        for idx_code in index_codes:
            # 1. 检查持仓基金是否跟踪该指数
            for h in user_holdings:
                h_index = h.get("index_code") or ""
                # 归一化比较（剥离 .SZ/.SH 后缀）
                from services.index_fund_mapper import _normalize_index_code
                if _normalize_index_code(h_index) == _normalize_index_code(idx_code):
                    matched_holdings.append({
                        "fund_code": h.get("fund_code", ""),
                        "fund_name": h.get("fund_name", ""),
                        "match_reason": f"跟踪 {sector} 相关指数 {idx_code}",
                    })

            # 2. 若无持仓命中，收集候选建仓基金
            if not matched_holdings:
                cands = _find_candidate_funds(idx_code, exclude_codes=holding_codes)
                # 去重（多个 sector 可能命中同一基金）
                existing_codes = {c["fund_code"] for c in candidate_funds}
                for c in cands:
                    if c["fund_code"] not in existing_codes:
                        candidate_funds.append(c)

    if matched_holdings:
        return "holding_impact", matched_holdings, []
    elif candidate_funds:
        max_cands = get_config_int("alerts.event_radar_max_candidate_funds", 5)
        return "opportunity", [], candidate_funds[:max_cands]
    else:
        return "market_watch", [], []


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
    3. LLM 提取未来事件
    4. 写入 market_events 表（去重）
    5. 状态流转扫描
    6. 板块匹配 + 3 级分级
    7. 生成 alert

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

    # 2. LLM 提取
    events = _extract_events_from_news(news, trace_id=trace_id)
    if not events:
        return {"extracted": 0, "new": 0, "updated": 0, "alerts_created": 0, "reason": "no_events"}

    # 3. 写入 market_events 表（幂等）
    from db.market_events import create_market_event, update_event_relevance
    new_count = 0
    for ev in events:
        sources = [
            {"title": n.get("news_title", ""), "url": n.get("news_url", ""),
             "publish_date": n.get("published_at", "")}
            for n in news[:3]  # 最多关联 3 条来源
        ]
        try:
            # 检查是否已存在
            from db.market_events import get_market_event, _gen_event_id
            eid = _gen_event_id(ev["title"], ev["expected_date"])
            existing = get_market_event(eid)
            create_market_event(
                title=ev["title"],
                summary=ev.get("summary", ""),
                event_type=ev.get("event_type", "theme"),
                direction=ev.get("direction", "neutral"),
                expected_date=ev["expected_date"],
                affected_sectors=ev.get("affected_sectors", []),
                affected_themes=ev.get("affected_themes", []),
                confidence=float(ev.get("confidence", 0.5)),
                sources=sources,
            )
            if not existing:
                new_count += 1
        except Exception as e:
            logger.warning(f"[event_radar:{trace_id}] 写入事件失败 '{ev.get('title','')}': {e}")

    # 4. 状态流转
    status_counts = _update_event_statuses()

    # 5. 板块匹配 + 分级 + 生成 alert
    from db.portfolio import list_holdings, create_alert
    holdings = list_holdings()
    alerts_created = 0

    # 对所有 upcoming/imminent 事件重新计算分级
    from db.market_events import list_active_events
    active = list_active_events()
    for ev_row in active:
        try:
            affected = json.loads(ev_row.get("affected_sectors") or "[]")
            event_dict = {"affected_sectors": affected}
            relevance, matched, candidates = _determine_relevance(event_dict, holdings)

            # 更新事件的分级字段
            update_event_relevance(ev_row["event_id"], relevance, matched, candidates)

            # 仅对新生成的事件（本次扫描首次检测）生成 alert，避免重复推送
            ev_id = ev_row["event_id"]
            ev_detected = ev_row.get("detected_date", "")
            today = datetime.now().strftime("%Y-%m-%d")
            if ev_detected != today:
                continue  # 不是今天首次检测，不重复推送

            # 构造 alert
            severity = {
                "holding_impact": "warning",
                "opportunity": "info",
                "market_watch": "info",
            }.get(relevance, "info")

            title_prefix = {"holding_impact": "持仓影响", "opportunity": "建仓机会",
                            "market_watch": "市场关注"}.get(relevance, "市场关注")
            alert_title = f"[{title_prefix}] {ev_row['title']}"
            content_parts = [f"预期日期：{ev_row.get('expected_date','')}"]
            if matched:
                codes = [m["fund_name"] for m in matched[:3]]
                content_parts.append(f"关联持仓：{', '.join(codes)}")
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
