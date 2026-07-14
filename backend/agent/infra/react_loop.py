"""
ReAct 循环 + 死循环检测 — 升级六。

实现"思考→行动→观察→再思考"的多步推理流程。

核心机制：
1. LLM 输出 Thought + Action，解析后调用工具得到 Observation
2. 将 Observation 加入上下文，继续下一轮思考
3. 死循环检测：
   - 相同 Action 签名（工具名+参数）连续出现 N 次 → 终止
   - 总轮次超过 MAX_ITERATIONS → 终止
   - 上下文长度超过阈值 → 终止
4. 终止后输出最终答案

注意：该模块为可选的高级推理路径，仅在复杂查询且 router 显式触发时启用。
"""

import json
import logging
import re
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5  # 最大推理轮次
DUPLICATE_ACTION_THRESHOLD = 2  # 相同 Action 连续出现此次数视为死循环
MAX_CONTEXT_CHARS = 8000  # 上下文长度上限


def _parse_react_response(text: str) -> dict:
    """
    解析 LLM 的 ReAct 输出。

    期望格式（宽松解析）：
        Thought: ...
        Action: {"tool": "...", "arguments": {...}}
    或
        Thought: ...
        Final Answer: ...

    返回 {"thought": str, "action": dict|None, "final_answer": str|None}
    """
    result: dict[str, Any] = {"thought": "", "action": None, "final_answer": None}

    # Thought
    m = re.search(r"Thought:\s*(.*?)(?=\n(?:Action|Final Answer):|$)", text, re.S | re.I)
    if m:
        result["thought"] = m.group(1).strip()

    # Final Answer
    m = re.search(r"Final Answer:\s*(.*?)(?=\n(?:Thought|Action):|$)", text, re.S | re.I)
    if m:
        result["final_answer"] = m.group(1).strip()
        return result

    # Action：支持 JSON 或 "tool_name(arg=val)" 两种格式
    m = re.search(r"Action:\s*(.*?)(?=\n(?:Thought|Final Answer|Observation):|$)", text, re.S | re.I)
    if m:
        action_str = m.group(1).strip()
        # 尝试 JSON 解析
        if action_str.startswith("{"):
            try:
                result["action"] = json.loads(action_str)
            except json.JSONDecodeError:
                # 容错：尝试提取 tool/arguments
                tm = re.search(r'"tool"\s*:\s*"([^"]+)"', action_str)
                if tm:
                    result["action"] = {"tool": tm.group(1), "arguments": {}}
        else:
            # 简单格式：tool_name
            result["action"] = {"tool": action_str.split("(")[0].strip(), "arguments": {}}

    return result


def _action_signature(action: dict) -> str:
    """生成 Action 的唯一签名（用于死循环检测）。"""
    if not action:
        return ""
    return f"{action.get('tool', '')}:{json.dumps(action.get('arguments', {}), sort_keys=True)}"


def detect_loop(action_history: list[str]) -> bool:
    """
    死循环检测：
    - 同一签名连续出现 >= DUPLICATE_ACTION_THRESHOLD 次
    - 或同一签名累计出现 >= 3 次
    """
    if not action_history:
        return False

    # 连续重复
    last = action_history[-1]
    consecutive = 1
    for sig in reversed(action_history[:-1]):
        if sig == last:
            consecutive += 1
        else:
            break
    if consecutive >= DUPLICATE_ACTION_THRESHOLD:
        logger.warning(f"[react_loop] 死循环检测：Action 连续重复 {consecutive} 次")
        return True

    # 累计重复
    counts = defaultdict(int)
    for sig in action_history:
        counts[sig] += 1
    for sig, c in counts.items():
        if c >= 3:
            logger.warning(f"[react_loop] 死循环检测：Action {sig} 累计出现 {c} 次")
            return True

    return False


