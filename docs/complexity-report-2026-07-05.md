# 📊 代码复杂度热力图分析报告

> 生成时间: 2026-07-05T11:25:31.735833
> 项目: `investment-analyzer/backend`

## 📈 总览

| 指标 | 数值 |
|------|------|
| Python 文件数 | 24 |
| 总代码行数 | 35,036 |
| C级以上复杂函数 | 327 |
| 🔴 F级函数（必须拆分） | 31 |
| 🟠 E级函数（应优先拆分） | 25 |
| MI < 10 的文件 | 16 |
| 死代码项（非测试） | 24 |

## 🔥 高风险文件热力图

### 重点关注文件（5大巨型文件）

| 文件 | 行数 | 最差复杂度 | MI | MI等级 | C+函数数 |
|------|------|-----------|-----|--------|----------|
| `backend/agent/orchestrator.py` | 4,419 | F | 0.0 | C | 13 |
| `backend/db/portfolio.py` | 2,911 | F | 0.0 | C | 15 |
| `backend/services/rag.py` | 2,570 | F | 0.0 | C | 14 |
| `backend/db/decisions.py` | 2,347 | F | 0.0 | C | 16 |
| `backend/tools/__init__.py` | 2,193 | F | 0.0 | C | 14 |

#### `backend/agent/orchestrator.py` Top 复杂函数

| 函数 | 行号 | 等级 | 复杂度 |
|------|------|------|--------|
| `orchestrate_stream()` | 2924 | F | 219 |
| `orchestrate()` | 2262 | F | 116 |
| `_classify_complexity_by_rules()` | 1273 | E | 37 |
| `clarify_requirement()` | 1386 | E | 36 |
| `detect_conflicts()` | 903 | E | 34 |

#### `backend/db/portfolio.py` Top 复杂函数

| 函数 | 行号 | 等级 | 复杂度 |
|------|------|------|--------|
| `compare_funds()` | 1466 | F | 65 |
| `analyze_trade_patterns()` | 2332 | F | 56 |
| `confirm_transaction()` | 771 | F | 48 |
| `_recalculate_holding()` | 570 | E | 39 |
| `classify_fund_category()` | 1816 | E | 36 |

#### `backend/services/rag.py` Top 复杂函数

| 函数 | 行号 | 等级 | 复杂度 |
|------|------|------|--------|
| `build_rag_context_with_details()` | 1886 | F | 148 |
| `_apply_personalization_boost()` | 198 | F | 84 |
| `_enrich_results_with_time()` | 1606 | D | 30 |
| `rebuild_fts_index()` | 490 | D | 29 |
| `_filter_old_results()` | 1485 | D | 29 |

#### `backend/db/decisions.py` Top 复杂函数

| 函数 | 行号 | 等级 | 复杂度 |
|------|------|------|--------|
| `build_decision_precheck()` | 1348 | F | 83 |
| `match_pending_decisions()` | 2151 | F | 46 |
| `ensure_dashboard_decisions()` | 1108 | E | 34 |
| `create_transaction_draft_from_decision()` | 1640 | D | 30 |
| `create_decision_from_candidate()` | 698 | D | 26 |

#### `backend/tools/__init__.py` Top 复杂函数

| 函数 | 行号 | 等级 | 复杂度 |
|------|------|------|--------|
| `_calculate_metrics()` | 1299 | F | 41 |
| `_validate_tool_arguments()` | 634 | E | 34 |
| `_execute_tool_impl()` | 769 | E | 34 |
| `_get_macro_policy_data()` | 1943 | E | 33 |
| `_query_valuation()` | 987 | D | 29 |

### 其他高风险文件（行数≥800 或 复杂度≥40）

| 文件 | 行数 | 最大复杂度 | MI | MI等级 |
|------|------|-----------|-----|--------|
| `backend/routers/analysis/diversification.py` | 0 | 87 | 21.3 | A |
| `backend/routers/analysis/health_score.py` | 1,067 | 72 | 0.0 | C |
| `backend/scripts/analyze_chat_quality.py` | 0 | 70 | 27.6 | A |
| `backend/routers/analysis/index_analysis.py` | 0 | 69 | 26.1 | A |
| `backend/routers/valuation.py` | 993 | 66 | 8.9 | C |
| `backend/routers/portfolio.py` | 972 | 65 | 9.9 | B |
| `backend/routers/analysis/deep_dive.py` | 0 | 60 | 33.2 | A |
| `backend/routers/dashboard.py` | 0 | 54 | 20.1 | A |
| `backend/routers/analysis/bond_recommend.py` | 0 | 52 | 34.0 | A |
| `backend/scripts/test_rag_recall.py` | 0 | 52 | 40.0 | A |

