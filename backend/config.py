"""配置管理 — 从环境变量读取 API Key 等敏感信息"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
# 优先加载 .env.keys（API Key），再加载 .env（配置项）
# load_dotenv 不覆盖已存在的环境变量，所以顺序很重要
load_dotenv(_ROOT / ".env.keys")
load_dotenv(_ROOT / ".env")

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

# Embedding 模型配置（用于 ChromaDB 向量检索）
# 可选: "BAAI/bge-small-zh-v1.5" (512维, 92MB) / "moka-ai/m3e-base" (768维, 400MB) / "BAAI/bge-large-zh-v1.5" (1024维, 1.2GB)
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "moka-ai/m3e-base")

# 盈米且慢 MCP API
YINGMI_API_KEY = os.getenv("YINGMI_API_KEY", "")

# 东方财富妙想 API
EASTMONEY_API_KEY = os.getenv("MX_APIKEY", "") or os.getenv("MX_API_KEY", "")

# 天天基金 Skills API
TTFUND_APIKEY = os.getenv("TTFUND_APIKEY", "")

# 仲裁 Agent 配置（DeepSeek R1 高级推理模型）
ARBITRATION_API_KEY = os.getenv("ARBITRATION_API_KEY", "")
ARBITRATION_BASE_URL = os.getenv("ARBITRATION_BASE_URL", "https://api.deepseek.com")
ARBITRATION_MODEL = os.getenv("ARBITRATION_MODEL", "deepseek-v4-pro")


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


# ── 调仓分析配置 ─────────────────────────────────────────

# ── 经典资产配置策略预设 ──
# 来源说明：
# - conservative: 经典保守型，债券为主，参考 Vanguard LifeStrategy Conservative
# - balanced: 经典 60/40 组合变体，全球最广泛的均衡配置
# - aggressive: 进取型，股票为主，适合长期投资
# - all-weather: 桥水全天候策略变体，风险平价思想，各类资产风险贡献均衡
# - age-rule: 年龄法则，股票比例 = (100 - 年龄)%，需配合 REBALANCE_AGE 使用
REBALANCE_STRATEGY_PRESETS = {
    "conservative": {
        "name": "保守型",
        "description": "以债券和货币为主，追求稳健收益，适合低风险偏好或临近用钱阶段",
        "source": "参考 Vanguard LifeStrategy Conservative / 经典保守组合",
        "base_allocation": {"equity": 0.20, "bond": 0.45, "money": 0.10, "hybrid": 0.10, "index": 0.05, "qdii": 0.10},
        "cash_targets": {"low": 0.05, "fair": 0.10, "high": 0.20},
    },
    "balanced": {
        "name": "均衡型",
        "description": "股债均衡配置，攻守兼备，适合大多数投资者",
        "source": "经典 60/40 组合变体（全球最广泛使用的资产配置模型）",
        "base_allocation": {"equity": 0.40, "bond": 0.30, "money": 0.05, "hybrid": 0.10, "index": 0.10, "qdii": 0.05},
        "cash_targets": {"low": 0.05, "fair": 0.10, "high": 0.15},
    },
    "aggressive": {
        "name": "进取型",
        "description": "以股票和指数为主，追求高收益，适合长期投资且能承受较大波动",
        "source": "参考 Vanguard LifeStrategy Aggressive / 进取型组合",
        "base_allocation": {"equity": 0.50, "bond": 0.15, "money": 0.02, "hybrid": 0.13, "index": 0.15, "qdii": 0.05},
        "cash_targets": {"low": 0.03, "fair": 0.05, "high": 0.10},
    },
    "all-weather": {
        "name": "全天候",
        "description": "桥水全天候策略变体，风险平价思想，各类资产风险贡献均衡，穿越牛熊",
        "source": "Ray Dalio All Weather 策略变体（风险平价模型，1996 年至今年化约 7-8%）",
        "base_allocation": {"equity": 0.30, "bond": 0.40, "money": 0.05, "hybrid": 0.05, "index": 0.10, "qdii": 0.10},
        "cash_targets": {"low": 0.05, "fair": 0.10, "high": 0.15},
    },
    "age-rule": {
        "name": "年龄法则",
        "description": "股票比例 = (100 - 年龄)%，自动随年龄调整风险偏好",
        "source": "经典年龄配置法则（Bogleheads / 沃尔特·克洛斯生命周期理论）",
        "base_allocation": None,  # 动态计算，需配合 REBALANCE_AGE
        "cash_targets": {"low": 0.05, "fair": 0.10, "high": 0.15},
    },
}

# 当前使用的策略（balanced / conservative / aggressive / all-weather / age-rule）
# 设为 "custom" 则使用 REBALANCE_BASE_ALLOCATION 自定义配比
REBALANCE_STRATEGY = os.getenv("REBALANCE_STRATEGY", "balanced")

# 年龄法则专用：投资者年龄（age-rule 策略必填）
REBALANCE_AGE = int(os.getenv("REBALANCE_AGE", "30"))

# 资产类型基础配比（JSON，键为类别，值为比例）
# 仅在 REBALANCE_STRATEGY=custom 时生效，其他策略自动覆盖
REBALANCE_BASE_ALLOCATION = os.getenv("REBALANCE_BASE_ALLOCATION",
    '{"equity":0.40,"bond":0.30,"money":0.05,"hybrid":0.10,"index":0.10,"qdii":0.05}')

# 估值调整系数（JSON，键为估值水平标签，值为调整倍数）
REBALANCE_VALUATION_ADJUSTMENT = os.getenv("REBALANCE_VALUATION_ADJUSTMENT",
    '{"极度低估":1.4,"低估":1.2,"合理":1.0,"偏高":0.8,"极度高估":0.6}')

# 估值百分位分界线（逗号分隔：极度低估上限,低估上限,合理上限,偏高上限）
REBALANCE_VALUATION_PERCENTILES = os.getenv("REBALANCE_VALUATION_PERCENTILES", "20,30,70,80")

# 偏离度阈值（逗号分隔：平衡上限,轻微上限）
REBALANCE_DRIFT_THRESHOLDS = os.getenv("REBALANCE_DRIFT_THRESHOLDS", "0.03,0.08")

# 现金目标比例（逗号分隔：低估时,合理时,高估时）
REBALANCE_CASH_TARGETS = os.getenv("REBALANCE_CASH_TARGETS", "0.05,0.10,0.15")

# 现金建议触发偏移（逗号分隔：超配偏移,欠配偏移）
REBALANCE_CASH_TRIGGERS = os.getenv("REBALANCE_CASH_TRIGGERS", "0.05,0.02")

# 资产类别偏离忽略阈值（低于此值不生成建议）
REBALANCE_DRIFT_IGNORE = float(os.getenv("REBALANCE_DRIFT_IGNORE", "0.05"))

# 低估指数建议数量上限
REBALANCE_UNDERVALUE_MAX = int(os.getenv("REBALANCE_UNDERVALUE_MAX", "2"))

# 低估指数建议金额范围（逗号分隔：最小,最大）
REBALANCE_UNDERVALUE_AMOUNT = os.getenv("REBALANCE_UNDERVALUE_AMOUNT", "1000,3000")

# 7日年化收益率（用于零钱账户计息）
CASH_ANNUAL_YIELD_7D = float(os.getenv("CASH_ANNUAL_YIELD_7D", "0.01512"))


def get_strategy_info(strategy: str = None) -> dict | None:
    """获取策略预设信息，包含名称、描述、来源。"""
    s = strategy or REBALANCE_STRATEGY
    preset = REBALANCE_STRATEGY_PRESETS.get(s)
    if not preset:
        return None
    return {
        "key": s,
        "name": preset["name"],
        "description": preset["description"],
        "source": preset["source"],
    }


def get_rebalance_config() -> dict:
    """解析所有调仓配置，返回结构化字典。优先从数据库读取活跃配置。"""
    # 优先从数据库读取
    try:
        from db import get_active_rebalance_config
        db_cfg = get_active_rebalance_config()
        if db_cfg and db_cfg.get("config"):
            cfg = db_cfg["config"]
            # 确保 strategy_info 存在
            if "strategy_info" not in cfg:
                cfg["strategy_info"] = get_strategy_info(cfg.get("strategy", "balanced"))
            return cfg
    except Exception:
        pass

    # 数据库无配置时，从环境变量解析
    percentiles = [float(x) for x in REBALANCE_VALUATION_PERCENTILES.split(",")]
    drift_thresholds = [float(x) for x in REBALANCE_DRIFT_THRESHOLDS.split(",")]
    cash_targets = [float(x) for x in REBALANCE_CASH_TARGETS.split(",")]
    cash_triggers = [float(x) for x in REBALANCE_CASH_TRIGGERS.split(",")]
    undervalue_amount = [float(x) for x in REBALANCE_UNDERVALUE_AMOUNT.split(",")]

    # 应用策略预设
    base_allocation = json.loads(REBALANCE_BASE_ALLOCATION)
    cash_targets_dict = {
        "low": cash_targets[0],
        "fair": cash_targets[1],
        "high": cash_targets[2],
    }

    strategy = REBALANCE_STRATEGY
    if strategy == "age-rule":
        # 年龄法则：股票+指数+混合 = (100 - age)%，其余按债券:货币:QDII = 6:1:1 分配
        equity_pct = max(0.10, (100 - REBALANCE_AGE) / 100)
        bond_pct = (1.0 - equity_pct) * 0.75
        money_pct = (1.0 - equity_pct) * 0.08
        qdii_pct = (1.0 - equity_pct) * 0.17
        base_allocation = {
            "equity": round(equity_pct * 0.60, 2),
            "bond": round(bond_pct, 2),
            "money": round(money_pct, 2),
            "hybrid": round(equity_pct * 0.20, 2),
            "index": round(equity_pct * 0.20, 2),
            "qdii": round(qdii_pct, 2),
        }
    elif strategy in REBALANCE_STRATEGY_PRESETS:
        preset = REBALANCE_STRATEGY_PRESETS[strategy]
        if preset["base_allocation"]:
            base_allocation = preset["base_allocation"]
        cash_targets_dict = preset.get("cash_targets", cash_targets_dict)

    return {
        "strategy": strategy,
        "strategy_info": get_strategy_info(strategy),
        "base_allocation": base_allocation,
        "valuation_adjustment": json.loads(REBALANCE_VALUATION_ADJUSTMENT),
        "valuation_percentiles": {
            "极度低估": percentiles[0],
            "低估": percentiles[1],
            "合理": percentiles[2],
            "偏高": percentiles[3],
        },
        "drift_thresholds": {
            "balanced": drift_thresholds[0],
            "slight": drift_thresholds[1],
        },
        "cash_targets": cash_targets_dict,
        "cash_triggers": {
            "excess": cash_triggers[0],
            "shortage": cash_triggers[1],
        },
        "drift_ignore": REBALANCE_DRIFT_IGNORE,
        "undervalue_max": REBALANCE_UNDERVALUE_MAX,
        "undervalue_amount": {
            "min": undervalue_amount[0],
            "max": undervalue_amount[1],
        },
    }


def list_strategy_presets() -> list[dict]:
    """列出所有可用的策略预设。"""
    result = []
    for key, preset in REBALANCE_STRATEGY_PRESETS.items():
        info = {
            "key": key,
            "name": preset["name"],
            "description": preset["description"],
            "source": preset["source"],
        }
        if key == "age-rule":
            # 年龄法则动态计算，显示当前年龄下的配比
            equity_pct = max(0.10, (100 - REBALANCE_AGE) / 100)
            bond_pct = (1.0 - equity_pct) * 0.75
            money_pct = (1.0 - equity_pct) * 0.08
            qdii_pct = (1.0 - equity_pct) * 0.17
            info["base_allocation"] = {
                "equity": round(equity_pct * 0.60, 2),
                "bond": round(bond_pct, 2),
                "money": round(money_pct, 2),
                "hybrid": round(equity_pct * 0.20, 2),
                "index": round(equity_pct * 0.20, 2),
                "qdii": round(qdii_pct, 2),
            }
            info["age"] = REBALANCE_AGE
        else:
            info["base_allocation"] = preset["base_allocation"]
        result.append(info)
    return result


# ── 目录常量 ──────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
STATIC_DIR = ROOT / "static"
IMAGES_DIR = ROOT / "data" / "images"
OUTPUT_DIR = ROOT / "output" / "tasks"
UPLOADS_DIR = ROOT / "data" / "uploads"
DD_IMAGES_DIR = ROOT / "data" / "dd_images"
VALUATION_IMAGES_DIR = ROOT / "data" / "valuation_images"
