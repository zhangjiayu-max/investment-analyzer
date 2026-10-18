"""候选 Prompt 静默对照：明确接入的对话、同一输入、有限抽样和人工发布。"""
import asyncio
import json
import logging
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='shadow')
_dispatch_lock = threading.Lock()
_supported_types = {'chat'}


def supports_shadow(agent_type):
    return agent_type in _supported_types


def init_shadow_db():
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS shadow_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, agent_type TEXT NOT NULL,
            current_prompt TEXT NOT NULL, candidate_prompt TEXT NOT NULL, is_active INTEGER DEFAULT 1,
            traffic_pct REAL DEFAULT 0.1, prompt_version_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS shadow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, config_id INTEGER REFERENCES shadow_configs(id),
            agent_type TEXT NOT NULL, input_data TEXT, production_output TEXT, shadow_output TEXT,
            production_score REAL, shadow_score REAL, score_reason TEXT,
            duration_ms INTEGER DEFAULT 0, token_usage INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')))""")
        from db._utils import _add_column_if_not_exists
        _add_column_if_not_exists(conn, 'shadow_configs', 'prompt_version_id', 'INTEGER')
        _add_column_if_not_exists(conn, 'shadow_runs', 'status', "TEXT DEFAULT 'legacy_unverified'")
        _add_column_if_not_exists(conn, 'shadow_runs', 'error_msg', "TEXT DEFAULT ''")
        # 额度账本独立于可删除的实验记录；只保存日期与计数。
        conn.execute('CREATE TABLE IF NOT EXISTS shadow_daily_usage (day TEXT PRIMARY KEY, runs INTEGER NOT NULL DEFAULT 0)')
        conn.execute("""INSERT INTO shadow_daily_usage(day, runs)
            SELECT date(created_at), COUNT(*) FROM shadow_runs GROUP BY date(created_at)
            ON CONFLICT(day) DO UPDATE SET runs=MAX(shadow_daily_usage.runs,excluded.runs)""")
        # 历史记录无法确认评分有效性，不冒充已验证样本。
        conn.execute('CREATE INDEX IF NOT EXISTS idx_shadow_runs_config ON shadow_runs(config_id)')
        conn.commit()
    finally:
        conn.close()


def create_shadow_config(name, agent_type, current_prompt, candidate_prompt, traffic_pct=0.1, prompt_version_id=None):
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError('名称不能为空且最多200字')
    if agent_type not in _supported_types:
        raise ValueError('目前仅支持已接入的普通 Agent 对话（chat）')
    if not isinstance(candidate_prompt, str) or not candidate_prompt.strip() or len(candidate_prompt) > 20000:
        raise ValueError('候选 Prompt 不能为空且最多20000字')
    if not isinstance(traffic_pct, (int, float)) or isinstance(traffic_pct, bool) or not math.isfinite(traffic_pct) or not 0 <= traffic_pct <= 1:
        raise ValueError('流量比例应在0到1之间')
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        cur = conn.execute('''INSERT INTO shadow_configs
            (name, agent_type, current_prompt, candidate_prompt, traffic_pct, prompt_version_id)
            VALUES (?, ?, ?, ?, ?, ?)''', (name.strip(), agent_type, current_prompt or '', candidate_prompt, traffic_pct, prompt_version_id))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_shadow_configs(active_only=True):
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        where = 'WHERE sc.is_active = 1' if active_only else ''
        rows = conn.execute(f'''SELECT sc.*, COUNT(sr.id) AS run_count,
            SUM(CASE WHEN sr.status='scored' THEN 1 ELSE 0 END) AS valid_runs,
            AVG(CASE WHEN sr.status='scored' THEN sr.production_score END) AS avg_prod_score,
            AVG(CASE WHEN sr.status='scored' THEN sr.shadow_score END) AS avg_shadow_score
            FROM shadow_configs sc LEFT JOIN shadow_runs sr ON sc.id=sr.config_id
            {where} GROUP BY sc.id ORDER BY sc.id DESC''').fetchall()
        return [{**dict(row), 'integrated': row['agent_type'] in _supported_types} for row in rows]
    finally:
        conn.close()


def list_shadow_runs(config_id=None, limit=100):
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        where, params = ('WHERE config_id=?', [config_id]) if config_id else ('', [])
        return [dict(row) for row in conn.execute(f'SELECT * FROM shadow_runs {where} ORDER BY id DESC LIMIT ?', [*params, max(1,min(limit,500))])]
    finally:
        conn.close()


