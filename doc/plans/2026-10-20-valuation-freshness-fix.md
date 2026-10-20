# 估值新鲜度修复方案与执行计划

目标：单指数分析、AI 对话和估值概览按同指数同指标的真实来源日期选取数据，避免把历史估值当作当前估值。

设计：统一比较主估值表、螺丝钉、六亿的本地候选，名称只通过唯一的标准名称或显式别名解析代码。来源日期有效且不超过 3 个自然日才可用于当前估值判断；否则补查在线来源。在线缓存的抓取时间不能替代估值日期，在线结果不得覆盖日期更新的本地结果。失败时保留历史数据与明确的降级说明，不输出当前低估/高估标签，不跨来源拼接百分位趋势。

采用共享的数据选择与展示函数，保留现有查询 API。仅修改当前取数链路，不更改历史数据，不加入自动抓图或后台更新任务。工作区原有的取消任务、解析日期等改动不纳入此次提交。

技术栈：Python / SQLite / pytest，外部行情调用在测试中隔离。

## 执行步骤

- [ ] 新建 `backend/tests/test_valuation_freshness.py`，复现螺丝钉无法查询、跨来源日期选择、在线旧数据覆盖、缓存过期、概览与问答绕过校验等问题；运行 pytest 确认失败。
- [ ] 在 `backend/db/valuation_sources.py` 统一日期状态、候选选择和图片估值名称解析；在 `backend/db/valuations.py` 接入三个本地来源，过期时先在线补查再降级，并校验缓存和来源日期。
- [ ] 在 `backend/tools/__init__.py` 让具体估值、在线估值和概览保留每个指标的日期/来源/新鲜度，不将过期数据标为当前机会。
- [ ] 在 `backend/services/valuation/context.py` 统一摘要与旧问答上下文；单指数分析接入本地多来源候选并保持既有历史证据分组。
- [ ] 运行新增回归和既有估值、分析、工具相关测试，检查完整 diff；仅暂存本次改动，提交并推送当前远程分支，核对远程提交哈希。

验收命令：`python3 -m pytest -q backend/tests/test_valuation_freshness.py backend/tests/test_online_valuation_provenance.py backend/tests/test_index_analysis_contract.py backend/tests/test_valuation_image_regressions.py backend/tests/test_valuation_dedup.py backend/tests/test_smart_add_p0_governance.py`。
