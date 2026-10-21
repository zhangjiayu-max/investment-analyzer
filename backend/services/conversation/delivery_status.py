"""交付状态纯函数：正文可交付性与质量验收分别记录，不写入历史数据。"""
from __future__ import annotations

import re


def has_delivery_content(content: str) -> bool:
    """空答、占位符和连接异常不能称为已生成分析。"""
    text = str(content or "").strip()
    return bool(text and not text.startswith(("⏳", "❌", "（执行失败", "⚠️ 本次分析因服务中断"))
                and not re.match(r"^(?:Connection error|Error:|执行失败|请求失败|AI 回复失败|分析过程遇到问题|专家预算已用尽，未执行追加分析|专家执行超时，本轮未取得完整分析)", text, re.I))


def delivery_reasons(metadata: dict) -> list[str]:
    """只解释已记录的失败原因，不将未完成审查误报为事实矛盾。"""
    reasons = []
    issues = metadata.get("quality_issues") or []
    if isinstance(issues, str):
        issues = [issues]
    labels = {
        "cross_review_budget_exhausted": "交叉审查因预算不足未完成",
        "cross_review_failed": "交叉审查未完成",
        "cross_review_timeout": "交叉审查超时未完成",
        "reflection_budget_exhausted": "反思审查因预算不足未完成",
        "reflection_failed": "反思审查未完成",
        "reflection_timeout": "反思审查超时未完成",
        "unresolved_conflict": "专家分歧尚未消解",
        "invalid_specialist_output": "专家输出未通过完整性校验",
        "specialist_output_truncated": "专家输出截断",
        "specialist_truncated": "专家输出截断",
        "specialist_invalid": "专家输出未通过完整性校验",
        "market_causation_unverified": "市场涨跌归因缺少可验证证据",
        "equity_target_direction_error": "权益目标调整方向存在错误",
        "stale_quote_as_current": "历史行情被误用为当期行情",
        "nonreal_trade_as_executed": "假设或系统交易被误用为真实成交",
        "trade_execution_unverified": "交易是否真实执行尚未核验",
    }
    for issue in issues:
        if isinstance(issue, str):
            reasons.append(labels.get(issue, issue if re.search(r"[\u4e00-\u9fff]", issue) else "质量核验未通过（" + issue + "）"))
    for expert in metadata.get("specialist_results") or []:
        if not isinstance(expert, dict) or expert.get("agent_key") in {"arbitrator", "arbitration"}:
            continue
        completion = expert.get("completion_status")
        quality = expert.get("output_quality") or {}
        quality_issues = quality.get("issues", []) if isinstance(quality, dict) else []
        if completion == "truncated" or expert.get("finish_reason") == "length" or "incomplete_completion" in quality_issues:
            reasons.append("专家输出截断")
        elif completion in {"failed", "degraded", "timeout"} or expert.get("status") in {"failed", "degraded", "timeout"} or (isinstance(quality, dict) and quality.get("valid") is False):
            reasons.append("专家输出未通过完整性校验")
    for key, label in (("reflection", "反思审查"), ("cross_review_status", "交叉审查")):
        review = metadata.get(key) or {}
        status = review.get("status") if isinstance(review, dict) else review
        if status == "budget_exhausted":
            reasons.append(label + "因预算不足未完成")
        elif status in {"failed", "timeout", "skipped", "incomplete"}:
            reasons.append(label + ("超时未完成" if status == "timeout" else "未完成"))
    return list(dict.fromkeys(reasons))


def delivery_metadata(content: str, metadata: dict | None = None) -> dict:
    """返回新 metadata；终态 degraded 可重试、不可断点恢复，质量门禁原样保留。"""
    result = dict(metadata or {})
    execution = result.get("execution_status")
    completion = result.get("completion_status")
    if execution in {"cancelled", "timeout"}:
        return result
    if not has_delivery_content(content):
        if completion or execution in {"completed", "degraded"}:
            result.update(execution_status="failed", completion_status="failed")
        return result
    experts_only = result.get("recovery_type") == "experts_only"
    degraded = (experts_only or execution == "degraded" or completion == "degraded"
                or result.get("quality_status") in {"degraded", "failed"})
    if completion == "failed" and not experts_only:
        result["execution_status"] = "failed"
        return result
    if degraded:
        result.update(execution_status="degraded", completion_status="degraded")
        result["delivery_reasons"] = delivery_reasons(result)
        result["delivery_message"] = ("仅专家结果，综合报告未生成；专家观点尚未完成综合核验。" if experts_only
                                      else "分析已生成，部分核验未完成。")
        result.pop("error_message", None)
    elif completion == "completed":
        result["execution_status"] = "completed"
    return result
