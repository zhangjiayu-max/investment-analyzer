"""债市数据路由 — /api/bond/*"""

import asyncio
import json
import logging
import re
import time
import html as html_mod

import requests as req
from fastapi import APIRouter, HTTPException

from db.config import get_config_int

from db import (
    list_holdings, get_cash_balance, get_total_cash_balance, get_analysis_agent,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
    DEFAULT_BOND_PROMPT,
    create_async_task, update_async_task, get_async_task,
)
from infra.state import track_agent, untrack_agent

router = APIRouter(prefix="/api/bond", tags=["bond"])

_background_tasks = set()


def _fetch_bond_data():
    """抓取有知有行债市温度数据，返回原始数据列表。"""
    try:
        resp = req.get(
            "https://youzhiyouxing.cn/data/macro",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=15,
        )
        resp.raise_for_status()

        match = re.search(r'data-cbond-history="([^"]+)"', resp.text)
        if not match:
            return []

        raw = html_mod.unescape(match.group(1))
        bracket_count = 0
        end_idx = 0
        for i, c in enumerate(raw):
            if c == "[":
                bracket_count += 1
            elif c == "]":
                bracket_count -= 1
                if bracket_count == 0:
                    end_idx = i + 1
                    break

        if end_idx == 0:
            logging.warning("[_fetch_bond_data] 未找到完整 JSON 数组")
            return []

        return json.loads(raw[:end_idx])
    except req.exceptions.Timeout:
        logging.warning("[_fetch_bond_data] 请求超时")
        return []
    except req.exceptions.RequestException as e:
        logging.warning(f"[_fetch_bond_data] 网络请求失败: {e}")
        return []
    except (json.JSONDecodeError, ValueError) as e:
        logging.warning(f"[_fetch_bond_data] JSON 解析失败: {e}")
        return []
    except Exception as e:
        logging.warning(f"[_fetch_bond_data] 未知错误: {e}")
        return []


@router.get("/market-temperature")
async def get_bond_market_temperature():
    """抓取有知有行债市温度数据。"""
    try:
        data = _fetch_bond_data()
        last = data[-1] if data else {}
        return {
            "history": data,
            "current": {
                "date": last.get("date"),
                "temperature": last.get("degree"),
                "rate": float(last["yield"]) if last.get("yield") else None,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"数据源请求失败: {e}")


@router.get("/yield-curve")
async def bond_yield_curve_api(country: str = "china"):
    """获取国债收益率曲线数据。"""
    from tools import _get_bond_yield_curve
    result = json.loads(_get_bond_yield_curve({"country": country}))
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.get("/market-overview")
async def bond_market_overview_api():
    """获取债市综合概况。"""
    from tools import _get_bond_market_overview
    result = json.loads(_get_bond_market_overview())
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result