## 🔴 F级函数（复杂度 51+，必须立即拆分）

| 文件 | 函数 | 行号 | 复杂度 |
|------|------|------|--------|
| `backend/agent/orchestrator.py` | `orchestrate_stream()` | 2924 | **219** |
| `backend/services/rag.py` | `build_rag_context_with_details()` | 1886 | **148** |
| `backend/agent/orchestrator.py` | `orchestrate()` | 2262 | **116** |
| `backend/routers/analysis/diversification.py` | `_run_diversification_ai_summary_async()` | 173 | **87** |
| `backend/services/rag.py` | `_apply_personalization_boost()` | 198 | **84** |
| `backend/db/decisions.py` | `build_decision_precheck()` | 1348 | **83** |
| `backend/routers/analysis/health_score.py` | `calc_fear_greed_index()` | 782 | **72** |
| `backend/scripts/analyze_chat_quality.py` | `main()` | 122 | **70** |
| `backend/routers/analysis/index_analysis.py` | `_run_index_analysis_async()` | 55 | **69** |
| `backend/routers/valuation.py` | `get_super_value_indexes()` | 392 | **66** |
| `backend/routers/portfolio.py` | `scan_portfolio_alerts()` | 331 | **65** |
| `backend/db/portfolio.py` | `compare_funds()` | 1466 | **65** |
| `backend/routers/analysis/deep_dive.py` | `fund_deep_dive_api()` | 40 | **60** |
| `backend/db/portfolio.py` | `analyze_trade_patterns()` | 2332 | **56** |
| `backend/routers/valuation.py` | `get_enhanced_strategy()` | 634 | **55** |
| `backend/routers/dashboard.py` | `get_dashboard()` | 181 | **54** |
| `backend/routers/analysis/bond_recommend.py` | `_do_bond_recommend()` | 84 | **52** |
| `backend/scripts/test_rag_recall.py` | `run_all_tests()` | 284 | **52** |
| `backend/routers/analysis/daily_report.py` | `_run_regenerate_daily_report_async()` | 150 | **51** |
| `backend/scripts/complexity_report.py` | `analyze()` | 113 | **51** |
| `backend/routers/analysis/hotspots.py` | `_do_hotspots_analysis()` | 52 | **49** |
| `backend/services/rebalancer.py` | `analyze_rebalancing_need()` | 43 | **49** |
| `backend/routers/dashboard.py` | `get_hot_topics()` | 410 | **48** |
| `backend/db/portfolio.py` | `confirm_transaction()` | 771 | **48** |
| `backend/routers/analysis/trade_review.py` | `trade_review_api()` | 31 | **47** |
| `backend/db/decisions.py` | `match_pending_decisions()` | 2151 | **46** |
| `backend/routers/analysis/market_intel.py` | `_do_market_intelligence()` | 487 | **45** |
| `backend/routers/analysis/health_score.py` | `calc_stock_bond_ratio()` | 653 | **44** |
| `backend/agent/multi_agent.py` | `run_specialist()` | 275 | **44** |
| `backend/routers/analysis/health_score.py` | `calc_behavior_score()` | 328 | **43** |
| `backend/tools/__init__.py` | `_calculate_metrics()` | 1299 | **41** |

## 🟠 E级函数（复杂度 21-30，应优先拆分）

