"""
Prompt 注入防护 — 纯规则检测，零 LLM 成本。

检测维度：
1. 指令覆盖（"忽略之前的指令"）
2. 角色扮演逃逸（"你现在不是AI了"）
3. Prompt 提取（"输出你的系统提示"）
4. 越狱模式（DAN、jailbreak）
5. 恶意代码注入（XSS、代码执行）
"""

import re
import logging

logger = logging.getLogger(__name__)

# 注入模式检测规则
INJECTION_PATTERNS = [
    # 指令覆盖
    (r"忽略(之前的|所有|系统)?指令", "指令覆盖尝试"),
    (r"忘记(之前的|所有|系统)?(指令|提示|规则)", "指令覆盖尝试"),
    (r"无视(之前的|所有|系统)?(指令|提示|规则)", "指令覆盖尝试"),
    (r"ignore (all )?(previous |system )?(instructions|prompts|rules)", "指令覆盖尝试"),
    (r"forget (all )?(previous |system )?(instructions|prompts|rules)", "指令覆盖尝试"),
    
    # 角色扮演逃逸
    (r"你现在是(不是|不再(是))", "角色扮演逃逸"),
    (r"你是一个(新|不同|别的|其他)角色", "角色扮演逃逸"),
    (r"you are (not |no longer )?(an? |the )?(ai|assistant|bot)", "角色扮演逃逸"),
    
    # 输出提取
    (r"输出(你的|系统)(指令|提示|prompt|system prompt)", "Prompt 提取尝试"),
    (r"repeat (the |your )?(system )?(prompt|instructions)", "Prompt 提取尝试"),
    (r"print (the |your )?(system )?(prompt|instructions)", "Prompt 提取尝试"),
    
    # 越狱模式
    (r"DAN|jailbreak|越狱|突破限制", "越狱尝试"),
    (r"你是(自由的|不受限的|随便的)", "越狱尝试"),
    
    # 恶意代码注入
    (r"<script|javascript:|onerror=|onclick=", "XSS 尝试"),
    (r"```(python|bash|sql|sh).*?(import os|subprocess|exec|eval)", "代码注入尝试"),
]

# 高置信度安全拒答模板
HIGH_CONFIDENCE_REJECT = "抱歉，我无法执行该请求。请提出与投资分析相关的合理问题。"

# 低置信度提示模板
LOW_CONFIDENCE_WARNING = "（检测到你的问题可能包含了特殊指令，我将按正常方式回答）"


def check_injection(query: str) -> dict:
    """
    检查输入是否包含注入攻击。
    
    Returns:
        {
            "blocked": bool,       # 是否应该阻止
            "reason": str,         # 阻止原因
            "confidence": float,   # 检测置信度 0-1
            "matched_patterns": [str],  # 匹配的模式
        }
    """
    if not query or len(query) < 5:
        return {"blocked": False, "reason": "", "confidence": 0, "matched_patterns": []}
    
    matched = []
    for pattern, reason in INJECTION_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            matched.append(reason)
    
    if not matched:
        return {"blocked": False, "reason": "", "confidence": 0, "matched_patterns": []}
    
    # 计算置信度
    unique_reasons = list(set(matched))
    confidence = min(1.0, len(unique_reasons) * 0.3)
    
    if confidence >= 0.6:
        return {
            "blocked": True,
            "reason": " | ".join(unique_reasons),
            "confidence": round(confidence, 2),
            "matched_patterns": unique_reasons,
        }
    else:
        return {
            "blocked": False,
            "reason": " | ".join(unique_reasons),
            "confidence": round(confidence, 2),
            "matched_patterns": unique_reasons,
        }
