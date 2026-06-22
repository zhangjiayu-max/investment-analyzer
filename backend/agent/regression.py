"""Prompt 变更回归测试 — 在 Agent prompt 更新后自动运行关联 eval cases。"""

import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

# 存储回归测试结果（内存缓存，重启清空）
_regression_results: dict[str, dict] = {}


async def _await_portfolio_record_result(record_id: int, timeout: int = 180) -> dict:
    """等待异步持仓分析完成，供回归测试内部调用。"""
    from db.portfolio import get_analysis_record_status

    deadline = time.time() + timeout
    while time.time() < deadline:
        record = get_analysis_record_status(record_id)
        if not record:
            raise ValueError("分析记录不存在")
        if record.get("status") == "done":
            return {
                "id": record_id,
                "result": record.get("result_data", ""),
                "token_usage": record.get("token_usage", 0),
            }
        if record.get("status") == "error":
            raise RuntimeError(record.get("error_msg") or "分析失败")
        await asyncio.sleep(1)
    raise TimeoutError("等待异步分析完成超时")


def _result_key(agent_id: int, agent_type: str) -> str:
    return f"{agent_type}:{agent_id}"


def get_regression_result(agent_id: int, agent_type: str) -> dict | None:
    """获取最近一次回归测试结果。"""
    return _regression_results.get(_result_key(agent_id, agent_type))


def set_regression_result(agent_id: int, agent_type: str, result: dict):
    """存储回归测试结果。"""
    _regression_results[_result_key(agent_id, agent_type)] = result


async def run_regression_tests(agent_id: int, agent_type: str = "conversation"):
    """运行指定 Agent 关联的所有 eval cases，计算回归测试结果。

    在 prompt 更新后异步调用，不阻塞主流程。
    """
    from db import list_eval_cases_by_agent, get_eval_case_avg_score
    from agent.eval_scorer import score_eval_result

    key = _result_key(agent_id, agent_type)
    logger.info(f"开始回归测试: agent_id={agent_id}, agent_type={agent_type}")

    # 标记为运行中
    set_regression_result(agent_id, agent_type, {
        "status": "running",
        "started_at": time.time(),
        "cases": [],
    })

    try:
        cases = list_eval_cases_by_agent(agent_id, agent_type)
        if not cases:
            set_regression_result(agent_id, agent_type, {
                "status": "completed",
                "message": "该 Agent 没有关联的评测用例",
                "cases": [],
                "summary": {"total": 0, "improved": 0, "degraded": 0, "unchanged": 0},
            })
            return

        results = []
        improved = 0
        degraded = 0
        unchanged = 0

        for case in cases:
            case_result = await _run_single_case(case, score_eval_result)
            results.append(case_result)

            if case_result["status"] == "improved":
                improved += 1
            elif case_result["status"] == "degraded":
                degraded += 1
            else:
                unchanged += 1

        set_regression_result(agent_id, agent_type, {
            "status": "completed",
            "completed_at": time.time(),
            "cases": results,
            "summary": {
                "total": len(cases),
                "improved": improved,
                "degraded": degraded,
                "unchanged": unchanged,
            },
        })
        logger.info(f"回归测试完成: {len(cases)} 用例, {improved} 提升, {degraded} 退步, {unchanged} 持平")

    except Exception as e:
        logger.error(f"回归测试失败: {e}")
        set_regression_result(agent_id, agent_type, {
            "status": "error",
            "error": str(e),
            "cases": [],
        })


