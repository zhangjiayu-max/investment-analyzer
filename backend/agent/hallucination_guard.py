"""
基金代码幻觉校验层 — 事中强制工具调用 + 事后代码比对

三层防护：
1. 事中：prompt 强制要求推荐代码前调 tool
2. 事后：回复中出现基金代码时，自动查询真实信息比对
3. 监控：校验失败录入 token_usage/hallucinations 表供归因迭代
"""

import re
import logging
import json
from typing import Optional
from typing import Any

logger = logging.getLogger(__name__)

# 6 位数字基金代码
_FUND_CODE_RE = re.compile(r"\b(\d{6})\b")

# 常见的非基金 6 位数字（避免误匹配）
_NON_FUND_CODES = {
    # 日期
    "000001",  # 上证指数
    "399001",  # 深证成指
}


async def verify_fund_codes_in_response(
    response_text: str,
    conversation_id: int,
    message_id: int,
) -> dict:
    """检测回复中的基金代码并逐一验证。

    返回:
        {
            "codes_found": [...],       # 所有匹配到的 6 位数字
            "verified": {...},          # {code: 真实基金名称}
            "hallucinations": [...]     # 声称的名称 vs 真实名称 不匹配的
        }
    """
    codes = list(set(_FUND_CODE_RE.findall(response_text)))
    # 过滤非基金代码
    codes = [c for c in codes if c not in _NON_FUND_CODES]

    result = {
        "codes_found": codes,
        "verified": {},
        "hallucinations": [],
    }

    if not codes:
        return result

    try:
        from services.market_data import get_fund_info

        for code in codes:
            try:
                info = get_fund_info(code)
                if info and info.get("name"):
                    result["verified"][code] = info["name"]
            except Exception as e:
                logger.debug(f"基金代码 {code} 查询失败: {e}")

    except Exception as e:
        logger.warning(f"批量基金代码验证失败: {e}")
        return result

    # 检测幻觉：从回复中提取声称的名称，与真实名称比对
    hallucinations = _detect_code_hallucinations(response_text, result["verified"])

    if hallucinations:
        result["hallucinations"] = hallucinations
        logger.warning(
            f"⚠️ 幻觉检测: conv={conversation_id} msg={message_id} — "
            f"{len(hallucinations)} 个代码声称名称与真实不一致: "
            + "; ".join(
                f"{h['code']}: 声称'{h['claimed']}' vs 真实'{h['actual']}'"
                for h in hallucinations[:5]
            )
        )

        # 记录到幻觉监控表
        _record_hallucination(conversation_id, message_id, hallucinations)

    return result


def _extract_claimed_name_for_code(text: str, code: str) -> Optional[str]:
    """在文本中提取与目标基金代码配套的声称名称。

    覆盖 4 种常见格式：
    1. `| **CODE** | NAME | ...` Markdown 表格
    2. `**CODE**（NAME）` 或 `**CODE**(NAME)` 内联加粗
    3. `CODE（NAME）` 或 `CODE(NAME)` 普通内联
    4. `CODE NAME` 紧邻（空格分隔）
    """
    escaped = re.escape(code)

    # 1. 表格行：| ... | CODE | NAME | ... |
    for line in text.split("\n"):
        if "|" not in line:
            continue
        if "---" in line:
            continue
        cells = [c.strip().lstrip("*").strip("*").strip() for c in line.split("|")]
        for i, cell in enumerate(cells):
            if cell == code or cell == f"**{code}**":
                # 代码列后面紧跟着名称列
                for j in range(i + 1, len(cells)):
                    if cells[j] and not _FUND_CODE_RE.fullmatch(cells[j]):
                        return cells[j]
                break

    # 2. **CODE**（NAME）或 **CODE**(NAME)
    m = re.search(rf"\*\*{escaped}\*\*[（(]([^）)]+)[）)]", text)
    if m:
        return m.group(1).strip()

    # 3. CODE（NAME）或 CODE(NAME)
    m = re.search(rf"{escaped}[（(]([^）)]+)[）)]", text)
    if m:
        return m.group(1).strip()

    # 4. CODE 后紧跟的中文名称（以字母/中文/ETF/LOF 开头，200 字以内）
    m = re.search(rf"{escaped}\s+([a-zA-Z\u4e00-\u9fff][\u4e00-\u9fff\w·]{2,200})", text)
    if m:
        candidate = m.group(1).strip()
        # 只取到下一个标点或数字
        candidate = re.sub(r"[,，。；;\n].*", "", candidate)
        if candidate and not _FUND_CODE_RE.fullmatch(candidate):
            return candidate

    return None


