# 知识库治理层（Knowledge Governance）

> 2026-10-23 建立。解决「知识库存的是大模型已有的东西，而大模型没有的东西没存进来」。

## 一、修复前的问题（实测数据，非推测）

| 缺陷 | 证据 |
|---|---|
| 无领域边界 | 579 条个人笔记里 **578 条** 是 AI 工程书籍笔记（LangChain / RAG / Prompt 工程），与投资检索共享同一向量空间 |
| 无流派标识 | 技术/交易派 3541 条（40%）、价值派 1992 条（22%），同库共存，检索按相似度投票，**不自称立场** |
| 无时序语义 | `as_of_date` 填写率 **10/8852 = 0.11%**，1896 年的统计与 2026 年的规律被同等对待 |
| 教训子系统空转 | 414 条 `atom_type='user_lesson'` 实为书摘；**没有任何一条** `category='user_lesson'`，`get_lessons_for_target()` 永远返回空 |
| 检索排序缺陷 | `db.knowledge.search_knowledge` 用 `ORDER BY importance DESC`，**完全忽略匹配质量** |
| 中文语义污染 | 查「黄金」召回「止损执行的**黄金**法则」「预训练**黄金**三角」「AI 笔记的**黄金**三角」 |

## 二、设计原则

1. **确定性优先于语义推断**。书名→流派用 37 本手工映射表，不用 LLM 猜。可审计、可复现、可 review。
2. **不物理删除**。非本域内容标注 `domain` 后靠检索层过滤，原记录保留 —— Obsidian 是用户的原始资料，知识库只是它的投影，在投影层删数据会让人误以为源文件也没了。
3. **预演优先**。所有治理写操作默认 dry-run，`--apply` 才落库。
4. **披露而非隐瞒**。流派分歧不消灭，摆到 LLM 面前要求它标明出处。
5. **仓位式权重**。有用度/流派偏置的幅度必须与 RRF 分同量级（±0.015），不允许压过相关性。

## 三、新增 schema（幂等，纯增列）

`knowledge_base` 新增 5 列：

| 列 | 取值 | 说明 |
|---|---|---|
| `domain` | `investment` / `ai_engineering` / `other` | 领域边界 |
| `school` | `value` / `technical` / `quant` / `macro` / `behavioral` / `history` / `asset` / `general` | 方法论流派 |
| `temporal_type` | `timeless` / `dated` / `regime_dependent` / `undated` | 内容时效性 |
| `retrieval_count` | int | 被检索次数（观测用，**不参与排序**） |
| `last_retrieved_at` | text | 最近被检索时间 |

## 四、关键判据

### 4.1 时序判定（避免概念定义被误判）

```
atom_type == 'data':
    有年份         → dated, as_of = 众数年份（平票取更早）
    无年份 + 含实证标记（数据显示/统计/截至/年间/实证…）→ undated（-2 分，需补）
    无年份 + 无实证标记                                  → timeless（定义/算例）
atom_type == 'policy'  → regime_dependent
其余（rule/principle/checklist/concept/strategy）→ timeless
```

> v1 判据只看 `atom_type=='data'`，把 1160 条概念定义打成了「有时效但缺时点」。
> 加入实证标记门槛后降到 534 条，误判率下降 54%。

### 4.2 流派路由

```
FRAMEWORK_SCHOOL_BIAS = {
  value:   {value: +0.30, history: +0.18, technical: -0.30, ...},
  trading: {technical: +0.30, value: +0.05, ...},
  quant:   {quant: +0.30, ...},
  macro:   {macro: +0.30, ...},
  neutral: {}   # 只标注，不偏置
}
实际加入分：school_bias × 0.01  →  最大 ±0.003
```

偏置幅度刻意做小：**流派是「解读视角」，不是「相关性」**。相关性高的技术派内容对价值框架依然有信息量（可以当反例读），不该被彻底压掉。

### 4.3 冲突披露

召回结果同时包含 `(value, technical)` 或 `(value, quant)` 或 `(technical, macro)` 时，在上下文**最前面**插入：

```
[知识库流派披露]
本次召回: 价值2条、技术交易1条。
当前审查框架: 价值（质量优先、长期持有）。
注意: 召回内容同时包含 价值 与 技术交易，两派方法论在「该不该择时」
「该不该长期持有」等问题上结论通常相反。请按当前框架取向组织论证；
若引用对立流派观点，必须显式标明出处流派，不得把两派论点混成一段统一结论。
每条证据末尾的 [流派:xx] 标识其方法论出处。
```

顺序很关键：放在末尾等同于事后声明。

### 4.4 中文实体消歧

```
IDIOM_GUARD = {
  "黄金": ("黄金法则","黄金三角","黄金窗口","黄金时期","黄金十年",
           "黄金时代","黄金坑","黄金比例","黄金分割","黄金周","黄金标准"),
  "龙头": ("水龙头","龙头股","龙头老大"),
  "白马": ("白马王子","白马股"),
}
```

判据：某词在文本中**只以固定搭配形式出现**时，不计为命中。
真实体必然有发生在搭配之外的独立出现，所以这条规则不会误杀实体。

## 五、实测效果

