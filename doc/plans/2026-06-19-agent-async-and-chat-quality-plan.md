# Agent Async And Chat Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all non-chat Agent analysis triggers asynchronous while tightening chat SSE persistence and answer quality behavior.

**Architecture:** Reuse existing SQLite records as task state, keep in-process FastAPI background tasks, and add small status APIs for polling. Chat remains SSE-based, with better routing, deduplication, and metadata consistency.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Vue 3 Composition API, axios.

---

### Task 1: Extend Analysis History State

**Files:**
- Modify: `backend/db/analysis.py`
- Modify: `backend/db/__init__.py`
- Test: backend import/route checks

- [ ] Add `status` and `error_msg` columns to `analysis_history`.
- [ ] Update `create_analysis_history()` to accept `status`, `result`, and optional contexts.
- [ ] Add `update_analysis_history()` and `get_analysis_history_status()`.
- [ ] Verify imports from `db` still expose the new helpers.

### Task 2: Async Index Deep Analysis

**Files:**
- Modify: `backend/routers/analysis.py`
- Test: backend route import check

- [ ] Change `/api/analysis/run` to create a running history record and return immediately.
- [ ] Move the existing deep-analysis body into `_run_index_analysis_async()`.
- [ ] Add `/api/analysis/history/{history_id}/status`.
- [ ] Ensure success writes `done`, result, token usage, and failure writes `error_msg`.

### Task 3: Async Portfolio Analysis Entrypoints

**Files:**
- Modify: `backend/routers/portfolio.py`
- Test: backend route import check

- [ ] Convert `/api/portfolio/analysis/diversification/ai-summary` to create a running record and run in background.
- [ ] Convert `/api/portfolio/analysis/ai` to create a running record and run in background.
- [ ] Reuse `/api/portfolio/analysis/{record_id}/status` for polling.
- [ ] Preserve existing record types and history list behavior.

### Task 4: Chat SSE Quality Fixes

**Files:**
- Modify: `backend/routers/conversations.py`
- Modify: `backend/agent/orchestrator.py`
- Test: lightweight static checks

- [ ] Fix duplicate user-message creation in the `chat` branch.
- [ ] Add `execution_status=completed` to simple branch assistant metadata.
- [ ] Pass `clarification.refined_query` into simple expert execution while logging original query.
- [ ] Keep reviewed final answer and persisted final answer aligned.

### Task 5: Frontend Polling

**Files:**
- Modify: `frontend/src/api/index.js`
- Modify: `frontend/src/components/ValuationHistory.vue`
- Modify: `frontend/src/components/PortfolioManagement.vue`
- Modify: `frontend/src/components/Dashboard.vue`
- Test: frontend test/build command

- [ ] Add index-analysis status API and polling helper.
- [ ] Update valuation deep-analysis UI to poll instead of waiting for completion.
- [ ] Update portfolio AI summary and general AI analysis to poll.
- [ ] Update dashboard panorama trigger to poll existing async result.

### Task 6: Verification

**Files:**
- No source edits expected.

- [ ] Run backend syntax/import checks for touched modules.
- [ ] Run focused frontend tests.
- [ ] Run frontend build if dependencies are available.
- [ ] Report any verification gaps clearly.

