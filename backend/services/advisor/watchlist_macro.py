"""关注列表宏观信号维度（P2-B）。

5 类宏观指标（akshare 数据源，与 tools/__init__.py 复用同款接口）：
- LPR（贷款市场报价利率）：1Y < 3.5% 宽松 +5，> 4.0% 收紧 -5
- SHIBOR 3M：< 1.5% 流动性充裕 +5，> 2.5% 偏紧 -5
- 美债 10Y：> 4.0% 全球资金压力 -5，< 3.5% 中性偏松 +3
- USDCNH 离岸人民币汇率：> 7.3 警惕 -5，< 7.0 利好 +3
- 政策信号：央行降准/降息 +8，加息/提准 -8

输出：
- macro_score: -15 ~ +15
- macro_signal: easing / tightening / neutral
- macro_reasons: List[str]
- macro_details: {lpr, shibor, us_treasury, usdcnh, policy}

闭合 P1 遗留断点：多维信号仅 tech/capital/sentiment 三维，缺宏观维度，
宏观转向（如美联储加息、人民币贬值）时单标的信号灯无法识别系统性风险。
"""
import logging
import threading
import time

from db.config import get_config_bool

logger = logging.getLogger(__name__)

_MACRO_CACHE = {}
_MACRO_CACHE_LOCK = threading.Lock()
_MACRO_CACHE_TTL = 60 * 60  # 1 小时（宏观数据日级更新）


def get_macro_score_cached() -> dict:
    """宏观信号（全局共享，1 小时缓存）。

    Returns:
        {
            "score": int,             # -15 ~ +15
            "signal": str,            # easing / tightening / neutral
            "reasons": List[str],
            "details": {lpr, shibor, us_treasury, usdcnh, policy},
            "disabled"?: bool         # 开关关闭时存在
        }
    """
    with _MACRO_CACHE_LOCK:
        cached = _MACRO_CACHE.get("macro")
        if cached and time.time() - cached[0] < _MACRO_CACHE_TTL:
            return cached[1]

    # 开关检查
    try:
        if not get_config_bool("watchlist.macro_signal_enabled", True):
            result = {"score": 0, "signal": "neutral", "reasons": [],
                      "details": {}, "disabled": True}
            return result
    except Exception:
        pass

    lpr_score, lpr_reason = _get_lpr_score()
    shibor_score, shibor_reason = _get_shibor_score()
    us_treasury_score, us_treasury_reason = _get_us_treasury_score()
    usdcnh_score, usdcnh_reason = _get_usdcnh_score()
    policy_score, policy_reason = _get_policy_score()

    total_score = (lpr_score + shibor_score + us_treasury_score
                   + usdcnh_score + policy_score)
    if total_score >= 8:
        signal = "easing"
    elif total_score <= -8:
        signal = "tightening"
    else:
        signal = "neutral"

    result = {
        "score": total_score,
        "signal": signal,
        "reasons": [r for r in [lpr_reason, shibor_reason, us_treasury_reason,
                                 usdcnh_reason, policy_reason] if r],
        "details": {
            "lpr": lpr_score,
            "shibor": shibor_score,
            "us_treasury": us_treasury_score,
            "usdcnh": usdcnh_score,
            "policy": policy_score,
        },
    }
    with _MACRO_CACHE_LOCK:
        _MACRO_CACHE["macro"] = (time.time(), result)
    return result


def _get_lpr_score() -> tuple[int, str]:
    """LPR 1Y 评分：宽松 +5 / 收紧 -5 / 中性 0。"""
    try:
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        import akshare as ak
        df = ak.macro_china_lpr()
        if df is None or df.empty:
            return 0, ""
        latest = df.iloc[-1]
        lpr_1y = float(latest.get("LPR1Y", 0) or 0)
        if lpr_1y == 0:
            return 0, ""
        if lpr_1y < 3.5:
            return 5, f"LPR 1Y {lpr_1y}% 宽松"
        elif lpr_1y > 4.0:
            return -5, f"LPR 1Y {lpr_1y}% 偏紧"
        return 0, ""
    except Exception as _e:
        logger.debug(f"[macro] LPR 获取失败: {_e}")
        return 0, ""


