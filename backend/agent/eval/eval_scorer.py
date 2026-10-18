"""评测自动评分 — LLM-as-Judge 对分析结果进行质量评估。

评估 Agent 从数据库加载（agents 表 id=3116），可通过 Agent 管理页面编辑 prompt。
"""

import json
import logging

from services.llm_service import _call_llm, MODEL_AUX
from db.config import get_config_int, get_config_float

logger = logging.getLogger(__name__)

# 评估 Agent ID（数据库中的质量评估师）
EVAL_AGENT_ID = 3116


def _get_eval_agent() -> dict | None:
    """从数据库加载评估 Agent。"""
    try:
        from db import get_agent
        return get_agent(EVAL_AGENT_ID)
    except Exception as e:
        logger.warning(f"加载评估 Agent 失败: {e}")
        return None


# 各 analysis_type 对应的输出规范（用于评估时对照打分）
_ANALYSIS_TYPE_SPECS: dict[str, str] = {
    "diversification": (
        "【分散度分析输出规范】\n"
        "- 总体判断：一句话概括分散度水平\n"
        "- 集中度分析：逐一列出超阈值的基金，含具体数值和阈值对比\n"
        "- 相关性分析：列出高相关基金对及相关系数\n"
        "- 行业/风格暴露：分析行业集中度及风险\n"
        "- 改进建议：3-5条具体可操作建议，含调整比例和标的\n"
        "- 每条分析标注数据来源\n"
        "- 输出长度≥500字，禁止过度压缩为要点式"
    ),
    "panorama": (
        "【全景分析输出规范】\n"
        "- 覆盖估值/趋势/风险/资金面多维度\n"
        "- 引用具体数据指标（分位点/PE/PB等）\n"
        "- 给出明确的综合判断和操作建议\n"
        "- 标注数据来源和置信度"
    ),
    "deep_dive": (
        "【深度分析输出规范】\n"
        "- 多维度交叉验证（估值/基本面/资金/技术面）\n"
        "- 引用具体数据并标注来源\n"
        "- 识别主要风险点和催化剂\n"
        "- 给出明确结论和操作建议"
    ),
    "enhanced_strategy": (
        "【增强策略输出规范】\n"
        "- 策略逻辑清晰，基于真实估值/持仓数据\n"
        "- 给出具体可执行的调仓建议（金额/比例/标的）\n"
        "- 包含风险提示和止损/止盈条件\n"
        "- 引用智能补仓档位数据作为建议依据"
    ),
    "market_intel": (
        "【市场情报输出规范】\n"
        "- 覆盖资金面/政策面/情绪面多维度\n"
        "- 引用具体数据（北向资金/南向资金/成交量等）\n"
        "- 给出明确的市场判断\n"
        "- 标注数据来源和时效性"
    ),
    "daily_report": (
        "【日报输出规范】\n"
        "- 覆盖市场概况/估值/资金/新闻多维度\n"
        "- 引用具体数据指标\n"
        "- 给出明确的市场温度和操作建议\n"
        "- 结构清晰，要点突出"
    ),
    "index_analysis": (
        "【指数分析输出规范】\n"
        "- 多维度估值分析（PE/PB/PS/分位点）\n"
        "- 趋势判断结合资金面和宏观环境\n"
        "- 给出明确的投资建议和风险提示\n"
        "- 引用具体数据并标注来源"
    ),
    "fund_analysis": (
        "【基金分析输出规范】\n"
        "- 穿透分析基金重仓股/行业分布\n"
        "- 估值/持仓协同分析\n"
        "- 建仓决策建议（金额/比例/节奏）\n"
        "- 引用组合约束红线并标注超限情况"
    ),
    "what_if": (
        "【情景推演输出规范】\n"
        "- 多情景分析（乐观/中性/悲观）\n"
        "- 引用具体数据支撑推演\n"
        "- 给出各情景概率和预期收益\n"
        "- 标注关键假设和风险点"
    ),
    "article_expert": (
        "【文章解读输出规范】\n"
        "- 提取核心观点和关键数据\n"
        "- 历史对比/机制拆解/量化验证三维度\n"
        "- 文章提及的板块/指数必须调用query_valuation查询\n"
        "- 标注数据来源，查询失败标注数据缺口"
    ),
}


