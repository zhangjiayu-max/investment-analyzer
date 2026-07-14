"""单专家自我反思 — 专家生成分析后自评，发现信息缺口或逻辑不足时主动补充。

设计要点：
- 在 multi_agent.py:743（answer 定型后、返回前）插入
- 用轻量模型评估 4 维度：数据充分性/逻辑严谨性/可执行性/持仓感知
- 输出 JSON：{sufficient, gaps, confidence, need_retry, issues}
- need_retry=True 且 gaps 非空时，注入补充提示，最多重试 1 次
- 失败降级：跳过反思，直接返回原 analysis

成本控制：
- 用 deepseek-v4-flash（轻量模型）
- 反思 prompt 控制在 500 tokens 内
- 开关 agent.self_reflection_enabled 默认 true（质量优先）
"""
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── 反思评估 Prompt ─────────────────────────
_REFLECTION_PROMPT = """你是分析质量评估员。请对以下专家分析进行自评，检查是否存在信息缺口或逻辑不足。

## 评估维度

1. **数据充分性**：关键数据是否有来源？是否引用了工具未返回的数据？
   - 检查：analysis 中提到的 PE/PB/百分位等数值，是否能在工具结果中找到对应来源
   - 问题示例：工具返回"未找到券商数据"，但 analysis 中却有"券商PB=1.21"

2. **逻辑严谨性**：结论是否有依据支撑？推理链是否完整？
   - 检查：BUY/SELL 建议是否有估值+趋势+风险多重支撑
   - 问题示例：只看估值低就建议买入，未考虑趋势和风险

3. **可执行性**：操作建议是否具体？
   - 检查：是否给出具体金额/比例/触发条件
   - 问题示例：只说"可以买入"，未说买多少、何时买、什么条件止损

4. **持仓感知**：是否考虑了用户现有持仓？
   - 检查：加仓建议是否检查了该基金已超 25% 上限
   - 问题示例：建议加仓某基金，但用户该基金已占 30%

## 输出格式（严格 JSON）

```json
{
  "sufficient": true/false,
  "confidence": 0.0-1.0,
  "gaps": ["缺口1", "缺口2"],
  "issues": ["问题1", "问题2"],
  "need_retry": true/false
}
```

- sufficient: 信息是否充分（无缺口且无严重问题）
- confidence: 分析质量置信度（0-1）
- gaps: 信息缺口列表（如"未验证基金代码"、"未考虑持仓影响"）
- issues: 具体问题列表
- need_retry: 是否需要专家补充重试（有可修复的 gaps 时为 true）

## 专家分析内容
{analysis}

## 工具调用记录
{tool_calls}

## 用户问题
{user_question}
"""


