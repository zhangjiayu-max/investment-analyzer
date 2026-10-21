"""对话质量评估器 — 自动评估多Agent对话的质量

支持两种评估模式：
1. 规则评估（快速，无 LLM 调用）
2. LLM 评估（智能，调用 LLM 进行深度分析）
"""

import json
import logging
import math
import re
from dataclasses import dataclass, asdict
from typing import Optional
from db.config import get_config_int, get_config_float
from agent.infra.output_quality import usable_tool_data as _tool_data

logger = logging.getLogger(__name__)


def _original_specialists(metadata: dict, *, valid_only=True) -> list[dict]:
    """只统计原始专家；仲裁、交叉审阅和同一专家的重复副本不增加覆盖。"""
    from agent.infra.output_quality import validate_specialist_output
    results = {}
    for result in metadata.get("specialist_results") or []:
        if (not isinstance(result, dict) or result.get("is_cross_review") or result.get("is_arbitration")
                or result.get("agent_key") in {"arbitrator", "arbitration"}
                or result.get("run_phase") in {"cross_review", "arbitration"}):
            continue
        if valid_only and not validate_specialist_output(result)["valid"]:
            continue
        key = result.get("agent_key") or result.get("agent") or json.dumps(result, sort_keys=True, default=str)
        results.setdefault(key, result)
    return list(results.values())




