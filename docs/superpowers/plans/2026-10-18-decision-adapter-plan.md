# 决策兼容适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 将 `decision_records` 用户档案与 `investment_decisions` 量化账本通过可审计关联和状态同步打通，保持历史数据与旧接口兼容。

**Architecture:** 在两张现有表增加可空的双向关联字段，由 `services/advisor/decision_adapter.py` 负责幂等创建档案、统一字段转换和状态同步。决策流水线在写入量化账本后调用适配层；档案状态/复盘路由在成功更新后调用适配层。前端只增加关联信息和跳转入口，仍使用现有页面。

**Tech Stack:** Python 3.11, sqlite3, FastAPI, Vue 3 `<script setup>`, pytest, Vitest.

---

### Task 1: 为双向关联和状态转换建立失败测试

**Files:**
- Create: `backend/tests/test_decision_adapter.py`
- Modify: `backend/tests/test_audit_contracts.py`

- [ ] **Step 1: 写关联字段和状态映射测试**

```python
def test_quant_decision_can_be_linked_to_archive(db):
    quant_id = create_quant_decision(...)
    archive = ensure_decision_record_for_investment(quant_id)
    assert archive["investment_decision_id"] == quant_id
    assert get_quant_decision(quant_id)["decision_record_id"] == archive["id"]

def test_archive_status_maps_to_quant_ledger():
    archive = create_archive_decision(...)
    link = link_decision_pair(archive["id"], quant_id)
    sync_archive_status(archive["id"], "accepted")
    assert get_quant_decision(quant_id)["status"] == "confirmed"
```

测试夹具使用现有 `DB_PATH` 临时数据库和 `db.init_db()`，不依赖行情或外部 API。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && pytest tests/test_decision_adapter.py -q`

Expected: FAIL，因为关联字段和适配函数尚未存在。

- [ ] **Step 3: 扩展审计契约测试**

断言 `db` 仍同时导出两套原有 CRUD，并新增适配层公共函数；确保兼容适配没有替换旧导出。

- [ ] **Step 4: 再次运行新增测试并确认仍是预期失败**

Run: `cd backend && pytest tests/test_decision_adapter.py backend/tests/test_audit_contracts.py -q`

Expected: 新测试失败，既有审计测试保持原有结果。

### Task 2: 增加数据库关联字段和幂等 CRUD

**Files:**
- Modify: `backend/db/decisions.py`
- Modify: `backend/db/investment_decisions.py`
- Modify: `backend/db/__init__.py`
- Test: `backend/tests/test_decision_adapter.py`

- [ ] **Step 1: 在初始化函数中增加兼容迁移**

在 `init_decision_tables()` 中为 `decision_records` 增加 `investment_decision_id INTEGER`；在 `init_investment_decisions_table()` 中为 `investment_decisions` 增加 `decision_record_id INTEGER`。使用项目已有的 `_add_column_if_not_exists` 或等价的安全检查，并为两个字段建立索引。

- [ ] **Step 2: 扩展行转换和 CRUD**

让两套 `get/list` 返回关联 ID；新增参数化函数：

```python
link_decision_records(archive_id: int, investment_id: int) -> bool
get_decision_by_investment_id(investment_id: int) -> dict | None
get_investment_decision_by_archive_id(archive_id: int) -> dict | None
```

链接函数必须检查两端记录存在，并拒绝把同一端绑定到不同记录。

- [ ] **Step 3: 运行关联测试**

Run: `cd backend && pytest tests/test_decision_adapter.py -q`

Expected: 关联字段、重复关联保护和旧数据库迁移测试通过；适配层状态同步测试仍失败。

### Task 3: 实现决策适配层

**Files:**
- Create: `backend/services/advisor/decision_adapter.py`
- Modify: `backend/db/__init__.py`
- Test: `backend/tests/test_decision_adapter.py`

- [ ] **Step 1: 实现量化账本到档案的字段转换**

实现以下公共函数：

```python
ensure_decision_record_for_investment(investment_id: int) -> dict
sync_archive_status(archive_id: int, archive_status: str, user_note: str = "") -> dict
sync_archive_review(archive_id: int, review: dict) -> dict
unified_decision(investment_id: int | None = None, archive_id: int | None = None) -> dict | None
```

字段转换固定为：`fund_code → target_code`、`fund_name → target_name`、`verdict/decision_type → decision_type`、`signal_snapshot + evidence → evidence`、`risk_check → risk`、数值置信度转换为 `high/medium/low`。创建档案时使用 `source_type="decision_pipeline"` 和量化账本 ID 作为 `source_id`。

- [ ] **Step 2: 实现幂等和失败隔离**

已有关联直接返回档案；没有关联才创建并链接。同步失败返回结构化错误，不抛出影响原业务的异常；日志必须包含两端 ID。

- [ ] **Step 3: 实现状态和复盘同步**

`accepted → confirmed`、`executed → executed`、`expired → expired`、`reviewed → closed`。`rejected/deferred` 不改量化状态，只追加用户备注或同步事件。复盘内容写入量化账本 `notes` 或 `review_result` 的扩展字段，保留量化回测数据。

- [ ] **Step 4: 运行适配层测试**

Run: `cd backend && pytest tests/test_decision_adapter.py -q`

Expected: PASS。

### Task 4: 接入流水线、档案状态和复盘路由

**Files:**
- Modify: `backend/services/advisor/decision_pipeline.py`
- Modify: `backend/routers/decision/decisions.py`
- Modify: `backend/routers/analysis/decision.py`
- Test: `backend/tests/test_decision_adapter.py`
- Test: `backend/tests/test_decision_full_loop.py`

- [ ] **Step 1: 流水线写入档案**

在 `run_investment_decision()` 成功创建 `investment_decisions` 后调用 `ensure_decision_record_for_investment()`，在返回卡片中增加 `decision_record_id` 和 `sync_status`。适配失败时仍返回量化卡片，并标记 `sync_status="failed"`。

- [ ] **Step 2: 档案状态路由同步账本**

在 `update_decision_status_api()` 成功更新档案后调用 `sync_archive_status()`，返回 `sync_status` 和统一对象。原状态校验和旧响应字段继续保留。

- [ ] **Step 3: 档案复盘路由同步用户反馈**

在 `record_decision_review_api()` 完成旧复盘写入后调用 `sync_archive_review()`，返回关联账本的回测字段和用户反馈摘要。

- [ ] **Step 4: 量化账本详情返回关联档案**

在 `/api/decision/ledger` 列表和详情响应中附加 `decision_record_id`；详情可选附加统一对象，避免改变已有账本字段含义。

- [ ] **Step 5: 运行后端回归测试**

Run: `cd backend && pytest tests/test_decision_adapter.py tests/test_decision_full_loop.py tests/test_audit_contracts.py -q`

Expected: PASS。

### Task 5: 前端显示关联和统一入口

**Files:**
- Modify: `frontend/src/api/index.js`
- Modify: `frontend/src/components/decision/DecisionCenter.vue`
- Modify: `frontend/src/components/decision/DecisionRecordsPage.vue`
- Create or modify: `frontend/src/__tests__/decisionAdapter.test.js`

- [ ] **Step 1: 添加统一对象 API**

新增 `getUnifiedDecision({ investmentId, archiveId })` 和 `syncDecisionRecord(archiveId)`，统一处理后端返回的 `sync_status`。

- [ ] **Step 2: 决策中心增加档案入口**

账本行显示“查看档案”；已有 `decision_record_id` 时直接打开/跳转决策档案，没有时显示“生成档案”，调用同步接口后刷新账本。

- [ ] **Step 3: 决策档案详情显示量化信息**

关联量化账本时展示置信度、建议金额、估值信号、组合风控、止盈计划、回测结果和同步状态；人工档案无关联时维持现有展示。

- [ ] **Step 4: 明确两类统计文案**

将档案统计标注为“用户复盘反馈”，将中心统计标注为“量化回测命中率”，不再共用“胜率”这一模糊标题。

- [ ] **Step 5: 运行前端测试**

Run: `cd frontend && npm test -- --run src/__tests__/decisionAdapter.test.js src/__tests__/auditProtocols.test.js`

Expected: PASS。

### Task 6: 全量验证和文档收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-10-18-decision-adapter-design.md`
- Modify: `docs/superpowers/plans/2026-10-18-decision-adapter-plan.md`