def evaluate_analysis(analysis: str, tool_calls: list, user_question: str,
                      agent_key: str = "", agent_name: str = "",
                      trace_id: str = "") -> Optional[dict]:
    """评估专家分析质量。

    Args:
        analysis: 专家生成的分析文本
        tool_calls: 工具调用记录列表 [{name, arguments, result_preview}, ...]
        user_question: 用户原始问题
        agent_key: 专家 key
        agent_name: 专家名称
        trace_id: 追踪 ID

    Returns:
        反思结果 dict：
        {
            "sufficient": bool,
            "confidence": float,
            "gaps": list,
            "issues": list,
            "need_retry": bool,
            "reflection_score": float  # 综合得分 0-1
        }
        或 None（评估失败时）
    """
    try:
        # 构建 tool_calls 摘要（避免 prompt 过长）
        tool_summary = _build_tool_summary(tool_calls)

        # 截断 analysis（避免 prompt 过长，保留前 2000 字）
        analysis_truncated = analysis[:2000] if len(analysis) > 2000 else analysis

        prompt = _REFLECTION_PROMPT.format(
            analysis=analysis_truncated,
            tool_calls=tool_summary,
            user_question=user_question[:500],
        )

        # 调用 LLM
        from services.llm_service import _call_llm
        from agent.orchestrator import _get_model_for_agent

        model = _get_model_for_agent("self_reflection")

        messages = [
            {"role": "system", "content": "你是金融分析质量评估员，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ]

        response = _call_llm(
            messages=messages,
            model=model,
            temperature=0.1,  # 低温度保证一致性
            max_tokens=500,   # 限制输出长度
            trace_id=trace_id,
            caller=f"self_reflection_{agent_key}",
        )

        if not response:
            logger.warning(f"[self_reflection] {agent_name} LLM 返回空")
            return None

        # 解析 JSON
        result = _parse_reflection_json(response)
        if not result:
            logger.warning(f"[self_reflection] {agent_name} JSON 解析失败: {response[:200]}")
            return None

        # 计算综合得分
        reflection_score = _calc_reflection_score(result)
        result["reflection_score"] = reflection_score

        logger.info(
            f"[self_reflection] {agent_name} 评估完成: "
            f"sufficient={result.get('sufficient')}, "
            f"confidence={result.get('confidence')}, "
            f"gaps={len(result.get('gaps', []))}, "
            f"need_retry={result.get('need_retry')}, "
            f"score={reflection_score:.2f}"
        )

        return result

    except Exception as e:
        logger.warning(f"[self_reflection] {agent_name} 评估异常: {e}")
        return None


def _build_tool_summary(tool_calls: list) -> str:
    """构建工具调用摘要（用于反思 prompt）。"""
    if not tool_calls:
        return "（无工具调用）"

    lines = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("arguments", {})
        preview = tc.get("result_preview", "")

        # 参数摘要
        if isinstance(args, dict):
            arg_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
        else:
            arg_str = str(args)[:50]

        lines.append(f"- {name}({arg_str}): {preview}")

    return "\n".join(lines)


def _parse_reflection_json(response: str) -> Optional[dict]:
    """解析反思 LLM 返回的 JSON（容错）。"""
    if not response:
        return None

    # 尝试直接解析
    try:
        return _validate_reflection_dict(json.loads(response))
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块提取
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if json_match:
        try:
            return _validate_reflection_dict(json.loads(json_match.group(1)))
        except json.JSONDecodeError:
            pass

    # 尝试从文本中提取第一个 JSON 对象
    json_match = re.search(r"\{[^{}]*\"sufficient\"[^{}]*\}", response, re.DOTALL)
    if json_match:
        try:
            return _validate_reflection_dict(json.loads(json_match.group(0)))
        except json.JSONDecodeError:
            pass

    return None


def _validate_reflection_dict(d: dict) -> Optional[dict]:
    """校验并规范化反思结果 dict。"""
    if not isinstance(d, dict):
        return None

    # 必须有 sufficient 字段
    if "sufficient" not in d:
        return None

    # 规范化字段
    result = {
        "sufficient": bool(d.get("sufficient", True)),
        "confidence": float(d.get("confidence", 0.5)),
        "gaps": list(d.get("gaps", [])) if isinstance(d.get("gaps"), list) else [],
        "issues": list(d.get("issues", [])) if isinstance(d.get("issues"), list) else [],
        "need_retry": bool(d.get("need_retry", False)),
    }

    # 置信度范围限制
    result["confidence"] = max(0.0, min(1.0, result["confidence"]))

    return result


def _calc_reflection_score(result: dict) -> float:
    """计算综合反思得分 0-1。

    评分逻辑：
    - sufficient=True: 基础分 0.7
    - confidence: 占 0.2 权重
    - gaps 数量: 每个 gap 扣 0.1（最多扣 0.3）
    - issues 数量: 每个 issue 扣 0.05（最多扣 0.2）
    """
    base = 0.7 if result.get("sufficient") else 0.4
    confidence_bonus = result.get("confidence", 0.5) * 0.2

    gaps_penalty = min(0.3, len(result.get("gaps", [])) * 0.1)
    issues_penalty = min(0.2, len(result.get("issues", [])) * 0.05)

    score = base + confidence_bonus - gaps_penalty - issues_penalty
    return max(0.0, min(1.0, score))


def build_retry_prompt(reflection: dict) -> str:
    """根据反思结果构建重试提示。

    Args:
        reflection: evaluate_analysis 返回的 dict

    Returns:
        注入给专家的重试提示文本
    """
    gaps = reflection.get("gaps", [])
    issues = reflection.get("issues", [])

    parts = []
    if gaps:
        parts.append("信息缺口：\n" + "\n".join(f"- {g}" for g in gaps))
    if issues:
        parts.append("分析问题：\n" + "\n".join(f"- {i}" for i in issues))

    if not parts:
        return ""

    return (
        "\n\n## ⚠️ 自我反思发现以下不足，请补充完善\n\n"
        + "\n\n".join(parts)
        + "\n\n请针对以上不足补充分析，确保数据有来源、逻辑有支撑、建议可执行。"
    )


def is_self_reflection_enabled() -> bool:
    """检查自我反思是否开启。"""
    try:
        from db.config import get_config
        return get_config("agent.self_reflection_enabled", "true").lower() == "true"
    except Exception:
        return True  # 默认开启


def get_max_retry() -> int:
    """获取反思后最大重试次数。"""
    try:
        from db.config import get_config_int
        return get_config_int("agent.self_reflection_max_retry", 1)
    except Exception:
        return 1
