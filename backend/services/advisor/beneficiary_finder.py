"""事件受益标的发现引擎 — 从事件生成结构化受益标的清单。

三级发现策略：
1. 主题→基金（benefit_level=strong）：affected_themes 匹配 theme_rules.keywords
2. 板块→指数→基金（benefit_level=medium）：affected_sectors → SECTOR_TO_INDEX → _find_candidate_funds
3. 事件类型兜底（benefit_level=weak）：event_type + 关键词 → 兜底标的库

总开关：alerts.beneficiary_finder_enabled（默认 false），关闭时返回空列表。
"""
import json
import logging

from db.config import get_config_bool
from db.theme_rules import list_theme_rules
from services.market.event_radar import SECTOR_TO_INDEX, _find_candidate_funds

logger = logging.getLogger(__name__)

# 总开关
_BENEFICIARY_FINDER_SWITCH = "alerts.beneficiary_finder_enabled"

# benefit_level 级别权重（去重时保留最高级别）
_LEVEL_RANK = {"strong": 3, "medium": 2, "weak": 1}

# 兜底标的库（event_type, keyword）→ [(fund_code, fund_name, index_code, index_name, vehicle_type)]
_EVENT_TYPE_FALLBACK = {
    ("policy", "芯片"): [("159995", "芯片ETF", "H30184", "中证全指半导体", "etf")],
    ("policy", "半导体"): [("159995", "芯片ETF", "H30184", "中证全指半导体", "etf")],
    ("policy", "新能源"): [("516160", "新能源ETF", "399808", "中证新能", "etf")],
    ("policy", "光伏"): [("516160", "新能源ETF", "399808", "中证新能", "etf")],
    ("policy", "人工智能"): [("159819", "人工智能ETF", "931071", "CS人工智能", "etf")],
    ("policy", "AI"): [("159819", "人工智能ETF", "931071", "CS人工智能", "etf")],
    ("policy", "红利"): [("009051", "易方达中证红利ETF联接A", "000922", "中证红利", "otc_fund")],
    ("industry", "医药"): [("512010", "医药ETF", "931140", "医药50", "etf")],
    ("industry", "消费"): [("159928", "消费ETF", "399997", "中证白酒", "etf")],
    ("macro", "利率"): [("014846", "博时恒乐债券A", "", "", "otc_fund")],
}


