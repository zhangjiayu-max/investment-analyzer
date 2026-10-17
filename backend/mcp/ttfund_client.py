"""天天基金 ttskill CLI 客户端。

CLI 登录型 Skill，通过本地 ttskill 二进制调用天天基金能力。
不再需要 API Key，用户扫码登录即可。

用法:
    from mcp.ttfund_client import get_ttfund_client
    client = get_ttfund_client()
    info = client.fund_base_info("110011")
"""

import logging
import json
import os
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ttskill CLI 路径（优先使用用户级安装）
_TTSKILL_BIN = None

# 天天基金 Skill API（用于安装基础包和同步）
_BASE_URL = "https://skills.tiantianfunds.com/ai-smart-skill-service/openapi"

# Skill 注册表（业务名 → ttskill invoke 使用的 skill_id）
# 注意：不再硬编码 version。调用时省略 --skill-version，让 ttskill CLI 解析
# 已安装版本，并在服务端要求更高版本时自动 update（见 invoke.js 的 auto-update）。
SKILLS = {
    "fund_base_info":    "TTFUND_BASE_INFOS",
    "fund_manager":      "TTFUND_MANAGER_INFO",
    "fund_condition":    "TTFUND_CONDITION_SELECT",
    "fund_holding":      "TTFUND_HOLDING_INFO",
    "fund_gold":         "TTFUND_GOLD_INFO",
    "fund_tg_strategy":  "TTFUND_STRATEGY_INFO",
    "fund_index":        "TTFUND_INDEX_INFO",
    "fund_nav":          "TTFUND_NAV_INFO",
    "fund_portfolio":    "TTFUND_PORTFOLIO_ANALYSIS",
    "fund_favor":        "TTFUND_FAVOR_ZX",
    "bond_market":       "TTFUND_BOND_MARKET",
    "fund_backtest":     "TTFUND_GROUP_BACKTEST",
    "fund_search":       "TTFUND_SEARCH",
    "stock_price":       "TTFUND_STOCK_PRICE_QUERY",
}

# ttskill invoke 的 action 映射
ACTION_MAP = {
    "fund_base_info":    "query",
    "fund_manager":      "query",
    "fund_condition":    "query",
    "fund_holding":      "query",
    "fund_gold":         "query",
    "fund_tg_strategy":  "query",
    "fund_index":        "query",
    "fund_nav":          "query",
    "fund_portfolio":    "query",
    "fund_favor":        "query",
    "bond_market":       "query",
    "fund_backtest":     "query",
    "fund_search":       "query",
    "stock_price":       "query",
}

_client_instance = None
_LOGIN_REQUIRED = "天天基金登录已失效或未授权，请在系统配置 → 天天基金 → 扫码登录（或运行 ttskill login --env prod --force）"
_LOGIN_ENDPOINT = "/api/config/ttfund/login"


class TtfundLoginRequired(RuntimeError):
    """用户需主动扫码授权，调用方应展示操作指引。"""


def _find_ttskill() -> str:
    """查找 ttskill CLI 可执行文件。"""
    global _TTSKILL_BIN
    if _TTSKILL_BIN:
        return _TTSKILL_BIN

    # 优先使用用户级安装（~/.local/bin/ttskill）
    home_bin = Path.home() / ".local" / "bin" / "ttskill"
    if home_bin.exists():
        _TTSKILL_BIN = str(home_bin)
        return _TTSKILL_BIN

    # 备选：项目内 bin 目录
    local_bin = Path(__file__).resolve().parent.parent / "bin" / "ttskill"
    if local_bin.exists():
        _TTSKILL_BIN = str(local_bin)
        return _TTSKILL_BIN

    # 系统 PATH
    import shutil
    which = shutil.which("ttskill")
    if which:
        _TTSKILL_BIN = which
        return _TTSKILL_BIN

    raise RuntimeError("ttskill CLI 未安装，请先调用 install()")


def is_installed() -> bool:
    """检查 ttskill 是否已安装。"""
    try:
        _find_ttskill()
        return True
    except RuntimeError:
        return False


