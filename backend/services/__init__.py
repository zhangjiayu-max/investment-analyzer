"""services 包 — 服务层模块重组后的向后兼容层。

服务文件已按功能归入子目录（llm/ rag/ fund/ portfolio/ valuation/
advisor/ market/ strategy/ content/ conversation/ finance/ quality/ index/），
但所有旧的 ``from services.xxx import yyy`` 导入路径仍可通过本兼容层正常工作。

实现方式：
- 通过 sys.meta_path 安装自定义查找器，将 ``services.<old_name>`` 的
  导入请求懒重定向到 ``services.<subdir>.<old_name>``。
- 对于 ``services.rag`` 和 ``services.valuation``（旧模块名与新子包名冲突），
  在对应子包的 ``__init__.py`` 中通过 ``__getattr__`` 转发属性访问。
"""

import importlib
import importlib.util
import sys


# 旧短名 → 新子路径（相对于 services 包）
# 注意：rag / valuation 不在此表中（与子包名冲突，在各自 __init__.py 中处理）
_ALIAS_MAP = {
    # llm
    "llm_service": "llm.llm_service",
    # rag（rag 本身冲突，仅 rag_enhanced 在此）
    "rag_enhanced": "rag.rag_enhanced",
    # fund
    "fund_analysis": "fund.fund_analysis",
    "fund_data_service": "fund.fund_data_service",
    "fund_manager": "fund.fund_manager",
    # portfolio
    "portfolio_intelligence": "portfolio.portfolio_intelligence",
    "portfolio_optimizer": "portfolio.portfolio_optimizer",
    "portfolio_context": "portfolio.portfolio_context",
    "portfolio_fact_layer": "portfolio.portfolio_fact_layer",
    "rebalancer": "portfolio.rebalancer",
    "attribution": "portfolio.attribution",
    "behavior_diagnosis": "portfolio.behavior_diagnosis",
    "stress_test": "portfolio.stress_test",
    "allocation_dashboard": "portfolio.allocation_dashboard",
    # valuation（valuation 本身冲突，仅子模块在此）
    "valuation_forecast": "valuation.valuation_forecast",
    "valuation_parser": "valuation.valuation_parser",
    "valuation_fusion": "valuation.valuation_fusion",
    # advisor
    "daily_position_advisor": "advisor.daily_position_advisor",
    "alert_scanner": "advisor.alert_scanner",
    "trade_plan_engine": "advisor.trade_plan_engine",
    "smart_add_planner": "advisor.smart_add_planner",
    "smart_add_metrics": "advisor.smart_add_metrics",
    "opportunity_engine": "advisor.opportunity_engine",
    "master_perspectives": "advisor.master_perspectives",
    # market
    "market_data": "market.market_data",
    "institutional_flow": "market.institutional_flow",
    "alert_news_service": "market.alert_news_service",
    "event_radar": "market.event_radar",
    # strategy
    "strategy_library": "strategy.strategy_library",
    "strategy_sandbox": "strategy.strategy_sandbox",
    "strategy_monitor": "strategy.strategy_monitor",
    # content
    "article_reader": "content.article_reader",
    "image_parser": "content.image_parser",
    # conversation
    "conversation_context": "conversation.conversation_context",
    "conversation_state": "conversation.conversation_state",
    "user_memory_service": "conversation.user_memory_service",
    # finance
    "finance_planner": "finance.finance_planner",
    "bucket_engine": "finance.bucket_engine",
    # quality
    "suggestion_accuracy": "quality.suggestion_accuracy",
    "decision_accuracy": "quality.decision_accuracy",
    "data_quality_report": "quality.data_quality_report",
    "data_lineage": "quality.data_lineage",
    # index
    "index_fund_mapper": "index.index_fund_mapper",
    "index_history_fetcher": "index.index_history_fetcher",
}


class _AliasLoader:
    """加载器：导入真实模块并将其作为别名模块返回。"""

    def __init__(self, real_name):
        self.real_name = real_name

    def create_module(self, spec):
        return importlib.import_module(self.real_name)

    def exec_module(self, module):
        # 真实模块在 create_module 中已完成初始化，无需重复执行
        pass


class _ServicesAliasFinder:
    """将 ``services.<old_name>`` 的导入懒重定向到 ``services.<subdir>.<old_name>``。

    仅处理 ``services`` 的直接子模块名（不含更深层的点号），避免拦截
    ``services.rag.rag_enhanced`` 这类新路径的正常导入。
    """

    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith("services."):
            return None
        short = fullname[len("services."):]
        if "." in short or short not in _ALIAS_MAP:
            return None
        real_name = "services." + _ALIAS_MAP[short]
        return importlib.util.spec_from_loader(fullname, _AliasLoader(real_name))


# 安装到 meta_path 最前面，确保优先于默认文件查找器
if not any(isinstance(f, _ServicesAliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _ServicesAliasFinder())
