# Unified Evidence Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one shared evidence layer for knowledge, market signals, alerts, decisions, and regression so conversation, dashboard, market radar, and decision flows all consume the same facts.

**Architecture:** Add a backend evidence service that aggregates RAG hits, market signals, watchlist/opportunity signals, decision review stats, and validation history into a single snapshot and a prompt-ready text block. Then adapt the existing shared-signals service and high-value API routes to consume that snapshot without changing the page structure.

**Tech Stack:** Python 3.11, FastAPI, SQLite, ChromaDB/RAG services, Vue 3, existing axios API layer.

---

### Task 1: Build the unified evidence service

**Files:**
- Create: `backend/services/unified_evidence.py`
- Modify: `backend/services/shared_signals.py`
- Test: `backend/tests/test_unified_evidence.py`

- [ ] **Step 1: Add evidence aggregation helpers and snapshot builder**

```python
def build_unified_evidence(user_id: str = "default", query: str = "", scenario_type: str = "general_analysis", limit: int = 5) -> dict:
    ...
```

- [ ] **Step 2: Make shared-signals a thin adapter over the unified snapshot**
- [ ] **Step 3: Add a focused backend test for the evidence keys and summary blocks**

### Task 2: Wire the evidence snapshot into core flows

**Files:**
- Modify: `backend/services/conversation/conversation_context.py`
- Modify: `backend/routers/conversation/conversations.py`
- Modify: `backend/routers/analysis/market_intel.py`
- Modify: `backend/routers/decision/decisions.py`
- Modify: `backend/routers/dashboard/dashboard.py`

- [ ] **Step 1: Inject the unified evidence block into conversation and specialist prompts**
- [ ] **Step 2: Include the unified evidence snapshot in market intelligence and decision stats responses**
- [ ] **Step 3: Make dashboard payloads expose the same shared evidence fields**

### Task 3: Surface the shared evidence in the UI

**Files:**
- Modify: `frontend/src/components/shared/SharedSignalsCard.vue`
- Modify: `frontend/src/components/Dashboard.vue`
- Modify: `frontend/src/components/decision/DecisionRecordsPage.vue`
- Modify: `frontend/src/components/market/MarketIntelligence.vue`

- [ ] **Step 1: Expand the shared card to show knowledge, validation, and regression snippets**
- [ ] **Step 2: Reuse the same card on dashboard, decision, and market pages**
- [ ] **Step 3: Run a focused frontend build to verify the new fields render cleanly**