def install() -> dict:
    """下载并安装 ttskill CLI 基础包。"""
    import platform, zipfile, io, stat, shutil

    # 检测平台
    sys = platform.system().lower()
    arch = platform.machine().lower()
    plat = "darwin" if sys == "darwin" else ("linux" if sys == "linux" else "win32")
    a = "arm64" if arch in ("arm64", "aarch64") else "x64"

    # 1) 获取基础包下载地址
    resp = httpx.get(
        f"{_BASE_URL}/base-package/resolve",
        params={"platform": plat, "arch": a, "env": "prod"},
        timeout=30,
    )
    resp.raise_for_status()
    pkg = resp.json().get("data", {})
    download_url = pkg.get("download_url", "")
    if not download_url:
        raise RuntimeError(f"获取基础包下载地址失败: {pkg}")

    # 2) 下载 zip 包
    logger.info(f"下载 ttskill 基础包 v{pkg.get('version', '?')}...")
    bin_resp = httpx.get(download_url, timeout=300, follow_redirects=True)
    bin_resp.raise_for_status()

    # 3) 解压到 ~/.local/share/ttskill-base/
    install_root = Path.home() / ".local" / "share" / "ttskill-base"
    if install_root.exists():
        shutil.rmtree(install_root)
    install_root.mkdir(parents=True)

    with zipfile.ZipFile(io.BytesIO(bin_resp.content)) as zf:
        zf.extractall(install_root)

    # 4) 找到 ttskill 入口并链接到 ~/.local/bin/
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ttskill_link = bin_dir / "ttskill"

    # 查找 ttskill 入口脚本
    entry = None
    for f in install_root.rglob("ttskill"):
        if f.is_file() and not f.name.startswith("."):
            entry = f
            break
    if not entry:
        for f in install_root.rglob("ttskill.js"):
            entry = f
            break

    if not entry:
        raise RuntimeError(f"安装包中未找到 ttskill 入口: {install_root}")

    # 创建包装脚本
    ttskill_link.write_text(
        f'#!/bin/sh\nexec "{install_root}/bin/../runtime/node" "{entry}" "$@"\n',
        encoding="utf-8",
    )
    ttskill_link.chmod(ttskill_link.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # 设置 node 可执行权限
    node_bin = entry.parent.parent / "runtime" / "node"
    if node_bin.exists():
        node_bin.chmod(node_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    global _TTSKILL_BIN
    _TTSKILL_BIN = str(ttskill_link)
    logger.info(f"ttskill 已安装到 {ttskill_link}")

    return {"ok": True, "path": str(ttskill_link), "version": pkg.get("version", "")}


def login() -> dict:
    """执行 ttskill login，打开浏览器让用户扫码。

    使用 --env prod --force 强制重新登录，避免已过期 token 被判为 already_logged_in。
    """
    ttskill = _find_ttskill()
    try:
        result = subprocess.run(
            [ttskill, "login", "--env", "prod", "--force"],
            capture_output=True, text=True, timeout=120,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return {"ok": True, "message": "登录成功", "output": output}
        else:
            return {"ok": False, "error": output}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "登录超时（120s），请重试"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_login() -> dict:
    """检查登录状态。

    判断逻辑：auth.current_user.is_expired 为 False 才算已登录。
    旧逻辑查 auth_token_present（不存在该字段）导致永远返回 False。
    """
    try:
        ttskill = _find_ttskill()
    except RuntimeError:
        return {"ok": True, "installed": False, "logged_in": False,
                "state": "not_installed", "error": "ttskill CLI 未安装，请在系统配置 → 天天基金安装"}
    try:
        result = subprocess.run(
            [ttskill, "status", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
            user = data.get("auth", {}).get("current_user", {})
            is_expired = user.get("is_expired", True)
            logged_in = bool(user) and is_expired is False
            status = {
                "ok": True,
                "installed": True,
                "logged_in": logged_in,
                "state": "ready" if logged_in else ("expired" if user else "login_required"),
                "expires_at": user.get("expires_at", ""),
            }
            if not logged_in:
                status.update(error=_LOGIN_REQUIRED, login_endpoint=_LOGIN_ENDPOINT)
            return status
        return {"ok": False, "installed": True, "logged_in": False, "state": "error",
                "error": "ttskill 状态检查失败，请在系统配置 → 天天基金重新检查或扫码登录",
                "login_endpoint": _LOGIN_ENDPOINT}
    except Exception as e:
        logger.warning("ttskill 状态检查失败: %s", type(e).__name__)
        return {"ok": False, "installed": True, "logged_in": False, "state": "error",
                "error": "ttskill 状态检查失败，请在系统配置 → 天天基金重新检查或扫码登录",
                "login_endpoint": _LOGIN_ENDPOINT}


def get_ttfund_client() -> "TtfundClient":
    """获取全局单例。"""
    global _client_instance
    if _client_instance is None:
        _client_instance = TtfundClient()
    return _client_instance


class TtfundClient:
    def __init__(self, timeout: int = 60):
        self._timeout = timeout

    def _invoke(self, skill_name: str, params: dict) -> dict:
        """通过 ttskill CLI 调用指定 skill。"""
        ttskill = _find_ttskill()

        skill_id = SKILLS.get(skill_name)
        if not skill_id:
            raise ValueError(f"未知 skill: {skill_name}")

        action = ACTION_MAP.get(skill_name, "query")
        body = json.dumps(params, ensure_ascii=False)

        try:
            # 不传 --skill-version：CLI 会解析已安装版本，服务端要求升级时自动 update，
            # 避免硬编码版本号过期后触发 skill_version_required 409。
            result = subprocess.run(
                [ttskill, "invoke", skill_id,
                 "--action", action,
                 "--body", body],
                capture_output=True, text=True, timeout=self._timeout,
            )

            output = result.stdout.strip()
            stderr = result.stderr.strip()

            # 检测是否需要登录
            combined = output + stderr
            if "cli_login_required" in combined.lower() or (
                result.returncode != 0 and "login" in combined.lower()
            ):
                raise TtfundLoginRequired(_LOGIN_REQUIRED)

            # 非零退出码且无有效输出
            if result.returncode != 0 and not output:
                raise RuntimeError(f"ttskill 调用失败: {stderr}")

            if not output:
                return {}

            data = json.loads(output)

            # 检查业务错误
            if data.get("code") and data["code"] != 0:
                detail = data.get("detail", "")
                if "未找到匹配结果" in str(detail):
                    return {}  # 无数据，返回空
                raise RuntimeError(f"ttskill 业务错误: {detail or data.get('message', '')}")

            # 提取业务结果
            raw = data.get("data", {}).get("raw_result", {}).get("body", {})
            if raw:
                return raw
            return data

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ttskill 调用超时 ({self._timeout}s)")
        except json.JSONDecodeError:
            return {"data": output}

    def _invoke_text(self, skill_name: str, params: dict) -> str:
        """调用 skill 并返回文本结果。"""
        try:
            data = self._invoke(skill_name, params)
            if isinstance(data, dict):
                for key in ("data", "result", "content", "message"):
                    val = data.get(key)
                    if isinstance(val, str) and len(val) > 10:
                        return val
                    if isinstance(val, dict):
                        return json.dumps(val, ensure_ascii=False, indent=2)
                return json.dumps(data, ensure_ascii=False, indent=2)
            return str(data)
        except TtfundLoginRequired:
            raise
        except Exception as e:
            logger.warning(f"ttfund {skill_name} 调用失败: {e}")
            return ""

    # ── 业务方法 ──────────────────────────────────────────

    def fund_base_info(self, fund_code: str) -> dict:
        """基金综合信息查询。"""
        return self._invoke("fund_base_info", {"fcode": fund_code})

    def fund_manager(self, manager_name: str) -> str:
        """基金经理信息查询。"""
        return self._invoke_text("fund_manager", {"manager_name": manager_name})

    def fund_holding(self, fund_code: str) -> dict:
        """基金重仓股/持仓查询。返回 {fund_profile, top_holdings, ...}"""
        return self._invoke("fund_holding", {"fund_id": fund_code, "holding_type": "all"})

    def fund_index(self, index_name: str) -> dict:
        """指数行情/估值/成分查询。返回 {index_profile, valuation, quote, ...}"""
        return self._invoke("fund_index", {"index_id": index_name, "query_scope": "all"})

    def fund_nav(self, fund_code: str, range: str = "1y") -> dict:
        """基金净值历史查询。返回 {fund_profile, nav_history: {items: [...]}}"""
        return self._invoke("fund_nav", {"fund_id": fund_code, "range": range})

    def fund_search(self, keyword: str) -> str:
        """基金搜索。"""
        return self._invoke_text("fund_search", {"query": keyword, "search_type": "fund"})

    def stock_price(self, stock_code: str) -> str:
        """股票实时行情。"""
        return self._invoke_text("stock_price", {"query": stock_code})

    def fund_condition(self, condition: str) -> str:
        """条件选基。"""
        return self._invoke_text("fund_condition", {"condition": condition})

    def bond_market(self) -> str:
        """债市行情。"""
        return self._invoke_text("bond_market", {})

    def fund_gold(self) -> str:
        """黄金行情。"""
        return self._invoke_text("fund_gold", {})