def run_react_loop(
    query: str,
    llm_call_fn,
    execute_tool_fn,
    max_iterations: int = MAX_ITERATIONS,
    trace_id: str = "",
) -> dict:
    """
    执行 ReAct 循环。

    Args:
        query: 用户问题
        llm_call_fn: async (messages) -> str  LLM 调用函数
        execute_tool_fn: (name, arguments, trace_id) -> str  工具执行函数
        max_iterations: 最大轮次
        trace_id: 链路 ID

    Returns:
        {
            "answer": str,
            "iterations": int,
            "steps": [{"thought", "action", "observation"}, ...],
            "terminated_reason": str | None,  # "loop_detected" | "max_iterations" | "context_limit" | None
        }
    """
    steps: list[dict] = []
    action_history: list[str] = []
    context = f"问题：{query}\n\n请使用 ReAct 格式推理：Thought / Action / Final Answer。"

    system_prompt = (
        "你是投资分析助手。请使用 ReAct（Reasoning + Acting）框架逐步推理。\n"
        "每一步输出：\n"
        "Thought: 你的思考\n"
        "Action: {\"tool\": \"工具名\", \"arguments\": {...}}\n"
        "当得到足够信息后，输出：\n"
        "Thought: ...\n"
        "Final Answer: 最终答案\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ]

    terminated_reason = None

    for i in range(max_iterations):
        # 上下文长度保护
        total_chars = sum(len(m["content"]) for m in messages)
        if total_chars > MAX_CONTEXT_CHARS:
            terminated_reason = "context_limit"
            logger.warning(f"[react_loop:{trace_id}] 上下文超限 ({total_chars} chars)，终止")
            break

        # 调用 LLM
        try:
            response = llm_call_fn(messages)
        except Exception as e:
            logger.error(f"[react_loop:{trace_id}] LLM 调用失败: {e}")
            terminated_reason = "llm_error"
            break

        parsed = _parse_react_response(response)

        # 已得到最终答案
        if parsed["final_answer"]:
            steps.append({
                "thought": parsed["thought"],
                "action": None,
                "observation": None,
                "final_answer": parsed["final_answer"],
            })
            return {
                "answer": parsed["final_answer"],
                "iterations": i + 1,
                "steps": steps,
                "terminated_reason": None,
            }

        # 执行 Action
        action = parsed["action"]
        if not action or not action.get("tool"):
            # LLM 未输出有效 Action 也未给出 Final Answer，强制结束
            steps.append({
                "thought": parsed["thought"],
                "action": None,
                "observation": None,
                "final_answer": response[:500],
            })
            return {
                "answer": response[:500] or "无法得出结论",
                "iterations": i + 1,
                "steps": steps,
                "terminated_reason": "no_action",
            }

        sig = _action_signature(action)
        action_history.append(sig)

        # 死循环检测
        if detect_loop(action_history):
            terminated_reason = "loop_detected"
            logger.warning(f"[react_loop:{trace_id}] 检测到死循环，终止推理")
            steps.append({
                "thought": parsed["thought"],
                "action": action,
                "observation": "[死循环检测] 重复的工具调用已终止",
                "final_answer": None,
            })
            break

        # 执行工具
        try:
            observation = execute_tool_fn(
                action["tool"],
                action.get("arguments", {}),
                trace_id,
            )
        except Exception as e:
            observation = f"工具执行失败: {e}"

        steps.append({
            "thought": parsed["thought"],
            "action": action,
            "observation": observation[:500] if observation else "",
            "final_answer": None,
        })

        # 将 Observation 加入上下文
        messages.append({"role": "assistant", "content": response})
        messages.append({
            "role": "user",
            "content": f"Observation: {observation[:800]}\n\n请继续推理，或给出 Final Answer。",
        })

    # 达到最大轮次仍未得到答案
    if not terminated_reason:
        terminated_reason = "max_iterations"

    # 尝试让 LLM 基于已有信息给出最终答案
    final_answer = "经过多步推理未能得出明确结论。"
    try:
        messages.append({
            "role": "user",
            "content": "已达到推理步数上限，请基于以上信息给出 Final Answer。",
        })
        resp = llm_call_fn(messages)
        parsed = _parse_react_response(resp)
        if parsed["final_answer"]:
            final_answer = parsed["final_answer"]
        elif resp:
            final_answer = resp[:800]
    except Exception as e:
        logger.warning(f"[react_loop:{trace_id}] 最终答案生成失败: {e}")

    return {
        "answer": final_answer,
        "iterations": len(steps),
        "steps": steps,
        "terminated_reason": terminated_reason,
    }