def _get_analysis_type_specs(analysis_type: str) -> str:
    """获取 analysis_type 对应的输出规范文本。"""
    if not analysis_type:
        return ""
    return _ANALYSIS_TYPE_SPECS.get(analysis_type, "")


def _build_eval_prompt(analysis_type: str, expected_quality: str, actual_result: str,
                       agent_prompt: str = "") -> str:
    """构建评估 system prompt。"""
    return f"""评估以下投资分析的质量。

标准：{expected_quality[:300]}

分析内容：
{actual_result[:2000]}

评分：1-10分，5分起步，优点加分缺点扣分。
输出JSON：{{"score":分数,"reason":"优点和缺点"}}

只输出JSON，不要其他文字。"""


def _judge_object(text: str) -> dict:
    """只接受完整JSON对象；允许Markdown围栏，不从自然语言猜测分数。"""
    text = text.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        if len(lines) < 3 or lines[-1].strip() != '```':
            raise ValueError('评分JSON不完整')
        text = '\n'.join(lines[1:-1])
    data = json.loads(text)
    if not isinstance(data,dict):
        raise ValueError('评分必须为JSON对象')
    return data


def _valid_judge_score(value) -> float:
    import math
    if type(value) not in (int,float) or not math.isfinite(value) or not 1 <= value <= 10:
        raise ValueError('评分必须为1-10的有效数字')
    return float(value)


def _judge_reason(value) -> str:
    if not isinstance(value,str) or not value.strip():
        raise ValueError('评分缺少有效评语')
    return value.strip()


def _parse_score_response(text: str) -> tuple[float, str]:
    """缺字段、越界和无效JSON均使评审失败，不制造默认分。"""
    data = _judge_object(text)
    score = data['score'] if 'score' in data else data.get('overall_score')
    reason = data['reason'] if 'reason' in data else data.get('overall_reason')
    return _valid_judge_score(score),_judge_reason(reason)


def _parse_multi_dim_response(text: str) -> dict:
    return _normalize_scores(_judge_object(text))


def _normalize_scores(data: dict) -> dict:
    """所有维度和总体分必须明确存在，禁止缺数据时补5分。"""
    result = {}
    for key in ('data_accuracy','logic','actionability'):
        item = data.get(key)
        if not isinstance(item,dict):
            raise ValueError(f'缺少评分维度: {key}')
        result[key] = {'score':_valid_judge_score(item.get('score')),'reason':_judge_reason(item.get('reason'))}
    result['overall_score'] = _valid_judge_score(data.get('overall_score'))
    result['overall_reason'] = _judge_reason(data.get('overall_reason'))
    return result


