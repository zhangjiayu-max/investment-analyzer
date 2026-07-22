"""领先指标聚合层 — 多 Provider 合并 + 去重 + 配置驱动。

核心职责：
1. 注册所有 Provider
2. 根据配置 alerts.leading_indicator_sources 启用对应 Provider（真正驱动代码）
3. 聚合去重，返回统一的 LeadingSignal 列表
"""
import logging

logger = logging.getLogger(__name__)

# Provider 注册表（provider_name → Provider 实例）
_PROVIDER_REGISTRY: dict = {}


def register_provider(provider):
    """注册 Provider 到全局注册表。"""
    _PROVIDER_REGISTRY[provider.provider_name] = provider
    logger.debug(f"[leading_indicators] 注册 Provider: {provider.provider_name}")


def _get_enabled_providers():
    """根据配置启用的 Provider 列表。真正驱动数据源选择。"""
    from db import get_config
    # 总开关
    if get_config("alerts.leading_indicator_enabled", "true") != "true":
        return []
    enabled_str = get_config("alerts.leading_indicator_sources", "policy_draft,capex,insider_trading")
    enabled_set = {s.strip() for s in enabled_str.split(",") if s.strip()}
    return [p for name, p in _PROVIDER_REGISTRY.items() if name in enabled_set]


def collect_leading_signals(lookback_days: int = 7):
    """聚合所有启用的 Provider，返回去重后的领先指标信号列表。

    Args:
        lookback_days: 回看天数

    Returns:
        list[LeadingSignal]
    """
    from db import get_config
    lookback = int(get_config("alerts.leading_indicator_lookback_days", str(lookback_days)))
    timeout = int(get_config("alerts.leading_indicator_fetch_timeout", "15"))

    providers = _get_enabled_providers()
    if not providers:
        logger.info("[leading_indicators] 无启用的 Provider，跳过")
        return []

    all_signals = []
    seen_keys = set()

    for provider in providers:
        try:
            signals = provider.fetch(lookback_days=lookback)
            for sig in signals:
                # 去重 key = provider_name + title + publish_date
                dedupe_key = f"{provider.provider_name}:{sig.title}:{sig.publish_date}"
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                all_signals.append(sig)
        except Exception as e:
            logger.warning(f"[leading_indicators] {provider.provider_name} 抓取失败: {e}")

    # 按发布日期降序
    all_signals.sort(key=lambda s: s.publish_date, reverse=True)
    logger.info(f"[leading_indicators] 聚合 {len(providers)} 个 Provider，共 {len(all_signals)} 条信号")
    return all_signals


# ── 自动注册所有 Provider ──
def _auto_register_all():
    """模块加载时自动注册所有 Provider。"""
    try:
        from services.market.leading_indicators.capex_provider import CapexProvider
        register_provider(CapexProvider())
    except Exception as e:
        logger.debug(f"[leading_indicators] capex_provider 注册失败: {e}")
    try:
        from services.market.leading_indicators.insider_trading_provider import InsiderTradingProvider
        register_provider(InsiderTradingProvider())
    except Exception as e:
        logger.debug(f"[leading_indicators] insider_trading_provider 注册失败: {e}")
    try:
        from services.market.leading_indicators.policy_draft_provider import PolicyDraftProvider
        register_provider(PolicyDraftProvider())
    except Exception as e:
        logger.debug(f"[leading_indicators] policy_draft_provider 注册失败: {e}")
    try:
        from services.market.leading_indicators.customs_provider import CustomsProvider
        register_provider(CustomsProvider())
    except Exception as e:
        logger.debug(f"[leading_indicators] customs_provider 注册失败: {e}")
    try:
        from services.market.leading_indicators.pmi_provider import PmiProvider
        register_provider(PmiProvider())
    except Exception as e:
        logger.debug(f"[leading_indicators] pmi_provider 注册失败: {e}")


# 模块加载时自动注册
_auto_register_all()