def get_shadow_stats(config_id=None):
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        where, params = ('WHERE config_id=?', [config_id]) if config_id else ('', [])
        row = conn.execute(f'''SELECT COUNT(*) AS total_runs,
            SUM(CASE WHEN status='scored' THEN 1 ELSE 0 END) AS valid_runs,
            AVG(CASE WHEN status='scored' THEN production_score END) AS avg_prod_score,
            AVG(CASE WHEN status='scored' THEN shadow_score END) AS avg_shadow_score,
            AVG(CASE WHEN status='scored' THEN CASE WHEN shadow_score > production_score + 0.5 THEN 1.0 ELSE 0.0 END END) AS shadow_win_rate,
            AVG(duration_ms) AS avg_duration_ms, SUM(token_usage) AS total_tokens
            FROM shadow_runs {where}''', params).fetchone()
        result = dict(row)
        result['valid_runs'] = result['valid_runs'] or 0
        filters = f"{where} AND status='scored'" if where else "WHERE status='scored'"
        rows = conn.execute(f'''SELECT CASE WHEN shadow_score > production_score + 0.5 THEN 'shadow_better'
            WHEN production_score > shadow_score + 0.5 THEN 'production_better' ELSE 'similar' END AS comparison,
            COUNT(*) AS cnt FROM shadow_runs {filters} GROUP BY comparison''', params)
        result['comparison'] = {r['comparison']: r['cnt'] for r in rows}
        return result
    finally:
        conn.close()


def schedule_shadow(agent_type, input_data, production_output):
    """仅调度已接入入口，每请求最多一个实验；忙时跳过，不堆积后台队列。"""
    from db.config import get_config
    if agent_type not in _supported_types or get_config('llm_cost.auto_shadow_mode', 'false') != 'true':
        return 0
    if not production_output or not input_data.get('messages') or len(json.dumps(input_data, ensure_ascii=False)) > 60000:
        return 0
    if not _dispatch_lock.acquire(blocking=False):
        return 0
    try:
        configs = [c for c in list_shadow_configs() if c['agent_type'] == agent_type]
        random.shuffle(configs)
        selected = next((c for c in configs if random.random() < c['traffic_pct']), None)
        if not selected:
            _dispatch_lock.release()
            return 0
        def work():
            try:
                asyncio.run(run_shadow(selected['id'], input_data, production_output, agent_type))
            except Exception:
                logger.exception('Shadow 后台任务失败')
            finally:
                _dispatch_lock.release()
        _executor.submit(work)
        return 1
    except Exception:
        _dispatch_lock.release()
        raise


def _completion_call(**kwargs):
    import importlib
    llm_service = importlib.import_module('services.llm_service')
    from config import get_llm_config
    kwargs['model'] = getattr(llm_service, 'MODEL', None) or get_llm_config()[2]
    response = llm_service._call_llm(**kwargs)
    content = response if isinstance(response, str) else response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise ValueError('模型未返回文本')
    tokens = getattr(getattr(response, 'usage', None), 'total_tokens', 0) or 0
    return content, tokens


def _reserve_daily_usage(conn, reserve=False):
    # 同事务补记历史运行，兼容升级前的记录，并保留删除前已消费次数。
    if reserve:
        conn.execute("UPDATE shadow_daily_usage SET runs=runs+1 WHERE day=date('now','localtime')")
    else:
        conn.execute("""INSERT INTO shadow_daily_usage(day,runs)
            VALUES (date('now','localtime'), (SELECT COUNT(*) FROM shadow_runs WHERE created_at >= date('now','localtime')))
            ON CONFLICT(day) DO UPDATE SET runs=MAX(shadow_daily_usage.runs,excluded.runs)""")