async def score_eval_result(expected_quality: str, actual_result: str,
                            analysis_type: str = "") -> tuple[float, str]:
    """对 eval 运行结果进行自动评分。

    Args:
        expected_quality: 期望的质量标准描述
        actual_result: 实际分析输出（截断到3000字）
        analysis_type: 分析类型（如 panorama, deep_dive 等）

    Returns:
        (score, reason) — 1-10分 + 评语
    """
    if not expected_quality or not expected_quality.strip():
        return 0.0, "未设置期望质量，跳过自动评分"

    if not actual_result or len(actual_result.strip()) < 20:
        return 1.0, "输出内容过短或为空"

    # 从数据库加载评估 Agent
    agent = _get_eval_agent()
    agent_prompt = agent["system_prompt"] if agent else ""

    prompt = _build_eval_prompt(
        analysis_type=analysis_type or "未指定",
        expected_quality=expected_quality[:500],
        actual_result=actual_result[:3000],
        agent_prompt=agent_prompt,
    )

    try:
        # 关闭 thinking mode，强制模型直接输出 JSON
        resp = _call_llm(
            caller="eval_scorer",
            model=MODEL_AUX,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "输出评分JSON："},
            ],
            temperature=get_config_float('llm.temperature_eval', 0.1),
            max_tokens=get_config_int('llm.max_tokens_eval_score', 800),
            extra_body={"thinking": {"type": "disabled"}},
        )
        msg = resp.choices[0].message
        text = msg.content or ""
        # MIMO thinking mode: content 可能在 reasoning_content 中
        reasoning = ""
        if hasattr(msg, "model_extra") and msg.model_extra:
            reasoning = msg.model_extra.get("reasoning_content") or ""
        if not reasoning:
            reasoning = getattr(msg, "reasoning_content", None) or ""
        logger.info(f"评分原始返回: content='{text[:200]}', reasoning='{reasoning[:200]}'")

        # 如果 content 为空但有 reasoning，从 reasoning 中提取结论
        if not text.strip() and reasoning.strip():
            logger.info("content 为空，尝试从 reasoning 提取结论")
            import re
            # 1. 先找 JSON
            json_match = re.search(r'\{[^{}]*"score"\s*:\s*\d+[^{}]*\}', reasoning)
            if json_match:
                text = json_match.group()
                logger.info(f"从 reasoning 提取到 JSON: {text[:100]}")
            else:
                # 2. 找最终结论性文字（在 reasoning 末尾）
                # 搜索 "综合"、"最终"、"给分" 等关键词附近的内容
                lines = reasoning.split('\n')
                for line in reversed(lines[-10:]):
                    line = line.strip()
                    if not line:
                        continue
                    # 找包含数字和"分"的行
                    if re.search(r'\d+\s*分', line):
                        text = line
                        logger.info(f"从 reasoning 末尾提取: {text[:100]}")
                        break

        score, reason = _parse_score_response(text)
        logger.info(f"评分结果: {score}分 — {reason}")
        return score, reason
    except Exception as e:
        logger.error(f"评分调用失败: {e}")
        return 0.0, f"评分失败: {str(e)[:100]}"


