"""AI 市场分析路由 — /api/analysis/*、/api/analysis-agents/*"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException

from db import (
    get_analysis_agent, list_analysis_agents, update_analysis_agent,
    create_analysis_history, list_analysis_history,
    get_analysis_history_item, get_analysis_history_status,
    update_analysis_history, delete_analysis_history,
    save_prompt_version,
    get_latest_valuation, get_valuation_history,
    list_holdings,
    create_async_task, update_async_task, get_async_task,
    get_config_float, get_config_int,
)
from llm_service import _call_llm, MODEL
from rag import build_rag_context_with_details, log_rag_search
from models.analysis import AnalysisRunRequest, AnalysisAgentUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])

_background_tasks = set()

