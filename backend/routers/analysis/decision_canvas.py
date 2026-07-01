"""
决策画布 API — 聚合分析结论，按共识/冲突/建议/学习四区呈现。

GET /api/decision/canvas

设计稿: docs/designs/2026-07-01-cross-system-bridge-layer.md Section 5

四区定义：
  - consensus:  同一 target，至少 2 个不同 source_type 结论方向一致
  - conflicts:  同一 target，action 方向相反（buy/increase ↔ sell/decrease/clear）
  - actionable: 所有 action ≠ hold 的结论，按 confidence 降序
  - learning:   从 key_variables 提炼或硬编码一条框架总结
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db._conn import _get_conn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["decision_canvas"])

# ── 行动方向判定 ──────────────────────────────────────
BULLISH_ACTIONS = {"buy", "increase"}
BEARISH_ACTIONS = {"sell", "decrease", "clear"}
NEUTRAL_ACTIONS = {"hold", "watch", ""}


def _action_direction(action: str) -> str:
    """将 action 标准化为方向字符串。"""
    a = (action or "").lower().strip()
    if a in BULLISH_ACTIONS:
        return "bullish"
    if a in BEARISH_ACTIONS:
        return "bearish"
    if a in NEUTRAL_ACTIONS:
        return "neutral"
    return "neutral"


def _action_label(action: str) -> str:
    """将 action 翻译为中文标签。"""
    mapping = {
        "buy": "买入",
        "sell": "卖出",
        "hold": "持有",
        "increase": "加仓",
        "decrease": "减仓",
        "clear": "清仓",
        "watch": "观察",
    }
    a = (action or "").lower().strip()
    return mapping.get(a, a or "待定")


def _source_type_label(source_type: str) -> str:
    """source_type → 中文显示标签。"""
    mapping = {
        "daily_report": "日报",
        "deep_dive": "深度",
        "orchestrator": "AI对话",
        "diversification": "分散度",
        "panorama": "全景",
        "hotspots": "热点",
        "rebalancing": "调仓",
        "bond_recommend": "债券",
        "what_if": "推演",
        "trade_review": "复盘",
        "index_analysis": "指数",
        "fee_analyzer": "费率",
        "portfolio_ai": "持仓AI",
        "four_pots": "四笔钱",
        "fund_analysis": "基金分析",
        "compare_diff": "对比",
    }
    return mapping.get(source_type, source_type)


def _deserialize_fields(record: dict) -> dict:
    """反序列化 JSON 字段（key_variables, data_basis）。"""
    for field in ("key_variables", "data_basis"):
        if record.get(field):
            if isinstance(record[field], str):
                try:
                    record[field] = json.loads(record[field])
                except (json.JSONDecodeError, TypeError):
                    record[field] = [record[field]]
        else:
            record[field] = []
    return record


@router.get("/api/decision/canvas")
def get_decision_canvas(hours: int = 24, limit: int = 50):
    """
    返回决策画布四区数据。

    Query params:
        hours  — 时间窗口（默认 24 小时）
        limit  — 每个区域的查询上限（默认 50）
    """
    try:
        conn = _get_conn()

        # ── 拉取时间窗口内所有结论 ──
        rows = conn.execute(
            """SELECT id, source_system, source_type, source_id, target_subject,
                      action, summary, reasoning, key_variables, data_basis,
                      confidence, urgent, created_at, expires_at
               FROM analysis_conclusions
               WHERE created_at >= datetime('now', 'localtime', ?)
               ORDER BY created_at DESC""",
            (f"-{hours} hours",),
        ).fetchall()
        conn.close()

        if not rows:
            return {
                "consensus": [],
                "conflicts": [],
                "actionable": [],
                "learning": {
                    "framework": "今日暂无分析结论。各分析模块运行后，共识区与关注区会自动填充。",
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
                "summary": {
                    "total": 0,
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            }

        conclusions = [_deserialize_fields(dict(r)) for r in rows]

        # ── 1. 共识区：同一 target 至少 2 个不同 source_type，方向一致 ──
        # 分组: target → {direction → [conclusions]}
        target_groups: dict[str, dict[str, list]] = {}
        for c in conclusions:
            target = c.get("target_subject", "")
            if not target:
                continue
            direction = _action_direction(c.get("action", ""))
            if direction == "neutral":
                continue
            if target not in target_groups:
                target_groups[target] = {}
            if direction not in target_groups[target]:
                target_groups[target][direction] = []
            target_groups[target][direction].append(c)

        consensus_items = []
        for target, dirs in target_groups.items():
            for direction, items in dirs.items():
                # 至少 2 条 且 source_type 不重复
                source_types = {it.get("source_type") for it in items}
                if len(items) >= 2 and len(source_types) >= 2:
                    items_sorted = sorted(items, key=lambda c: c.get("confidence", 0), reverse=True)
                    # 取第一条作为主结论，其他作为确认
                    main = items_sorted[0]
                    confirmations = items_sorted[1:]
                    consensus_items.append({
                        "target_subject": target,
                        "direction": direction,
                        "direction_label": "看多" if direction == "bullish" else "看空",
                        "summary": main.get("summary", ""),
                        "reasoning": main.get("reasoning", ""),
                        "action": main.get("action", ""),
                        "action_label": _action_label(main.get("action", "")),
                        "confidence": main.get("confidence", 0.5),
                        "confirmations": [
                            {
                                "source_type": cf.get("source_type"),
                                "source_label": _source_type_label(cf.get("source_type", "")),
                                "summary": cf.get("summary", ""),
                                "action": cf.get("action", ""),
                                "action_label": _action_label(cf.get("action", "")),
                            }
                            for cf in confirmations
                        ],
                        "key_variables": main.get("key_variables", []),
                    })

        # 按置信度降序
        consensus_items.sort(key=lambda x: x["confidence"], reverse=True)

        # ── 2. 冲突区：同一 target，截然相反的 action ──
        conflict_targets = set()
        for target, dirs in target_groups.items():
            has_bullish = "bullish" in dirs
            has_bearish = "bearish" in dirs
            if has_bullish and has_bearish:
                conflict_targets.add(target)

        conflict_items = []
        for target in conflict_targets:
            dirs = target_groups[target]
            bullish_items = dirs.get("bullish", [])
            bearish_items = dirs.get("bearish", [])

            conflict_items.append({
                "target_subject": target,
                "bullish_view": {
                    "action": bullish_items[0].get("action", ""),
                    "action_label": _action_label(bullish_items[0].get("action", "")),
                    "summary": bullish_items[0].get("summary", ""),
                    "reasoning": bullish_items[0].get("reasoning", ""),
                    "source_type": bullish_items[0].get("source_type"),
                    "source_label": _source_type_label(bullish_items[0].get("source_type", "")),
                    "key_variables": bullish_items[0].get("key_variables", []),
                    "confidence": bullish_items[0].get("confidence", 0.5),
                },
                "bearish_view": {
                    "action": bearish_items[0].get("action", ""),
                    "action_label": _action_label(bearish_items[0].get("action", "")),
                    "summary": bearish_items[0].get("summary", ""),
                    "reasoning": bearish_items[0].get("reasoning", ""),
                    "source_type": bearish_items[0].get("source_type"),
                    "source_label": _source_type_label(bearish_items[0].get("source_type", "")),
                    "key_variables": bearish_items[0].get("key_variables", []),
                    "confidence": bearish_items[0].get("confidence", 0.5),
                },
                "conditional_advice": _build_conditional_advice(
                    bullish_items[0], bearish_items[0]
                ),
            })

        # ── 3. 建议区：所有非 hold/non-neutral 的结论，按置信度排序 ──
        actionable_items = []
        for c in conclusions:
            action = (c.get("action") or "").lower().strip()
            if action in NEUTRAL_ACTIONS:
                continue
            actionable_items.append({
                "id": c.get("id"),
                "target_subject": c.get("target_subject", ""),
                "action": c.get("action", ""),
                "action_label": _action_label(c.get("action", "")),
                "summary": c.get("summary", ""),
                "reasoning": c.get("reasoning", ""),
                "source_type": c.get("source_type"),
                "source_label": _source_type_label(c.get("source_type", "")),
                "source_system": c.get("source_system"),
                "key_variables": c.get("key_variables", []),
                "confidence": c.get("confidence", 0.5),
                "urgent": bool(c.get("urgent", 0)),
                "created_at": c.get("created_at"),
                "time_window": "24h",
                "condition_trigger": _suggest_condition(c),
            })

        # ── 4. 学习区：从 key_variables 提炼 ──
        all_key_vars = []
        all_source_types = set()
        for c in conclusions:
            all_source_types.add(c.get("source_type", ""))

            kvs = c.get("key_variables", [])
            if isinstance(kvs, list):
                for v in kvs:
                    if v and isinstance(v, str):
                        all_key_vars.append(v)
            elif isinstance(kvs, str) and kvs:
                all_key_vars.append(kvs)

        learning = {
            "framework": _generate_learning_framework(
                all_key_vars, len(conclusions), all_source_types
            ),
            "key_variables_seen": list(set(all_key_vars))[:10],
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        return {
            "consensus": consensus_items[:limit],
            "conflicts": conflict_items[:limit],
            "actionable": actionable_items[:limit],
            "learning": learning,
            "summary": {
                "total": len(conclusions),
                "consensus_count": len(consensus_items),
                "conflict_count": len(conflict_items),
                "actionable_count": len(actionable_items),
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }

    except Exception as e:
        logger.warning(f"get_decision_canvas 失败: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "error": str(e),
                "consensus": [],
                "conflicts": [],
                "actionable": [],
                "learning": {
                    "framework": "决策画布数据加载失败，请稍后重试。",
                },
            },
        )


# ── 辅助函数 ──────────────────────────────────────────


def _suggest_condition(conclusion: dict) -> str:
    """根据结论内容，生成一条条件化触发建议。"""
    action = (conclusion.get("action") or "").lower().strip()
    key_vars = conclusion.get("key_variables", [])
    if isinstance(key_vars, str):
        try:
            key_vars = json.loads(key_vars)
        except (json.JSONDecodeError, TypeError):
            key_vars = [key_vars] if key_vars else []
    if not isinstance(key_vars, list):
        key_vars = []

    # 尝试从 key_variables 中提取条件
    var_str = "、".join(key_vars[:2]) if key_vars else "市场信号"
    if action in ("buy", "increase"):
        return f"当{var_str}确认改善时执行"
    if action in ("sell", "decrease", "clear"):
        return f"当{var_str}恶化到临界值时执行"
    return f"关注{var_str}变化"


def _build_conditional_advice(bullish_item: dict, bearish_item: dict) -> dict:
    """根据冲突双方的关键变量，构建条件化建议。"""
    bbull_vars = bullish_item.get("key_variables", []) or []
    bbear_vars = bearish_item.get("key_variables", []) or []
    if isinstance(bull_vars := bbull_vars, str):
        bull_vars = [bull_vars] if bull_vars else []
    if isinstance(bear_vars := bbear_vars, str):
        bear_vars = [bear_vars] if bear_vars else []

    bull_vars = bull_vars if isinstance(bull_vars, list) else []
    bear_vars = bear_vars if isinstance(bear_vars, list) else []
    all_vars = list(set(bull_vars + bear_vars))

    bull_side = _source_type_label(bullish_item.get("source_type", ""))
    bear_side = _source_type_label(bearish_item.get("source_type", ""))

    if len(all_vars) >= 2:
        advice = f"分歧来自 {' vs '.join(all_vars[:2])}：若你更看重 {all_vars[0]}，可参考 {bull_side} 建议；若更关注 {all_vars[1]}，可参考 {bear_side} 建议。"
    elif len(all_vars) == 1:
        advice = f"双方对 {all_vars[0]} 的判断权重不同，请结合自身风险偏好做决定。"
    else:
        advice = f"{bull_side} 与 {bear_side} 对同一标的看法不同，建议等待更多数据后再决策。"

    return {
        "advice": advice,
        "path_a": f"如果信任 {bull_side} 判断 → 可考虑 {_action_label(bullish_item.get('action', ''))}",
        "path_b": f"如果信任 {bear_side} 判断 → 可考虑 {_action_label(bearish_item.get('action', ''))}",
    }


def _generate_learning_framework(
    key_variables: list[str],
    conclusion_count: int,
    source_types: set,
) -> str:
    """根据关键变量和来源类型，生成一条"今天学到的框架"。"""
    default = (
        "投资决策不是非黑即白，而是权衡。"
        "每次看到分歧，先问自己：驱动结论的核心变量是什么？"
        "是估值、趋势、情绪还是资金面？"
        "不同变量在不同时间框架下的权重不同——学会区分，你就比 90% 的散户更理性。"
    )

    if not key_variables and conclusion_count == 0:
        return default

    if not key_variables:
        return (
            f"今日 {conclusion_count} 条分析结论来自 {len(source_types)} 个分析视角。"
            "多元化信息来源是避免决策偏见的有效手段。"
        )

    unique_vars = list(dict.fromkeys(key_variables))  # 保持顺序去重
    top_vars = unique_vars[:5]

    if len(top_vars) >= 3:
        var_list = "、".join(top_vars[:3])
        return (
            f"今日分析的核心变量集中在「{var_list}」上。"
            "不同分析模块对这些变量的权重评估不同，这正是分歧的来源。"
            "在做决策前，确认你最关心的变量是什么——估值派看便宜还是趋势派看方向？"
            "没有绝对的对错，只有适合你风险偏好的选择。"
        )
    elif len(top_vars) >= 2:
        var_list = "、".join(top_vars)
        return (
            f"今日分析聚焦在「{var_list}」两个维度。"
            "在做出投资决策时，建议同时评估这两个变量的交互影响。"
        )
    else:
        var_list = top_vars[0]
        return (
            f"今日分析核心关注「{var_list}」。"
            "单一变量的判断往往不够全面，建议下次结合多个维度交叉验证。"
        )