async def _run_single_case(case: dict, score_fn) -> dict:
    """运行单个 eval case 并评分。"""
    from db import create_eval_run, update_eval_run, get_eval_case_avg_score

    case_id = case["id"]
    case_name = case.get("name", f"Case #{case_id}")
    analysis_type = case.get("analysis_type", "ai")

    # 记录旧平均分（排除即将创建的 run）
    old_avg = get_eval_case_avg_score(case_id)

    start = time.time()
    try:
        # 运行分析
        result_data = await _execute_analysis(case)
        duration_ms = int((time.time() - start) * 1000)

        # 保存 run
        run_id = create_eval_run(
            case_id=case_id,
            analysis_type=analysis_type,
            result_summary=result_data[:500],
            result_data=result_data[:5000],
            duration_ms=duration_ms,
        )

        # 评分
        expected_quality = case.get("expected_quality", "")
        score, reason = await score_fn(expected_quality, result_data, analysis_type)
        update_eval_run(run_id, score=score, score_reason=reason)

        # 判断变化
        new_avg = get_eval_case_avg_score(case_id, exclude_run_id=run_id)
        if old_avg and new_avg:
            diff = score - old_avg
            if diff > 0.3:
                status = "improved"
            elif diff < -0.3:
                status = "degraded"
            else:
                status = "unchanged"
        else:
            status = "new"  # 首次运行，无法对比

        return {
            "case_id": case_id,
            "case_name": case_name,
            "score": score,
            "reason": reason,
            "old_avg": round(old_avg, 2) if old_avg else None,
            "duration_ms": duration_ms,
            "status": status,
        }

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        return {
            "case_id": case_id,
            "case_name": case_name,
            "score": 0,
            "reason": f"运行失败: {str(e)[:100]}",
            "old_avg": round(old_avg, 2) if old_avg else None,
            "duration_ms": duration_ms,
            "status": "error",
        }


async def _execute_analysis(case: dict) -> str:
    """根据 analysis_type 执行对应的分析，返回结果文本。"""
    import json
    from fastapi.responses import StreamingResponse

    input_params = json.loads(case.get("input_params", "{}"))
    analysis_type = case.get("analysis_type", "ai")

    if analysis_type == "panorama":
        from routers.analysis.panorama import panorama_analysis_api
        from models.portfolio import PanoramaAnalysisRequest
        result = await panorama_analysis_api(PanoramaAnalysisRequest())
        if isinstance(result, StreamingResponse):
            return "流式输出（已在后台执行）"
        return json.dumps(result, ensure_ascii=False)[:5000]

    elif analysis_type == "deep_dive":
        from routers.analysis.deep_dive import fund_deep_dive_api
        from models.portfolio import DeepDiveRequest
        holding_id = input_params.get("holding_id")
        result = await fund_deep_dive_api(holding_id, DeepDiveRequest())
        return json.dumps(result, ensure_ascii=False)[:5000]

    elif analysis_type == "trade_review":
        from routers.analysis.trade_review import trade_review_api
        from models.portfolio import TradeReviewRequest
        result = await trade_review_api(TradeReviewRequest(
            start_date=input_params.get("start_date", ""),
            end_date=input_params.get("end_date", ""),
        ))
        return json.dumps(result, ensure_ascii=False)[:5000]

    elif analysis_type == "what_if":
        from routers.analysis.what_if import what_if_analysis_api
        from models.portfolio import WhatIfRequest
        result = await what_if_analysis_api(WhatIfRequest(
            scenario=input_params.get("scenario", ""),
            parameter=input_params.get("parameter", ""),
        ))
        return json.dumps(result, ensure_ascii=False)[:5000]

    elif analysis_type == "diversification_ai":
        from routers.analysis.diversification import portfolio_diversification_ai_summary
        result = await portfolio_diversification_ai_summary()
        if isinstance(result, dict) and result.get("status") == "running":
            result = await _await_portfolio_record_result(result["id"])
        return json.dumps(result, ensure_ascii=False)[:5000]

    elif analysis_type == "ai":
        from routers.analysis.portfolio_ai import portfolio_ai_analysis_api
        from models.portfolio import PortfolioAiAnalysisRequest
        question = input_params.get("question", "")
        result = await portfolio_ai_analysis_api(PortfolioAiAnalysisRequest(question=question))
        if isinstance(result, dict) and result.get("status") == "running":
            result = await _await_portfolio_record_result(result["id"])
        return json.dumps(result, ensure_ascii=False)[:5000]

    else:
        raise ValueError(f"不支持的分析类型: {analysis_type}")