| 查询 | 修复前 | 修复后 |
|---|---|---|
| `黄金 能不能买 保值` | 黄金**法则**、黄金**三角**(AI笔记)、黄金**投资窗口** | 《黄金简史》通胀证据；《股市长线法宝》黄金长期回报；《通向财务自由之路》黄金降低组合回报 |
| `LangChain 提示词 模板` | 召回 AI 工程笔记 | **0 条**（领域过滤，正确返回空） |
| `持仓 集中度 分散` | 只有书籍理论 | 命中用户教训「HHI=0.195，等效 5.1 个标的」 |
| `止损 择时 该不该卖`（framework=value） | 混合 | 价值派占 2/3 且排首，conflict=True 已披露 |
| 同上（framework=trading） | 混合 | 技术派占 2/3 且排首，conflict=True 已披露 |

治理后覆盖率：

```
domain          100%      (investment 8281 / ai_engineering 578 / other 1)
school         93.47%     (technical 3541 / value 1992 / history 1065 / asset 581
                           macro 462 / quant 412 / behavioral 161 / general 67)
temporal_type   100%      (timeless 6374 / dated 1109 / regime 843 / undated 534)
as_of_date     19.31%     (1711 条，原为 0.11%)
usefulness     99.56%
矛盾项            0         (atom_type='user_lesson' 与 category 不一致的条数)
```

## 六、用户教训：从「0 条」到「8 条真实」

原来的 414 条 `user_lesson` 全是书摘误标。现在改为从 `portfolio_transactions` + `portfolio_holdings` 生成，
严格排除 `is_hypothetical=1` / `is_system=1` / 未确认（pending）记录。生成结果：

1. 实盘组合整体浮亏 **-9.9%**（成本 63.83 万，现值 57.49 万）
2. **10 只**持仓浮亏超 15%，其中 **7 只**无任何减仓记录
3. 持仓成本集中度 **HHI=0.195（等效 5.1 个标的）**
4. 真实交易：买入 **39** 笔、卖出 **6** 笔（卖出/买入 = **15.38%**）
5. 交易表中存在 **271 笔模拟交易（246.23 万）**，统计时必须排除
6. **12 只**持仓缺少建仓日期，持有期类结论不可采信
7. **3 只**已清仓标的仍留在持仓表，会虚增持仓数量
8. 资金分布在 2 个账户，单一账户最高占 **71.9%**

幂等：以 title 为键去重，重复运行 `new=0`。

## 七、代码位置

| 文件 | 职责 |
|---|---|
| `backend/services/knowledge_governance.py` | 判定规则、schema 迁移、治理动作、运行时元数据缓存、框架路由、实体消歧 |
| `backend/services/user_lessons.py` | 从真实交易生成教训 |
| `backend/scripts/kb_governance.py` | CLI（默认预演，`--apply` 落库） |
| `backend/services/rag/rag.py` | 领域过滤、流派标注与偏置、冲突披露、有用度重算、实体消歧接入 |
| `backend/db/knowledge.py` | `search_knowledge` 排序修复、`add_knowledge` 自动推断治理字段 |
| `backend/routers/knowledge/knowledge.py` | `/api/knowledge/governance/report`、`/governance/run` |
| `backend/tests/test_knowledge_governance.py` | 38 项确定性回归 |

## 八、操作手册

```bash
cd backend

# 查看现状
python3 scripts/kb_governance.py --report

# 预演（默认，不写库）
python3 scripts/kb_governance.py --all

# 落库
python3 scripts/kb_governance.py --all --apply

# 新增教训后单独索引（否则写进去了但检不到）
python3 scripts/kb_governance.py --index-lessons --apply
```

配置项（`rag_config` 表；**注意键名不带 `rag.` 前缀**，与该表既有约定一致）：

| key | 默认 | 说明 |
|---|---|---|
| `default_domains` | `investment` | 默认检索领域，`*` 取消过滤 |
| `default_framework` | `value` | 审查框架 `value/trading/quant/macro/neutral` |
| `rag.exclude_note_from_investment_search` | `true` | 已有开关，R-5 引入 |

> 坑：`get_rag_config(key, default)` 的 `default` 参数优先级**高于** `_RAG_CONFIG_DEFAULTS`。
> 调用时传了 default 会让代码默认值和表配置一起失效。本模块的两个调用都刻意不传 default。

## 九、已知局限（未在本轮处理）

1. **流派按书、不按原子**。同一本书内部也可能有跨流派观点，当前粒度无法体现。这是可靠性换精度的取舍。
2. **`as_of_date` 仍只有 19%**。剩下 81% 是无具体年份的方法论/概念条目，本来就不需要时点。
3. **ChromaDB 元数据未重建**。`domain`/`school` 已写入新建索引，但历史 8860 条向量没有这些 metadata。当前靠 Python 后置过滤兜住，若要真正在向量层过滤需执行 `scripts/reindex_vectors.py`（耗时长）。
4. **`entity_occurrences` 掩码法有边界情况**。若固定搭配与实体互相嵌套（如「黄金法则」里再出现独立的「黄金」），计数会偏保守，倾向多保留。
5. **`scripts/sync_knowledge_to_rag.py` 是一个会重复索引的历史脚本**（写入 `content_type='knowledge'`，与 `book`/`note` 重复）。本轮未删除，但**不要运行它**。
6. **流派路由目前影响排序，不改变召回集**。RRF 融合后的候选池由 FTS/向量决定，流派只调池内排名。要让流派影响「谁进候选池」，需要在检索时就按 `school` 做 where 过滤。
