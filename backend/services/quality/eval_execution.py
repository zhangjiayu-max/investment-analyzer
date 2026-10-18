"""评测复用真实分析入口；Prompt 覆盖限定当前异步上下文，不修改生产配置。"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import importlib
import json
import time
import uuid

ANALYSIS_NAMES = {
    'panorama':'全景诊断分析师', 'deep_dive':'基金深度分析师',
    'trade_review':'交易复盘分析师', 'what_if':'情景推演分析师',
    'diversification_ai':'分散度分析师',
}
class UnsupportedEvaluation(ValueError):
    """历史来源无法映射到实际执行器，明确跳过且不产生分数。"""


def resolve_case_type(kind: str) -> str:
    return {'chat':'orchestrator','agent_chat':'orchestrator','agent_tools':'orchestrator',
            'deepdive':'deep_dive','trade-review':'trade_review','diversification':'diversification_ai'}.get(kind,kind)


_prompt_override = ContextVar('evaluation_prompt_override', default=None)


def get_prompt_override(target: str) -> str | None:
    override = _prompt_override.get()
    return override[1] if override and override[0] == target else None


@contextmanager
def prompt_override_scope(target: str, content: str | None):
    token = _prompt_override.set((target, content) if content is not None else None)
    try:
        yield
    finally:
        _prompt_override.reset(token)


def production_prompt(analysis_type: str) -> str:
    analysis_type = resolve_case_type(analysis_type)
    from db.analysis import get_analysis_agent_by_name
    from db.agents import load_specialist_agents
    from db.config import get_config
    if analysis_type in ANALYSIS_NAMES:
        agent = get_analysis_agent_by_name(ANALYSIS_NAMES[analysis_type])
        if not agent:
            raise ValueError('分析 Agent 不存在或已禁用')
        return agent['system_prompt']
    if analysis_type == 'orchestrator':
        from agent.orchestrator import build_orchestrator_system_prompt
        return build_orchestrator_system_prompt()
    if analysis_type == 'ai':
        from routers.analysis.portfolio_ai import DEFAULT_AI_ANALYSIS_PROMPT
        return get_config('agent.portfolio_ai_prompt', '') or DEFAULT_AI_ANALYSIS_PROMPT
    specialist = load_specialist_agents().get(analysis_type)
    if not specialist:
        raise UnsupportedEvaluation(f'来源类型 {analysis_type} 无可验证的实际Agent，请编辑用例选择目标')
    return specialist['system_prompt']


async def await_analysis_result(result: dict, timeout: float = 600) -> dict:
    """等待生产任务完成；任务失败/超时不会被当作有效模型产出。"""
    from db.async_tasks import get_async_task
    from db.portfolio import get_analysis_record_status
    deadline = time.monotonic() + timeout
    while isinstance(result, dict) and result.get('status') in ('pending','running'):
        if time.monotonic() >= deadline:
            raise TimeoutError('等待目标分析任务完成超时')
        task_id = result.get('task_id')
        if task_id:
            task = get_async_task(task_id)
            if not task:
                raise RuntimeError('目标分析任务不存在')
            if task['status'] == 'error':
                raise RuntimeError(task.get('error_msg') or '目标分析失败')
            if task['status'] == 'done':
                terminal = task.get('result') or {}
                if isinstance(terminal,str):
                    terminal = json.loads(terminal)
                if not isinstance(terminal,dict):
                    terminal = {'result':terminal}
                record_id = terminal.get('record_id') or task.get('source_id') or result.get('record_id') or result.get('id')
                text = terminal.get('result') or terminal.get('answer') or terminal.get('analysis') or terminal.get('result_data')
                if not text and record_id:
                    record = get_analysis_record_status(record_id)
                    if not record or record.get('status') != 'done':
                        raise RuntimeError('目标任务结束但分析源记录未成功完成')
                    terminal = {**terminal,'result':record.get('result_data'),'token_usage':record.get('token_usage',0),'record_id':record_id}
                result = {**terminal, 'source_task_id':task_id}
                break
        else:
            record_id = result.get('record_id') or result.get('id')
            record = get_analysis_record_status(record_id) if record_id else None
            if not record:
                raise RuntimeError('异步分析未返回有效任务或记录编号')
            if record['status'] == 'error':
                raise RuntimeError(record.get('error_msg') or '目标分析失败')
            if record['status'] == 'done':
                result = {'result':record['result_data'],'token_usage':record.get('token_usage',0),'record_id':record_id}
                break
        await asyncio.sleep(0.05 if timeout < 1 else 1)
    if not isinstance(result,dict) or result.get('error') or result.get('status') in ('error','failed'):
        raise RuntimeError(str(result.get('error') if isinstance(result,dict) else result))
    text = result.get('answer') or result.get('analysis') or result.get('result') or result.get('result_data')
    if not text:
        raise RuntimeError('真实分析返回空结果')
    return {**result, 'result':text, 'token_usage':result.get('token_usage') or result.get('tokens_used') or 0}


async def execute_case(case: dict, prompt_override: str | None = None) -> dict:
    """按生产类型分发，并等到完整分析结果；不使用通用 LLM 代替目标 Agent。"""
    from db.agents import load_specialist_agents
    from models.portfolio import (PanoramaAnalysisRequest,DeepDiveRequest,TradeReviewRequest,
                                  WhatIfRequest,PortfolioAiAnalysisRequest)
    params = case.get('input_params') or '{}'
    params = json.loads(params) if isinstance(params,str) else params
    if not isinstance(params,dict):
        raise ValueError('用例参数必须为JSON对象')
    kind = resolve_case_type(case['analysis_type'])
    routes = {
        'panorama':('panorama','panorama_analysis_api',lambda: (PanoramaAnalysisRequest(**params),)),
        'deep_dive':('deep_dive','fund_deep_dive_api',lambda:(int(params['holding_id']),DeepDiveRequest())),
        'trade_review':('trade_review','trade_review_api',lambda:(TradeReviewRequest(**params),)),
        'what_if':('what_if','what_if_analysis_api',lambda:(WhatIfRequest(**params),)),
        'diversification_ai':('diversification','portfolio_diversification_ai_summary',lambda:()),
        'ai':('portfolio_ai','portfolio_ai_analysis_api',lambda:(PortfolioAiAnalysisRequest(**params),)),
    }
    scope_key = ANALYSIS_NAMES.get(kind,kind)
    with prompt_override_scope(scope_key,prompt_override):
        if kind in routes:
            module, func, args = routes[kind]
            route = importlib.import_module('routers.analysis.' + module)
            result = await getattr(route,func)(*args())
        elif kind == 'orchestrator':
            from agent.orchestrator import orchestrate
            question = params.get('question','').strip()
            if not question:
                raise ValueError('编排器评测需要question参数')
            result = await asyncio.to_thread(orchestrate,question,[],trace_id='eval-'+uuid.uuid4().hex)
        elif kind in load_specialist_agents():
            from agent.multi_agent import run_specialist
            question = params.get('question','').strip()
            if not question:
                raise ValueError('专家评测需要question参数')
            result = await asyncio.to_thread(run_specialist,kind,question,trace_id='eval-'+uuid.uuid4().hex,
                                             system_prompt_override=prompt_override)
        else:
            raise UnsupportedEvaluation(f'来源类型 {kind} 未配置实际Agent，请编辑用例选择目标')
        result = await await_analysis_result(result)
    result['prompt_digest'] = hashlib.sha256((prompt_override or production_prompt(kind)).encode()).hexdigest()
    return result


def evaluation_request_signature() -> str:
    override = _prompt_override.get()
    return hashlib.sha256(json.dumps(override,ensure_ascii=False).encode()).hexdigest() if override else ''


def capture_data_evidence(case: dict) -> dict:
    """记录本地输入证据摘要；外部动态工具返回不在冻结承诺之内。"""
    from db._conn import _get_conn
    conn = _get_conn()
    evidence = {'input_params':case.get('input_params','{}')}
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('portfolio_holdings','index_valuations','dd_valuations'):
            if table in tables:
                # 排序后计算内容指纹，覆盖同日期内的数据修订。
                where = " WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM index_valuations)" if table == 'index_valuations' else ""
                rows = [dict(row) for row in conn.execute(f'SELECT * FROM {table}{where} ORDER BY rowid')]
                evidence[table] = hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    finally:
        conn.close()
    digest = hashlib.sha256(json.dumps(evidence,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'hash':digest,'local_inputs':evidence,'external_data_frozen':False}
