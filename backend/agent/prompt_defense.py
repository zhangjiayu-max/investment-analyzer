"""幻觉分层防御 — Prompt 层防御指令注入 + 金融专用数据校验。

借鉴企业级 AI 面试题「分层防幻觉」思想：
1. Prompt 层（事中）→ 强制事实约束指令
2. 验证层（事后）→ Self-Consistency + 规则校验
3. 监控层（持续）→ Bad case 自动回流

金融场景特殊性：基金代码编造、历史净值编造、费率数据编造会造成真实金钱损失。
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── 通用防御 Prompt（事中）──────────────────────────────────

FORCED_DEFENSE_PROMPT = """
【事实约束 - 必须遵守】
1. 如果不知道具体数据（基金代码、净值、费率），说"无法获取"而非编造
2. 所有数据引用必须标注来源：估值数据/行情数据/持仓数据/KYC
3. 如果两个数据源有冲突，明确指出差异
4. 禁止给出具体买卖价格建议（只给比例、时窗）
5. 基金代码必须为6位数字，如果记不住就说记不住
6. 历史收益率只能引用"数据来源：Wind/天天基金"，不能自己算
7. 涉及具体数字时，如果上下文未提供，必须声明"数据待核实"
"""

# 按分析类型的专用防御
_TYPE_DEFENSE: dict[str, str] = {
    "deep_dive": "\n8. 基金业绩数据只引用最近3年，更早的数据标注'历史业绩不代表未来'",
    "daily_report": "\n8. 市场评论基于当日收盘数据，标注发布时间",
    "diversification": "\n8. 相关性数据标注计算区间",
    "portfolio_trade": "\n8. 买卖建议必须基于实际持仓，不能凭空假设仓位",
    "asset_allocation": "\n8. 配置比例总和必须为100%，偏离度计算需说明基准",
}

# 触发防御的分析类型（默认所有分析都注入通用防御）
DEFENSE_ENABLED_TYPES = {
    "deep_dive", "daily_report", "diversification", "portfolio_trade",
    "asset_allocation", "panorama", "valuation", "risk", "allocation",
    "market", "strategy",
}


def attach_defense_prompt(system_prompt: str, analysis_type: str = "") -> str:
    """为 system prompt 附加防御指令。

    Args:
        system_prompt: 原始 system prompt
        analysis_type: 分析类型，用于附加专用防御

    Returns:
        附加防御指令后的 system prompt。如果 system_prompt 已包含防御指令则不重复注入。
    """
    if "事实约束" in system_prompt:
        return system_prompt  # 已注入，避免重复

    defense = FORCED_DEFENSE_PROMPT
    extra = _TYPE_DEFENSE.get(analysis_type, "")
    if extra:
        defense += extra

    return system_prompt.rstrip() + "\n" + defense


# ── 金融专用数据校验（事后，纯规则）──────────────────────

# 常见合法基金代码前缀范围（A/C 类等，6 位数字）
_FUND_CODE_RE = re.compile(r"\b(\d{6})\b")
# 百分比
_PERCENT_RE = re.compile(r"(-?\d+\.?\d*)\s*%")
# 管理费
_MGMT_FEE_RE = re.compile(r"管理费[约]?(\d+\.?\d*)\s*%")
# 净值
_NAV_RE = re.compile(r"净值[约]?(\d+\.?\d*)")


def validate_financial_data(output: str) -> dict:
    """检查 output 中的金融数据是否合理（纯规则，不调 LLM）。

    Returns:
        {"passed": bool, "issues": list[str], "issue_count": int}
    """
    issues: list[str] = []

    # 1. 收益率范围检查：单日/单年合理范围 -30% ~ +30%
    for match in _PERCENT_RE.finditer(output):
        val = float(match.group(1))
        # 排除比例类（仓位 0-100、分位 0-100）— 仅对明显是收益率的上下文检查
        context_start = max(0, match.start() - 10)
        context = output[context_start:match.end() + 5]
        if any(kw in context for kw in ["收益", "涨幅", "跌幅", "回报", "年化", "增长"]):
            if val > 50 or val < -50:
                issues.append(f"收益率超出合理范围: {val}%")

    # 2. 管理费率检查：0.1% ~ 2.0%
    for match in _MGMT_FEE_RE.finditer(output):
        val = float(match.group(1))
        if val > 3.0 or val < 0.01:
            issues.append(f"管理费率不合理: {val}%")

    # 3. 净值合理性检查：0.1 ~ 20（极端基金除外）
    for match in _NAV_RE.finditer(output):
        val = float(match.group(1))
        if val > 50 or val < 0.01:
            issues.append(f"净值数据不合理: {val}")

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "issue_count": len(issues),
    }


# ── Bad Case 自动回流 ──────────────────────────────────────

def auto_capture_bad_case(output: str, validation_result: dict,
                           self_consistency: dict | None = None,
                           user_feedback: str = "") -> bool:
    """自动将可疑案例加入 bad case 池。

    触发条件（任一个）：
    - Self-Consistency score < 0.7
    - 金融数据校验失败
    - 用户负面反馈

    Returns:
        True 表示已捕获并写入 bad case 池
    """
    is_bad = False
    reasons: list[str] = []

    if self_consistency and self_consistency.get("score", 1.0) < 0.7:
        is_bad = True
        reasons.append(f"self_consistency={self_consistency.get('score', 0):.2f}")

    if not validation_result.get("passed", True):
        is_bad = True
        reasons.extend(validation_result.get("issues", []))

    if user_feedback in ("negative", "bad", "thumbs_down"):
        is_bad = True
        reasons.append("用户负面反馈")

    if not is_bad:
        return False

    try:
        from db._conn import _get_conn
        from datetime import datetime
        conn = _get_conn()
        conn.execute("""
            INSERT INTO llm_feedback (caller, input_summary, output_summary, rating, tags, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "auto_bad_case",
            "",
            output[:500],
            "negative",
            ",".join(reasons[:5]),
            f"自动捕获: {'; '.join(reasons)}",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        conn.commit()
        conn.close()
        logger.warning(f"自动捕获 bad case: {reasons}")
        return True
    except Exception as e:
        logger.warning(f"写入 bad case 失败: {e}")
        return False
