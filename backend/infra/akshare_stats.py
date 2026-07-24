"""akshare 调用统计 — 成功率监控、失败分类、自动预警。

设计目标：
- 每次调用按 outcome 分类（success/timeout/failure/circuit_open/anti_crawl）
- 按 fn_name 聚合，提供 1h/24h 滚动窗口
- 失败率 > 50% 时自动 ERROR 日志，便于运维定位
- 通过 /api/admin/akshare-stats API 暴露统计数据

G-akshare-stats（2026-07-24）：解决"成功率无统计、失败降级不知情"问题。
案例：conv#136 后用户反馈 akshare 持续降级，但开发者完全无感知。
"""
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 统计保留窗口：24h（每条记录带时间戳，查询时按窗口过滤）
_MAX_RECORDS = 5000
_RETENTION_SECONDS = 24 * 3600  # 24h

_lock = threading.Lock()
_records: deque = deque(maxlen=_MAX_RECORDS)  # [(ts, fn_name, outcome, error_msg)]


@dataclass
class FnStats:
    """单个 akshare 函数的聚合统计。"""
    success: int = 0
    timeout: int = 0
    failure: int = 0          # akshare 抛异常（如 JSONDecodeError）
    circuit_open: int = 0     # 熔断器开启导致跳过
    anti_crawl: int = 0       # 反爬检测命中（返回 HTML 而非 JSON）
    last_error: str = ""
    last_success_ts: float = 0
    last_failure_ts: float = 0

    @property
    def total(self) -> int:
        return self.success + self.timeout + self.failure + self.circuit_open + self.anti_crawl

    @property
    def success_rate(self) -> float:
        t = self.total
        return round(self.success / t, 4) if t > 0 else 0.0

    @property
    def failure_rate(self) -> float:
        t = self.total
        if t == 0:
            return 0.0
        return round((self.failure + self.timeout + self.anti_crawl) / t, 4)


# ── 标记反爬特征 ──────────────────────────────────────────
# akshare 内部 demjson 解析失败时抛 JSONDecodeError，
# 错误信息常见模式："Can not decode value starting with character ';'"
# 此时东财/中财网返回的是 HTML/JS 而非 JSON，属于反爬或限流。
_ANTI_CRAWL_MARKERS = (
    "Can not decode value starting with character",
    "Length mismatch: Expected axis has 1 elements",  # 列数不匹配（页面结构变化）
    "Expecting value: line 1 column 1",               # JSON 解析失败
    "<!DOCTYPE",                                       # HTML 返回
    "<html",
)


def is_anti_crawl_error(error_msg: str) -> bool:
    """判断错误是否为反爬/限流导致（返回了非 JSON 内容）。"""
    if not error_msg:
        return False
    err_lower = error_msg.lower()
    return any(marker.lower() in err_lower for marker in _ANTI_CRAWL_MARKERS)


def record_call(fn_name: str, outcome: str, error_msg: str = "") -> None:
    """记录一次 akshare 调用结果。

    Args:
        fn_name: akshare 函数名（如 fund_portfolio_hold_em）
        outcome: success | timeout | failure | circuit_open | anti_crawl
        error_msg: 失败时的错误信息（用于 last_error 和反爬分类）
    """
    ts = time.time()
    # 反爬特征识别：失败错误信息含反爬标记则归为 anti_crawl
    if outcome == "failure" and is_anti_crawl_error(error_msg):
        outcome = "anti_crawl"

    with _lock:
        _records.append((ts, fn_name, outcome, error_msg[:200]))

    # 失败时 debug 日志（避免反爬高频时刷屏）
    if outcome != "success":
        logger.debug(f"[akshare-stats] {fn_name} {outcome}: {error_msg[:100]}")


def get_stats(window_seconds: int = 3600) -> dict:
    """获取最近 window_seconds 秒内的统计。

    Returns:
        {
            "window_seconds": 3600,
            "total_calls": 100,
            "overall_success_rate": 0.85,
            "by_function": [
                {"fn_name": "...", "success": 80, "timeout": 5, ...},
                ...
            ]
        }
    """
    cutoff = time.time() - window_seconds
    by_fn: dict[str, FnStats] = defaultdict(FnStats)

    with _lock:
        records_snapshot = list(_records)

    total_calls = 0
    total_success = 0
    for ts, fn_name, outcome, err in records_snapshot:
        if ts < cutoff:
            continue
        stats = by_fn[fn_name]
        if outcome == "success":
            stats.success += 1
            stats.last_success_ts = ts
        elif outcome == "timeout":
            stats.timeout += 1
            stats.last_failure_ts = ts
            if not stats.last_error:
                stats.last_error = err
        elif outcome == "failure":
            stats.failure += 1
            stats.last_failure_ts = ts
            stats.last_error = err
        elif outcome == "anti_crawl":
            stats.anti_crawl += 1
            stats.last_failure_ts = ts
            stats.last_error = err
        elif outcome == "circuit_open":
            stats.circuit_open += 1
        total_calls += 1
        if outcome == "success":
            total_success += 1

    by_function = []
    for fn_name, stats in sorted(by_fn.items(), key=lambda x: -x[1].total):
        by_function.append({
            "fn_name": fn_name,
            "success": stats.success,
            "timeout": stats.timeout,
            "failure": stats.failure,
            "anti_crawl": stats.anti_crawl,
            "circuit_open": stats.circuit_open,
            "total": stats.total,
            "success_rate": stats.success_rate,
            "failure_rate": stats.failure_rate,
            "last_error": stats.last_error,
            "last_success_ts": int(stats.last_success_ts) if stats.last_success_ts else None,
            "last_failure_ts": int(stats.last_failure_ts) if stats.last_failure_ts else None,
        })

    overall_rate = round(total_success / total_calls, 4) if total_calls > 0 else 0.0

    # 失败率 > 50% 且样本量足够时输出 ERROR 预警
    if total_calls >= 10 and overall_rate < 0.5:
        failing_fns = [f for f in by_function if f["success_rate"] < 0.5 and f["total"] >= 5]
        if failing_fns:
            logger.error(
                f"[akshare-stats] 整体成功率 {overall_rate*100:.1f}% 低于阈值 50%，"
                f"高失败接口: {[(f['fn_name'], f['success_rate']) for f in failing_fns[:3]]}"
            )

    return {
        "window_seconds": window_seconds,
        "total_calls": total_calls,
        "overall_success_rate": overall_rate,
        "overall_failure_rate": round(1 - overall_rate, 4) if total_calls > 0 else 0.0,
        "by_function": by_function,
    }


def reset() -> None:
    """重置统计（测试用）。"""
    with _lock:
        _records.clear()