async def evaluate_llm_output(query: str, output: str, context: str = "",
                               target_type: str = "", target_id: int = None,
                               analysis_type: str = "") -> dict:
    """对 LLM 产出进行多维度质量评估。

    Args:
        query: 用户原始问题
        output: LLM 产出文本
        context: 上下文信息（数据来源等）
        target_type: 目标类型（analysis/daily_report/chat）
        target_id: 关联目标 ID
        analysis_type: 分析类型（diversification/panorama/...），用于注入对应输出规范

    Returns:
        {
            "data_accuracy": {"score": int, "reason": str},
            "logic": {"score": int, "reason": str},
            "actionability": {"score": int, "reason": str},
            "overall_score": int,
            "overall_reason": str,
        }
    """
    if not output or len(output.strip()) < 50:
        return {
            "data_accuracy": {"score": 1, "reason": "输出过短"},
            "logic": {"score": 1, "reason": "输出过短"},
            "actionability": {"score": 1, "reason": "输出过短"},
            "overall_score": 1,
            "overall_reason": "输出过短",
        }

    # 从数据库加载评估 Agent
    agent = _get_eval_agent()
    agent_prompt = agent["system_prompt"] if agent else ""

    # 按 analysis_type 注入对应输出规范，作为评分对照标准
    type_specs = _get_analysis_type_specs(analysis_type)
    type_specs_block = f"\n\n## 类型特定要求（{analysis_type}）\n{type_specs}" if type_specs else ""

    # context 上限提升到 2500 字（原 1000 字过短，新闻原文/持仓明细常被截断，
    # 导致评分器看不到完整上下文，把引用的新闻数据误判为 LLM 幻觉）
    _ctx = (context or "")[:2500] if context else "无"

    prompt = f"""{agent_prompt}
{type_specs_block}

## 待评估内容

### 用户问题
{query[:500]}

### LLM 产出（前3000字）
{output[:3000]}

### 上下文信息（含新闻原文/持仓明细/MCP数据等，是 LLM 的输入依据）
{_ctx}

## 评分规则（严格遵守）

### 数据准确性评分要点
- **上下文引用不算捏造**：LLM 产出中引用的数据，凡能在"上下文信息"中找到出处的（如新闻原文的个股名/市值/涨跌幅、持仓明细的基金名/占比、MCP 的相关系数等），均视为**有据可查**，不得判为"幻觉"或"捏造"
- **真正的数据问题**才是扣分项：
  - 产出数据与上下文**明显矛盾**（如上下文是涨3%，产出写成跌5%）
  - 上下文**完全没有**的数据被 LLM 编造（如上下文只有占比统计无基金名，LLM 却编造具体基金名）
  - 数据来源标注与实际不符（如标注"持仓明细"但数据来自臆测）
- 上下文截断时，仅对**完整可见部分**核对，截断部分不作为扣分依据

### 逻辑评分要点
- 分析框架是否完整（是否符合 analysis_type 对应输出规范）
- 推理链条是否自洽（结论是否由数据支撑）
- 是否识别关键风险和催化剂

### 可执行性评分要点
- 建议是否具体可操作（金额/比例/标的/时机）
- 是否标注优先级和约束条件
- 是否结合用户实际持仓/画像

## 重要：输出要求
你必须直接输出一个 JSON 对象，不要输出任何其他文字、思考过程或解释。

输出格式：
{{"data_accuracy": {{"score": 整数, "reason": "【优点】xxx【扣分】xxx"}}, "logic": {{"score": 整数, "reason": "【优点】xxx【扣分】xxx"}}, "actionability": {{"score": 整数, "reason": "【优点】xxx【扣分】xxx"}}, "overall_score": 整数, "overall_reason": "综合评语"}}

注意：
- score 必须是 1-10 的整数
- reason 必须包含具体的优点和扣分点，禁止泛泛而谈，**每条 reason 控制在 80 字以内**（避免输出超长导致截断）
- overall_reason 控制在 120 字以内
- 直接输出 JSON，不要有其他任何内容
- 不要输出 ```json 代码块标记"""

    try:
        # 关闭 thinking mode，强制模型直接输出 JSON
        resp = _call_llm(
            caller="quality_evaluator",
            model=MODEL_AUX,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请对上述产出进行多维度评估。\n\n直接输出JSON，不要其他文字："},
            ],
            temperature=get_config_float('llm.temperature_eval', 0.1),
            max_tokens=get_config_int('llm.max_tokens_eval_score', 800),
            extra_body={"thinking": {"type": "disabled"}},
        )
        msg = resp.choices[0].message
        text = msg.content or ""
        # MIMO thinking mode
        if not text.strip() and hasattr(msg, "model_extra") and msg.model_extra:
            text = msg.model_extra.get("reasoning_content") or ""
        if not text.strip():
            text = getattr(msg, "reasoning_content", None) or ""
        scores = _parse_multi_dim_response(text)

        logger.info(f"质量评估完成 [{analysis_type or '未知'}]: 综合{scores['overall_score']}分 "
                    f"(数据{scores['data_accuracy']['score']}, "
                    f"逻辑{scores['logic']['score']}, "
                    f"可执行{scores['actionability']['score']})")

        # 异步保存到 llm_feedback，含各维度评语（存入 comment 字段）
        try:
            from db import save_llm_feedback
            comment_data = {
                "analysis_type": analysis_type,
                "data_accuracy_reason": scores["data_accuracy"]["reason"],
                "logic_reason": scores["logic"]["reason"],
                "actionability_reason": scores["actionability"]["reason"],
                "overall_reason": scores["overall_reason"],
            }
            save_llm_feedback(
                caller="quality_evaluator",
                input_summary=query[:200],
                output_summary=output[:200],
                rating="neutral",
                comment=json.dumps(comment_data, ensure_ascii=False),
                score_data_accuracy=scores["data_accuracy"]["score"],
                score_logic=scores["logic"]["score"],
                score_actionability=scores["actionability"]["score"],
                target_type=target_type,
                target_id=target_id,
            )
        except Exception as e:
            logger.warning(f"保存质量评估结果失败: {e}")

        return scores

    except Exception as e:
        logger.error(f"质量评估调用失败: {e}")
        return {
            "data_accuracy": {"score": 0, "reason": f"评估失败: {str(e)[:50]}"},
            "logic": {"score": 0, "reason": f"评估失败: {str(e)[:50]}"},
            "actionability": {"score": 0, "reason": f"评估失败: {str(e)[:50]}"},
            "overall_score": 0,
            "overall_reason": f"评估失败: {str(e)[:50]}",
        }
