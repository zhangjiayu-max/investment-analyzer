"""全局可变状态 — 集中管理，避免散落在 app.py 中。"""

import asyncio
import time

# 后台分析任务进度跟踪
analyze_progress: dict[int, dict] = {}
analyze_cancel: set[int] = set()
analyze_tasks: dict[int, asyncio.Task] = {}
reanalyze_tasks: dict[int, asyncio.Task] = {}
vision_semaphore = asyncio.Semaphore(3)

# Agent 运行状态跟踪
running_agents: dict[str, dict] = {}

# 爬取并发控制
crawl_semaphore = asyncio.Semaphore(3)

# Dashboard 缓存
hot_topics_cache: dict = {"data": None, "ts": 0}


def track_agent(uid: str, agent: str, task: str = ""):
    """注册一个正在运行的 Agent。"""
    running_agents[uid] = {"agent": agent, "task": task, "started_at": time.time()}


def untrack_agent(uid: str):
    """移除已完成的 Agent。"""
    running_agents.pop(uid, None)
