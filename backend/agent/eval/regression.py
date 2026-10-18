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
    """获取最近一次回归测试结果。优先读内存，fallback 读 DB。"""
    key = _result_key(agent_id, agent_type)
    if key in _regression_results:
        return _regression_results[key]
    # fallback: 从 DB 读取最近一条
    try:
        from db.eval import get_latest_regression_result
        return get_latest_regression_result(agent_id, agent_type)
    except Exception as e:
        logger.debug(f"从 DB 读取回归结果失败: {e}")
        return None


def set_regression_result(agent_id: int, agent_type: str, result: dict):
    """存储回归测试结果（内存 + DB 持久化）。"""
    _regression_results[_result_key(agent_id, agent_type)] = result
    # 同步写入 DB（仅 completed/error 状态持久化，running 状态不写）
    if result.get("status") in ("completed", "partial", "skipped", "error"):
        try:
            from db.eval import save_regression_result
            save_regression_result(agent_id, agent_type, result)
        except Exception as e:
            logger.warning(f"回归结果持久化失败: {e}")


async def run_regression_tests(agent_id: int, agent_type: str = "conversation"):
    """运行指定 Agent 关联的所有 eval cases，计算回归测试结果。

    在 prompt 更新后异步调用，不阻塞主流程。
    """
    from db import list_eval_cases_by_agent, get_eval_case_avg_score
    from agent.eval.eval_scorer import score_eval_result

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
                "summary": {"total": 0, "scored":0,"improved": 0, "degraded": 0, "unchanged": 0,"new":0,"skipped":0,"error":0},
            })
            return

        results = []
        counts = {key:0 for key in ('improved','degraded','unchanged','new','skipped','error')}
        for case in cases:
            case_result = await _run_single_case(case, score_eval_result)
            results.append(case_result)
            counts[case_result['status']] += 1
        scored = sum(counts[key] for key in ('improved','degraded','unchanged','new'))
        state = ('error' if not scored and counts['error'] else 'skipped' if not scored
                 else 'partial' if counts['error'] or counts['skipped'] else 'completed')
        set_regression_result(agent_id,agent_type,{
            'status':state,'completed_at':time.time(),'cases':results,
            'summary':{'total':len(cases),'scored':scored,**counts},
            'error':'没有成功评分的用例' if state=='error' else '',
        })
        logger.info('回归测试终结: %s %s',state,counts)

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
    run_id = None
    try:
        # 运行分析
        result_data = await _execute_analysis(case)
        duration_ms = int((time.time() - start) * 1000)

        # 保存 run
        run_id = create_eval_run(
            case_id=case_id,
            analysis_type=analysis_type,
            result_summary=result_data[:500],
            result_data=result_data,
            case_snapshot=case,
            duration_ms=duration_ms,
        )

        # 评分
        expected_quality = case.get("expected_quality", "")
        if not expected_quality.strip():
            update_eval_run(run_id,score_status='skipped',score_reason='未设置质量标准')
            return {'case_id':case_id,'case_name':case_name,'score':None,'reason':'未设置质量标准','old_avg':old_avg,'duration_ms':duration_ms,'status':'skipped'}
        score, reason = await asyncio.to_thread(lambda: asyncio.run(score_fn(expected_quality, result_data, analysis_type)))
        if score <= 0 or reason.startswith('评分失败'):
            raise RuntimeError(reason)
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
        from services.quality.eval_execution import UnsupportedEvaluation
        skipped = isinstance(e,UnsupportedEvaluation)
        if run_id:
            update_eval_run(run_id,score_status='skipped' if skipped else 'error',score_reason=str(e))
        return {
            "case_id": case_id,
            "case_name": case_name,
            "score": None,
            "reason": str(e)[:150],
            "old_avg": round(old_avg, 2) if old_avg else None,
            "duration_ms": duration_ms,
            "status": "skipped" if skipped else "error",
        }


async def _execute_analysis(case: dict) -> str:
    """Agent 修改后回归也等待真实执行终态，与治理评测保持一致。"""
    from services.quality.eval_execution import execute_case
    result = await execute_case(case)
    return json.dumps(result,ensure_ascii=False)