def _delivery_metrics(message: dict, metadata: dict) -> dict:
    """交付完整度与事实正确性分开记录。"""
    originals = _original_specialists(metadata, valid_only=False)
    valid = _original_specialists(metadata)
    recovery = metadata.get("recovery_type")
    has_synthesis = recovery not in {"experts_only", "specialist_merge", "no_expert"}
    if re.search(r"仅(?:恢复|保留|有)?专家(?:分析|结果)|综合报告未(?:生成|完成)", message.get("content", "") or ""):
        has_synthesis = False
    return {"original_specialists": len(originals), "valid_specialists": len(valid),
            "invalid_specialists": max(0, len(originals) - len(valid)), "has_final_synthesis": has_synthesis}


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

    # 维度权重（5维度增强版）
    WEIGHTS = {
        "execution": 0.15,    # 执行效率
        "data": 0.20,         # 数据利用
        "collaboration": 0.15, # 专家协作
        "response": 0.15,     # 响应质量
        # 新增 5 个评测维度
        "accuracy": 0.10,     # 准确性：数据是否正确
        "completeness": 0.10, # 完整性：是否覆盖了用户问题的各个方面
        "practicality": 0.05, # 实用性：是否有可操作的建议
        "timeliness": 0.05,   # 时效性：数据是否最新
        "risk_warning": 0.05, # 风险提示：是否标注了风险
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

        # 显式消息可以早于默认最近50条，直接按会话与主键读取。
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            if message_id is not None:
                row = conn.execute("SELECT * FROM messages WHERE id=? AND conversation_id=? AND role='assistant'",
                                   (message_id,conversation_id)).fetchone()
            else:
                row = conn.execute("SELECT * FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                                   (conversation_id,)).fetchone()
            if row is None:
                raise ValueError('未找到指定 assistant 消息')
            assistant_msg = dict(row)
            message_id = assistant_msg['id']
            agent_runs = [dict(r) for r in conn.execute(
                'SELECT * FROM agent_runs WHERE conversation_id=? AND message_id=? ORDER BY id',
                (conversation_id,message_id)).fetchall()]
        finally:
            conn.close()

        metadata = {}
        if assistant_msg and assistant_msg.get("metadata"):
            try:
                metadata = json.loads(assistant_msg["metadata"])
            except Exception:
                pass
        metadata = self._evaluation_context(metadata, assistant_msg, agent_runs)

        # 评估各维度
        execution_dim = self._evaluate_execution(metadata, agent_runs)
        data_dim = self._evaluate_data_utilization(metadata, agent_runs)
        collaboration_dim = self._evaluate_collaboration(metadata, agent_runs)
        response_dim = self._evaluate_response_quality(assistant_msg, metadata)
        # 新增 5 个评测维度
        accuracy_dim = self._evaluate_accuracy(assistant_msg, metadata)
        completeness_dim = self._evaluate_completeness(assistant_msg, metadata)
        practicality_dim = self._evaluate_practicality(assistant_msg, metadata)
        timeliness_dim = self._evaluate_timeliness(assistant_msg, metadata)
        risk_warning_dim = self._evaluate_risk_warning(assistant_msg, metadata)

        dimensions = [execution_dim, data_dim, collaboration_dim, response_dim,
                      accuracy_dim, completeness_dim, practicality_dim,
                      timeliness_dim, risk_warning_dim]

        # 计算总分
        auto_score = sum(d.score * d.weight for d in dimensions)
        # 结构完整、专家成功不能覆盖交付失败或事实尚未核验。
        statuses = {metadata.get("execution_status"), metadata.get("completion_status"), metadata.get("quality_status")}
        accuracy_status = accuracy_dim.metrics.get("verification_status", "unverified")
        score_cap = (0 if "cancelled" in statuses else 49 if "failed" in statuses or accuracy_status == "failed"
                     else 59 if "degraded" in statuses else 69 if accuracy_status != "verified" else 100)
        auto_score = min(auto_score, score_cap)

        # 生成改进建议
        suggestions = self._generate_suggestions(dimensions, metadata)
        if score_cap < 100:
            suggestions.append(f"整体评分上限{score_cap}分：交付状态或事实核验门槛未通过；规则评分仅作质量诊断。")

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
                "specialist_count": len(_original_specialists(metadata)),
                "duration_ms": metadata.get("duration_ms"),
                "duration_source": metadata.get("duration_source", "unknown"),
                "verification_status": accuracy_status,
                "score_cap": score_cap,
                "execution_status": metadata.get("execution_status", "unknown"),
                "completion_status": metadata.get("completion_status", "unknown"),
                # 兼容两种字段：orchestrator 写入 cross_review(bool)，路由层落库为 cross_review_results(list)
                "has_cross_review": collaboration_dim.metrics.get("has_cross_review", False),
                "has_arbitration": collaboration_dim.metrics.get("has_arbitration", False),
            },
            suggestions=suggestions,
        )

        # 触发进化机制（异步执行，不阻塞返回）
        if trigger_evolution:
            import asyncio
            try:
                from agent.eval.conversation_evolution import process_conversation_evaluation
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

    def _evaluation_context(self, metadata: dict, message: dict, runs: list) -> dict:
        """仅补齐本消息的原问题、落库证据和实际trace耗时，不改写数据库。"""
        from datetime import datetime
        from db._conn import _get_conn
        from db.agents import deserialize_agent_run_payload

        metadata = dict(metadata)
        metadata.setdefault("evaluated_at", message.get("created_at"))
        specialists = list(metadata.get("specialist_results") or [])
        calls = list(metadata.get("tool_calls") or [])
        for run in runs:
            tools, evidence = deserialize_agent_run_payload(run.get("tool_calls"))
            calls.extend(tools)
            if not metadata.get("specialist_results") and run.get("run_phase", "primary") == "primary":
                specialists.append({"agent_key": run.get("agent_key"), "analysis": run.get("result", ""),
                                    **evidence, "status": run.get("status", "success"),
                                    "evidence": evidence, "tool_calls": tools})
        # 同源副本只保留一次，避免重复事实/工具成功率被抬高。
        metadata["tool_calls"] = list({json.dumps(c, sort_keys=True, ensure_ascii=False, default=str): c
                                       for c in calls if isinstance(c, dict)}.values())
        metadata["specialist_results"] = specialists
        value = metadata.get("duration_ms")
        known_duration = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
        metadata["duration_source"] = "metadata" if known_duration else "unknown"
        if not known_duration:
            metadata["duration_ms"] = None
        conn = _get_conn()
        try:
            query = conn.execute("SELECT content FROM messages WHERE conversation_id=? AND id<? AND role='user' ORDER BY id DESC LIMIT 1",
                                 (message["conversation_id"], message["id"])).fetchone()
            if query:
                metadata.setdefault("original_query", query["content"])
            if not known_duration:
                trace = metadata.get("trace_id")
                row = conn.execute("SELECT total_ms,started_at,finished_at FROM execution_traces WHERE trace_id=? AND conversation_id=?",
                                   (trace, message["conversation_id"])).fetchone() if trace else None
                if row:
                    recorded = row["total_ms"]
                    if not isinstance(recorded, (int, float)) or recorded <= 0:
                        try:
                            recorded = (datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds() * 1000
                        except (ValueError, TypeError):
                            recorded = None
                    if recorded and recorded > 0:
                        metadata.update(duration_ms=recorded, duration_source="execution_traces")
                if metadata["duration_ms"] is None:
                    rows = conn.execute("SELECT started_at,finished_at FROM stream_channels WHERE conversation_id=? AND message_id=? "
                                        "AND (? IS NULL OR trace_id=?)",
                                        (message["conversation_id"], message["id"], trace, trace)).fetchall()
                    intervals = []
                    for channel in rows:
                        try:
                            start, end = datetime.fromisoformat(channel["started_at"]), datetime.fromisoformat(channel["finished_at"])
                            if end > start:
                                intervals.append((start, end))
                        except (ValueError, TypeError):
                            continue
                    if intervals:
                        duration = (max(end for _, end in intervals) - min(start for start, _ in intervals)).total_seconds() * 1000
                        metadata.update(duration_ms=duration, duration_source="stream_channels")
        finally:
            conn.close()
        return metadata

    def _evaluate_execution(self, metadata: dict, agent_runs: list) -> EvalDimension:
        """评估执行效率"""
        metrics = {}
        details = []
        score = 100.0

        # 1. 总耗时评估
        duration_ms = metadata.get("duration_ms")
        duration_s = (duration_ms / 1000 if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool)
                      and math.isfinite(duration_ms) and duration_ms > 0 else None)
        complexity = metadata.get("complexity", "medium")

        # 根据复杂度设定合理的耗时阈值
        time_thresholds = {
            "simple": {"good": 30, "ok": 60, "bad": 120},
            "medium": {"good": 60, "ok": 120, "bad": 300},
            "complex": {"good": 120, "ok": 300, "bad": 600},
        }
        thresholds = time_thresholds.get(complexity, time_thresholds["medium"])

        if duration_s is None:
            time_score = 50
        elif duration_s <= thresholds["good"]:
            time_score = 100
        elif duration_s <= thresholds["ok"]:
            time_score = 80
        elif duration_s <= thresholds["bad"]:
            time_score = 60
        else:
            time_score = max(40, 100 - (duration_s - thresholds["bad"]) / 10)

        metrics["duration_score"] = time_score
        metrics["duration_seconds"] = duration_s
        metrics["duration_status"] = "unknown" if duration_s is None else "recorded"
        metrics["duration_source"] = metadata.get("duration_source", "metadata" if duration_s is not None else "unknown")
        details.append("耗时未知，未计为0ms或快速响应" if duration_s is None else
                       f"耗时 {duration_s:.0f}s（{complexity}复杂度阈值: 良好<{thresholds['good']}s）")

        # 2. 重复调用检测
        duplicate_count = self._detect_duplicate_calls(agent_runs)
        duplicate_penalty = duplicate_count * 15  # 每次重复扣15分
        metrics["duplicate_calls"] = duplicate_count
        metrics["duplicate_penalty"] = duplicate_penalty
        if duplicate_count > 0:
            details.append(f"检测到 {duplicate_count} 次重复调用（扣{duplicate_penalty}分）")
            score -= duplicate_penalty

        # 3. 用实际起止区间计算并发节省比例；缺时间不虚构并行度。
        from datetime import datetime
        intervals = []
        for run in agent_runs:
            try:
                start, end = run.get('started_at'), run.get('completed_at')
                if start and end:
                    start = datetime.fromisoformat(start).timestamp()
                    end = datetime.fromisoformat(end).timestamp()
                    if end > start:
                        intervals.append((start,end))
            except (ValueError,TypeError):
                continue
        if len(intervals) > 1:
            total = sum(end-start for start,end in intervals)
            wall = max(end for _,end in intervals)-min(start for start,_ in intervals)
            parallelism = max(0.0,1-wall/total)
            metrics['parallelism'] = parallelism
            metrics['parallelism_score'] = min(100,50+parallelism*100)
            details.append(f'专家并行度: {parallelism:.0%}')

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

        specialist_results = _original_specialists(metadata)
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

        # 2. 完整且可用的数据才计为工具成功。
        successful_tools = sum(1 for tc in tool_calls if isinstance(tc, dict) and _tool_data(tc) is not None)
        total_tools = len(tool_calls)
        tool_success_rate = successful_tools / total_tools if total_tools > 0 else 0.0
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
        # 修复（2026-07-31 conv#195）：债券/混合型基金本身不跟踪指数估值，
        # 强制要求 has_valuation_data 会系统性低估这类对话的 data 维度。
        # 新逻辑：若对话主题是债券/固收+类基金，估值缺失不扣分，改由"资产配置/归因数据"替代。
        has_valuation = False
        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            if any(kw in analysis for kw in ["估值", "PE", "PB", "百分位", "分位"]):
                has_valuation = True
                break
        has_valuation = has_valuation and (any(
            _tool_data(call) is not None and re.search(r"valuation|估值|index", call.get("name", call.get("tool", "")), re.I)
            for call in tool_calls if isinstance(call, dict)) or any(sr.get("data_refs") for sr in specialist_results))
        metrics["has_valuation_data"] = has_valuation
        if has_valuation:
            details.append("✓ 引用了估值数据")

        # 检测对话主题是否为债券/固收+类（这类基金无指数估值属合理）
        all_text = " ".join([sr.get("analysis", "") for sr in specialist_results])
        is_bond_topic = any(kw in all_text for kw in [
            "债券型", "二级债基", "固收+", "纯债", "转债", "混合二级",
            "中债", "可转债", "债基", "债券基金"
        ])
        # 债券类对话用"资产配置/归因数据"替代估值数据
        has_asset_allocation = any(
            kw in all_text for kw in ["资产配置", "股票仓位", "权益仓位", "重仓股", "穿透", "归因", "仓位"]
        )
        metrics["is_bond_topic"] = is_bond_topic
        metrics["has_asset_allocation_data"] = has_asset_allocation
        if is_bond_topic and not has_valuation:
            # 债券类对话：用资产配置/归因数据替代估值数据
            if has_asset_allocation:
                details.append("✓ 债券类话题：引用了资产配置/归因数据（替代估值）")
            else:
                details.append("△ 债券类话题：无估值数据（合理，但建议补充资产配置归因）")
        elif not has_valuation:
            details.append("× 未引用估值数据")

        # 综合分数
        # 修复：债券类话题时，估值分由资产配置数据替代
        if is_bond_topic:
            # 债券类：估值20分 → 由资产配置数据替代
            valuation_score = 20 if has_asset_allocation else (10 if has_valuation else 0)
            score = (
                rag_rate * 35 +
                tool_success_rate * 25 +
                (20 if has_portfolio else 0) +
                valuation_score
            )
        else:
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

        specialist_results = _original_specialists(metadata)
        complexity = metadata.get("complexity", "medium")

        # 1. 专家覆盖度
        expected_specialists = {
            "simple": 1,
            "medium": 1,
            "complex": 2,
        }
        expected = expected_specialists.get(complexity, 1)
        actual = len(specialist_results)
        coverage = min(1.0, actual / expected) if expected > 0 else 1.0
        metrics["specialist_coverage"] = coverage
        metrics["expected_specialists"] = expected
        metrics["actual_specialists"] = actual
        details.append(f"专家覆盖: {actual}/{expected} ({coverage:.0%})")

        # 2. 交叉审阅
        # 兼容两种字段：orchestrator 写入 cross_review(bool)，路由层落库为 cross_review_results(list)
        reviews = metadata.get("cross_review_results") or []
        has_cross_review = bool(actual and any(
            isinstance(review, dict) and review.get("status") in {"success", "completed", "done", "validated"}
            and review.get("completion_status") not in {"truncated", "failed", "degraded"}
            for review in reviews))
        metrics["has_cross_review"] = has_cross_review
        if has_cross_review:
            details.append("✓ 触发了交叉审阅")
            score += 20
        elif complexity == "complex":
            details.append("⚠ complex任务未触发交叉审阅")

        # 3. 仲裁
        arbitration = metadata.get("arbitration") or {}
        valid_keys = {sr.get("agent_key") for sr in specialist_results}
        has_arbitration = bool(isinstance(arbitration, dict) and actual
                               and arbitration.get("arbitration_mode") not in {None, "insufficient_evidence"}
                               and set(arbitration.get("supporting_agents") or []) & valid_keys)
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

        # 主文本：完整 assistant 回答（修复 conv#195：不被仲裁片段替换）
        content = message.get("content", "") or ""
        specialist_results = metadata.get("specialist_results", [])

        # 补充：仲裁片段仅作风险/置信度检测的补充，不替换 content
        arbitration_text = ""
        if specialist_results:
            for sr in reversed(specialist_results):
                if sr.get("is_arbitration"):
                    arbitration_text = sr.get("analysis", "") or ""
                    break
        # 合并文本用于后续关键词检测（结构化仍以 content 为准）
        detect_text = content + "\n" + arbitration_text

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

        # 3. 风险提示（用 detect_text 覆盖完整回答+仲裁）
        risk_keywords = ["风险", "注意", "谨慎", "可能导致", "不确定性", "风险提示"]
        has_risk_warning = any(kw in detect_text for kw in risk_keywords)
        metrics["has_risk_warning"] = has_risk_warning
        if has_risk_warning:
            details.append("✓ 包含风险提示")

        # 4. 可操作性（用 detect_text 覆盖完整回答+仲裁）
        action_keywords = ["建议", "操作", "配置", "调整", "买入", "卖出", "持有", "等待"]
        action_count = sum(1 for kw in action_keywords if kw in detect_text)
        actionability_score = min(100, action_count * 20)
        metrics["actionability_score"] = actionability_score
        metrics["action_keywords_count"] = action_count
        details.append(f"可操作性: {action_count} 个行动关键词")

        # 5. 置信度声明（用 detect_text 覆盖完整回答+仲裁）
        has_confidence = bool(re.search(r'(置信度|信心|确定性|概率)', detect_text))
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

        delivery = _delivery_metrics(message, metadata)
        metrics.update(delivery)
        if delivery["invalid_specialists"]:
            score = min(score, 70 * delivery["valid_specialists"] / max(1, delivery["original_specialists"]) + 30)
            details.append("专家分析存在截断或无效输出，响应质量按有效完成率降级。")
        coverage = self._evaluate_completeness(message, metadata).metrics.get("coverage_rate", 0)
        metrics["question_coverage"] = coverage
        score = min(score, 40 + 60 * coverage)
        if not delivery["has_final_synthesis"]:
            score = min(score, 50)
            details.append("仅交付专家恢复内容，缺少最终综合报告。")

        return EvalDimension(
            name="response",
            score=min(100, score),
            weight=self.WEIGHTS["response"],
            metrics=metrics,
            details=details,
        )

    def _detect_duplicate_calls(self, agent_runs: list) -> int:
        """检测重复的 agent 调用

        修复（2026-07-31 conv#195）：
        - 旧逻辑：同一 agent_key 多次调用即算重复（排除 cross_review）
        - 新逻辑：补充工具级重复检测——同一基金+同一 detail_type 被多次查询才算重复；
          同基金不同 detail_type（如 all vs holdings）不算重复（all 已含 holdings 但语义不同）。
        - 同时检测专家间对同一基金的冗余查询（不同专家查同一只基金同一detail_type）。
        """
        if not agent_runs:
            return 0

        # 按 agent_key 分组
        calls_by_agent = {}
        for run in agent_runs:
            key = run.get("agent_key", "")
            if key not in calls_by_agent:
                calls_by_agent[key] = []
            calls_by_agent[key].append(run)

        # 检测重复（同一 agent 被调用多次，排除交叉审阅）
        duplicate_count = 0
        for key, runs in calls_by_agent.items():
            if len(runs) > 1:
                # 排除交叉审阅（is_cross_review）
                non_cross_review = [r for r in runs if not r.get("is_cross_review")]
                if len(non_cross_review) > 1:
                    duplicate_count += len(non_cross_review) - 1

        # 补充：工具级重复检测（同基金+同detail_type 的冗余查询）
        # 收集所有 agent_runs 的 tool_calls
        fund_query_set = set()  # (fund_code_or_name, detail_type)
        tool_dup_count = 0
        for run in agent_runs:
            tc_str = run.get("tool_calls", "")
            if not tc_str:
                continue
            try:
                from db.agents import deserialize_agent_run_payload
                calls, _ = deserialize_agent_run_payload(tc_str)
                for c in calls:
                    if not isinstance(c, dict):
                        continue
                    if c.get("name") != "query_fund_info":
                        continue
                    args = c.get("arguments", {}) or {}
                    fund_key = (args.get("fund_code") or args.get("fund_name") or "").strip()
                    detail = (args.get("detail_type") or "all").strip()
                    if not fund_key:
                        continue
                    query_key = (fund_key, detail)
                    if query_key in fund_query_set:
                        tool_dup_count += 1
                    else:
                        fund_query_set.add(query_key)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

        # 工具级重复只扣一半分（避免双重惩罚：agent 级已扣过）
        duplicate_count += tool_dup_count

        return duplicate_count

    # ── 新增 5 个评测维度 ──

    def _evaluate_accuracy(self, message: dict, metadata: dict) -> EvalDimension:
        """评估准确性：数据是否正确（交叉验证 + 数据来源检查）"""
        metrics = {}
        details = []

        if not message:
            return EvalDimension(
                name="accuracy", score=0, weight=self.WEIGHTS["accuracy"],
                metrics=metrics, details=["无响应消息"],
            )

        from services.conversation.factual_checks import check_final_fact_status
        from services.conversation.quality_contract import action_constraint_issues, selected_asset_actions

        content = message.get("content", "") or ""
        specialists = _original_specialists(metadata)
        calls = [c for c in metadata.get("tool_calls", []) if isinstance(c, dict)]
        complete_calls = [c for c in calls if not c.get("result_truncated")]
        points = [number for sr in specialists for number in re.findall(r"\d+(?:\.\d+)?%", sr.get("analysis", "") or "")]
        metrics.update(total_data_points=len(points), unique_data_points=len(set(points)),
                       repeated_mentions=len(points) - len(set(points)), cross_validated_count=0,
                       has_source_attribution=bool(re.search(r"来源|数据源|根据|来自", content)),
                       incomplete_tool_results=len(calls) - len(complete_calls))
        # 相同字符串既不能识别标的/单位/窗口，也不能证明来源独立；一律不奖励。
        details.append("重复引用不构成交叉核验，规则检查不能证明全部事实正确。")
        snapshot = metadata.get("portfolio_snapshot") or metadata.get("snapshot") or {}
        actions = selected_asset_actions(metadata.get("arbitration"), specialists)
        fact_status = check_final_fact_status(content, snapshot, complete_calls, original_query=metadata.get("original_query"), actions=actions)
        issues = list(fact_status["issues"])
        unverified = list(fact_status["unverified"])
        issues.extend(action_constraint_issues(actions, complete_calls))
        reported = metadata.get("quality_issues") or []
        # 质量门禁含执行/审查缺口，并不都是事实反证。
        review_issues = []
        if isinstance(reported, list):
            for issue in reported:
                issue = str(issue)
                if re.search(r"budget_exhausted|review|reflection|specialist|truncated|incomplete|unverified|synthesis", issue):
                    review_issues.append(issue)
                else:
                    unverified.append(issue)
        for review in metadata.get("cross_review_results") or []:
            if isinstance(review, dict) and review.get("status") not in {"success", "completed", "done", "validated"}:
                review_issues.append("cross_review_" + str(review.get("status", "unverified")))
        validation = metadata.get("factual_validation") or {}
        validation = validation if isinstance(validation, dict) else {}
        checks = validation.get("checks") or {}
        checks = checks if isinstance(checks, dict) else {}
        # 状态必须来自明确的事实核验记录；日期新鲜/工具成功/排版齐全均不代替核验。
        gates = {key: checks.get(key, "unverified") for key in
                 ("source_type", "metric_type", "unit", "date", "causal_evidence", "intent")}
        gates = {key: value if isinstance(value, str) and value in {"verified", "failed", "not_applicable"} else "unverified"
                 for key, value in gates.items()}
        required = {"source_type", "intent"}
        if re.search(r"\d|PE|PB|PS|分位|估值|行情", content, re.I):
            required.update({"metric_type", "unit", "date"})
        for key in required:
            if gates[key] == "not_applicable":
                gates[key] = "unverified"
        validation_issues = validation.get("issues") or []
        review_only_failure = bool(validation_issues and all(
            re.search(r"budget_exhausted|review|reflection|truncated|incomplete|unverified", str(issue))
            for issue in validation_issues))
        if validation.get("status") == "failed" and not review_only_failure:
            issues.append("factual_validation_failed")
        if review_only_failure:
            review_issues.extend(str(issue) for issue in validation_issues)
        for key in ("cross_review_status", "reflection"):
            review = metadata.get(key) or {}
            review_status = review.get("status") if isinstance(review, dict) else review
            if review_status in {"budget_exhausted", "failed", "timeout", "skipped", "incomplete"}:
                review_issues.append(key + "_" + review_status)
        query = metadata.get("original_query", "") or ""
        is_attribution = bool(re.search(r"为什么|为何|原因|做空|归因", query))
        if not is_attribution and gates["causal_evidence"] == "unverified":
            gates["causal_evidence"] = "not_applicable"
        # 因果问题需要机制证据；不因其他数值已核验而豁免。
        if is_attribution and gates["causal_evidence"] == "not_applicable":
            gates["causal_evidence"] = "unverified"
        if "unrequested_trade_plan" in issues:
            gates["intent"] = "failed"
        if metadata.get("unresolved_disagreements"):
            review_issues.append("unresolved_disagreements")
        issues = list(dict.fromkeys(issues))
        if issues or "failed" in gates.values():
            status, score = "failed", 40
        elif (isinstance(validation, dict) and validation.get("status") == "verified"
              and all(value in {"verified", "not_applicable"} for value in gates.values())
              and not metrics["incomplete_tool_results"] and not review_issues and not unverified
              and all(_tool_data(call) is not None for call in calls)):
            status, score = "verified", 90
        else:
            status, score = "unverified", 60 if metrics["has_source_attribution"] else 50
        metrics.update(verification_status=status, quality_gates=gates, factual_issues=issues,
                       unverified_issues=list(dict.fromkeys(unverified)), review_issues=list(dict.fromkeys(review_issues)),
                       has_data_contradiction=bool(fact_status["issues"]))
        details.append({"verified": "已记录必要事实核验；得分仍不代表统计准确率。",
                        "unverified": "事实未核验：来源类型、指标类型、单位、日期、因果与意图需要明确核验记录。",
                        "failed": "事实或意图门槛未通过：" + "、".join(issues or [k for k,v in gates.items() if v == "failed"])}[status])
        return EvalDimension(name="accuracy", score=score, weight=self.WEIGHTS["accuracy"],
                             metrics=metrics, details=details)

    def _evaluate_completeness(self, message: dict, metadata: dict) -> EvalDimension:
        """评估完整性：是否覆盖了用户问题的各个方面"""
        metrics = {}
        details = []

        if not message:
            return EvalDimension(
                name="completeness", score=0, weight=self.WEIGHTS["completeness"],
                metrics=metrics, details=["无响应消息"],
            )

        content = message.get("content", "") or ""
        query = metadata.get("original_query", "") or ""
        # 标题、重复段落和篇幅不能代替对实际问题的回答。
        body = "\n".join(dict.fromkeys(line.strip() for line in content.splitlines()
                                     if line.strip() and not re.match(r"^#{1,6}\s", line.strip())))
        clauses = [clause for clause in re.split(r"[。！？\n]", body) if len(clause.strip()) >= 10]
        substantive = "。".join(clauses)
        if re.search(r"为什么|为何|原因|归因|做空", query):
            aspects = {
                "原因及不确定性": r"原因|归因|因为|导致|驱动|无法确定|不能确定",
                "归因证据边界": r"(?:缺少|缺乏|未取得|未获取|无法|不能|没有|待验证|假设)[^。]{0,30}(?:数据|证据|判断|确定)|(?:证据|数据)[^。]{0,30}(?:不足|缺失|不完整)",
            }
            if re.search(r"做空|卖空", query):
                aspects["做空证据与判断"] = r"做空|卖空"
        else:
            candidates = {
                "估值分析": (r"估值|PE|PB|分位", r"PE|PB|估值|分位"),
                "风险评估": (r"风险|回撤|波动|亏损", r"风险|回撤|波动|亏损"),
                "行情与时间范围": (r"市场|行情|今天|上午|大跌|涨|跌", r"市场|行情|涨|跌|盘中"),
                "操作条件": (r"买|卖|持有|加仓|减仓|操作|定投", r"买入|卖出|持有|加仓|减仓|等待|定投|观望"),
                "配置约束": (r"配置|仓位|比例|组合", r"配置|仓位|比例|敞口|组合"),
            }
            aspects = {name: answer for name, (question, answer) in candidates.items()
                       if re.search(question, query, re.I)}
            if not aspects:
                aspects = {"问题相关回答": re.escape(query[:12]) if query else r"证据|结论|原因|风险|建议"}
        covered = sum(bool(re.search(pattern, substantive, re.I)) for pattern in aspects.values())
        coverage_rate = covered / len(aspects) if aspects else 0
        metrics.update(coverage_rate=coverage_rate, covered_aspects=covered, total_aspects=len(aspects),
                       required_aspects=list(aspects), content_length=len(content))
        for aspect, pattern in aspects.items():
            details.append(("✓ 覆盖：" if re.search(pattern, substantive, re.I) else "✗ 未覆盖：") + aspect)
        delivery = _delivery_metrics(message, metadata)
        metrics.update(delivery)
        score = coverage_rate * 100
        if delivery["invalid_specialists"]:
            valid_rate = delivery["valid_specialists"] / max(1, delivery["original_specialists"])
            score = min(score, 40 + 60 * valid_rate)
            details.append("原始专家输出未全部完成，不能评为完整交付。")
        if not delivery["has_final_synthesis"]:
            score = min(score, 40)
            details.append("综合报告未生成，当前仅有恢复的专家内容。")
        return EvalDimension(name="completeness", score=score, weight=self.WEIGHTS["completeness"],
                             metrics=metrics, details=details)

    def _evaluate_practicality(self, message: dict, metadata: dict) -> EvalDimension:
        """评估实用性：是否有可操作的建议"""
        metrics = {}
        details = []

        if not message:
            return EvalDimension(
                name="practicality", score=0, weight=self.WEIGHTS["practicality"],
                metrics=metrics, details=["无响应消息"],
            )

        # 主文本：完整 assistant 回答（修复 conv#195：不被仲裁片段替换）
        content = message.get("content", "") or ""
        specialist_results = metadata.get("specialist_results", [])

        # 补充：仲裁片段仅作补充检测
        arbitration_text = ""
        if specialist_results:
            for sr in reversed(specialist_results):
                if sr.get("is_arbitration"):
                    arbitration_text = sr.get("analysis", "") or ""
                    break
        detect_text = content + "\n" + arbitration_text

        # 检查可操作性指标（用 detect_text 覆盖完整回答+仲裁）
        actionable_patterns = {
            "具体操作": [r'买入', r'卖出', r'持有', r'加仓', r'减仓', r'定投', r'止盈', r'止损'],
            "具体金额/比例": [r'\d+%?', r'\d+元', r'\d+万', r'仓位.*?\d'],
            "触发条件": [r'当.*?时', r'如果.*?则', r'触发', r'条件', r'信号'],
            "时间框架": [r'短期', r'中期', r'长期', r'\d+个月', r'\d+周'],
        }

        practicality_score = 0
        for aspect, patterns in actionable_patterns.items():
            if any(re.search(p, detect_text) for p in patterns):
                practicality_score += 25
                details.append(f"✓ 包含：{aspect}")

        # 检查是否有模糊表达（扣分）
        vague_patterns = [r'根据自身情况', r'可以考虑', r'适当调整', r'仅供参考', r'建议关注']
        vague_count = sum(1 for p in vague_patterns if re.search(p, detect_text))
        metrics["vague_expressions"] = vague_count
        if vague_count > 0:
            details.append(f"⚠ 包含 {vague_count} 个模糊表达")
            practicality_score -= vague_count * 10

        metrics["practicality_score"] = practicality_score

        return EvalDimension(
            name="practicality",
            score=min(100, max(0, practicality_score)),
            weight=self.WEIGHTS["practicality"],
            metrics=metrics,
            details=details,
        )

    def _evaluate_timeliness(self, message: dict, metadata: dict) -> EvalDimension:
        """评估时效性：数据是否最新"""
        metrics = {}
        details = []

        if not message:
            return EvalDimension(
                name="timeliness", score=0, weight=self.WEIGHTS["timeliness"],
                metrics=metrics, details=["无响应消息"],
            )

        from datetime import datetime, date
        content = message.get("content", "") or ""
        query = metadata.get("original_query", "") or ""
        snapshot = metadata.get("portfolio_snapshot") or metadata.get("snapshot") or {}
        reference = snapshot.get("as_of") or metadata.get("evaluated_at")
        try:
            as_of = date.fromisoformat(str(reference)[:10])
        except (ValueError, TypeError):
            as_of = datetime.now().date()
        intraday = bool(re.search(r"盘中|上午|下午|现在|今天|今日|实时", query))
        stamps = []

        def market_stamps(value):
            if isinstance(value, dict):
                # 只取与实际行情指标在同一对象的日期，避免请求日期/报告日期充当行情日期。
                has_metric = any(key in value and value[key] is not None for key in
                                 ("price", "last_price", "current_point", "change_pct", "涨跌幅", "最新价", "最新点位"))
                if has_metric:
                    for key in ("as_of", "timestamp", "datetime", "date", "trade_date", "data_date", "更新时间", "时间", "日期"):
                        if value.get(key):
                            yield str(value[key])
                            break
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        yield from market_stamps(child)
            elif isinstance(value, list):
                for child in value:
                    yield from market_stamps(child)

        for call in metadata.get("tool_calls") or []:
            if not isinstance(call, dict) or not re.search(r"quotation|quote|market|finance_data|行情", str(call.get("name", call.get("tool", ""))), re.I):
                continue
            value = _tool_data(call)
            if value is not None:
                stamps.extend(market_stamps(value))
        parsed = []
        intraday_stamps = []
        for stamp in stamps:
            try:
                stamp_date = date.fromisoformat(stamp[:10])
                parsed.append(stamp_date)
                if re.search(r"[T ]\d{1,2}:\d{2}", stamp) and stamp_date == as_of:
                    intraday_stamps.append(stamp)
            except (ValueError, TypeError):
                continue
        # 当日日期仍不能证明已取得盘中行情；需要有时间戳的实际报价。
        current = bool(intraday_stamps) if intraday else bool(parsed and all(0 <= (as_of - stamp).days <= 3 for stamp in parsed))
        limitation = bool(re.search(r"(?:盘中|当日|今天|今日|实时)[^。]{0,18}(?:未获取|未取得|缺少|无数据|无法|尚未)|只有[^。]{0,30}收盘", content))
        score = 90 if current else (65 if limitation else 30 if intraday else 50)
        metrics.update(requires_intraday=intraday, market_data_current=current, market_data_dates=sorted(set(stamps)),
                       as_of=as_of.isoformat(), has_timeliness_limitation=limitation,
                       has_recent_date=bool(parsed and any(0 <= (as_of - stamp).days <= 3 for stamp in parsed)))
        details.append("实际行情证据满足所问时间范围。" if current else
                       "未取得满足原问题时间范围的行情；文本出现日期或‘今天’不代表数据新鲜。")
        if limitation:
            details.append("正文明确披露时效缺口，保留部分时效得分。")
        return EvalDimension(name="timeliness", score=score, weight=self.WEIGHTS["timeliness"],
                             metrics=metrics, details=details)

    def _evaluate_risk_warning(self, message: dict, metadata: dict) -> EvalDimension:
        """评估风险提示：是否标注了风险

        修复（2026-07-31 conv#195）：旧逻辑一旦存在仲裁，就用仲裁短片段替换 content，
        导致完整回答中的"### 第5段：风险提示与盲点"等风险内容被丢弃，risk_warning 恒低分。
        新逻辑：以完整 assistant content 为主，仲裁片段仅作补充（合并检测，不替换）。
        """
        metrics = {}
        details = []

        if not message:
            return EvalDimension(
                name="risk_warning", score=0, weight=self.WEIGHTS["risk_warning"],
                metrics=metrics, details=["无响应消息"],
            )

        # 主文本：完整 assistant 回答（包含所有段落，不被子片段替换）
        full_content = message.get("content", "") or ""

        # 补充：仲裁片段也参与检测（但绝不替换 full_content）
        arbitration_text = ""
        specialist_results = metadata.get("specialist_results", [])
        if specialist_results:
            for sr in reversed(specialist_results):
                if sr.get("is_arbitration"):
                    arbitration_text = sr.get("analysis", "") or ""
                    break

        # 合并检测文本：完整回答 + 仲裁片段
        detect_text = full_content + "\n" + arbitration_text

        # 风险关键词检查（覆盖金融对话常见风险表述）
        risk_keywords = {
            "直接风险提示": [r'风险提示', r'风险警示', r'风险因素', r'投资有风险', r'风险自负', r'风险点'],
            "风险类型": [r'市场风险', r'流动性风险', r'信用风险', r'政策风险', r'操作风险',
                        r'集中度风险', r'债市调整风险', r'估值风险', r'数据完整性风险'],
            "风险量化": [r'最大回撤', r'波动率', r'夏普比率', r'VaR', r'\d+%.*?风险',
                        r'占比.*?[\d.]+%'],
            "风险应对": [r'止损', r'对冲', r'分散', r'减仓', r'降低仓位',
                        r'不超过', r'严格控制', r'上限'],
            "免责声明": [r'不构成投资建议', r'仅供参考', r'据此操作.*?后果自负'],
        }

        risk_score = 0
        for category, patterns in risk_keywords.items():
            if any(re.search(p, detect_text) for p in patterns):
                risk_score += 20
                details.append(f"✓ 包含：{category}")

        metrics["risk_score"] = risk_score
        metrics["has_risk_warning"] = risk_score > 0
        metrics["full_content_len"] = len(full_content)
        metrics["arbitration_text_len"] = len(arbitration_text)

        if risk_score == 0:
            details.append("⚠ 缺少任何风险提示")

        return EvalDimension(
            name="risk_warning",
            score=min(100, risk_score),
            weight=self.WEIGHTS["risk_warning"],
            metrics=metrics,
            details=details,
        )

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
                if dim.metrics.get("duration_seconds") is None:
                    suggestions.append("⏱️ 未记录执行耗时，建议补齐 trace 起止时间")
                elif dim.metrics.get("duration_score", 100) < 60:
                    suggestions.append(f"⏱️ 执行耗时较长（{dim.metrics.get('duration_seconds', 0):.0f}秒），建议减少专家数量或简化分析流程")

            elif dim.name == "data":
                rag_rate = dim.metrics.get("rag_rate", 0)
                if rag_rate < 0.5:
                    suggestions.append(f"📚 知识库引用不足（{rag_rate:.0%}），建议增加RAG检索提升数据支撑")
                if not dim.metrics.get("has_portfolio_data"):
                    suggestions.append("💼 未引用用户持仓数据，建议结合持仓进行个性化分析")
                # 修复（conv#195）：债券类话题不要求指数估值，改建议资产配置归因
                is_bond_topic = dim.metrics.get("is_bond_topic", False)
                if is_bond_topic:
                    if not dim.metrics.get("has_asset_allocation_data"):
                        suggestions.append("📊 债券类话题建议补充资产配置/重仓股/归因数据")
                else:
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
    from services.llm_service import _call_llm, MODEL_AUX
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        if message_id is not None:
            target_msg = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND id=? AND role='assistant'",
                (conversation_id, message_id)).fetchone()
        else:
            target_msg = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                (conversation_id,)).fetchone()
        if target_msg is None:
            return {"error": "未找到目标消息"}
        target_msg = dict(target_msg)
        question = conn.execute(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' AND id<? ORDER BY id DESC LIMIT 1",
            (conversation_id, target_msg['id'])).fetchone()
        user_query = question['content'][:200] if question else ''
    finally:
        conn.close()

    # 构建评估 prompt
    content = target_msg.get("content", "")[:2000]  # 缩短输入

    prompt = f"""评估以下投资分析质量，输出JSON：

问题：{user_query[:100]}
分析：{content[:1500]}

评估维度（0-100分）：
1. data_accuracy：数据准确性——引用的数据是否正确、是否有来源
2. logic：逻辑一致性——分析逻辑是否自洽
3. actionability：可执行性——是否有具体可操作的建议
4. completeness：完整性——是否覆盖了用户问题的各个方面
5. timeliness：时效性——数据是否最新、是否标注了数据日期
6. risk_warning：风险提示——是否充分标注了投资风险

输出格式：
{{"total_score":80,"dimensions":{{"data_accuracy":{{"score":85,"comment":"数据清晰"}},"logic":{{"score":75,"comment":"逻辑合理"}},"actionability":{{"score":80,"comment":"建议具体"}},"completeness":{{"score":80,"comment":"覆盖全面"}},"timeliness":{{"score":75,"comment":"数据较新"}},"risk_warning":{{"score":85,"comment":"风险提示充分"}}}},"suggestions":["建议1"],"summary":"总结"}}"""

    try:
        response = _call_llm(
            caller="conversation_evaluator",
            model=MODEL_AUX,
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
                    "actionability": {"score": 60, "comment": "无法评估"},
                    "completeness": {"score": 60, "comment": "无法评估"},
                    "timeliness": {"score": 60, "comment": "无法评估"},
                    "risk_warning": {"score": 60, "comment": "无法评估"}
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


def get_low_score_conversations(limit: int = 10, days: int = 30,
                                 score_threshold: float = 60) -> list[dict]:
    """获取低分对话列表。

    Args:
        limit: 最多返回条数
        days: 查询天数范围
        score_threshold: 低分阈值（低于此分视为低分）

    Returns:
        [
            {
                "conversation_id": 123,
                "title": "对话标题",
                "auto_score": 45.5,
                "score_breakdown": {"accuracy": 40, "completeness": 50, ...},
                "created_at": "2026-07-01 10:30:00",
                "suggestions": ["建议1", "建议2"],
            }
        ]
    """
    from db._conn import _get_conn

    conn = _get_conn()

    try:
        rows = conn.execute("""
            SELECT
                ce.conversation_id,
                ce.auto_score,
                ce.auto_score_breakdown,
                ce.suggestions,
                ce.created_at,
                c.title
            FROM conversation_evaluations ce
            LEFT JOIN conversations c ON ce.conversation_id = c.id
            WHERE ce.auto_score < ?
              AND ce.created_at >= datetime('now', ?)
            ORDER BY ce.auto_score ASC
            LIMIT ?
        """, (score_threshold, f"-{days} days", limit)).fetchall()
    except Exception as e:
        logger.warning(f"查询低分对话失败: {e}")
        conn.close()
        return []

    conn.close()

    results = []
    for row in rows:
        row = dict(row)
        # 解析 score_breakdown JSON
        breakdown = {}
        try:
            if row.get("auto_score_breakdown"):
                breakdown = json.loads(row["auto_score_breakdown"])
        except (json.JSONDecodeError, TypeError):
            pass

        # 解析 suggestions JSON
        suggestions = []
        try:
            if row.get("suggestions"):
                suggestions = json.loads(row["suggestions"])
        except (json.JSONDecodeError, TypeError):
            pass

        results.append({
            "conversation_id": row.get("conversation_id"),
            "title": row.get("title", "未命名"),
            "auto_score": row.get("auto_score", 0),
            "score_breakdown": breakdown,
            "created_at": row.get("created_at", ""),
            "suggestions": suggestions if isinstance(suggestions, list) else [],
        })

    return results


async def auto_evaluate_conversation(conversation_id: int, message_id: int = None) -> bool:
    """对话完成后自动触发评估（开关控制，默认开启）。

    开关：eval.auto_evaluate_enabled（默认 true）
    失败不抛异常，仅记日志，避免影响主流程。
    """
    from db.config import get_config_bool
    if not get_config_bool("eval.auto_evaluate_enabled", True):
        return False
    try:
        evaluator = get_evaluator()
        # 自动评估用规则模式（不调LLM，避免成本），trigger_evolution=True 触发进化
        result = evaluator.evaluate(
            conversation_id=conversation_id,
            message_id=message_id,
            trigger_evolution=True,
            use_llm=False,
        )
        # 持久化到 conversation_evaluations 表
        from db.eval import save_conversation_evaluation
        save_conversation_evaluation(result)
        logger.info(f"[auto-eval] 对话 {conversation_id} 自动评估完成，分数: {result.auto_score}")
        return True
    except Exception as e:
        logger.warning(f"[auto-eval] 对话 {conversation_id} 自动评估失败: {e}")
        return False
