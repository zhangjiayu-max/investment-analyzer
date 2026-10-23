# 知识库治理论证与交付（2026-10-23）

## 一句话结论

按 P0→P3 全部落地：知识库从「一堆互相矛盾、不分领域、不标时点的文本」变成**可路由、可追溯、会自我披露分歧**的证据层。设计细节见 `docs/KNOWLEDGE_GOVERNANCE.md`。

## 做了什么

| 优先级 | 事项 | 结果 |
|---|---|---|
| P0 | 领域边界 + 流派标注 + 检索路由 | domain 覆盖 100%，school 覆盖 93.47% |
| P0 | 清理非投资笔记污染 | 578 条 AI 工程笔记标注 `ai_engineering` 并从投资检索剔除，记录未删除 |
| P1 | 补 `as_of_date` + 时序类型 | 填写率 0.11% → **19.31%**；`temporal_type` 覆盖 100% |
| P1 | 重建 `user_lesson` | 从 0 条真实教训 → **8 条**，全部来自实盘表且可追溯 |
| P2 | 修检索精度 | 搜索排序改为标题命中优先；新增固定搭配消歧，`黄金法则/黄金三角` 不再冒充「黄金资产」 |
| P3 | 启用 `usefulness_score` | 从全 0（等于没做）→ 0-10 分双源（质量先验 + 反馈），覆盖率 99.56% |

## 关键判断（不是所有问题都该用同一种解法）

1. **流派按「书」映射，不按原子猜。** 一本书的方法论自洽，按原子做语义分类会得出「止损法则属于价值派」这种自相矛盾的标签。牺牲精度换可靠性。
2. **不物理删除非本域内容。** Obsidian 是用户的原始资料，知识库只是投影。在投影层删数据会让人误以为源文件也没了。
3. **流派分歧不消灭，摆到 LLM 面前。** 召回同时包含价值派与技术派时，在上下文最前面插 `[知识库流派披露]`，要求标明出处流派、不得融合两派。隐瞒分歧比暴露分歧危险得多。
4. **偏置幅度必须与 RRF 同量级。** 流派偏置最终入分 ±0.003（RRF 是 0.01~0.03）；有用度原实现 `score*0.05` 能加 0.5，会完全压过相关性，已改为以 5 为中位、斜率 0.003。
5. **加权必须在截断之前。** 原代码 `[:limit]` 在函数最开头，所有加权都在其后 —— 加权只能改变「已入选者的排序」，改变不了「谁入选」。治理的意义恰恰在于改变入选集合，已前移。

## 实测效果

```
「黄金 能不能买 保值」
  修复前: 黄金法则(止损) / 黄金三角(AI笔记) / 黄金投资窗口
  修复后: 《黄金简史》通胀证据 / 《股市长线法宝》长期回报 / 《通向财务自由之路》降低组合回报

「LangChain 提示词 模板」        → 0 条（领域过滤，正确返回空）
「持仓 集中度 分散」              → 命中用户教训「HHI=0.195，等效 5.1 个标的」
「止损 择时 该不该卖」
  framework=value   → 价值派 2/3 且排首，conflict=True 已披露
  framework=trading → 技术派 2/3 且排首，conflict=True 已披露
```

## 对「理财决策」的直接价值

原来的教训子系统**整体空转**（零条 `category='user_lesson'`，查询永远返回空）。现在每次涉及持仓/止损/集中度的分析，都会带回这些只有这个系统才有的证据：

- 实盘浮亏 **-9.9%**（成本 63.83 万 → 现值 57.49 万）
- **10 只**持仓浮亏超 15%，其中 **7 只**从未出现卖出动过
- 成本集中度 **HHI=0.195**，28 只有成本的标的，等效风险敞口只有 **5.1** 个
- 真实交易：买入 **39** 笔、卖出 **6** 笔（15.38%）
- 交易表里混着 **271 笔模拟交易（246.23 万）**，任何交易频率统计漏掉过滤就会被淹没
- **12 只**缺建仓日期 → 「买入后跌 X%」这类结论必须先回溯净值才能采信

## 验证

- 新增 `backend/tests/test_knowledge_governance.py`：**38 项**确定性回归（领域/时序/原子类型/实体消歧/框架路由/教训生成的数字与幂等）
- 相关回归：`test_rag_evidence_quality.py` + `test_knowledge_quality_regressions.py` 共 **88 项**全过
- 全量套件：**1982 passed / 2 failed**。两条失败均为既有基线问题（`test_arbitration_keeps_asset_actions...` 是 2026-08-02 质量门收紧后测试未同步；`test_sell_preview_profit_calculation` 是 cost_basis 语义漂移），与本次改动无关

## 操作手册

```bash
cd backend
python3 scripts/kb_governance.py --report          # 看现状
python3 scripts/kb_governance.py --all             # 预演（默认不写库）
python3 scripts/kb_governance.py --all --apply     # 落库
python3 scripts/kb_governance.py --index-lessons --apply   # 新增教训后必须索引
```

API：`GET /api/knowledge/governance/report`、`POST /api/knowledge/governance/run`（默认 `dry_run=true`）。

## 还没做的（诚实清单）

1. ChromaDB 历史 8860 条向量的 `domain`/`school` metadata 未重建 —— 当前靠 Python 后置过滤兜住，要在向量层真过滤需跑 `scripts/reindex_vectors.py`（耗时长）。
2. 流派只影响排序，不改候选池。要让它影响「谁进候选池」，需在检索时按 `school` 做 where 过滤。
3. `as_of_date` 还有 81% 为空，但这些多为不含年份的方法论/概念条目，本来不需要时点。
4. `scripts/sync_knowledge_to_rag.py` 是会**重复索引**的历史脚本（写 `content_type='knowledge'`，与 `book`/`note` 重复），本轮未删，但**不要运行**。
