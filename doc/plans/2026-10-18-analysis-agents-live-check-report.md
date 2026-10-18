# 12 个分析 Agent 真实运行检查

检查日期：2026-10-18，Asia/Shanghai。完成于 14:09:51。

结论：本机服务中的 12 个分析 Agent 均已通过真实业务调用并生成非空结果。检查中发现并修复交易复盘 HTTP 500、热门话题解析不兼容、全景报告截断/推理耗尽输出额度三个代码问题；代理失效通过重载客户端后复测恢复。

环境：`http://127.0.0.1:8000`，当前 MiMo 模型和数据库原有 Prompt，最多两个分析并发。运行前创建本地 SQLite 备份；本轮仅执行分析入口，正常生成报告和建议候选。

## 逐项结果

耗时来自本轮统一分析日志；源记录编号可用于本机排查。每项均检查来源记录中实际保存的非空结果。

| Agent ID | 分析 Agent | 结论 | 分析耗时（秒） | 源记录 | 结果字符数 |
|---|---|---|---:|---|---:|
| 1 | 市场日报分析师 | 通过 | 45.5 | 历史 #211 | 2467 |
| 2 | 分散度分析师 | 通过 | 72.9 | 持仓分析 #215 | 1232 |
| 3 | 全景诊断分析师 | 通过 | 168.2 | 持仓分析 #223 | 5496 |
| 4 | 基金深度分析师 | 通过 | 90.6 | 持仓分析 #217 | 3732 |
| 5 | 交易复盘分析师 | 通过 | 71.4 | 持仓分析 #218 | 2216 |
| 8 | 债券配置顾问 | 通过 | 140.8 | 持仓分析 #219 | 2724 |
| 9 | 指数深度分析师 | 通过 | 59.4 | 历史 #209 | 3457 |
| 10 | 市场情报分析师 | 通过 | 88.9 | 历史 #207 | 401 |
| 11 | 增强策略分析师 | 通过 | 68.2 | 历史 #210 | 2000 |
| 12 | 情景推演分析师 | 通过 | 146.2 | 持仓分析 #221 | 3979 |
| 13 | 热点分析专家 | 通过 | 97.9 | 历史 #206 | 3173 |
| 14 | 指定基金分析师 | 通过 | 57.6 | 持仓分析 #220 | 2766 |

## 实测入口与输入

| Agent ID | 真实业务接口 | 本轮输入 |
|---|---|---|
| 1 | `POST /api/dashboard/daily-report/regenerate` | 重新生成今日日报 |
| 2 | `POST /api/portfolio/analysis/diversification/ai-summary?agent_id=2` | 当前持仓 |
| 3 | `POST /api/portfolio/analysis/panorama` | 当前持仓 |
| 4 | `POST /api/portfolio/analysis/deep-dive/5` | 现有持仓 161725 |
| 5 | `POST /api/portfolio/analysis/trade-review` | 原始交易记录（未限定日期） |
| 8 | `POST /api/bond/ai-recommend` | 当前持仓及债券数据 |
| 9 | `POST /api/analysis/run` | 中证白酒 399997.SZ |
| 10 | `POST /api/market-intelligence/overview/trigger` | 市场情报刷新 |
| 11 | `GET /api/valuation/enhanced-strategy` | 当前低估指数候选 |
| 12 | `POST /api/portfolio/analysis/what-if` | 市场整体下跌 10% |
| 13 | `POST /api/dashboard/hotspots-analysis` | 市场热点分析 |
| 14 | `POST /api/portfolio/analysis/fund-analysis` | 基金 161725 |

## 已修复的具体问题

1. **交易复盘启动前 HTTP 500**：历史快照存在 `pe_percentile: null`，统计求和抛 TypeError。现在仅统计合法 PE 分位，兼容数字字符串和百分号；坏快照、空值不会中断分析，也不会用默认 50% 代替。报告明确标注有效快照覆盖率和缺失数量。用原始交易重跑，源记录 #218 成功。

2. **市场情报热门话题被吞掉**：实际盈米返回 `data` 列表，原代码当字典访问 `.get()`。现在兼容 `data.items`、`data` 列表和顶层列表，单个坏 JSON 或非对象条目不会丢弃其余有效数据。实调修复后的采集函数正常返回列表，本次上游无话题。

3. **全景诊断不完整结果被标为成功**：首轮 #216 正文在表格中间结束，completion 用满 6000 token；复测 #222 用满额度但没有可见正文。增加有界续写，`finish_reason=length` 时保留 `reasoning_content` 和原上下文，最多追加两轮，累计实际 token；包括仅推理未输出正文的情况。持续截断或空正文会明确失败，不会作为完成报告保存。最终成功记录见上表。

最终实测任务 #459 在日志中真实触发一次续写，两次模型调用合计 92556 token；源记录 #223 保存了 5496 字符正文，并有完整结尾。此机制会增加长报告的调用耗时与 token 用量。

## 运行环境与数据限制

- 首轮债券、指数分析出现连接错误，且测试进程后续请求指向停止监听的本机 1082 代理。新进程直连本机服务正常，模型服务返回认证响应。重载服务重建客户端后，同一业务入口复测成功；未永久修改代理配置。
- 日志中仍有基金穿透数据上游 HTTP 429（日调用额度用尽）及 akshare 解析失败；部分基金走东方财富备用数据源。指数 `882011` 的估值补全仍存在缺失。这些属于实际数据完整性限制，分析可运行不代表数据全部齐全。
- 本轮没有进行投资判断正确率评分，也不能据此证明所有网络状态与所有输入都可用。

## 开发者模式的 Agent 管理

现有页面支持 Prompt 编辑、版本和回滚、查看 Prompt 变更触发的回归结果，尚无逐个分析 Agent 的手动试跑按钮。本轮直接调用上表业务接口完成检查；结果保存在相应业务历史和统一分析日志。此次业务实测不会伪装成管理页的质量回归评分。

## 自动回归验证

先复现失败，再修复。最终以下定向回归共 **102 项通过**：

```sh
python3 -m pytest -q \
  backend/tests/test_analysis_text_completion.py \
  backend/tests/test_market_hot_topic_payloads.py \
  backend/tests/test_trade_review_snapshot_health.py \
  backend/tests/test_analysis_route_health.py \
  backend/tests/test_portfolio_analysis.py \
  backend/tests/test_user_critical_contracts.py
```

另执行 `git diff --check`。测试输出有已有的 Pydantic `min_items` 弃用提示。