def discover_beneficiaries(event: dict, user_id: str = "default") -> list[dict]:
    """从事件生成结构化受益标的清单。

    三级发现策略：
    1. 主题→基金（benefit_level=strong）：affected_themes 匹配 theme_rules.keywords
    2. 板块→指数→基金（benefit_level=medium）：affected_sectors → SECTOR_TO_INDEX → _find_candidate_funds
    3. 事件类型兜底（benefit_level=weak）：event_type + 关键词 → 兜底标的库

    每个受益标的 dict 结构：
    {
        "fund_code": "159995",
        "fund_name": "芯片ETF",
        "index_code": "H30184",
        "index_name": "中证全指半导体",
        "vehicle_type": "etf",
        "benefit_level": "strong",
        "benefit_logic": "国产替代加速，半导体供应链订单受益",
        # Phase 2 填充的字段先留空
        "valuation_percentile": None,
        "valuation_status": "unknown",
        "is_holding": 0,
        "is_watching": 0,
        "recommendation_tier": None,
        "match_score": 0,
    }
    """
    # 总开关检查
    try:
        if not get_config_bool(_BENEFICIARY_FINDER_SWITCH, False):
            return []
    except Exception:
        return []

    if not event or not isinstance(event, dict):
        return []

    event_id = event.get("event_id", "")
    title = event.get("title", "") or ""
    summary = event.get("summary", "") or ""
    event_type = event.get("event_type", "") or ""
    affected_sectors = _parse_json_field(event.get("affected_sectors", []))
    affected_themes = _parse_json_field(event.get("affected_themes", []))

    # fund_code -> beneficiary dict（去重用）
    bucket: dict[str, dict] = {}

    # ── 策略1：主题 → 基金（benefit_level=strong）──
    try:
        rules = list_theme_rules(active_only=True)
    except Exception as e:
        logger.warning(f"[beneficiary_finder] 加载主题规则失败: {e}")
        rules = []

    for rule in rules:
        theme_name = rule.get("theme", "") or ""
        keywords = rule.get("keywords", []) or []
        # 命中判定：affected_themes 命中 theme，或 title/summary 命中 keyword
        hit = False
        if theme_name and theme_name in affected_themes:
            hit = True
        if not hit:
            text = f"{title} {summary}"
            for kw in keywords:
                if kw and kw in text:
                    hit = True
                    break
        if not hit:
            continue

        funds = rule.get("funds", []) or []
        for f in funds:
            fund_code = f.get("fund_code", "")
            if not fund_code:
                continue
            item = _new_beneficiary(
                fund_code=fund_code,
                fund_name=f.get("fund_name", ""),
                index_code=rule.get("index_code", "") or "",
                index_name=f.get("index_name", ""),
                vehicle_type=f.get("vehicle_type", ""),
                benefit_level="strong",
                benefit_logic=_gen_logic(event_type, f"主题【{theme_name}】政策利好，配置型受益"),
            )
            _merge(bucket, item)

    # ── 策略2：板块 → 指数 → 基金（benefit_level=medium）──
    for sector in affected_sectors:
        index_codes = SECTOR_TO_INDEX.get(sector, [])
        for index_code in index_codes:
            try:
                candidates = _find_candidate_funds(index_code)
            except Exception as e:
                logger.warning(
                    f"[beneficiary_finder] 查候选基金失败 sector={sector} index={index_code}: {e}"
                )
                candidates = []
            for c in candidates:
                fund_code = c.get("fund_code", "")
                if not fund_code:
                    continue
                item = _new_beneficiary(
                    fund_code=fund_code,
                    fund_name=c.get("fund_name", ""),
                    index_code=index_code,
                    index_name="",
                    vehicle_type=c.get("fund_type", "") or "",
                    benefit_level="medium",
                    benefit_logic=_gen_logic(event_type, f"板块【{sector}】事件催化，指数 {index_code} 跟踪标的受益"),
                )
                _merge(bucket, item)

    # ── 策略3：event_type 兜底（benefit_level=weak）──
    for (et, kw), fund_tuples in _EVENT_TYPE_FALLBACK.items():
        if et != event_type:
            continue
        # 关键词命中 title/summary/affected_themes
        hit = False
        if kw in title or kw in summary:
            hit = True
        if kw in affected_themes:
            hit = True
        if not hit:
            continue
        for ft in fund_tuples:
            fund_code, fund_name, idx_code, idx_name, vtype = ft
            item = _new_beneficiary(
                fund_code=fund_code,
                fund_name=fund_name,
                index_code=idx_code,
                index_name=idx_name,
                vehicle_type=vtype,
                benefit_level="weak",
                benefit_logic=_gen_logic(event_type, f"事件类型兜底标的【{kw}】"),
            )
            _merge(bucket, item)

    beneficiaries = list(bucket.values())
    strong_n = sum(1 for b in beneficiaries if b["benefit_level"] == "strong")
    medium_n = sum(1 for b in beneficiaries if b["benefit_level"] == "medium")
    weak_n = sum(1 for b in beneficiaries if b["benefit_level"] == "weak")
    logger.info(
        f"[beneficiary_finder] event_id={event_id} 发现 {len(beneficiaries)} 个受益标的 "
        f"(strong={strong_n}, medium={medium_n}, weak={weak_n})"
    )
    return beneficiaries


def _parse_json_field(val) -> list:
    """解析可能为 JSON 字符串或 list 的字段。"""
    if not val:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _new_beneficiary(
    fund_code: str,
    fund_name: str,
    index_code: str,
    index_name: str,
    vehicle_type: str,
    benefit_level: str,
    benefit_logic: str,
) -> dict:
    """构造受益标的 dict（Phase 2 字段留空）。"""
    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "index_code": index_code,
        "index_name": index_name,
        "vehicle_type": vehicle_type,
        "benefit_level": benefit_level,
        "benefit_logic": benefit_logic,
        "valuation_percentile": None,
        "valuation_status": "unknown",
        "is_holding": 0,
        "is_watching": 0,
        "recommendation_tier": None,
        "match_score": 0,
    }


def _merge(bucket: dict, item: dict) -> None:
    """合并受益标的，同 fund_code 保留 benefit_level 最高的。"""
    code = item["fund_code"]
    existing = bucket.get(code)
    if existing is None:
        bucket[code] = item
        return
    if _LEVEL_RANK.get(item["benefit_level"], 0) > _LEVEL_RANK.get(existing["benefit_level"], 0):
        # 保留更高级别
        bucket[code] = item
    # 否则丢弃较低级别


def _gen_logic(event_type: str, detail: str) -> str:
    """按 event_type 拼接利好逻辑前缀。"""
    prefix = {
        "policy": "政策利好",
        "industry": "产业利好",
        "macro": "宏观利好",
    }.get(event_type, "事件利好")
    return f"{prefix}：{detail}"
