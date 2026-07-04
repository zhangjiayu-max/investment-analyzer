"""对话质量评估器 — 自动评估多Agent对话的质量

支持两种评估模式：
1. 规则评估（快速，无 LLM 调用）
2. LLM 评估（智能，调用 LLM 进行深度分析）
"""

import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Optional
from db.config import get_config_int, get_config_float

logger = logging.getLogger(__name__)


@dataclass
class EvalDimension:
    """评估维度"""
    name: str
    score: float  # 0-100
    weight: float
    metrics: dict
    details: list


@dataclass
class ConversationEvaluation:
    """对话评估结果"""
    conversation_id: int
    message_id: Optional[int]
    auto_score: float
    auto_score_breakdown: dict
    dimensions: list[EvalDimension]
    metadata: dict
    suggestions: list[str]


class ConversationQualityEvaluator:
    """对话质量评估器"""

    # 维度权重
    WEIGHTS = {
        "execution": 0.30,    # 执行效率
        "data": 0.25,         # 数据利用
        "collaboration": 0.25, # 专家协作
        "response": 0.20,     # 响应质量
    }

    def evaluate(self, conversation_id: int, message_id: int = None,
                 trigger_evolution: bool = True, use_llm: bool = False) -> ConversationEvaluation:
        """评估对话质量

        参数:
            conversation_id: 对话 ID
            message_id: 消息 ID（可选）
            trigger_evolution: 是否触发进化机制（默认 True）
            use_llm: 是否使用 LLM 进行智能评估（默认 False）
        """
        from db.conversations import get_conversation, get_messages
        from db.agents import get_agent_runs

        # 获取对话信息
        conv = get_conversation(conversation_id)
        if not conv:
            raise ValueError(f"对话 {conversation_id} 不存在")

        # 获取消息
        messages = get_messages(conversation_id)
        if not messages:
            raise ValueError(f"对话 {conversation_id} 没有消息")

        # 获取 agent 执行记录
        agent_runs = get_agent_runs(conversation_id)

        # 获取 assistant 消息的 metadata
        assistant_msg = None
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                assistant_msg = msg
                break

        metadata = {}
        if assistant_msg and assistant_msg.get("metadata"):
            try:
                metadata = json.loads(assistant_msg["metadata"])
            except Exception:
                pass

        # 评估各维度
        execution_dim = self._evaluate_execution(metadata, agent_runs)
        data_dim = self._evaluate_data_utilization(metadata, agent_runs)
        collaboration_dim = self._evaluate_collaboration(metadata, agent_runs)
        response_dim = self._evaluate_response_quality(assistant_msg, metadata)

        dimensions = [execution_dim, data_dim, collaboration_dim, response_dim]

        # 计算总分
        auto_score = sum(d.score * d.weight for d in dimensions)

        # 生成改进建议
        suggestions = self._generate_suggestions(dimensions, metadata)

        result = ConversationEvaluation(
            conversation_id=conversation_id,
            message_id=message_id or (assistant_msg["id"] if assistant_msg else None),
            auto_score=round(auto_score, 1),
            auto_score_breakdown={
                d.name: round(d.score, 1) for d in dimensions
            },
            dimensions=[asdict(d) for d in dimensions],
            metadata={
                "complexity": metadata.get("complexity", "unknown"),
                "specialist_count": len(metadata.get("specialist_results", [])),
                "duration_ms": metadata.get("duration_ms", 0),
                "has_cross_review": metadata.get("cross_review", False),
                "has_arbitration": metadata.get("arbitration", False),
            },
            suggestions=suggestions,
        )

        # 触发进化机制（异步执行，不阻塞返回）
        if trigger_evolution:
            import asyncio
            try:
                from agent.conversation_evolution import process_conversation_evaluation
                evolution_data = {
                    "auto_score": result.auto_score,
                    "auto_score_breakdown": result.auto_score_breakdown,
                    "suggestions": result.suggestions,
                }
                # 如果已经在事件循环中，使用 create_task
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        process_conversation_evaluation(conversation_id, evolution_data)
                    )
                except RuntimeError:
                    # 没有运行中的事件循环，使用线程执行
                    import threading
                    def run_evolution():
                        asyncio.run(
                            process_conversation_evaluation(conversation_id, evolution_data)
                        )
                    threading.Thread(target=run_evolution, daemon=True).start()
            except Exception as e:
                logger.warning(f"触发进化机制失败: {e}")

        return result

    def _evaluate_execution(self, metadata: dict, agent_runs: list) -> EvalDimension:
        """评估执行效率"""
        metrics = {}
        details = []
        score = 100.0

        # 1. 总耗时评估
        duration_ms = metadata.get("duration_ms", 0)
        duration_s = duration_ms / 1000
        complexity = metadata.get("complexity", "medium")

        # 根据复杂度设定合理的耗时阈值
        time_thresholds = {
            "simple": {"good": 30, "ok": 60, "bad": 120},
            "medium": {"good": 60, "ok": 120, "bad": 300},
            "complex": {"good": 120, "ok": 300, "bad": 600},
        }
        thresholds = time_thresholds.get(complexity, time_thresholds["medium"])

        if duration_s <= thresholds["good"]:
            time_score = 100
        elif duration_s <= thresholds["ok"]:
            time_score = 80
        elif duration_s <= thresholds["bad"]:
            time_score = 60
        else:
            time_score = max(40, 100 - (duration_s - thresholds["bad"]) / 10)

        metrics["duration_score"] = time_score
        metrics["duration_seconds"] = duration_s
        details.append(f"耗时 {duration_s:.0f}s（{complexity}复杂度阈值: 良好<{thresholds['good']}s）")

        # 2. 重复调用检测
        duplicate_count = self._detect_duplicate_calls(agent_runs)
        duplicate_penalty = duplicate_count * 15  # 每次重复扣15分
        metrics["duplicate_calls"] = duplicate_count
        metrics["duplicate_penalty"] = duplicate_penalty
        if duplicate_count > 0:
            details.append(f"检测到 {duplicate_count} 次重复调用（扣{duplicate_penalty}分）")
            score -= duplicate_penalty

        # 3. 专家并行度
        specialist_results = metadata.get("specialist_results", [])
        if len(specialist_results) > 1:
            # 检查是否有并行执行（通过 duration_ms 相近判断）
            durations = [s.get("duration_ms", 0) for s in specialist_results if s.get("duration_ms")]
            if durations:
                avg_duration = sum(durations) / len(durations)
                total_duration = sum(durations)
                parallelism = 1 - (total_duration / (avg_duration * len(durations))) if avg_duration > 0 else 0
                parallelism_score = min(100, 50 + parallelism * 100)
                metrics["parallelism"] = parallelism
                metrics["parallelism_score"] = parallelism_score
                details.append(f"专家并行度: {parallelism:.0%}")

        # 综合分数
        final_score = (time_score * 0.6 + metrics.get("parallelism_score", 80) * 0.4) - duplicate_penalty
        final_score = max(0, min(100, final_score))

        return EvalDimension(
            name="execution",
            score=final_score,
            weight=self.WEIGHTS["execution"],
            metrics=metrics,
            details=details,
        )

    def _evaluate_data_utilization(self, metadata: dict, agent_runs: list) -> EvalDimension:
        """评估数据利用"""
        metrics = {}
        details = []
        score = 0.0

        specialist_results = metadata.get("specialist_results", [])
        tool_calls = metadata.get("tool_calls", [])

        # 1. RAG 命中率
        rag_hits = 0
        total_specialists = len(specialist_results)

        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            # 检查是否引用了知识库内容
            if any(kw in analysis for kw in ["知识库", "根据《", "参考", "书中", "文章"]):
                rag_hits += 1

        rag_rate = rag_hits / total_specialists if total_specialists > 0 else 0
        metrics["rag_hits"] = rag_hits
        metrics["rag_rate"] = rag_rate
        details.append(f"RAG命中: {rag_hits}/{total_specialists} ({rag_rate:.0%})")

        # 2. 工具调用成功率
        successful_tools = sum(1 for tc in tool_calls if not tc.get("error"))
        total_tools = len(tool_calls)
        tool_success_rate = successful_tools / total_tools if total_tools > 0 else 1.0
        metrics["tool_success_rate"] = tool_success_rate
        metrics["successful_tools"] = successful_tools
        metrics["total_tools"] = total_tools
        details.append(f"工具调用成功率: {successful_tools}/{total_tools} ({tool_success_rate:.0%})")

        # 3. 持仓数据利用
        has_portfolio = False
        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            if any(kw in analysis for kw in ["持仓", "您的", "您的基金", "占比", "债券型"]):
                has_portfolio = True
                break
        metrics["has_portfolio_data"] = has_portfolio
        if has_portfolio:
            details.append("✓ 引用了用户持仓数据")

        # 4. 估值数据利用
        has_valuation = False
        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            if any(kw in analysis for kw in ["估值", "PE", "PB", "百分位", "分位"]):
                has_valuation = True
                break
        metrics["has_valuation_data"] = has_valuation
        if has_valuation:
            details.append("✓ 引用了估值数据")

        # 综合分数
        score = (
            rag_rate * 35 +
            tool_success_rate * 25 +
            (20 if has_portfolio else 0) +
            (20 if has_valuation else 0)
        )

        return EvalDimension(
            name="data",
            score=min(100, score),
            weight=self.WEIGHTS["data"],
            metrics=metrics,
            details=details,
        )

    def _evaluate_collaboration(self, metadata: dict, agent_runs: list) -> EvalDimension:
        """评估专家协作"""
        metrics = {}
        details = []
        score = 0.0

        specialist_results = metadata.get("specialist_results", [])
        complexity = metadata.get("complexity", "medium")

        # 1. 专家覆盖度
        expected_specialists = {
            "simple": 1,
            "medium": 1,
            "complex": 2,
        }
        expected = expected_specialists.get(complexity, 1)
        actual = len([s for s in specialist_results if not s.get("is_cross_review")])
        coverage = min(1.0, actual / expected) if expected > 0 else 1.0
        metrics["specialist_coverage"] = coverage
        metrics["expected_specialists"] = expected
        metrics["actual_specialists"] = actual
        details.append(f"专家覆盖: {actual}/{expected} ({coverage:.0%})")

        # 2. 交叉审阅
        has_cross_review = metadata.get("cross_review", False)
        metrics["has_cross_review"] = has_cross_review
        if has_cross_review:
            details.append("✓ 触发了交叉审阅")
            score += 20
        elif complexity == "complex":
            details.append("⚠ complex任务未触发交叉审阅")

        # 3. 仲裁
        has_arbitration = metadata.get("arbitration", False)
        metrics["has_arbitration"] = has_arbitration
        if has_arbitration:
            details.append("✓ 触发了仲裁")
            score += 15

        # 4. 专家分歧识别
        if len(specialist_results) >= 2:
            # 检查是否有方向性分歧
            bullish_count = 0
            bearish_count = 0
            for sr in specialist_results:
                analysis = sr.get("analysis", "").lower()
                if any(kw in analysis for kw in ["低估", "机会", "建议买", "加仓"]):
                    bullish_count += 1
                elif any(kw in analysis for kw in ["高估", "风险高", "减仓", "回避"]):
                    bearish_count += 1

            has_disagreement = bullish_count > 0 and bearish_count > 0
            metrics["has_disagreement"] = has_disagreement
            if has_disagreement:
                details.append(f"✓ 识别到专家分歧（看多:{bullish_count}, 看空:{bearish_count}）")

        # 综合分数
        score += coverage * 65
        if has_cross_review:
            score = min(100, score)

        return EvalDimension(
            name="collaboration",
            score=min(100, score),
            weight=self.WEIGHTS["collaboration"],
            metrics=metrics,
            details=details,
        )

    def _evaluate_response_quality(self, message: dict, metadata: dict) -> EvalDimension:
        """评估响应质量"""
        metrics = {}
        details = []

        if not message:
            return EvalDimension(
                name="response",
                score=0,
                weight=self.WEIGHTS["response"],
                metrics=metrics,
                details=["无响应消息"],
            )

        content = message.get("content", "")
        specialist_results = metadata.get("specialist_results", [])

        # 使用仲裁结果或最终响应
        if specialist_results:
            # 找仲裁结果
            for sr in reversed(specialist_results):
                if sr.get("is_arbitration"):
                    content = sr.get("analysis", content)
                    break

        # 1. 结构化程度
        has_headers = bool(re.search(r'^#{1,3}\s', content, re.MULTILINE))
        has_tables = bool(re.search(r'\|.*\|.*\|', content))
        has_lists = bool(re.search(r'^[-*]\s', content, re.MULTILINE))
        has_bold = bool(re.search(r'\*\*.*?\*\*', content))

        structure_score = sum([
            30 if has_headers else 0,
            25 if has_tables else 0,
            20 if has_lists else 0,
            15 if has_bold else 0,
            10,  # 基础分
        ])
        metrics["structure"] = {
            "headers": has_headers,
            "tables": has_tables,
            "lists": has_lists,
            "bold": has_bold,
        }
        details.append(f"结构化: {'✓' if has_headers else '×'} 标题 {'✓' if has_tables else '×'} 表格")

        # 2. 数据引用
        has_numbers = bool(re.search(r'\d+\.?\d*', content))
        has_percentages = bool(re.search(r'\d+\.?\d*%', content))
        has_specific_data = bool(re.search(r'(PE|PB|分位|温度|收益率)', content))

        data_score = sum([
            30 if has_numbers else 0,
            30 if has_percentages else 0,
            40 if has_specific_data else 0,
        ])
        metrics["data_citation"] = {
            "numbers": has_numbers,
            "percentages": has_percentages,
            "specific_data": has_specific_data,
        }
        details.append(f"数据引用: {'✓' if has_specific_data else '×'} 具体指标")

        # 3. 风险提示
        risk_keywords = ["风险", "注意", "谨慎", "可能导致", "不确定性", "风险提示"]
        has_risk_warning = any(kw in content for kw in risk_keywords)
        metrics["has_risk_warning"] = has_risk_warning
        if has_risk_warning:
            details.append("✓ 包含风险提示")

        # 4. 可操作性
        action_keywords = ["建议", "操作", "配置", "调整", "买入", "卖出", "持有", "等待"]
        action_count = sum(1 for kw in action_keywords if kw in content)
        actionability_score = min(100, action_count * 20)
        metrics["actionability_score"] = actionability_score
        metrics["action_keywords_count"] = action_count
        details.append(f"可操作性: {action_count} 个行动关键词")

        # 5. 置信度声明
        has_confidence = bool(re.search(r'(置信度|信心|确定性|概率)', content))
        metrics["has_confidence_statement"] = has_confidence
        if has_confidence:
            details.append("✓ 包含置信度声明")

        # 综合分数
        score = (
            structure_score * 0.25 +
            data_score * 0.25 +
            (100 if has_risk_warning else 50) * 0.15 +
            actionability_score * 0.25 +
            (100 if has_confidence else 60) * 0.10
        )

        return EvalDimension(
            name="response",
            score=min(100, score),
            weight=self.WEIGHTS["response"],
            metrics=metrics,
            details=details,
        )

    def _detect_duplicate_calls(self, agent_runs: list) -> int:
        """检测重复的 agent 调用"""
        if not agent_runs:
            return 0

        # 按 agent_key 分组
        calls_by_agent = {}
        for run in agent_runs:
            key = run.get("agent_key", "")
            if key not in calls_by_agent:
                calls_by_agent[key] = []
            calls_by_agent[key].append(run)

        # 检测重复（同一 agent 被调用多次）
        duplicate_count = 0
        for key, runs in calls_by_agent.items():
            if len(runs) > 1:
                # 排除交叉审阅（is_cross_review）
                non_cross_review = [r for r in runs if not r.get("is_cross_review")]
                if len(non_cross_review) > 1:
                    duplicate_count += len(non_cross_review) - 1

        return duplicate_count

    def _generate_suggestions(self, dimensions: list[EvalDimension], metadata: dict) -> list[str]:
        """生成改进建议（按优先级排序）"""
        suggestions = []

        # 按分数排序，优先处理最低分的维度
        sorted_dims = sorted(dimensions, key=lambda d: d.score)

        for dim in sorted_dims:
            if dim.score >= 70:
                continue  # 跳过合格的维度

            if dim.name == "execution":
                if dim.metrics.get("duplicate_calls", 0) > 0:
                    suggestions.append(f"🔧 检测到{dim.metrics['duplicate_calls']}次重复调用，建议优化编排逻辑避免重复执行")
                if dim.metrics.get("duration_score", 100) < 60:
                    suggestions.append(f"⏱️ 执行耗时较长（{dim.metrics.get('duration_seconds', 0):.0f}秒），建议减少专家数量或简化分析流程")

            elif dim.name == "data":
                rag_rate = dim.metrics.get("rag_rate", 0)
                if rag_rate < 0.5:
                    suggestions.append(f"📚 知识库引用不足（{rag_rate:.0%}），建议增加RAG检索提升数据支撑")
                if not dim.metrics.get("has_portfolio_data"):
                    suggestions.append("💼 未引用用户持仓数据，建议结合持仓进行个性化分析")
                if not dim.metrics.get("has_valuation_data"):
                    suggestions.append("📊 未引用估值数据，建议查询并引用PE/PB等估值指标")

            elif dim.name == "collaboration":
                complexity = metadata.get("complexity", "medium")
                has_cross_review = dim.metrics.get("has_cross_review", False)
                specialist_coverage = dim.metrics.get("specialist_coverage", 1)

                # 复杂任务未触发交叉审阅
                if complexity in ("complex", "medium") and not has_cross_review and dim.metrics.get("actual_specialists", 0) >= 2:
                    suggestions.append("🔄 多专家协作未触发交叉审阅，建议启用交叉审阅验证分析结果")

                # 专家覆盖不足
                if specialist_coverage < 0.8:
                    suggestions.append(f"👥 专家覆盖不足（{dim.metrics.get('actual_specialists', 0)}/{dim.metrics.get('expected_specialists', 0)}），建议调用更多相关专家")

                # 未识别到分歧（但实际可能有分歧）
                if dim.metrics.get("actual_specialists", 0) >= 2 and not dim.metrics.get("has_disagreement"):
                    suggestions.append("🤝 建议增加专家分歧识别，让仲裁法官更好地综合不同观点")

            elif dim.name == "response":
                if not dim.metrics.get("has_risk_warning"):
                    suggestions.append("⚠️ 缺少风险提示，建议添加投资风险提醒")
                if dim.metrics.get("actionability_score", 0) < 60:
                    suggestions.append("🎯 可操作性不足，建议提供具体的投资建议（如买入/卖出/持有/等待）")
                if not dim.metrics.get("has_confidence_statement"):
                    suggestions.append("📈 建议添加置信度声明，让用户了解分析的确定性")

        # 限制建议数量，避免过多
        return suggestions[:3]


