"""KYC 理财画像 — 问卷题库 + 画像读写 + 画像转文本

借鉴私人银行 KYC（Know Your Customer）理念，构建投资专属画像：
- risk_tolerance        风险偏好（保守|稳健|平衡|进取|激进）
- investment_horizon    投资期限（short|medium|long）
- capital_scale         资金体量（small|medium|large）
- investment_experience 投资经验（novice|intermediate|advanced|professional）
- loss_tolerance        亏损承受度（low|medium|high）
- focus_assets          关注品种（JSON 数组）

画像作为"懂你"的基石，贯穿专家 prompt、工具选择、RAG 重排。
"""

import json
import logging

logger = logging.getLogger(__name__)


# ── 维度元信息（供前端渲染 + 后端校验）──────────────────

DIMENSION_META = {
    "risk_tolerance": {
        "label": "风险偏好",
        "options": [
            {"value": "conservative", "label": "保守", "desc": "优先保本，难以接受亏损"},
            {"value": "steady", "label": "稳健", "desc": "可接受小幅波动换取稳定收益"},
            {"value": "balanced", "label": "平衡", "desc": "愿意承担中等波动博取中等收益"},
            {"value": "aggressive", "label": "进取", "desc": "愿意承担较大波动博取较高收益"},
            {"value": "radical", "label": "激进", "desc": "追求高收益，能承受大幅波动"},
        ],
    },
    "investment_horizon": {
        "label": "投资期限",
        "options": [
            {"value": "short", "label": "短期（<1年）", "desc": "资金近期可能动用"},
            {"value": "medium", "label": "中期（1-3年）", "desc": "资金中期闲置"},
            {"value": "long", "label": "长期（>3年）", "desc": "资金长期不动"},
        ],
    },
    "capital_scale": {
        "label": "资金体量",
        "options": [
            {"value": "small", "label": "小（<10万）", "desc": ""},
            {"value": "medium", "label": "中（10-100万）", "desc": ""},
            {"value": "large", "label": "大（>100万）", "desc": ""},
        ],
    },
    "investment_experience": {
        "label": "投资经验",
        "options": [
            {"value": "novice", "label": "新手", "desc": "刚入门，了解有限"},
            {"value": "intermediate", "label": "进阶", "desc": "有几年经验，熟悉常见品种"},
            {"value": "advanced", "label": "资深", "desc": "长期投资，理解多种策略"},
            {"value": "professional", "label": "专业", "desc": "具备专业背景或从业经验"},
        ],
    },
    "loss_tolerance": {
        "label": "亏损承受度（单笔最大可接受亏损）",
        "options": [
            {"value": "low", "label": "低（<5%）", "desc": "几乎不能接受亏损"},
            {"value": "medium", "label": "中（5-15%）", "desc": "可接受一定幅度亏损"},
            {"value": "high", "label": "高（>15%）", "desc": "能承受较大亏损等待回本"},
        ],
    },
    "focus_assets": {
        "label": "关注品种（可多选）",
        "multiple": True,
        "options": [
            {"value": "index", "label": "指数"},
            {"value": "fund", "label": "基金"},
            {"value": "bond", "label": "债券"},
            {"value": "stock", "label": "股票"},
            {"value": "gold", "label": "黄金/商品"},
            {"value": "cash", "label": "现金/货币"},
        ],
    },
}

# ── 问卷题库 ──────────────────────────────────

KYC_QUESTIONS = [
    {
        "id": "risk_tolerance",
        "dimension": "risk_tolerance",
        "question": "如果你的投资组合在一个月内下跌了 20%，你会怎么做？",
        "help": "这反映你的主观风险承受意愿",
    },
    {
        "id": "loss_tolerance",
        "dimension": "loss_tolerance",
        "question": "单笔投资你能接受的最大亏损幅度是多少？",
        "help": "这反映你的客观亏损承受能力",
    },
    {
        "id": "investment_horizon",
        "dimension": "investment_horizon",
        "question": "这笔投资资金大概多久内不会动用？",
        "help": "投资期限决定策略选择",
    },
    {
        "id": "capital_scale",
        "dimension": "capital_scale",
        "question": "你计划投入的资金大致规模？",
        "help": "仅用于适配建议仓位，不会存储精确金额",
    },
    {
        "id": "investment_experience",
        "dimension": "investment_experience",
        "question": "你的投资经验处于哪个阶段？",
        "help": "决定建议的复杂度",
    },
    {
        "id": "focus_assets",
        "dimension": "focus_assets",
        "question": "你主要关注哪些投资品种？（可多选）",
        "help": "决定重点分析哪些品种",
    },
]

KYC_DIMENSIONS = ["risk_tolerance", "investment_horizon", "capital_scale",
                  "investment_experience", "loss_tolerance", "focus_assets"]


def get_kyc_questionnaire() -> dict:
    """返回问卷题库 + 维度元信息（供前端渲染）。"""
    return {
        "questions": KYC_QUESTIONS,
        "dimensions": DIMENSION_META,
    }


# ── 画像读写 ──────────────────────────────────