def _get_shibor_score() -> tuple[int, str]:
    """SHIBOR 3M 评分：充裕 +5 / 偏紧 -5 / 中性 0。"""
    try:
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        import akshare as ak
        df = ak.macro_china_shibor_all()
        if df is None or df.empty:
            return 0, ""
        latest = df.iloc[-1]
        shibor_3m = float(latest.get("3M-定价", 0) or 0)
        if shibor_3m == 0:
            return 0, ""
        if shibor_3m < 1.5:
            return 5, f"SHIBOR 3M {shibor_3m}% 流动性充裕"
        elif shibor_3m > 2.5:
            return -5, f"SHIBOR 3M {shibor_3m}% 偏紧"
        return 0, ""
    except Exception as _e:
        logger.debug(f"[macro] SHIBOR 获取失败: {_e}")
        return 0, ""


def _get_us_treasury_score() -> tuple[int, str]:
    """美债 10Y 评分：压力 -5 / 偏松 +3 / 中性 0。"""
    try:
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        import akshare as ak
        df = ak.bond_zh_us_rate()
        if df is None or df.empty:
            return 0, ""
        latest = df.iloc[-1]
        us_10y = latest.get("美国国债收益率10年")
        if us_10y is None:
            return 0, ""
        us_10y = float(us_10y)
        if us_10y > 4.0:
            return -5, f"美债 10Y {us_10y}% 全球资金压力"
        elif us_10y < 3.5:
            return 3, f"美债 10Y {us_10y}% 中性偏松"
        return 0, ""
    except Exception as _e:
        logger.debug(f"[macro] 美债收益率获取失败: {_e}")
        return 0, ""


def _get_usdcnh_score() -> tuple[int, str]:
    """USDCNH 离岸人民币汇率评分：贬值警惕 -5 / 升值利好 +3 / 中性 0。

    使用 akshare currency_boc_safe（中国银行外汇牌价）。
    """
    try:
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        import akshare as ak
        # 中行外汇牌价（美元）
        try:
            df = ak.currency_boc_safe(symbol="美元", start_date="20250101", end_date="20260721")
        except Exception:
            # 旧接口兜底
            df = ak.fx_boc_sina(symbol="美元")
        if df is None or df.empty:
            return 0, ""
        latest = df.iloc[-1]
        # 中行外汇牌价字段：现汇卖出价/现钞卖出价/现汇买入价等
        rate = None
        for col in ["现汇卖出价", "中行折算价", "现汇买入价"]:
            v = latest.get(col)
            if v is not None:
                try:
                    rate = float(v) / 100.0  # 中行报价通常以 100 美元为本金
                    break
                except (ValueError, TypeError):
                    continue
        if rate is None or rate == 0:
            return 0, ""
        if rate > 7.3:
            return -5, f"USDCNH {rate:.2f} 贬值压力"
        elif rate < 7.0:
            return 3, f"USDCNH {rate:.2f} 升值利好"
        return 0, ""
    except Exception as _e:
        logger.debug(f"[macro] USDCNH 获取失败: {_e}")
        return 0, ""


def _get_policy_score() -> tuple[int, str]:
    """政策信号评分：央行降准/降息 +8 / 加息/提准 -8 / 中性 0。

    复用 tools/__init__.py _get_macro_policy_data 中的 RRR 调整幅度判断。
    """
    try:
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        import akshare as ak
        df = ak.macro_china_reserve_requirement_ratio()
        if df is None or df.empty:
            return 0, ""
        latest = df.iloc[0]  # 最新一条
        rrr_change = float(latest.get("大型金融机构-调整幅度", 0) or 0)
        if rrr_change < 0:
            return 8, f"近期降准 {abs(rrr_change)} 个百分点，政策宽松"
        elif rrr_change > 0:
            return -8, f"近期提准 {rrr_change} 个百分点，政策收紧"
        return 0, ""
    except Exception as _e:
        logger.debug(f"[macro] 政策信号获取失败: {_e}")
        return 0, ""