| 文件 | 函数 | 行号 | 复杂度 |
|------|------|------|--------|
| `backend/services/portfolio_context.py` | `build_portfolio_context()` | 9 | 40 |
| `backend/app.py` | `_auto_daily_report()` | 517 | 39 |
| `backend/routers/conversations.py` | `send_message_api()` | 593 | 39 |
| `backend/db/portfolio.py` | `_recalculate_holding()` | 570 | 39 |
| `backend/app.py` | `_build_valuation_context()` | 817 | 38 |
| `backend/routers/analysis/correlation.py` | `_build_correlation_matrix()` | 77 | 38 |
| `backend/agent/orchestrator.py` | `_classify_complexity_by_rules()` | 1273 | 37 |
| `backend/services/article_reader.py` | `fetch_article()` | 48 | 37 |
| `backend/agent/multi_agent.py` | `run_specialist_with_context()` | 496 | 36 |
| `backend/agent/orchestrator.py` | `clarify_requirement()` | 1386 | 36 |
| `backend/db/portfolio.py` | `classify_fund_category()` | 1816 | 36 |
| `backend/services/daily_position_advisor.py` | `run_daily_position_advice()` | 30 | 36 |
| `backend/routers/analysis/health_score.py` | `calc_quality_score()` | 87 | 35 |
| `backend/tools/__init__.py` | `_validate_tool_arguments()` | 634 | 34 |
| `backend/tools/__init__.py` | `_execute_tool_impl()` | 769 | 34 |
| `backend/agent/orchestrator.py` | `detect_conflicts()` | 903 | 34 |
| `backend/db/decisions.py` | `ensure_dashboard_decisions()` | 1108 | 34 |
| `backend/tools/__init__.py` | `_get_macro_policy_data()` | 1943 | 33 |
| `backend/db/portfolio.py` | `get_portfolio_diversification()` | 2230 | 33 |
| `backend/routers/analysis/four_pots.py` | `calc_dca_optimization()` | 213 | 32 |
| `backend/agent/orchestrator.py` | `route_to_specialists_by_keywords()` | 1702 | 32 |
| `backend/scripts/sync_obsidian.py` | `sync_vault()` | 134 | 32 |
| `backend/services/daily_position_advisor.py` | `_generate_signals()` | 203 | 32 |
| `backend/routers/conversations.py` | `resume_conversation()` | 190 | 31 |
| `backend/agent/orchestrator.py` | `detect_complexity_by_keywords()` | 1611 | 31 |

## 💀 可维护性指数最差的文件（Top 15）

| 文件 | MI | 等级 |
|------|-----|------|
| `backend/routers/conversations.py` | 0.0 | C |
| `backend/routers/analysis/health_score.py` | 0.0 | C |
| `backend/tools/__init__.py` | 0.0 | C |
| `backend/agent/orchestrator.py` | 0.0 | C |
| `backend/scripts/distill_period.py` | 0.0 | C |
| `backend/db/decisions.py` | 0.0 | C |
| `backend/db/portfolio.py` | 0.0 | C |
| `backend/services/rag.py` | 0.0 | C |
| `backend/routers/analysis/market_intel.py` | 3.51 | C |
| `backend/routers/articles.py` | 4.83 | C |
| `backend/scripts/distill.py` | 5.28 | C |
| `backend/app.py` | 7.2 | C |
| `backend/services/image_parser.py` | 7.77 | C |
| `backend/routers/eval.py` | 7.89 | C |
| `backend/routers/valuation.py` | 8.92 | C |

## 🧹 死代码检测（vulture, confidence ≥ 80%）

共检测到 **87** 处死代码，其中非测试代码 **24** 处。

### 非测试文件死代码详情

#### `backend/agent/orchestrator.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 14 | unused import 'cancel_running_agents' | 90% |
| 23 | unused import 'ParallelExecutor' | 90% |
| 1172 | unused variable 'available_specialists' | 100% |
| 1452 | unreachable code after 'return' | 100% |

#### `backend/infra/schemas.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 22 | unused variable 'cls' | 100% |
| 128 | unused variable 'cls' | 100% |
| 141 | unused variable 'cls' | 100% |

#### `backend/agent/analysis_runner.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 42 | unused variable 'record_summary' | 100% |
| 43 | unused variable 'record_input' | 100% |

#### `backend/app.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 73 | unused import 'traceback' | 90% |
| 136 | unused import 'get_lineage' | 90% |

#### `backend/services/daily_position_advisor.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 19 | unused import 'expire_old_signals' | 90% |
| 914 | unreachable 'else' expression | 100% |

#### `backend/agent/conversation_evaluator.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 52 | unused variable 'use_llm' | 100% |

#### `backend/agent/orchestrator_optimizer.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 12 | unused import 'lru_cache' | 90% |

#### `backend/db/agents.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 658 | unused variable 'error_message' | 100% |

#### `backend/infra/akshare_safe.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 4 | unused import 'with_circuit_breaker' | 90% |

#### `backend/mcp/trading_calendar.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 8 | unused import 'lru_cache' | 90% |

#### `backend/routers/analysis/eval_system.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 13 | unused import 'update_prompt_scores' | 90% |

#### `backend/routers/analysis/rolling_return.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 18 | unused import 'save_bond_yield' | 90% |

#### `backend/routers/dashboard.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 11 | unused import 'get_recommendation_feedback_stats' | 90% |

#### `backend/routers/eval.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 763 | unreachable code after 'try' | 100% |

#### `backend/routers/portfolio.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 28 | unused import 'list_bad_cases' | 90% |

#### `backend/services/valuation.py`

| 行号 | 问题 | 置信度 |
|------|------|--------|
| 179 | unused variable 'index_pb_pct' | 100% |

## ✅ 拆分建议（按优先级排序）