def get_kyc_profile(user_id: str = "default") -> dict:
    """获取结构化 KYC 画像。

    返回: {dimension: value, ..., "kyc_completed": bool, "kyc_completed_at": str}
    """
    from db import get_user_profile

    profile = get_user_profile(user_id) or {}
    result = {}
    for dim in KYC_DIMENSIONS:
        val = profile.get(dim, "")
        if dim == "focus_assets" and val:
            try:
                val = json.loads(val) if isinstance(val, str) else val
            except (json.JSONDecodeError, TypeError):
                val = []
        result[dim] = val
    result["kyc_completed"] = bool(profile.get("kyc_completed", 0))
    result["kyc_completed_at"] = profile.get("kyc_completed_at", "")
    result["kyc_version"] = profile.get("kyc_version", 0)
    result["kyc_source"] = profile.get("kyc_source", "")
    return result


def submit_kyc_answers(user_id: str = "default", answers: dict = None,
                       source: str = "questionnaire") -> dict:
    """提交问卷答案，写入 user_profiles 并标记完成。

    answers: {dimension: value}，focus_assets 为 list，其它为 str
    """
    from datetime import datetime
    from db import update_user_profile

    answers = answers or {}
    fields = {}
    for dim in KYC_DIMENSIONS:
        if dim not in answers:
            continue
        val = answers[dim]
        if dim == "focus_assets":
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    val = [val] if val else []
            fields[dim] = json.dumps(val, ensure_ascii=False)
        else:
            fields[dim] = val if isinstance(val, str) else str(val)

    # 标记完成 + 版本递增
    fields["kyc_completed"] = 1
    fields["kyc_completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields["kyc_source"] = source
    prev = get_kyc_profile(user_id)
    fields["kyc_version"] = int(prev.get("kyc_version", 0)) + 1

    update_user_profile(user_id, **fields)
    logger.info(f"KYC 画像已更新 (user_id={user_id}, source={source}, version={fields['kyc_version']})")
    return get_kyc_profile(user_id)


def update_kyc_dimension(user_id: str = "default", dimension: str = "", value=None,
                         source: str = "conversation", confidence: float = 0.5) -> bool:
    """更新单个 KYC 维度（用于对话中持续学习 / 手动修改）。

    留痕到 user_preference_learnings，高置信度(>=0.7)直接回写 user_profiles。
    """
    if dimension not in KYC_DIMENSIONS or value is None or value == "":
        return False

    from db import update_user_profile, _get_conn

    # 1. 留痕（所有更新都记录，便于追溯）
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO user_preference_learnings
               (user_id, preference_key, preference_value, source, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, f"kyc_{dimension}",
             json.dumps(value, ensure_ascii=False) if dimension == "focus_assets" else str(value),
             source, confidence)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"KYC 留痕失败: {e}")

    # 2. 高置信度直接回写主画像
    if confidence >= 0.7:
        try:
            val = json.dumps(value, ensure_ascii=False) if dimension == "focus_assets" else str(value)
            update_user_profile(user_id, **{dimension: val, "kyc_source": source})
            return True
        except Exception as e:
            logger.error(f"KYC 回写失败: {e}")
    return False


# ── 画像 → 文本（注入专家 prompt 用）──────────────────

_DIMENSION_LABELS = {
    "risk_tolerance": "风险偏好",
    "investment_horizon": "投资期限",
    "capital_scale": "资金体量",
    "investment_experience": "投资经验",
    "loss_tolerance": "亏损承受度",
    "focus_assets": "关注品种",
}
_RISK_LABELS = {"conservative": "保守", "steady": "稳健", "balanced": "平衡", "aggressive": "进取", "radical": "激进"}
_HORIZON_LABELS = {"short": "短期(<1年)", "medium": "中期(1-3年)", "long": "长期(>3年)"}
_SCALE_LABELS = {"small": "小(<10万)", "medium": "中(10-100万)", "large": "大(>100万)"}
_EXP_LABELS = {"novice": "新手", "intermediate": "进阶", "advanced": "资深", "professional": "专业"}
_LOSS_LABELS = {"low": "低(<5%)", "medium": "中(5-15%)", "high": "高(>15%)"}
_ASSET_LABELS = {"index": "指数", "fund": "基金", "bond": "债券", "stock": "股票", "gold": "黄金", "cash": "现金"}


def kyc_profile_to_text(user_id: str = "default", dimensions: list = None) -> str:
    """把 KYC 画像渲染成可注入 prompt 的 <kyc_profile> 文本段。

    dimensions: 只渲染指定维度（按专家职责裁剪）；None=全部。
    返回空串表示无可用画像（调用方应跳过注入）。
    """
    profile = get_kyc_profile(user_id)
    dims = dimensions or KYC_DIMENSIONS
    parts = []
    for dim in dims:
        val = profile.get(dim, "")
        if not val:
            continue
        label = _DIMENSION_LABELS.get(dim, dim)
        if dim == "risk_tolerance":
            val_txt = _RISK_LABELS.get(val, val)
        elif dim == "investment_horizon":
            val_txt = _HORIZON_LABELS.get(val, val)
        elif dim == "capital_scale":
            val_txt = _SCALE_LABELS.get(val, val)
        elif dim == "investment_experience":
            val_txt = _EXP_LABELS.get(val, val)
        elif dim == "loss_tolerance":
            val_txt = _LOSS_LABELS.get(val, val)
        elif dim == "focus_assets":
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    val = []
            val_txt = "、".join(_ASSET_LABELS.get(a, a) for a in val) if val else ""
        else:
            val_txt = str(val)
        if val_txt:
            parts.append(f"{label}：{val_txt}")
    if not parts:
        return ""
    return "<kyc_profile>\n" + "；".join(parts) + "\n</kyc_profile>"