- [ ] **Step 1: 运行后端目标回归**

Run: `cd backend && pytest tests/test_decision_adapter.py tests/test_decision_full_loop.py tests/test_backtest_history_regressions.py tests/test_audit_contracts.py -q`

Expected: PASS。

- [ ] **Step 2: 运行前端决策相关测试和构建**

Run: `cd frontend && npm test -- --run src/__tests__/decisionAdapter.test.js src/__tests__/auditProtocols.test.js && npm run build`

Expected: 测试通过，Vite 构建成功。

- [ ] **Step 3: 检查变更范围**

Run: `git diff --stat && git status --short`

Expected: 只包含决策适配代码、决策测试和本阶段文档；现有家庭资产相关未提交文件不被改写。

- [ ] **Step 4: 提交 A 阶段变更**

```bash
git add backend/db/decisions.py backend/db/investment_decisions.py backend/db/__init__.py \
  backend/services/advisor/decision_adapter.py backend/services/advisor/decision_pipeline.py \
  backend/routers/decision/decisions.py backend/routers/analysis/decision.py \
  backend/tests/test_decision_adapter.py backend/tests/test_decision_full_loop.py \
  backend/tests/test_audit_contracts.py frontend/src/api/index.js \
  frontend/src/components/decision/DecisionCenter.vue \
  frontend/src/components/decision/DecisionRecordsPage.vue \
  frontend/src/__tests__/decisionAdapter.test.js \
  docs/superpowers/specs/2026-10-18-decision-adapter-design.md \
  docs/superpowers/plans/2026-10-18-decision-adapter-plan.md
git commit -m "feat: unify decision archive and quant ledger"
```
