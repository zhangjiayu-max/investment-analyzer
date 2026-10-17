"""核心就绪检查，区别轻量存活与可选向量依赖。"""
import sys
from db._conn import _get_conn


def check_readiness():
    dependencies = {}
    try:
        conn = _get_conn()
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = sorted({'system_config','portfolio_holdings','conversations'} - tables)
            dependencies['sqlite'] = {'status':'ready' if not missing else 'uninitialized', 'missing_tables':missing}
        finally:
            conn.close()
    except Exception:
        dependencies['sqlite'] = {'status':'unavailable'}
    rag = sys.modules.get('services.rag.rag')
    dependencies['vector_search'] = {'status':'ready' if rag and getattr(rag,'_chroma_collection',None) is not None else 'not_initialized',
                                     'required':False}
    return {'ready':dependencies['sqlite']['status']=='ready', 'dependencies':dependencies}
