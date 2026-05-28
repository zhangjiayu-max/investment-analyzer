"""配置管理 — 从环境变量读取 API Key 等敏感信息"""

import os
from dotenv import load_dotenv

load_dotenv()

# 当前使用的模型提供商: "deepseek" 或 "mimo"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mimo")

# DeepSeek API 配置（OpenAI 兼容接口）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 小米 MIMO API 配置（OpenAI 兼容接口）
MIMO_API_KEY = os.getenv("MIMO_API_KEY", "")
MIMO_BASE_URL = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
MIMO_MODEL = os.getenv("MIMO_MODEL", "mimo-v2.5-pro")

# MIMO 套餐 API（对话用，优先级高于普通 API）
MIMO_PLAN_API_KEY = os.getenv("MIMO_PLAN_API_KEY", "")
MIMO_PLAN_BASE_URL = os.getenv("MIMO_PLAN_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
MIMO_PLAN_MODEL = os.getenv("MIMO_PLAN_MODEL", "mimo-v2.5-pro")

# 视觉模型配置（用于图片解析）
VISION_API_KEY = os.getenv("VISION_API_KEY", MIMO_API_KEY)
VISION_BASE_URL = os.getenv("VISION_BASE_URL", MIMO_BASE_URL)
VISION_MODEL = os.getenv("VISION_MODEL", "mimo-v2-omni")

# 盈米且慢 MCP API
YINGMI_API_KEY = os.getenv("YINGMI_API_KEY", "")


def get_llm_config() -> tuple[str, str, str]:
    """返回主用 LLM 配置 (api_key, base_url, model)。优先 MIMO 套餐 API。"""
    if MIMO_PLAN_API_KEY:
        return MIMO_PLAN_API_KEY, MIMO_PLAN_BASE_URL, MIMO_PLAN_MODEL
    if LLM_PROVIDER == "mimo":
        return MIMO_API_KEY, MIMO_BASE_URL, MIMO_MODEL
    return DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL


def get_llm_fallback_config() -> tuple[str, str, str] | None:
    """返回兜底 LLM 配置，主用失败时使用。"""
    if MIMO_PLAN_API_KEY and DEEPSEEK_API_KEY:
        return DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    if MIMO_API_KEY and DEEPSEEK_API_KEY:
        return DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    return None


def get_vision_config() -> tuple[str, str, str]:
    """返回视觉模型配置 (api_key, base_url, model)。"""
    return VISION_API_KEY, VISION_BASE_URL, VISION_MODEL


# ── Token 预算配置 ─────────────────────────────────────────
DAILY_TOKEN_LIMIT = int(os.getenv("DAILY_TOKEN_LIMIT", "500000"))
TOKEN_WARN_THRESHOLD = float(os.getenv("TOKEN_WARN_THRESHOLD", "0.8"))
TOKEN_BUDGET_BYPASS = os.getenv("TOKEN_BUDGET_BYPASS", "0") == "1"