def _detect_code_hallucinations(
    text: str, verified_codes: dict[str, str]
) -> list[dict]:
    """检测 LLM 回复中声称的基金名称是否与真实名称一致。"""
    hallucinations = []

    for code, real_name in verified_codes.items():
        claimed_name = _extract_claimed_name_for_code(text, code)
        if not claimed_name:
            continue

        if not _names_similar(claimed_name, real_name):
            hallucinations.append({
                "code": code,
                "claimed": claimed_name,
                "actual": real_name,
            })

    return hallucinations


def _parse_markdown_table_fund_map(text: str) -> dict[str, str]:
    """从 Markdown 表格中提取基金代码 → 声称名称的映射。

    匹配模式：
    | **515960** | 国泰中证高端装备制造ETF | ...
    """
    result = {}
    for line in text.split("\n"):
        # 跳过表头
        if "---" in line and "|" in line:
            continue
        if "|" not in line:
            continue

        cells = [c.strip().lstrip("*").strip("*").strip() for c in line.split("|")]
        for i, cell in enumerate(cells):
            if _FUND_CODE_RE.fullmatch(cell):
                code = cell
                # 取该行的第一列非空文本作为声称名称
                # 通常代码在第一列或第二列
                for other in cells:
                    if other != code and other and not _FUND_CODE_RE.fullmatch(other):
                        result[code] = other
                        break
                break

    return result


def _names_similar(claimed: str, real: str) -> bool:
    """简单判断两个基金名称是否指向同一只基金。

    两步判断：
    1. 基金公司名精确匹配（中国式：x×基金/×x资管）— 不匹配直接返回 False
    2. 关键词 Jacard 重叠率 ≥ 50% — 够严格不会漏判行业类幻觉
    """
    import re

    claimed = claimed.strip()
    real = real.strip()

    # ── 第 0 步：完全相同 ──
    if claimed == real:
        return True

    # ── 第 1 步：提取基金公司名 ──
    def extract_company(name: str) -> str:
        """提取基金公司名称，如 国泰、华夏、易方达 等。"""
        # 匹配「xxx基金」「xxx资管」
        m = re.match(r"([\u4e00-\u9fff\w]+?)(?:基金|资管)", name)
        if m:
            return m.group(1)
        return name[:4]  # fallback: 前 4 个字符

    claimed_co = extract_company(claimed)
    real_co = extract_company(real)

    # ── 第 2 步：关键词重叠率 ──
    claimed_clean = claimed.lower().replace(" ", "")
    real_clean = real.lower().replace(" ", "")

    def keywords(s):
        try:
            import jieba
            return set(w for w in jieba.cut(s) if len(w) >= 2)
        except Exception:
            return set(s[i : i + 2] for i in range(len(s) - 1))

    c_kw = keywords(claimed_clean)
    r_kw = keywords(real_clean)

    total = c_kw | r_kw
    if not total:
        return False

    jaccard = len(c_kw & r_kw) / len(total)

    # ── 第 3 步：判断 ──
    # 基金公司名必须完全一致
    if claimed_co != real_co:
        return False

    # 关键词重叠率阈值：≥ 50%
    return jaccard >= 0.5


def _parse_markdown_table_fund_map(text: str) -> dict[str, str]:
    """从 Markdown 表格中提取基金代码 → 声称名称的映射。（保留用于表格场景的优先级提取）"""


def _record_hallucination(
    conversation_id: int, message_id: int, hallucinations: list[dict]
):
    """将幻觉检测结果持久化到数据库，供后续归因分析。"""
    try:
        from db._conn import _get_conn

        conn = _get_conn()
        for h in hallucinations:
            conn.execute(
                """INSERT INTO token_usage
                   (model, prompt_tokens, completion_tokens, total_tokens, caller, trace_id)
                   VALUES ('hallucination_detect', 0, 0, 0, ?, ?)""",
                (
                    f"fund_code:{h['code']}:claimed={h['claimed'][:30]}",
                    f"conv={conversation_id}|msg={message_id}",
                ),
            )
        conn.commit()
        conn.close()
        logger.info(f"幻觉记录已写入: {len(hallucinations)}条")
    except Exception as e:
        logger.debug(f"幻觉记录写入失败（不影响主流程）: {e}")


# ── 快速预检（同步，可在 orchestrator 回调中调用） ──


def quick_check_fund_codes(text: str) -> list[str]:
    """快速提取回复中的基金代码，不做验证。
    用于在 orchestrator 层面判断是否需要唤起验证流程。
    """
    codes = list(set(_FUND_CODE_RE.findall(text)))
    return [c for c in codes if c not in _NON_FUND_CODES]
