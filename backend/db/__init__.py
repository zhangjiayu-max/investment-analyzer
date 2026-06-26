"""SQLite 数据层 — 从各子模块重导出，保持 `from db import xxx` 向后兼容。"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from db._conn import DB_PATH, _get_conn, _row_to_dict
from db._utils import (
    _add_column_if_not_exists,
    _fix_unique_constraint,
    _fix_holdings_unique_constraint,
    _migrate_old_schema,
    _extract_url_id,
)

# 估值数据 — 从 db.valuations 重导出
from db.valuations import (
    save_valuation, get_valuation_history, get_latest_valuation,
    list_valuation_indexes, list_index_freshness, search_indexes_by_keyword,
    get_index_info, save_index_info, get_valuation_by_image,
    save_dd_valuation, list_dd_valuations, get_dd_valuation, get_dd_parsed_image_paths,
)

# 任务 CRUD
from db.tasks import create_task, update_task, get_task, list_tasks, delete_task

# 螺丝钉图片解析任务 CRUD
from db.dd_tasks import (
    create_dd_parse_task, update_dd_parse_task, get_dd_parse_task,
    find_running_task, list_dd_parse_tasks,
)

# Token 用量 + 性能监控
from db.token_usage import (
    list_token_usage, count_token_usage, get_today_token_total,
    get_token_usage_summary, get_token_budget_info, get_token_usage_by_caller,
    get_token_usage_daily, get_performance_stats, get_performance_by_agent,
    get_cost_estimate, get_token_usage_hourly, get_trace_tokens, get_token_usage_by_model,
    ensure_indexes,
)

# 推荐 + 反馈 + 用户画像
from db.dashboard import (
    save_recommendations, list_recommendations, verify_recommendation,
    auto_verify_pending_recommendations, save_recommendation_feedback,
    list_recommendation_feedback, get_recommendation_feedback_stats,
    save_llm_feedback, list_llm_feedback, get_user_profile,
    update_user_profile, increment_feedback_count,
    create_chat_feedback,
    get_quality_summary, get_quality_trend, get_low_quality_items,
)

# Agent 系统
from db.agents import (
    _init_preset_agents, _init_wealth_specialists, list_agents, get_agent, create_agent, update_agent,
    delete_agent, save_prompt_version, list_prompt_versions, get_prompt_version,
    create_agent_run, get_agent_runs,
    load_specialist_agents, clear_specialist_cache,
)

# 对话 + 消息 + 摘要
from db.conversations import (
    list_conversations, get_conversation, create_conversation, update_conversation,
    delete_conversation, get_messages, create_message, update_message_metadata,
    update_message_content_and_metadata, get_conversation_summary, save_conversation_summary,
    create_assistant_placeholder, mark_message_execution_status,
    get_latest_recoverable_assistant, retry_assistant_message,
)

# 评测集
from db.eval import (
    init_eval_tables, create_eval_case, list_eval_cases, get_eval_case,
    update_eval_case, delete_eval_case, create_eval_run, update_eval_run,
    list_eval_runs, get_eval_stats, get_eval_run_detail,
    list_eval_cases_by_agent, get_eval_case_avg_score,
    save_rag_feedback, get_rag_feedback_stats,
)

# 文章 + 分析记录 + 作者文章 + 链接文章
from db.articles import (
    sync_articles, list_articles, get_article, get_article_by_seq, get_article_by_url,
    create_article, update_article, delete_article,
    create_analysis_record, update_analysis_record,
    list_all_analysis_records, get_analysis_records, get_analysis_record,
    create_author_article, update_author_article, get_author_article_by_url,
    list_author_articles, get_author_article, delete_author_article, count_author_articles,
    create_linked_article, list_linked_articles, get_linked_article, update_linked_article_file,
    delete_linked_article, update_linked_article_embed_status, save_document_chunks,
    get_document_chunks,
)

# 持仓管理全领域
from db.portfolio import (
    create_holding, get_holding, get_holding_by_fund, list_holdings, update_holding, delete_holding,
    get_portfolio_summary, get_cash_balance, get_total_cash_balance, add_cash, set_cash_balance,
    accrue_cash_interest, save_rebalance_config, get_active_rebalance_config,
    list_rebalance_configs, get_rebalance_config_by_id, rollback_rebalance_config,
    create_transaction, list_transactions, _recalculate_holding, confirm_transaction,
    auto_confirm_due_transactions,
    settle_transaction, delete_transaction, fetch_fund_nav, get_fund_nav_history,
    refresh_holding_price, refresh_all_fund_prices, lookup_fund_info, classify_bond_type,
    classify_fund_category, get_fund_holdings, create_alert, list_alerts,
    get_unread_alert_count, mark_alert_read, delete_alert, add_transaction_tag,
    remove_transaction_tag, get_transaction_tags, get_portfolio_diversification,
    get_transaction_summary, clear_all_portfolio_data, create_portfolio_analysis_record,
    list_portfolio_analysis_records, get_portfolio_analysis_record,
    update_analysis_feedback, list_bad_cases, list_all_bad_cases,
    delete_portfolio_analysis_record, save_analysis_cache, get_analysis_cache,
    get_cached_fund_holdings, get_portfolio_penetration,
    backfill_valuation_snapshots,
    save_portfolio_snapshot, list_portfolio_snapshots,
)

# AI 分析 Agent + 历史 + Prompt 常量
from db.analysis import (
    _init_analysis_tables, list_analysis_agents, get_analysis_agent,
    update_analysis_agent, create_analysis_history, list_analysis_history,
    get_analysis_history_item, get_analysis_history_status, update_analysis_history,
    delete_analysis_history,
    DEFAULT_MARKET_ANALYST_PROMPT, DEFAULT_DIVERSIFICATION_PROMPT,
    DEFAULT_PANORAMA_PROMPT, DEFAULT_FUND_DEEP_DIVE_PROMPT,
    DEFAULT_TRADE_REVIEW_PROMPT, DEFAULT_WHATIF_PROMPT,
    DEFAULT_HOTSPOTS_PROMPT, DEFAULT_BOND_PROMPT,
    DEFAULT_INDEX_DEEP_ANALYSIS_PROMPT,
)

# 债券知识库 + 投资策略
from db.bond_knowledge import (
    seed_bond_knowledge, get_bond_market_data, save_bond_market_data,
    seed_investment_strategy_knowledge,
)

# 系统配置
from db.config import (
    init_default_configs, get_config, get_config_int, get_config_float, get_config_list,
    list_configs, update_config, reset_configs,
)

# 关注列表
from db.watchlist import (
    add_to_watchlist, get_watchlist_item, get_watchlist_by_fund,
    list_watchlist, update_watchlist_item, remove_from_watchlist,
    batch_add_to_watchlist, refresh_watchlist_navs, get_watchlist_summary,
)

# 主题机会
from db.opportunities import (
    init_opportunity_tables, save_opportunity, get_opportunity,
    list_opportunities, update_opportunity_status,
    create_decision_from_opportunity, mark_opportunity_bought,
)

# 目标账户 / 资金桶
from db.goal_buckets import (
    init_goal_bucket_tables, create_goal_bucket, get_goal_bucket,
    list_goal_buckets, update_goal_bucket, delete_goal_bucket,
    get_goal_bucket_summary,
)

# 回测结果持久化
from db.backtest_results import (
    init_backtest_tables, save_backtest, list_backtests,
    get_backtest, delete_backtest, link_backtest_to_decision,
)

# 知识图谱
from db.knowledge_graph import (
    init_knowledge_graph, add_entity, get_entity, search_entities,
    add_relationship, get_related_entities,
    add_entity_mention, get_entities_for_knowledge, get_knowledge_for_entity,
    bulk_import_entities,
)
from db.health_score import (
    save_health_score, get_health_score, list_health_scores,
    save_bond_yield, get_latest_bond_yield, get_bond_yield_history,
)

# 异步分析任务
from db.async_tasks import (
    init_async_tasks_table, create_async_task, update_async_task,
    get_async_task, list_async_tasks, get_latest_async_task,
    get_latest_done_task,
)

# 理财决策中枢
from db.decisions import (
    init_decision_tables, create_decision, create_decision_action,
    create_chat_decision_draft,
    list_decisions, list_today_decisions, get_decision,
    update_decision_status, update_decision_action_status,
    ensure_dashboard_decisions, list_due_decision_reviews,
    record_decision_review, build_decision_precheck,
    create_peer_review, list_peer_reviews, count_high_risk_reviews,
    match_pending_decisions, create_transaction_draft_from_decision,
    create_recommendation_candidate, get_recommendation_candidate,
    list_recommendation_candidates, update_recommendation_candidate_status,
    extract_recommendation_candidates_from_analysis, create_decision_from_candidate,
    defer_recommendation_candidate, expire_recommendation_candidates,
    create_candidate_from_structured_recommendation,
    get_decision_stats,
)


def init_db():
    """建表，启动时调用。各子模块的 init_tables(conn) 负责创建自己的表。"""
    conn = _get_conn()

    # ── 指数估值表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT,
            snapshot_date TEXT NOT NULL,
            current_point REAL,
            change_pct REAL,
            metric_type TEXT NOT NULL DEFAULT '市盈率',
            current_value REAL,
            percentile REAL,
            danger_value REAL,
            median REAL,
            opportunity_value REAL,
            max_value REAL,
            min_value REAL,
            avg_value REAL,
            zscore REAL,
            background_color TEXT,
            source_image TEXT,
            source_url TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(index_code, snapshot_date, metric_type)
        )
    """)

    # 迁移：为已有表添加 background_color 字段
    _add_column_if_not_exists(conn, "index_valuations", "background_color", "TEXT")

    # 迁移旧表数据（pe_ttm/pb 分列 → metric_type 统一字段）
    try:
        conn.execute("SELECT pe_ttm FROM index_valuations LIMIT 1")
        # 旧表存在，迁移
        _migrate_old_schema(conn)
    except sqlite3.OperationalError:
        pass  # 已经是新表

    # 修复 UNIQUE 约束：确保包含 metric_type（处理已存在但约束不对的表）
    _fix_unique_constraint(conn)

    # ── 螺丝钉估值表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dd_valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_path TEXT UNIQUE,
            image_url TEXT,
            update_date TEXT,
            market_temperature REAL,
            index_count INTEGER,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── 螺丝钉图片解析任务表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dd_parse_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_path TEXT NOT NULL,
            image_name TEXT,
            parse_type TEXT DEFAULT 'dd',
            status TEXT DEFAULT 'pending',
            result_json TEXT,
            dd_id INTEGER,
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── 任务表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            author TEXT,
            publish_time TEXT,
            content_text TEXT,
            codes_found TEXT,
            market_data TEXT,
            llm_analysis TEXT,
            images_dir TEXT,
            local_images TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── 文章管理表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seq INTEGER,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            publish_time TEXT,
            status TEXT DEFAULT 'pending',
            images_dir TEXT,
            manifest_path TEXT,
            image_count INTEGER DEFAULT 0,
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER REFERENCES articles(id),
            image_index INTEGER,
            image_path TEXT UNIQUE,
            image_url TEXT,
            index_code TEXT,
            index_name TEXT,
            metric_type TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            raw_response TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── Agent 系统表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            system_prompt TEXT NOT NULL,
            knowledge_scope TEXT,
            icon TEXT DEFAULT 'robot',
            is_preset INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '新对话',
            agent_id INTEGER REFERENCES agents(id),
            context_data TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS author_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            author TEXT DEFAULT '研究员雷牛牛',
            publish_time TEXT,
            summary TEXT,
            content_text TEXT,
            content_html TEXT,
            article_type TEXT,
            tags TEXT,
            read_count INTEGER,
            like_count INTEGER,
            images TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "author_articles", "images", "TEXT")
    _add_column_if_not_exists(conn, "author_articles", "content_html", "TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS linked_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            title TEXT DEFAULT '',
            file_path TEXT,
            file_size INTEGER,
            file_type TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "linked_articles", "file_path", "TEXT")
    _add_column_if_not_exists(conn, "linked_articles", "file_size", "INTEGER")
    _add_column_if_not_exists(conn, "linked_articles", "file_type", "TEXT")
    _add_column_if_not_exists(conn, "linked_articles", "embed_status", "TEXT DEFAULT 'pending'")
    _add_column_if_not_exists(conn, "linked_articles", "chunks_count", "INTEGER DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            char_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (article_id) REFERENCES linked_articles(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            agent_key TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            query TEXT,
            result TEXT,
            tool_calls TEXT,
            duration_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_conv ON agent_runs(conversation_id)")

    # ── 持仓管理表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            fund_code TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            index_code TEXT,
            index_name TEXT,
            shares REAL DEFAULT 0,
            cost_price REAL DEFAULT 0,
            current_price REAL,
            total_cost REAL DEFAULT 0,
            current_value REAL,
            profit_loss REAL,
            profit_rate REAL,
            buy_date TEXT,
            last_update TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, fund_code)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_user ON portfolio_holdings(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_index ON portfolio_holdings(index_code)")
    _add_column_if_not_exists(conn, "portfolio_holdings", "price_updated_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_holdings", "account", "TEXT DEFAULT '默认账户'")
    _add_column_if_not_exists(conn, "portfolio_holdings", "today_change_pct", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_holdings", "today_profit", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_holdings", "fund_category", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "portfolio_holdings", "has_base_position", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_holdings", "manager_name", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "portfolio_holdings", "manager_company", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "portfolio_holdings", "last_buy_price", "REAL")
    _add_column_if_not_exists(conn, "portfolio_holdings", "last_buy_date", "TEXT")
    # 回填已有持仓的 fund_category
    rows = conn.execute(
        "SELECT id, fund_name FROM portfolio_holdings WHERE fund_category IS NULL OR fund_category = ''"
    ).fetchall()
    for r in rows:
        cat = classify_fund_category(r["fund_name"])
        if cat != "equity":  # 默认就是 equity 类型，只回填非默认的
            conn.execute("UPDATE portfolio_holdings SET fund_category = ? WHERE id = ?", (cat, r["id"]))
    # 回填 last_buy_price / last_buy_date（从交易记录取最近一次买入价）
    try:
        conn.execute("""
            UPDATE portfolio_holdings SET
                last_buy_price = (
                    SELECT price FROM portfolio_transactions
                    WHERE holding_id = portfolio_holdings.id
                      AND transaction_type = 'buy' AND status IN ('confirmed','settled') AND price > 0
                    ORDER BY id DESC LIMIT 1
                ),
                last_buy_date = (
                    SELECT transaction_date FROM portfolio_transactions
                    WHERE holding_id = portfolio_holdings.id
                      AND transaction_type = 'buy' AND status IN ('confirmed','settled') AND price > 0
                    ORDER BY id DESC LIMIT 1
                )
            WHERE last_buy_price IS NULL
        """)
    except Exception:
        pass
    _fix_holdings_unique_constraint(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER REFERENCES portfolio_holdings(id) ON DELETE CASCADE,
            user_id TEXT DEFAULT 'default',
            fund_code TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            amount REAL NOT NULL,
            shares REAL,
            price REAL,
            transaction_date TEXT NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_holding ON portfolio_transactions(holding_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_fund ON portfolio_transactions(fund_code)")
    _add_column_if_not_exists(conn, "portfolio_transactions", "status", "TEXT DEFAULT 'confirmed'")
    _add_column_if_not_exists(conn, "portfolio_transactions", "submitted_shares", "REAL")
    _add_column_if_not_exists(conn, "portfolio_transactions", "submitted_amount", "REAL")
    _add_column_if_not_exists(conn, "portfolio_transactions", "confirmed_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "settled_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "transaction_time", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "expected_confirm_date", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "is_system", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_transactions", "fund_name", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "account", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "fee", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_transactions", "valuation_snapshot", "TEXT")

    # ── 交易操作审计日志 ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_tx_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_id INTEGER,
            holding_id INTEGER,
            fund_code TEXT,
            fund_name TEXT,
            action TEXT NOT NULL,
            operator TEXT DEFAULT 'user',
            before_status TEXT,
            before_shares REAL,
            before_amount REAL,
            before_price REAL,
            after_status TEXT,
            after_shares REAL,
            after_amount REAL,
            after_price REAL,
            input_shares REAL,
            input_amount REAL,
            input_price REAL,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_tx ON portfolio_tx_audit_log(tx_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_fund ON portfolio_tx_audit_log(fund_code)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            alert_type TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            title TEXT NOT NULL,
            content TEXT,
            related_fund_code TEXT,
            related_fund_name TEXT,
            source TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON portfolio_alerts(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_read ON portfolio_alerts(is_read)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_cash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default' UNIQUE,
            balance REAL DEFAULT 0,
            last_interest_date TEXT,
            today_interest REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "portfolio_cash", "last_interest_date", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_cash", "today_interest", "REAL DEFAULT 0")

    # ── 持仓快照表（每日记录市值） ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            snapshot_date TEXT NOT NULL,
            total_value REAL DEFAULT 0,
            total_cost REAL DEFAULT 0,
            cash REAL DEFAULT 0,
            holding_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, snapshot_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_user ON portfolio_snapshots(user_id, snapshot_date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS transaction_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER REFERENCES portfolio_transactions(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_tags_tx ON transaction_tags(transaction_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_analysis_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            analysis_type TEXT NOT NULL,
            summary TEXT,
            input_data TEXT,
            result_data TEXT,
            token_usage INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_par_user ON portfolio_analysis_records(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_par_type ON portfolio_analysis_records(analysis_type)")
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN agent_id INTEGER DEFAULT NULL")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN feedback TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN feedback_note TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN root_cause TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN root_cause_detail TEXT DEFAULT ''")
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rebalance_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            strategy TEXT NOT NULL,
            config_json TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_user ON rebalance_config(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_active ON rebalance_config(is_active)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL UNIQUE,
            index_name TEXT,
            info TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            agent_type TEXT NOT NULL DEFAULT 'conversation',
            system_prompt TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apv_agent ON agent_prompt_versions(agent_id, agent_type)")

    # ── Token 用量统计表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            caller TEXT DEFAULT '',
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "token_usage", "caller", "TEXT DEFAULT ''")

    # 初始化预设 Agent
    _init_preset_agents(conn)

    # 初始化理财专家团队编排专家（wealth_advisor/behavior_coach/macro_strategist）
    _init_wealth_specialists(conn)

    # 初始化评测集表
    init_eval_tables(conn)

    # 初始化健康分表
    from db.health_score import init_health_score_tables
    init_health_score_tables(conn)

    # 初始化记忆表（记忆生命周期管理）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT DEFAULT '',
            evidence_count INTEGER DEFAULT 1,
            confidence REAL DEFAULT 0.5,
            is_pinned INTEGER DEFAULT 0,
            is_compacted INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            last_accessed TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_memories_type ON user_memories(memory_type)")

    # ── 系统配置表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 初始化默认配置（传入连接避免死锁）
    init_default_configs(conn)

    # Skill 文档表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bond_market_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_type TEXT NOT NULL,
            content TEXT NOT NULL,
            snapshot_date TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── RAG 检索配置表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── 指数代码映射表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_code_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT,
            aliases TEXT,
            sina_code TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(index_code)
        )
    """)

    # 初始化 AI 分析表（传入连接避免死锁）
    _init_analysis_tables(conn)

    # ── Agent Harness 工程化：Trace 全链路追踪 ──────────────────────────
    # execution_traces 表：记录一次对话的完整执行链路
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT UNIQUE NOT NULL,
            conversation_id INTEGER,
            query TEXT,
            complexity TEXT,
            status TEXT DEFAULT 'running',
            started_at TEXT DEFAULT (datetime('now','localtime')),
            finished_at TEXT,
            total_ms INTEGER,
            phase_timings TEXT,
            quality_metrics TEXT,
            error_category TEXT DEFAULT 'none',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_conv ON execution_traces(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_status ON execution_traces(status)")

    # tool_audit_logs 表：记录每次工具调用的审计日志
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT,
            tool_name TEXT NOT NULL,
            arguments TEXT,
            result_preview TEXT,
            success INTEGER DEFAULT 1,
            error_category TEXT DEFAULT 'none',
            duration_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_audit_trace ON tool_audit_logs(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_audit_name ON tool_audit_logs(tool_name)")

    # orchestration_config 表：编排策略配置
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orchestration_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # agent_runs / rag_logs / token_usage 增加 trace_id 字段
    _add_column_if_not_exists(conn, "agent_runs", "trace_id", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "token_usage", "trace_id", "TEXT DEFAULT ''")
    # rag_logs 的 trace_id 在 log_rag_search 中通过 CREATE TABLE 包含

    # agent_runs 增加 status 字段（success / error / timeout）
    _add_column_if_not_exists(conn, "agent_runs", "status", "TEXT DEFAULT 'success'")

    # agent_runs 增加时间追踪字段
    _add_column_if_not_exists(conn, "agent_runs", "started_at", "TEXT")
    _add_column_if_not_exists(conn, "agent_runs", "completed_at", "TEXT")

    # analysis_history 支持异步任务状态
    _add_column_if_not_exists(conn, "analysis_history", "status", "TEXT DEFAULT 'done'")
    _add_column_if_not_exists(conn, "analysis_history", "error_msg", "TEXT DEFAULT ''")

    # knowledge_base 表：投资知识库
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            subcategory TEXT,
            title TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            source TEXT,
            keywords TEXT,
            importance INTEGER DEFAULT 5,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_category ON knowledge_base(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_subcategory ON knowledge_base(subcategory)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_importance ON knowledge_base(importance)")

    # ── 编排检查点表（增强1：状态机检查点）──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orchestration_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id INTEGER NOT NULL,
            message_id INTEGER,
            phase TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(conv_id, message_id, phase)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_checkpoint_conv ON orchestration_checkpoints(conv_id, message_id)")

    # ── 实体记忆表（增强4：实体记忆）──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entity_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_code TEXT DEFAULT '',
            attribute TEXT NOT NULL,
            old_value TEXT DEFAULT '',
            new_value TEXT NOT NULL,
            source TEXT DEFAULT 'analysis',
            source_id INTEGER,
            snapshot_date TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_memory_name ON entity_memory(entity_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_memory_date ON entity_memory(snapshot_date)")
    _add_column_if_not_exists(conn, "knowledge_base", "atom_type", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "knowledge_base", "evidence_level", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "knowledge_base", "as_of_date", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "knowledge_base", "valid_until", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "knowledge_base", "limitations", "TEXT DEFAULT '[]'")
    _add_column_if_not_exists(conn, "knowledge_base", "counterpoints", "TEXT DEFAULT '[]'")

    # 编排配置默认值
    _default_orchestration_config = [
        ("cross_review_enabled", "true", "是否启用交叉审阅"),
        ("cross_review_min_specialists", "2", "触发交叉审阅的最少专家数"),
        ("cross_review_trigger", "disagreement", "触发条件: always / disagreement / never"),
        ("arbitration_enabled", "true", "是否启用仲裁"),
        ("arbitration_complexity", "complex", "仲裁触发的最低复杂度"),
        ("max_turns", "6", "orchestrator 最大轮次"),
        ("max_tool_timeout", "30", "工具调用超时秒数"),
        ("checkpoint_enabled", "true", "是否启用检查点存档"),
        ("dynamic_spawn_enabled", "true", "是否启用动态Agent选择"),
        ("human_in_loop_enabled", "true", "是否启用人在回路确认"),
        ("human_in_loop_timeout", "30", "人在回路确认超时秒数"),
    ]
    for key, value, desc in _default_orchestration_config:
        conn.execute("""
            INSERT OR IGNORE INTO orchestration_config (key, value, description)
            VALUES (?, ?, ?)
        """, (key, value, desc))

    # ── 关注列表表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            fund_code TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            fund_category TEXT DEFAULT '',
            index_code TEXT,
            index_name TEXT,
            target_price REAL,
            target_percentile REAL,
            notes TEXT,
            priority INTEGER DEFAULT 0,
            status TEXT DEFAULT 'watching',
            current_nav REAL,
            current_percentile REAL,
            nav_updated_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, fund_code)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status)")

    # ── 异步分析任务表 ──────────────────────────────────────
    init_async_tasks_table(conn)

    # ── 理财决策中枢表 ──────────────────────────────────────
    init_decision_tables(conn)

    # ── 短线主题机会表 ──────────────────────────────────────
    init_opportunity_tables(conn)

    # ── 目标账户 / 资金桶表 ──────────────────────────────────
    init_goal_bucket_tables(conn)

    # ── 回测结果持久化表 ──────────────────────────────────
    init_backtest_tables(conn)

    # ── 知识图谱表 ──────────────────────────────────────
    init_knowledge_graph(conn)

    conn.commit()
    conn.close()
