"""会话信号检测 — 从运行时事实中提取语义信号

借鉴 ContextGo 的 Session Signals 设计：
- user_interrupt: 用户中断
- repeated_request: 重复请求
- strategy_shift: 策略变化
- tool_failure_cluster: 工具连续失败
- memory_candidate_created: 记忆候选产生
- context_window_prepared: 上下文窗口准备完成

信号用于：
1. 自适应调整 Agent 行为
2. 触发记忆维护任务
3. 提升用户体验
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ── 信号存储 ──────────────────────────────────────────

# 会话级信号（内存中，会话结束即清除）
_session_signals = defaultdict(list)

# 信号检测器注册表
_detectors = []


def register_detector(name: str, detector_func, priority: int = 50):
    """注册一个信号检测器。"""
    _detectors.append({
        "name": name,
        "detector": detector_func,
        "priority": priority,
    })
    _detectors.sort(key=lambda d: d["priority"])


def detect_signals(conversation_id: int, event_type: str, event_data: dict) -> list:
    """检测信号。

    参数:
        conversation_id: 对话ID
        event_type: 事件类型 (user_message|tool_call|agent_response|error)
        event_data: 事件数据

    返回:
        检测到的信号列表 [{"signal": str, "data": dict}]
    """
    signals = []
    for detector in _detectors:
        try:
            result = detector["detector"](conversation_id, event_type, event_data)
            if result:
                if isinstance(result, list):
                    signals.extend(result)
                else:
                    signals.append(result)
        except Exception as e:
            logger.warning(f"信号检测器 {detector['name']} 失败: {e}")

    # 存储信号
    for sig in signals:
        _session_signals[conversation_id].append({
            "signal": sig.get("signal", ""),
            "data": sig.get("data", {}),
            "timestamp": datetime.now().isoformat(),
        })

    return signals


def get_session_signals(conversation_id: int, signal_type: str = None) -> list:
    """获取会话信号。"""
    signals = _session_signals.get(conversation_id, [])
    if signal_type:
        return [s for s in signals if s["signal"] == signal_type]
    return signals


def clear_session_signals(conversation_id: int):
    """清除会话信号。"""
    _session_signals.pop(conversation_id, None)


# ── 内置信号检测器 ──────────────────────────────────────


def detect_repeated_request(conversation_id: int, event_type: str, event_data: dict) -> dict:
    """检测重复请求。"""
    if event_type != "user_message":
        return None

    query = event_data.get("content", "")
    if not query:
        return None

    # 获取历史用户消息
    signals = get_session_signals(conversation_id, "user_message")
    recent_queries = [s["data"].get("content", "") for s in signals[-5:]]

    # 简单相似度检测
    for prev in recent_queries:
        if _is_similar(query, prev):
            return {
                "signal": "repeated_request",
                "data": {"current": query[:100], "previous": prev[:100]},
            }

    # 记录用户消息信号
    _session_signals[conversation_id].append({
        "signal": "user_message",
        "data": {"content": query[:200]},
        "timestamp": datetime.now().isoformat(),
    })

    return None


def detect_tool_failure_cluster(conversation_id: int, event_type: str, event_data: dict) -> dict:
    """检测工具连续失败。"""
    if event_type != "error":
        return None

    tool_name = event_data.get("tool_name", "")
    if not tool_name:
        return None

    # 统计最近的工具失败
    signals = get_session_signals(conversation_id, "tool_failure")
    recent_failures = [s for s in signals if s["data"].get("tool_name") == tool_name]

    # 记录本次失败
    _session_signals[conversation_id].append({
        "signal": "tool_failure",
        "data": {"tool_name": tool_name, "error": event_data.get("error", "")[:200]},
        "timestamp": datetime.now().isoformat(),
    })

    # 如果同一工具连续失败 3 次以上
    if len(recent_failures) >= 2:  # 加上本次共 3 次
        return {
            "signal": "tool_failure_cluster",
            "data": {
                "tool_name": tool_name,
                "failure_count": len(recent_failures) + 1,
                "hint": f"工具 {tool_name} 连续失败 {len(recent_failures) + 1} 次，建议切换到备用方案",
            },
        }

    return None


def detect_strategy_shift(conversation_id: int, event_type: str, event_data: dict) -> dict:
    """检测用户策略变化。"""
    if event_type != "user_message":
        return None

    query = event_data.get("content", "")

    # 策略变化关键词
    shift_keywords = [
        "换一种", "换个方式", "不对", "重新", "从头来",
        "不是这个", "我要的是", "我说的是",
    ]

    for kw in shift_keywords:
        if kw in query:
            return {
                "signal": "strategy_shift",
                "data": {"query": query[:200], "keyword": kw},
            }

    return None


def detect_user_frustration(conversation_id: int, event_type: str, event_data: dict) -> dict:
    """检测用户沮丧/不满。"""
    if event_type != "user_message":
        return None

    query = event_data.get("content", "")

    # 沮丧关键词
    frustration_keywords = [
        "什么鬼", "垃圾", "没用", "太差了", "不对",
        "为什么又", "怎么还是", "说了多少遍",
        "？？？", "!!!", "。。。", "???",
    ]

    for kw in frustration_keywords:
        if kw in query:
            return {
                "signal": "user_frustration",
                "data": {"query": query[:200], "keyword": kw},
            }

    return None


def detect_long_silence(conversation_id: int, event_type: str, event_data: dict) -> dict:
    """检测长时间沉默后返回。"""
    if event_type != "user_message":
        return None

    signals = get_session_signals(conversation_id)
    if not signals:
        return None

    # 找到最后一条信号的时间
    last_signal = signals[-1]
    try:
        last_time = datetime.fromisoformat(last_signal["timestamp"])
        now = datetime.now()
        gap = (now - last_time).total_seconds()

        # 如果沉默超过 30 分钟
        if gap > 1800:
            return {
                "signal": "long_silence",
                "data": {"gap_seconds": gap, "hint": "用户离开了一段时间，可能需要回顾之前的讨论"},
            }
    except Exception:
        pass

    return None


def _is_similar(q1: str, q2: str) -> bool:
    """简单相似度判断。"""
    import re
    q1_clean = re.sub(r'[^\w]', '', q1)
    q2_clean = re.sub(r'[^\w]', '', q2)

    if not q1_clean or not q2_clean:
        return False

    if q1_clean == q2_clean:
        return True

    if abs(len(q1_clean) - len(q2_clean)) < 5:
        common = sum(1 for c in q1_clean if c in q2_clean)
        if common / max(len(q1_clean), len(q2_clean)) > 0.8:
            return True

    return False


# ── 信号驱动的行为调整 ──────────────────────────────────


def get_adaptive_behavior(conversation_id: int) -> dict:
    """根据会话信号返回自适应行为建议。

    返回:
        {
            "should_ask_clarification": bool,  # 是否需要澄清
            "should_switch_strategy": bool,     # 是否切换策略
            "should_inject_context": str,       # 需要注入的额外上下文
            "tone_adjustment": str,             # 语气调整
        }
    """
    signals = get_session_signals(conversation_id)
    if not signals:
        return {}

    behavior = {}

    # 检查重复请求
    repeated = [s for s in signals if s["signal"] == "repeated_request"]
    if len(repeated) >= 2:
        behavior["should_ask_clarification"] = True
        behavior["clarification_hint"] = "您似乎在重复同样的问题，是否需要我换一种方式解释？"

    # 检查工具失败
    tool_failures = [s for s in signals if s["signal"] == "tool_failure_cluster"]
    if tool_failures:
        behavior["should_switch_strategy"] = True
        behavior["strategy_hint"] = tool_failures[-1]["data"].get("hint", "")

    # 检查用户沮丧
    frustration = [s for s in signals if s["signal"] == "user_frustration"]
    if frustration:
        behavior["tone_adjustment"] = "empathetic"
        behavior["tone_hint"] = "用户似乎有些不满，请更加耐心和详细地解释"

    # 检查策略变化
    strategy_shift = [s for s in signals if s["signal"] == "strategy_shift"]
    if strategy_shift:
        behavior["should_inject_context"] = f"用户希望改变策略: {strategy_shift[-1]['data'].get('query', '')[:100]}"

    # 检查长时间沉默
    long_silence = [s for s in signals if s["signal"] == "long_silence"]
    if long_silence:
        behavior["should_inject_context"] = "用户离开了一段时间，可能需要简要回顾之前的讨论"

    return behavior


# ── 注册内置检测器 ──────────────────────────────────────


def register_builtin_detectors():
    """注册所有内置信号检测器。"""
    register_detector("repeated_request", detect_repeated_request, priority=10)
    register_detector("tool_failure_cluster", detect_tool_failure_cluster, priority=20)
    register_detector("strategy_shift", detect_strategy_shift, priority=30)
    register_detector("user_frustration", detect_user_frustration, priority=40)
    register_detector("long_silence", detect_long_silence, priority=50)

    logger.info("内置信号检测器已注册")


# 自动注册
register_builtin_detectors()