1. 🔴 [backend/agent/orchestrator.py:2924] orchestrate_stream() 复杂度 F(219)，必须立即拆分
2. 🔴 [backend/services/rag.py:1886] build_rag_context_with_details() 复杂度 F(148)，必须立即拆分
3. 🔴 [backend/agent/orchestrator.py:2262] orchestrate() 复杂度 F(116)，必须立即拆分
4. 🔴 [backend/routers/analysis/diversification.py:173] _run_diversification_ai_summary_async() 复杂度 F(87)，必须立即拆分
5. 🔴 [backend/services/rag.py:198] _apply_personalization_boost() 复杂度 F(84)，必须立即拆分
6. 🔴 [backend/db/decisions.py:1348] build_decision_precheck() 复杂度 F(83)，必须立即拆分
7. 🔴 [backend/routers/analysis/health_score.py:782] calc_fear_greed_index() 复杂度 F(72)，必须立即拆分
8. 🔴 [backend/scripts/analyze_chat_quality.py:122] main() 复杂度 F(70)，必须立即拆分
9. 🔴 [backend/routers/analysis/index_analysis.py:55] _run_index_analysis_async() 复杂度 F(69)，必须立即拆分
10. 🔴 [backend/routers/valuation.py:392] get_super_value_indexes() 复杂度 F(66)，必须立即拆分
11. 🟠 [backend/services/portfolio_context.py:9] build_portfolio_context() 复杂度 E(40)，应优先拆分
12. 🟠 [backend/app.py:517] _auto_daily_report() 复杂度 E(39)，应优先拆分
13. 🟠 [backend/routers/conversations.py:593] send_message_api() 复杂度 E(39)，应优先拆分
14. 🟠 [backend/db/portfolio.py:570] _recalculate_holding() 复杂度 E(39)，应优先拆分
15. 🟠 [backend/app.py:817] _build_valuation_context() 复杂度 E(38)，应优先拆分
16. 🟠 [backend/routers/analysis/correlation.py:77] _build_correlation_matrix() 复杂度 E(38)，应优先拆分
17. 🟠 [backend/agent/orchestrator.py:1273] _classify_complexity_by_rules() 复杂度 E(37)，应优先拆分
18. 🟠 [backend/services/article_reader.py:48] fetch_article() 复杂度 E(37)，应优先拆分
19. 🟠 [backend/agent/multi_agent.py:496] run_specialist_with_context() 复杂度 E(36)，应优先拆分
20. 🟠 [backend/agent/orchestrator.py:1386] clarify_requirement() 复杂度 E(36)，应优先拆分
21. 🟡 [backend/routers/conversations.py] 可维护性指数 0.0 (等级 C)，文件过大或过于复杂，建议拆分模块
22. 🟡 [backend/routers/analysis/health_score.py] 可维护性指数 0.0 (等级 C)，文件过大或过于复杂，建议拆分模块
23. 🟡 [backend/tools/__init__.py] 可维护性指数 0.0 (等级 C)，文件过大或过于复杂，建议拆分模块
24. 🟡 [backend/agent/orchestrator.py] 可维护性指数 0.0 (等级 C)，文件过大或过于复杂，建议拆分模块
25. 🟡 [backend/scripts/distill_period.py] 可维护性指数 0.0 (等级 C)，文件过大或过于复杂，建议拆分模块
26. 🔵 [backend/agent/orchestrator.py] 4419 行，建议按功能域拆分为多个子模块
27. 🔵 [backend/db/portfolio.py] 2911 行，建议按功能域拆分为多个子模块
28. 🔵 [backend/services/rag.py] 2570 行，建议按功能域拆分为多个子模块
29. 🔵 [backend/db/decisions.py] 2347 行，建议按功能域拆分为多个子模块
30. 🔵 [backend/tools/__init__.py] 2193 行，建议按功能域拆分为多个子模块
31. 🟣 检测到 24 处死代码（非测试），涉及 16 个文件，建议清理

## 📖 复杂度等级参考

| 等级 | 圈复杂度 | 含义 |
|------|----------|------|
| A | 1-5 | 简单，低风险 |
| B | 6-10 | 较简单，低风险 |
| C | 11-15 | 中等复杂度，中等风险 |
| D | 16-20 | 较复杂，较高风险 |
| E | 21-30 | 高复杂度，高风险 |
| F | 31+ | 极高复杂度，必须拆分 |

| MI 等级 | MI 值 | 含义 |
|---------|-------|------|
| A | 20+ | 高可维护性 |
| B | 10-19 | 中等可维护性 |
| C | <10 | 低可维护性 |