async def evaluate_with_llm(conversation_id: int, message_id: int = None) -> dict:
    """使用 LLM 进行智能评估

    返回：
    {
        "total_score": 85,
        "dimensions": {
            "data_accuracy": {"score": 90, "comment": "..."},
            "logic": {"score": 80, "comment": "..."},
            "actionability": {"score": 85, "comment": "..."}
        },
        "suggestions": ["..."],
        "summary": "..."
    }
    """
    from db.conversations import get_messages
    from services.llm_service import _call_llm, MODEL

    messages = get_messages(conversation_id)
    if not messages:
        return {"error": "无消息"}

    # 提取要评估的消息
    target_msg = None
    if message_id:
        for msg in messages:
            if msg.get("id") == message_id:
                target_msg = msg
                break
    else:
        # 找最后一条 assistant 消息
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                target_msg = msg
                break

    if not target_msg:
        return {"error": "未找到目标消息"}

    # 提取用户问题
    user_query = ""
    for msg in messages:
        if msg.get("role") == "user":
            user_query = msg.get("content", "")[:200]
            break

    # 构建评估 prompt
    content = target_msg.get("content", "")[:2000]  # 缩短输入

    prompt = f"""评估以下投资分析质量，输出JSON：

问题：{user_query[:100]}
分析：{content[:1500]}

评估维度（0-100分）：
1. data_accuracy：数据准确性
2. logic：逻辑一致性
3. actionability：可执行性

输出格式：
{{"total_score":80,"dimensions":{{"data_accuracy":{{"score":85,"comment":"数据清晰"}},"logic":{{"score":75,"comment":"逻辑合理"}},"actionability":{{"score":80,"comment":"建议具体"}}}},"suggestions":["建议1"],"summary":"总结"}}"""

    try:
        response = _call_llm(
            caller="conversation_evaluator",
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是专业的投资分析质量评估专家。只输出 JSON，不要其他文字。"},
                {"role": "user", "content": prompt},
            ],
            temperature=get_config_float('llm.temperature_eval', 0.2),
            max_tokens=get_config_int('llm.max_tokens_eval', 1000),
        )

        raw = response.choices[0].message.content
        if not raw:
            logger.warning("LLM 返回空内容")
            return {"error": "LLM 返回空内容"}

        raw = raw.strip()
        logger.info(f"LLM 评估原始返回: {raw[:500]}")

        # 如果返回为空，使用默认结果
        if not raw:
            return {
                "total_score": 60,
                "dimensions": {
                    "data_accuracy": {"score": 60, "comment": "无法评估"},
                    "logic": {"score": 60, "comment": "无法评估"},
                    "actionability": {"score": 60, "comment": "无法评估"}
                },
                "suggestions": ["LLM 评估失败，请使用快速评估"],
                "summary": "LLM 评估返回空内容"
            }

        # 解析 JSON - 多种容错策略
        # 1. 去除 markdown 代码块
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # 2. 尝试直接解析
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # 3. 提取第一个 {...}
            import re
            match = re.search(r'\{[^{}]*\{[^{}]*\}[^{}]*\}', raw, re.DOTALL)
            if not match:
                match = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
            if match:
                result = json.loads(match.group())
            else:
                raise ValueError("无法从 LLM 返回中提取 JSON")

        # 验证必要字段
        if "total_score" not in result:
            # 尝试从 dimensions 计算
            dims = result.get("dimensions", {})
            if dims:
                scores = [d.get("score", 0) for d in dims.values() if isinstance(d, dict)]
                result["total_score"] = sum(scores) / len(scores) if scores else 0

        return result

    except Exception as e:
        logger.error(f"LLM 评估失败: {e}")
        return {"error": str(e)}


# 单例
_evaluator = None


def get_evaluator() -> ConversationQualityEvaluator:
    """获取评估器单例"""
    global _evaluator
    if _evaluator is None:
        _evaluator = ConversationQualityEvaluator()
    return _evaluator