async def run_shadow(config_id, input_data, production_output, agent_type=''):
    from db.config import get_config
    from db._conn import _get_conn
    if get_config('llm_cost.auto_shadow_mode', 'false') != 'true':
        return None
    conn = _get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM shadow_configs WHERE id=? AND is_active=1', (config_id,)).fetchone()
        # DB事务保证跨进程共享每日20次上限；计入失败，避免故障不断重试花费。
        _reserve_daily_usage(conn, reserve=False)
        count = conn.execute("SELECT COALESCE((SELECT runs FROM shadow_daily_usage WHERE day=date('now','localtime')),0)").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM shadow_runs WHERE status='running' AND created_at >= datetime('now','localtime','-10 minutes')").fetchone()[0]
        if not row or row['agent_type'] not in _supported_types or count >= 20 or running:
            conn.rollback()
            return None
        config = dict(row)
        cur = conn.execute('''INSERT INTO shadow_runs
            (config_id, agent_type, input_data, production_output, status) VALUES (?, ?, ?, ?, 'running')''',
            (config_id, config['agent_type'], json.dumps(input_data, ensure_ascii=False), production_output))
        run_id = cur.lastrowid
        _reserve_daily_usage(conn, reserve=True)
        conn.commit()
    finally:
        conn.close()
    started = time.monotonic()
    candidate, tokens, scores, error = '', 0, {}, ''
    status = 'execution_failed'
    try:
        messages = [dict(m) for m in input_data.get('messages', [])]
        if not messages:
            messages = [{'role':'user', 'content':json.dumps(input_data, ensure_ascii=False)}]
        system = next((m for m in messages if m.get('role') == 'system'), None)
        if system is None:
            messages.insert(0, {'role':'system', 'content':config['candidate_prompt']})
        else:
            # 原对话将RAG附在基础Prompt之后；只替换Prompt前缀，保留相同证据。
            base = input_data.get('base_prompt')
            suffix = system['content'][len(base):] if base and system['content'].startswith(base) else ''
            system['content'] = config['candidate_prompt'] + suffix
        candidate, tokens = await asyncio.to_thread(_completion_call, caller=f"shadow_{config['agent_type']}", messages=messages, temperature=0.3, max_tokens=3000)
        status = 'scoring_failed'
        scores = await _compare_outputs(input_data, production_output, candidate, config['agent_type'])
        tokens += scores.pop('token_usage', 0)
        status = 'scored'
    except Exception as exc:
        error = str(exc)[:1000]
        logger.warning('Shadow #%s %s: %s', config_id, status, error)
    duration = int((time.monotonic() - started)*1000)
    conn = _get_conn()
    try:
        conn.execute('''UPDATE shadow_runs SET shadow_output=?, production_score=?, shadow_score=?,
            score_reason=?, duration_ms=?, token_usage=?, status=?, error_msg=? WHERE id=?''',
            (candidate, scores.get('production_score'), scores.get('shadow_score'), scores.get('reason',''), duration, tokens, status, error, run_id))
        conn.commit()
    finally:
        conn.close()
    return {**scores, 'status':status, 'error':error, 'duration_ms':duration, 'run_id':run_id}


async def _compare_outputs(input_data, prod_output, shadow_output, agent_type):
    """评分失败抛出异常，绝不伪造中性有效分。比较文本窗口一致。"""
    prompt = f'''请评审以下投资分析输出（1到10分），关注数据依据、逻辑、可操作性、风险提示。
输入：{json.dumps(input_data, ensure_ascii=False)[:6000]}
Production：{prod_output[:10000]}
Shadow：{shadow_output[:10000]}
仅返回JSON：{{"production_score":7.5,"shadow_score":8,"reason":"说明"}}'''
    response, tokens = await asyncio.to_thread(_completion_call, caller='shadow_judge', messages=[{'role':'user','content':prompt}], temperature=0.1, max_tokens=500)
    start, end = response.find('{'), response.rfind('}')
    result = json.loads(response[start:end+1])
    for key in ['production_score','shadow_score']:
        val = result.get(key)
        if isinstance(val, bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or not 1 <= val <= 10:
            raise ValueError('评分不在1到10之间')
    return {**result, 'token_usage':tokens}


def toggle_shadow_config(config_id, is_active):
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        row = conn.execute('SELECT agent_type FROM shadow_configs WHERE id=?',(config_id,)).fetchone()
        if row and is_active and row['agent_type'] not in _supported_types:
            raise ValueError('该类型尚未接入生产执行，请创建chat实验')
        cur = conn.execute("UPDATE shadow_configs SET is_active=?, updated_at=datetime('now','localtime') WHERE id=?", (int(is_active),config_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_shadow_config(config_id):
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        with conn:
            _reserve_daily_usage(conn, reserve=False)
            conn.execute('DELETE FROM shadow_runs WHERE config_id=?',(config_id,))
            cur = conn.execute('DELETE FROM shadow_configs WHERE id=?',(config_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


async def auto_promote_shadows():
    """兼容定时入口：仅形成待人工审核建议，实验不会自动发布生产Prompt。"""
    recommendations = []
    for cfg in list_shadow_configs():
        stats = get_shadow_stats(cfg['id'])
        if stats['valid_runs'] >= 30 and (stats['shadow_win_rate'] or 0) > 0.6 and (stats['avg_shadow_score'] or 0) - (stats['avg_prod_score'] or 0) > 0.5:
            recommendations.append({'config_id':cfg['id'], 'agent_type':cfg['agent_type'], 'action':'review_required', 'runs':stats['valid_runs']})
    return recommendations
